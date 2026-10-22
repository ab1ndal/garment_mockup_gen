# Optimistic publish for batch card review

**Date:** 2026-08-02
**Status:** implemented 2026-10-21 (`feat/optimistic-batch-review`)

## Problem

Approving a batch card blocks the reviewer for 2–5s. `POST /api/batch/{id}/accept`
runs the whole publish inline: download the staged PNG from `mockups-temp`, upload
the archival PNG, re-encode to WEBP, upload that, write four DB rows, delete the
staged object. `BatchTab.run()` awaits all of it before removing the card and
closing the review modal, so the reviewer stares at a disabled button between
every card. Reviewing a batch of 40 cards spends several minutes doing nothing.

Reject and Edit are quicker (no image work) but still round-trip, so the review
rhythm is uneven — the reviewer learns which buttons stall and which don't.

## Goal

Every review action — Accept, Reject, Edit — advances immediately. A failed
publish surfaces the moment it fails, naming the card, and the card comes back
for another try.

## Non-goals

- **A backend publish worker.** Moving publish onto a background pool (a
  `publishing` state, bounded drainers, orphan recovery) bounds memory by
  construction and survives a closed tab, but the browser then only learns of a
  failure on the next poll. That loses the immediate notification, which is the
  point of the feature. Revisit only if client concurrency turns out to bite.
- **A retry budget on publishes.** Failures come back to Ready for a manual
  re-approve, matching how failed generations already behave.
- **The restart-mid-publish hole.** A server restart between the status claim and
  the final transition leaves a row `published` with `storage_path` still set and
  no `productimages` row. That exists today and this change does not widen it.
- **The Failed tab's Retry button.** Stays synchronous — different tab, cheap call.

## Design

### Behaviour

On click, the card is removed from the grid, the review modal closes, and the
tab's count decrements locally. No spinner, no disabled buttons. The request runs
in the background.

- **Success:** nothing further. A `warning` in the response (e.g. the staged file
  could not be removed) still shows as a notice.
- **Failure:** the existing alert banner fires immediately. The message names the
  card, because the reviewer has moved on by then and "Action failed." is useless
  three cards later: `BC25349 (Red) could not be published: <reason>`. A quiet
  refresh then brings the card back into Ready. (The server does not write an
  error onto the row when it reverts a failed publish, so the banner is the
  only place the reason appears.)
- **409 Conflict:** the card stays removed and the banner says it was already
  handled — unchanged from today, and reachable when two tabs review at once.

The server already reverts a failed publish from `published` back to `ready`
(`backend/routers/batch.py:196`), so no backend change is needed to make the card
reappear.

### Client mechanics

`BatchTab` gains a `pending` set of card ids with an action in flight.

- The grid filters pending ids out on every render, **including the quiet-refresh
  path**. Without this there is a race: the poll timer no longer pauses during an
  action, so a refresh landing between the optimistic removal and the request
  completing would re-fetch the card as still `ready` and resurrect it, only for
  it to vanish again a moment later.
- Ids leave the set when the request settles, either way.
- The poll effect drops `busyId !== null` from its guard — nothing blocks polling
  anymore — but keeps pausing while a card is open for review, so a refresh can't
  swap the card out from under the reviewer.
- `busyId` survives only for the Failed tab's Retry.
- Counts decrement locally on click for immediate feedback, then resync from
  `loadCounts()` when the request settles.

### Memory

This feature lets the reviewer fire more concurrent publishes than before, on a
512MB Render instance that has already OOM'd once (see
`2026-08-02` memory work: batch reference loading, WEBP cap, encode semaphore).

It is safe because `publish._ENCODE_SLOTS` bounds the expensive part. A request
arriving while two encodes are running blocks **before** decoding — the semaphore
is acquired outside `Image.open` — so it holds only the ~3MB of staged PNG bytes,
not a decoded buffer. Peak RSS is therefore set by the semaphore, not by how fast
the reviewer clicks or how many tabs are open.

That is the claim the design rests on, so it gets verified rather than assumed:
measure peak RSS across a rising number of concurrent approvals and confirm it
flattens once the semaphore saturates. If it does not flatten, add a client-side
send queue capped at 2 before shipping.

### Edge cases

- **Tab closed mid-request:** FastAPI does not cancel a running sync endpoint, so
  the publish completes and the card ends up correct.
- **Two tabs approving the same card:** one wins the conditional status update,
  the other gets 409 and the "already handled" banner.
- **Rapid clicks on one card:** the card leaves the DOM on the first click, so a
  second click is not reachable.

## Testing

**As built:** the frontend has no test runner (no vitest/jest in
`frontend/package.json`), and adding one was out of scope, so the client
behaviours below were checked by `tsc -b` and `vite build` only, not by
automated tests. The memory claim was settled by a related backend change
(`perf/parallel-publish-uploads`): uploads now run outside the encode slot, the
encoded WEBPs held during upload total ~0.15 MB per publish, and the decoded
image is freed before any network I/O. The suspected "Drive hangs approval"
symptom turned out to be `async def` routes blocking the event loop, fixed on
`fix/blocking-async-endpoints`.

Planned checks:

- **Resurrect race:** a quiet refresh landing while an id is pending must not
  re-add that card to the grid.
- **Failure path:** a rejected promise fires the banner with the card named, and
  the card is refetched into Ready.
- **409 path:** card stays removed, "already handled" banner.
- **Counts:** local decrement on click, resync on settle.
- **Memory:** peak RSS flattens as concurrent approvals rise past the semaphore
  bound.

Backend is unchanged and covered by the existing suite.

## Files

- `frontend/src/components/BatchTab.tsx` — `run()`, the `pending` set, the poll
  guard, grid filtering, the card-naming error message.
- Frontend tests alongside the existing ones.

No migration: `batch_items.status` is plain `text` with no CHECK constraint, and
this design adds no new status anyway.
