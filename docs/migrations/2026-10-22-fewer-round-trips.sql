-- Collapse sequential PostgREST round-trips into single SQL function calls.
--
-- The backend runs in the US and the database in ap-south-1 (Mumbai, next to
-- the app's users), so every PostgREST call costs ~650ms regardless of how
-- little work Postgres does (x-envoy-upstream-service-time is 3-55ms). Publish
-- made 5 calls in a row and the backfill badge counts 4; these functions make
-- each one call.
--
-- Additive only: new functions, no table changes, so the Inventory app and any
-- backend still running the old code are unaffected. Executable by the backend's
-- service role only.

-- Publish's three writes, in one transaction: the audit row, the product's
-- base_mockup flag, and the storefront productimages row. Previously a failure
-- between them could leave a variation row with no productimages row.
-- ``mockups`` has one row per product; a missing row is left missing, exactly as
-- the old ``update ... where productid = ...`` did.
create or replace function public.publish_mockup_rows(
  p_productid    text,
  p_imageurl     text,
  p_color        text,
  p_theme        text,
  p_displayorder integer,
  p_prompt_text  text,
  p_prompt_id    bigint,
  p_created_by   uuid
) returns bigint
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_variation_id bigint;
begin
  insert into public.mockup_variations
    (productid, prompt_text, image_url, kind, prompt_id, created_by, color)
  values
    (p_productid, p_prompt_text, p_imageurl, 'image', p_prompt_id, p_created_by, p_color)
  returning variation_id into v_variation_id;

  update public.mockups set base_mockup = true where productid = p_productid;

  insert into public.productimages
    (productid, imageurl, displayorder, phototheme, productcolor)
  values
    (p_productid, p_imageurl, p_displayorder, p_theme, p_color);

  return v_variation_id;
end;
$$;

revoke execute on function public.publish_mockup_rows(text, text, text, text, integer, text, bigint, uuid)
  from public, anon, authenticated;
grant execute on function public.publish_mockup_rows(text, text, text, text, integer, text, bigint, uuid)
  to service_role;

-- Backfill badge counts in one grouped query (same shape as batch_status_counts).
-- Statuses with no rows are absent; the caller fills them with 0.
create or replace function public.backfill_status_counts()
returns table(status text, n bigint)
language sql
stable
security invoker
set search_path = ''
as $$
  select status, count(*)::bigint
  from public.backfill_items
  group by status
$$;

revoke execute on function public.backfill_status_counts() from public, anon, authenticated;
grant execute on function public.backfill_status_counts() to service_role;
