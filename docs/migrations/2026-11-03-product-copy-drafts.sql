-- AI drafts of a product's title detail, description and tags, kept until a person
-- approves them. Nothing here touches `products`: an approved draft reaches it only
-- through the Approve and Accept routes.
--
-- Apply BEFORE deploying the backend that reads it. Additive: no existing table
-- changes, so the Inventory app and a backend on older code are unaffected.
--
-- Why a row per paid draft and not one row per product: a Regenerate must not erase
-- what the last one cost, because the daily spend cap sums these rows. `active` marks
-- the one draft the screens use; the unique index below is what stops two reviewers
-- opening the same product from paying twice.
create table if not exists public.product_copy_drafts (
  id            bigint generated always as identity primary key,
  productid     text not null references public.products(productid) on delete cascade,
  active        boolean not null default true,
  status        text not null default 'queued'
                check (status in ('queued', 'drafting', 'ready', 'failed')),
  -- Which review card asked for it, e.g. 'batch:12' or 'backfill:<file id>'. A card
  -- whose own images changed re-drafts; another colour's card reuses this draft.
  source_card   text not null,
  images_key    text not null,            -- hash of the image ids in image_refs
  image_refs    jsonb not null,           -- {originals: [drive ids], mockup_drive, mockup_storage}
  note          text,                     -- reviewer's note for a Regenerate
  attempts      integer not null default 0,
  error         text,
  draft         jsonb,                    -- the model's answer, once status = 'ready'
  input_tokens  integer not null default 0,
  output_tokens integer not null default 0,
  cost_usd      numeric(10, 4) not null default 0,
  drafted_at    timestamptz,              -- when money was last spent on this row
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create unique index if not exists product_copy_drafts_one_active
  on public.product_copy_drafts (productid) where active;
create index if not exists product_copy_drafts_queue
  on public.product_copy_drafts (status, id) where status in ('queued', 'drafting');
create index if not exists product_copy_drafts_spend
  on public.product_copy_drafts (drafted_at) where drafted_at is not null;

-- Unapproved text and spend figures: only the backend's service role may read them.
alter table public.product_copy_drafts enable row level security;
revoke all on public.product_copy_drafts from public, anon, authenticated;
grant all on public.product_copy_drafts to service_role;
