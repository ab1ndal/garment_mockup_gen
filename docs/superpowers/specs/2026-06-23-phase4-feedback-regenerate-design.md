# Phase 4 Design — Feedback → Regenerate Loop (in-session variation review)

**Date:** 2026-06-23
**Companion to:** `docs/plans/2026-06-21-implementation-plan.md` (Phase 4),
`docs/superpowers/plans/2026-06-23-variant-aware-generate-approve.md` (Phase 3).
**Status:** Design approved. Ready for implementation plan.

## Goal

Add an iterate-to-perfection loop to mockup generation: after a preview is
generated, the user reviews it against the source images, optionally writes a
feedback note, and regenerates — either **refining** the current image (model
sees its own prior output) or **trying again** fresh from the source images. A
running, in-session history of variations lets the user flip between attempts
and approve the best one. Approve/publish is unchanged.

## Scope reconciliation

Phase 3 already shipped the parts the original Phase 4 line listed under
"approve": upload to the public `mockups` bucket, flip `mockups.base_mockup`,
write `productimages`, append a `mockup_variations` audit row, and an inline
preview → Approve / Disapprove / Download / Upload-corrected flow in
`ProductsTab.tsx`.

The genuinely new Phase 4 work is therefore narrowed to the **feedback →
regenerate loop** plus an **input-vs-output review surface**. The "approve"
half is done and is not re-specified here.

## Locked decisions

1. **In-session history only.** Variations + their feedback live in React state
   during the review session (base64 images, like the current preview). Nothing
   new is persisted until Approve. No schema change, no Storage clutter from
   rejected attempts. History is lost on reload / navigating away — accepted.
2. **Per-regen mode toggle.** Two buttons: **Refine this** (passes the active
   variation image as an extra reference so the model edits its own output) and
   **Try again** (regenerate fresh from the source images only). Both fold the
   feedback note into the prompt.
3. **Persist on Approve only.** The existing `/approve` flow publishes the
   chosen image exactly as today — untouched.
4. **No new endpoint, no schema/Storage/migration, no new dependencies.**

## Backend

### `POST /api/generate/image` — one optional field

Extend `GenerateRequest` with `refine_image_b64: str | None = None`.

- When present: decode the base64 → PIL image, append it as the **last**
  reference after the Drive sources. Then call the unchanged
  `service.generate_mockup_bytes(images, prompt)`.
- **Combined reference cap:** Drive sources + refine image are capped at
  `_MAX_REFS = 14`. If over, the **refine image is the one dropped** (sources
  define the garment and matter more); log when this happens.
- **Source requirement relaxed for refine:** a fresh generation still requires
  ≥1 `image_ids`; a refine is valid with the prior image alone. Rule: require at
  least one of (`image_ids`, `refine_image_b64`).
- **Feedback is prompt-only:** the frontend folds the note into the `prompt`
  string it sends. The backend stays feedback-agnostic — no new feedback param.

No change to `mockup_variations`, `/approve`, repos, Storage, or any migration.

### Endpoint-shape rationale (rejected alternative)

A separate `/refine` endpoint was rejected: it would duplicate the Drive
download + generate logic for no benefit. A refine is just a fresh generation
with one extra reference image, so extending `/image` is the minimal surface.

## Frontend (`ProductsTab.tsx` / `GenerationStage`)

### State — in-session variation history

Replaces the single `previewB64`:

```ts
type Variation = {
  b64: string;
  promptUsed: string;   // full prompt sent (base + folded feedback)
  feedback: string;     // note that PRODUCED this variation ("" for the first)
  mode: "fresh" | "refine";
};
const [variations, setVariations] = useState<Variation[]>([]);
const [activeIdx, setActiveIdx] = useState(0);   // which variation is in focus
const [feedback, setFeedback] = useState("");    // note for the NEXT regen
```

The first **Generate Image** pushes `variations[0]` (`mode: "fresh"`,
`feedback: ""`).

### `api.ts`

Add optional `refine_image_b64?: string` to the `generateImage` request type.
No other API change.

### Review/iteration panel (replaces the current Preview block)

Same `card` / `field` / `btn` design system; no new dependencies.

Layout (desktop; stacks vertically on `<sm`):

```
Variation 3 of 5 · refine                        [REFINE]
┌── sources (picked) ──┐   ┌─── active variation ───────┐
│ [t][t][t]            │   │   large image, max-w-full   │
└──────────────────────┘   └─────────────────────────────┘
History  [1][2][3•][4][5]        ← horizontal filmstrip
Feedback for next version  (optional)
[ textarea ............................. ]
[ Refine this ]  [ Try again ]
──────────────── publish ────────────────
[ Approve & publish ]   Download   Upload corrected
```

### Data flow (one regenerate)

1. User types feedback (optional), clicks **Refine this** or **Try again**.
2. `prompt = feedback.trim() ? `${promptText}\n\nRevision note: ${feedback.trim()}` : promptText`.
3. `generateImage({ ...base, prompt, refine_image_b64: refineMode ? variations[activeIdx].b64 : undefined })`.
4. On success: push a new `Variation` (recording its `feedback` and `mode`), set
   `activeIdx` to it, clear the `feedback` box.
5. On failure: surface in the existing `msg` area; `variations[]` and `activeIdx`
   unchanged; **feedback text preserved** so the user can retry.

### UX decisions (per ui-ux-pro-max guidance)

- **Side-by-side input vs output:** reuse the already-picked source thumbnails
  on the left, active variation large on the right.
- **History filmstrip:** horizontal-scroll row of variation thumbnails. Active =
  `ring-2 ring-accent` (matches the existing source-pick state). Each is a real
  `<button aria-label="View variation N — refine: {feedback}">`. Counter uses
  tabular figures ("3 of 5"). Clicking is non-destructive — it only changes
  focus. **This replaces the old Disapprove button:** the user doesn't reject,
  they iterate or pick another variation.
- **Single primary CTA:** **Approve & publish** is the lone `btn-primary`, set
  off by a `publish` divider. **Download** and **Upload corrected** are
  subordinate. (Existing behavior, relocated.)
- **Two regen buttons:** **Refine this** emphasized, **Try again** plain; helper
  line states the difference. Both disable + show a spinner while `busy`;
  **Refine this** is disabled when there is no active variation.
- **Loading:** a regenerate takes ~10–30 s — spinner on the clicked button plus
  a skeleton placeholder box sized to the chosen aspect ratio, so the new image
  drops in without layout shift (CLS).
- **Feedback field:** visible label, helper text "Leave empty to regenerate
  unchanged." Folded into the prompt only when non-empty.
- **Accessibility:** `alt="Variation N"` on images, focus rings kept,
  `fresh` / `refine` shown as text badges (not color-only), filmstrip
  transitions respect `prefers-reduced-motion`.
- Optional small text button **Start over** clears the session list.

The video section is unchanged — it still gates on `publishedUrl ||
product.base_mockup`.

## Error handling

### Backend

- `refine_image_b64` decode failure (bad base64 / not an image) → `400
  "Invalid refine image."` (decode in the router, wrapped in try/except like the
  upload-validity check in `/approve`).
- Combined reference count over `_MAX_REFS` → drop the refine image, keep
  sources, log it.
- Neither source nor refine provided → `400`.
- Drive download / Gemini failures reuse the existing `502` / `503` codes; no
  new failure modes are introduced.

### Frontend

- Regenerate failure → existing `msg` error area; `variations[]` and `activeIdx`
  unchanged; feedback text preserved.
- Feedback box cleared only on success.
- Approve / Download / Upload-corrected error handling unchanged.

## Testing (TDD)

### Backend (`tests/test_generate_api.py`)

- `/image` with `refine_image_b64` → the decoded image is appended to the parts
  passed to `service.generate_mockup_bytes` (mock the service, inspect call
  args).
- Refine-only (no `image_ids`, valid `refine_image_b64`) → `200`, not `400`.
- Neither source nor refine → `400`.
- Bad `refine_image_b64` → `400`.
- Over-cap (15 sources + a refine image) → the service receives ≤14 references
  and the refine image is the one dropped.
- Existing `/image` and `/approve` tests stay green (no regression).

### Frontend

- No unit-test harness exists in `frontend/` today; keep parity by gating on
  `npm run build` (typecheck + build).
- Manual smoke documented: generate → refine with a note (model sees the prior
  image) → try-again (fresh) → switch variations via the filmstrip → approve the
  active one → publishes.

## Out of scope (future)

- Cross-session / persisted variation history (would need schema columns +
  Storage prefix + a reject-cleanup story).
- Auto-regenerate without a manual button.
- Side-by-side diff overlay between two variations.
- Feedback persistence as structured data (kept as ad-hoc prompt text this
  phase).
