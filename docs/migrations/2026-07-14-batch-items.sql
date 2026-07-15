-- Batch Generate worklist. One row per (product, color) card produced by a
-- batch enqueue. Cards persist here so review survives sessions and is
-- concurrency-safe (optimistic status transitions, mirroring backfill_items).
--
-- status lifecycle:
--   queued      -> awaiting generation by the background worker
--   generating  -> claimed by the worker (reset to queued on crash recovery)
--   ready        -> mockup generated + staged in Drive, awaiting review
--   failed      -> generation errored after retries (retryable -> queued)
--   published   -> accepted, copied to Supabase Storage, Drive copy deleted
--   rejected    -> discarded, Drive copy deleted
create table if not exists public.batch_items (
  id            bigint generated always as identity primary key,
  batch_id      uuid not null,
  productid     text not null references public.products(productid),
  color         text,                                  -- null = colorless product
  image_ids     jsonb not null,                        -- Drive source file ids used
  prompt_text   text not null,
  status        text not null default 'queued',
  drive_file_id text,                                  -- staged mockup in Drive (set when ready)
  thumbnail_link text,                                 -- Drive thumbnailLink of the staged mockup
  error         text,
  model         text not null,
  resolution    text not null,
  aspect_ratio  text not null,
  created_by    uuid references public.profiles(id),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create index if not exists batch_items_status_idx on public.batch_items (status);
create index if not exists batch_items_batch_idx on public.batch_items (batch_id);

-- Server-side (service-role) access only, like every other table here.
alter table public.batch_items enable row level security;
