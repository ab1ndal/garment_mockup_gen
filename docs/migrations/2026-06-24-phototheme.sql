-- Multi-variant publishing: a photo-theme dimension on productimages.
-- Apply via Supabase MCP (apply_migration) or the SQL editor.
--
-- A published mockup is now keyed by (productid, caption, phototheme) instead
-- of (productid, caption). `caption` keeps holding the color; `phototheme`
-- holds the photo-theme label (the prompt label, with a "·<aspect>" suffix for
-- non-1:1 shots). This lets different themes / aspect ratios of the same
-- product+color coexist instead of overwriting one another.
--
-- `productimages` is shared with Inventory-Management. The column is additive
-- with a safe default, so existing reads are unaffected; existing rows backfill
-- to 'Default' (= the default-prompt 1:1 image) via the column default.

alter table public.productimages
  add column if not exists phototheme text not null default 'Default';
