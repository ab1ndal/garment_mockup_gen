-- docs/migrations/2026-07-12-edit-presets.sql
-- Global edit presets for the Drive product-shot import flow.
-- Apply via Supabase MCP (project epotsxdugwfhyeiudjox), like other docs/migrations/*.sql.
create table if not exists public.edit_presets (
    preset_id  bigint generated always as identity primary key,
    name       text not null unique,
    params     jsonb not null,
    is_default boolean not null default false,
    created_by uuid,
    created_at timestamptz not null default now()
);

-- backstop: at most one default preset
create unique index if not exists edit_presets_one_default
    on public.edit_presets (is_default) where is_default;

-- server writes via service-role (bypasses RLS); no anon policies
alter table public.edit_presets enable row level security;
