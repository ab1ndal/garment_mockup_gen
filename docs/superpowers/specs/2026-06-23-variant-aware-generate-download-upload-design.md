# Design — Phase 3 partial close: variant-aware generate + download/upload

**Date:** 2026-06-23
**Branch:** `feat/phase3-image-generation`
**Status:** Approved — ready for implementation plan
**Companion to:** `docs/plans/2026-06-21-implementation-plan.md` (Phase 3)

## Goal

Tighten the existing image-generation flow and partially close Phase 3:

1. Require at least one source image selected before generating.
2. Surface a product's **colors** (the visual variation) from `productsizecolors`; let the user pick one per generation.
3. Give the generated image a **meaningful name**: `{productid}_{color-slug}_{shorthex}.png`.
4. **Attach** productid + color to the `mockup_variations` row.
5. Allow **reliable download** of the generated image.
6. Allow **uploading a corrected image** back, stored as a new variation and shown in place.

## Out of scope (deferred)

- Correction note / auto-regenerate loop.
- All Phase-4 publish behavior: no `mockups` flag flip (`base_mockup`/`mockup`/etc.), no `productimages` write, no approve-to-official pipeline.

## Context — current state

- `POST /api/generate/image` already: resolves product → Drive folder → downloads selected refs (`image_ids`, falls back to all) → generates with Gemini → uploads PNG to Supabase Storage (`mockups` bucket, key `{productid}/{uuid}.png`) → inserts a `mockup_variations` row → returns a 7-day signed URL + `variation_id`.
- Frontend `ProductsTab.tsx` already has source-image multi-select (`picked` → `image_ids`) and shows the result with a `<a download>` link.
- `mockup_variations` columns: `variation_id, productid, prompt_id, prompt_text, image_url, kind, created_by, created_at`. **No `color` column.**
- `productsizecolors`: `productid, size, color, stock, variantid(uuid PK)`. ~10395 rows / 3594 products. Size does not change mockup appearance; color does. Data has dupes/typos (`Grey ` vs `Grey`, `Parrot_green` vs `Parrot Green`).

## Decisions

- **Variant granularity = color only.** Size is invisible in a mockup; one mockup per color. `variantid` is per size×color combo, so it would produce visually identical duplicates — not used.
- **Color is optional** on generate. Products without `productsizecolors` rows still generate (name omits color).
- **Source-image selection is required** (≥1) for image generation; the silent fallback-to-all is removed for `/image`.
- **Additive migration only.** Add nullable `color text` to `mockup_variations`. No existing table altered; no inventory data rewritten (color dedup/trim is read-side only).
- **Download via backend proxy.** The Supabase signed URL is cross-origin, so the browser ignores `<a download>`. A proxy endpoint streams bytes with `Content-Disposition: attachment`.
- **Uploaded corrected image** is a new `mockup_variations` row marked by `prompt_text = "(manual upload)"` (no schema change, stays out of Phase-4 status modeling).

## Data

Migration (via MCP `apply_migration`):

```sql
alter table mockup_variations add column color text;
```

Color list query (read-only):

```sql
select distinct color from productsizecolors where productid = :pid;
```

Post-process in `variants_repo`: trim, drop empties, case-insensitive dedup (keep first canonical spelling), sort.

## Components

### `mockup_generator/db/variants_repo.py` (new)
- `list_colors(client, productid) -> list[str]` — distinct colors, trimmed, deduped (case-insensitive), empties dropped, sorted.

### `mockup_generator/db/mockup_variations_repo.py`
- `insert(...)` gains `color: str | None = None` (omitted from payload when `None`).
- `get(client, variation_id) -> dict | None` — returns the row (needs `productid`, `image_url`, `color`).

### `mockup_generator/integrations/storage_client.py`
- `upload_mockup(productid, data, key, ...)` unchanged signature; callers pass a meaningful `key` (`{color-slug}_{shorthex}`), so the stored path is `{productid}/{color-slug}_{shorthex}.png`.
- `download_mockup(object_path, *, bucket=_BUCKET) -> bytes` (new) — service client `store.download(path)`.
- Slug helper (lowercase, trim, non-alphanumeric → `-`, collapse repeats) — in `storage_client` or a small util; `shorthex` = first 8 chars of a uuid4 hex (uniqueness so re-gens don't overwrite).

### Backend endpoints
- `GET /api/products/{productid}/colors` → `{ "colors": [...] }`.
- `POST /api/generate/image`:
  - `GenerateRequest += color: str | None = None`.
  - **400** if `image_ids` is empty (`"Select at least one source image."`).
  - `color` → name key + stored on the row.
- `POST /api/generate/upload` — multipart: `productid`, optional `color`, file `image`.
  - Read bytes → `PIL.Image.open` (rejects non-images) → re-encode PNG → `storage_client.upload_mockup` → `mockup_variations_repo.insert(prompt_text="(manual upload)", color=color, created_by=user.id)`.
  - Returns `{ status, detail, image_url(signed), variation_id }`.
  - Reasonable size guard (reject oversized uploads).
- `GET /api/generate/variations/{variation_id}/download`:
  - `mockup_variations_repo.get` → `image_url` (object path) → `storage_client.download_mockup` → stream `image/png` with `Content-Disposition: attachment; filename="{productid}_{color}_{variation_id}.png"`.
  - 404 if no row; 503 if storage not configured.

### Frontend — `ProductsTab.tsx` + `api.ts`
- `api.ts`: `getProductColors(productid)`, `color` field on `generateImage`, `uploadCorrectedImage(form)` (→ `POST /api/generate/upload`), `downloadVariation(variationId)` (authed `fetch` to `GET /api/generate/variations/{id}/download` → blob → object URL → click).
- `GenerationStage`:
  - Fetch colors on product load; **color dropdown** (optional, `— no color —` default).
  - **Generate button disabled until `pickedCount > 0`**; helper text already says "Select one or more images".
  - `generateImage` sends `color`.
  - Result section: Download triggers authed blob download (not raw signed URL); add **"Upload corrected image"** file input → `uploadCorrectedImage` → swap `resultUrl` + `variation_id` to the uploaded one; show the generated filename.

## Error handling

- Empty `image_ids` on `/image` → 400.
- Upload of a non-image / corrupt file → 400 (PIL raises).
- Oversized upload → 413/400.
- Missing variation on download → 404.
- Storage/Drive not configured → 503 (existing pattern).

## Testing

- `tests/test_generate_api.py`: empty `image_ids` → 400; `color` flows into storage key + row.
- New `tests/test_variations_upload_download.py`: upload endpoint (mock storage + repo), download endpoint (mock `download_mockup`, assert attachment header + bytes).
- New `tests/test_variants_repo.py`: `list_colors` trims, dedups case-insensitively, drops empties, sorts.
- Frontend build stays clean.

## Verification

- Select a pending product → colors load → pick a color → pick ≥1 source image → Generate → result named `{productid}_{color}_{hex}.png`, row has `color`.
- Generate blocked with 0 source images selected.
- Download saves the file locally (not just opens a tab).
- Upload a corrected PNG → new variation row, result swaps to the uploaded image.
