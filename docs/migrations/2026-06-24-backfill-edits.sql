-- Phase 7 backfill: "Flag for Edits" third review path.
-- When a reviewer sends a generated mockup back for manual editing, the Drive
-- original moves to the edit/ worklist subfolder and a record lands here so a
-- future pass can pick up what needs fixing. file_id is stable across the Drive
-- move, so it is the handle used to re-locate the image later.
create table if not exists public.backfill_edits (
  id          bigint generated always as identity primary key,
  file_id     text not null,
  productid   text references public.products(productid),  -- nullable: unknown product allowed
  comment     text,                                        -- nullable: edit note is optional
  status      text not null default 'pending',             -- pending -> done when the edit is applied
  created_by  uuid references public.profiles(id),
  created_at  timestamptz not null default now()
);

-- Pending edits are the queue the future pickup reads.
create index if not exists backfill_edits_status_idx on public.backfill_edits (status);

-- Lock the table to server-side access only (matches every other table here).
-- The backend writes via the service-role client, which bypasses RLS, so no
-- anon/authenticated policies are needed.
alter table public.backfill_edits enable row level security;
