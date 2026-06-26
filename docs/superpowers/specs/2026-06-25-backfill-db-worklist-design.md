# DB-backed backfill review worklist

**Date:** 2026-06-25
**Status:** Design approved, pending spec review

## Problem

The backfill review tab treats Google Drive folder location as the source of
truth for review state (`published/` `rejected/` `edit/` `skipped/`). Every
listing triggers a `scan_folder_of_folders` (cached 300s + manual refresh). With
**multiple reviewers** now fast-processing a **fixed** backlog (no new photos are
being added to Drive), those scans happen far too often and are slow and
rate-limited. There is also no race protection — two reviewers can act on the
same card.

## Goal

Move review state into Postgres so all review reads hit the DB, not Drive. Seed
the table **once** from Drive; after that the only Drive calls on the request
path are (a) per-page thumbnail fetches and (b) downloading PNG bytes when
publishing. Support real pagination, concurrent reviewers, and a manual rescan
escape hatch.

## Decisions (locked)

- **Drive moves kept + DB mirror.** Files still move between reserved folders so
  downstream consumers of `edit/` / `rejected/` keep working; the DB row mirrors
  status and is authoritative for the worklist.
- **Seed once + manual rescan.** A single scan populates the table; an on-demand
  rescan reconciles. No automatic/scheduled scans.
- **Optimistic atomic transitions.** No locks; a conditional `UPDATE ... WHERE
  status='pending'` decides the winner.
- **Thumbnails fetched from Drive per page** (not cached).
- **No reviewer identity stored.** Status + timestamps only.
- **Real DB pagination** (offset/limit) — only the current page is fetched.
- **Rescan allowed by any authenticated reviewer** (not admin-gated).

## Section 1 — Data model

New table, one row per generation, seeded once from Drive.

```sql
create table public.backfill_items (
  file_id     text primary key,                            -- Drive id, stable across moves
  productid   text references public.products(productid),  -- nullable (unknown product)
  alpha       text,                                        -- parsed variant marker from filename
  filename    text not null,
  status      text not null default 'pending',             -- pending|skipped|edit|regenerate|published
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index backfill_items_status_idx on public.backfill_items (status);

alter table public.backfill_items enable row level security;  -- server-side (service role) only
```

- `status` drives the sub-tabs: `pending`→To review, `skipped`→Skipped,
  `edit`→Edits, `regenerate`→Regenerate, `published`→archived (hidden from UI).
- `unknown_product` is **not** stored — derived at query time from the products
  join (`productid is null` or no matching product row).
- Seed maps the file's current Drive folder → status: root=`pending`,
  `published/`=`published`, `rejected/`=`regenerate`, `edit/`=`edit`,
  `skipped/`=`skipped`.
- `backfill_edits` is unchanged (edit notes/comment + downstream pickup queue),
  keyed by `file_id`.

Migration file: `docs/migrations/2026-06-25-backfill-items.sql`.

## Section 2 — Read path

`GET /api/backfill/items?status=<status>&offset=<n>&limit=<n>`

1. **Count** — `select count(*) where status=:status` → pager total.
2. **Page** — `select * where status=:status order by filename limit :limit offset :offset`.
3. **Product names** — one batched `products` query `where productid in (<page pids>)`;
   map name and set `unknown_product` for missing/null. No N+1.
4. **Thumbnails** — `drive_client.thumbnails_for(page)` for the ≤20 page rows only.

The full folder-of-folders scan is removed from the request path entirely.

`GET /api/backfill/counts` → `select status, count(*) group by status` → one call
feeds all sub-tab count badges.

## Section 3 — Write path

All actions: **DB claim first (authoritative + race-safe), then Drive move (mirror).**

**Skip / flag-edit / flag-regenerate:**

```sql
update backfill_items set status=:new, updated_at=now()
where file_id=:id and status='pending'
returning file_id;
```

- 0 rows → another reviewer already handled it → HTTP `409`; frontend drops the
  card with an "already handled" notice. This conditional update is the
  optimistic-concurrency winner — no locks.
- 1 row → move the Drive file to the matching reserved folder. If the move fails,
  the DB is already correct → return a non-fatal warning (mirrors the existing
  approve-archive-failure pattern). `flag-edit` also inserts the `backfill_edits`
  row as today.

**Approve & publish** (needs PNG bytes):

1. Atomic claim `pending→published` (same conditional update). 0 rows → `409`.
2. `download_file` → `publish_image` (Supabase mockups/variations) → move Drive
   original to `published/`.
3. If publish throws → compensating update `published→pending` and return the
   error, so the card returns for retry.

**Unskip:** `skipped→pending` conditional update + move file back to the worklist
root. No scan — the row already exists, status just flips.

**Valid transitions:** only from `pending` (skip / edit / regenerate / publish),
plus `skipped→pending` (unskip). `edit` and `regenerate` are terminal in the UI.

## Section 4 — Seed + rescan

`POST /api/backfill/rescan` (any authenticated reviewer) and an equivalent
one-off seed script.

- Runs `scan_folder_of_folders` once plus lists the four reserved folders, then
  **upserts** each file into `backfill_items` with status mapped from its folder.
- Upsert by `file_id` = idempotent. New files → `pending`. For existing rows the
  Drive folder wins (reconciles files moved manually outside the app).
- This is the **only** code path that scans Drive.
- Surfaced as a "Rescan Drive" toolbar button.

The existing `backfill_service` (in-memory cache, `_TTL`, `get_index`, `paginate`,
`evict`) is deleted — the table replaces it. The `list_bucket` Drive helper added
earlier is removed from the read path (rescan uses the scan directly).

## Section 5 — Frontend

- `BackfillTab` sub-tabs unchanged in shape; each reads `?status=` with a
  **Next/Prev pager** (offset/limit) and a count badge from `/counts`.
- On a `409` from any action: show "already handled by another reviewer" and drop
  the card.
- "Rescan Drive" button in the toolbar (any reviewer).
- Existing Skip / Unskip / Review wiring stays; endpoints point at the new
  status-based paths.

## Section 6 — Testing

- **Repo unit tests:** conditional transition returns 0 vs 1 row; seed status
  mapping per folder; upsert idempotency.
- **API tests:** pagination (offset/limit/total), `409` on a double-action,
  approve revert-on-publish-failure, rescan reconcile, `/counts` grouping.
- Drive + Supabase mocked as in the existing `tests/test_backfill_*`.

## Out of scope / YAGNI

- Reviewer identity / per-transition history log.
- Thumbnail caching into Supabase Storage.
- Scheduled/cron reconciliation.
- Claim/lock concurrency model.
