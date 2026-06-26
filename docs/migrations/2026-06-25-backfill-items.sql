-- DB-backed backfill review worklist.
-- Replaces the per-request Google Drive scan: review state now lives in Postgres,
-- seeded once from Drive (and reconciled on demand via the rescan endpoint). One
-- row per generated mockup. file_id is the Drive id, stable across folder moves,
-- so it is the primary handle.
--
-- status mirrors the reserved Drive subfolder the file sits in:
--   pending      -> worklist root, awaiting review
--   skipped      -> skipped/      (deferred, re-reviewable)
--   edit         -> edit/         (flagged for manual edit; see backfill_edits)
--   regenerate   -> rejected/     (flagged for regeneration)
--   published    -> published/    (approved + published; hidden from the UI)
create table if not exists public.backfill_items (
  file_id     text primary key,                            -- Drive id, stable across moves
  productid   text references public.products(productid),  -- nullable: unknown product allowed
  alpha       text,                                        -- parsed variant marker from filename
  filename    text not null,
  thumbnail_link text,                                     -- Drive thumbnailLink; proxied per page, refreshed on rescan
  status      text not null default 'pending',
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

-- Every read filters and counts by status (one query per sub-tab + the badge counts).
create index if not exists backfill_items_status_idx on public.backfill_items (status);

-- Server-side access only (matches every other table here). The backend writes
-- via the service-role client, which bypasses RLS, so no anon/authenticated
-- policies are needed.
alter table public.backfill_items enable row level security;
