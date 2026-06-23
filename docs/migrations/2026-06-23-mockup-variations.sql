-- Phase 3: image generation output storage
-- Apply via Supabase MCP (apply_migration) or the SQL editor.

-- 1) One row per generated mockup (audit + future review/listing UI).
create table if not exists public.mockup_variations (
  variation_id bigint generated always as identity primary key,
  productid    text not null references public.products(productid),
  prompt_id    bigint references public.prompts(prompt_id),  -- nullable; prompt may be ad-hoc/edited
  prompt_text  text not null,                                -- exact prompt used (audit)
  image_url    text not null,                                -- Storage object path (signed URL minted on read)
  kind         text not null default 'image',                -- 'image' | 'video' (video later)
  created_by   uuid references public.profiles(id),
  created_at   timestamptz not null default now()
);

create index if not exists mockup_variations_productid_idx
  on public.mockup_variations (productid);

-- RLS: active profiles may read; server writes use the service key (bypasses RLS).
alter table public.mockup_variations enable row level security;

drop policy if exists mockup_variations_select_active on public.mockup_variations;
create policy mockup_variations_select_active
  on public.mockup_variations for select
  to authenticated
  using (
    exists (
      select 1 from public.profiles p
      where p.id = auth.uid() and p.is_active = true
    )
  );

-- 2) Private Storage bucket for generated mockups. Backend uploads with the
--    service role; browser renders via short-lived signed URLs.
insert into storage.buckets (id, name, public)
values ('mockups', 'mockups', false)
on conflict (id) do nothing;
