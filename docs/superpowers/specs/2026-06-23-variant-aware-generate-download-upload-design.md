# Design — Phase 3 close: variant-aware generate → human approve/publish

**Date:** 2026-06-23
**Branch:** `feat/phase3-image-generation`
**Status:** Approved — ready for implementation plan
**Companion to:** `docs/plans/2026-06-21-implementation-plan.md` (Phase 3 + part of Phase 4 publish)

## Goal

1. Require at least one source image selected before generating.
2. Surface a product's **colors** (the visual variation) from `productsizecolors`; user picks one per generation.
3. Generate → **display only** (no persistence). Human reviews.
4. **Approve** publishes; **disapprove** discards. No regeneration in this spec.
5. Approve accepts either the generated image **or** a manually-corrected upload.
6. Meaningful name for the published image: `{productid}_{color-slug}_{shorthex}.png`.
7. On approve: upload to Supabase Storage, flip `mockups.base_mockup = true`, insert `productimages(productid, imageurl, caption=color)`, and record a `mockup_variations` audit row.

## Out of scope (deferred)

- Regeneration / correction-note → re-generate loop.
- Side-by-side review screen, feedback history (rest of Phase 4).
- Video generation.

## Flow

```
generate (preview)            approve (publish)                 disapprove
─────────────────             ─────────────────                 ──────────
≥1 source + color?            client POSTs final bytes          client discards
  → Gemini → PNG              (generated echo OR corrected)      preview.
  → return base64             → upload public bucket             no request,
  (NO storage, NO DB)         → mockup_variations (audit)        no writes.
                              → mockups.base_mockup = true
                              → productimages insert
                              → return public URL
```

- **Generate is stateless**: returns base64, writes nothing. Avoids persisting unverified mockups.
- **Approve is the only writer.** Two entry points (approve-generated, approve-corrected) share one publish routine; both send image bytes via multipart.
- **Download** of the preview is client-side (from the base64 already in the browser) — no backend proxy. Published images are downloadable via their public URL.

## Context — current state

- `POST /api/generate/image` currently uploads to Storage + inserts a `mockup_variations` row inline. **This changes**: generate becomes preview-only; persistence moves to approve.
- Frontend `ProductsTab.tsx` has source-image multi-select (`picked` → `image_ids`) and shows the result.
- `mockup_variations`: `variation_id, productid, prompt_id, prompt_text, image_url, kind, created_by, created_at`. **No `color` column** (add it).
- `productimages`: `imageid(PK serial), productid, imageurl(text NOT NULL), caption(text), displayorder(int default 0)`. 0 rows.
- `mockups`: PK `productid`, one row per product (3594 rows). Flip `base_mockup`.
- `productsizecolors`: `productid, size, color, stock, variantid`. Color is the visual variant; size is invisible in a mockup. Data has dupes/typos (`Grey `/`Grey`, `Parrot_green`).
- Storage bucket `mockups` exists and is **private**.

## Decisions

- **Variant granularity = color only.** Color optional on generate (products without `productsizecolors` rows still work; name omits color).
- **Source-image selection required** (≥1) for `/image`; the silent fallback-to-all is removed.
- **Bucket goes public, view-only for anon.** Setup step flips `mockups` bucket to public so the shop page renders images via a permanent public URL. Public bucket = anonymous **read by URL only**; insert/update/delete remain service-role only (backend uses the service client), so shoppers cannot delete or browse/list the bucket — view only. No anon write/list policy added.
- **`productimages.imageurl` = permanent public URL** (`get_public_url`). Directly usable by the shop page.
- **Additive DB migration only**: `alter table mockup_variations add column color text;`. No existing table altered. Inventory color data is read-only (trim/dedup happens read-side).
- **Corrected upload is not regeneration** — a human-edited replacement image, published the same way.

## Components

### Setup (infra, one-time, via MCP)
- Flip `mockups` Storage bucket to **public** (`update storage.buckets set public = true where id = 'mockups'`). Confirm no anon insert/update/delete policy exists on `storage.objects` for the bucket.
- Migration: `alter table mockup_variations add column color text;`.

### `mockup_generator/db/variants_repo.py` (new)
- `list_colors(client, productid) -> list[str]` — distinct colors, trimmed, case-insensitive deduped (keep first canonical spelling), empties dropped, sorted.

### `mockup_generator/db/mockup_variations_repo.py`
- `insert(...)` gains `color: str | None = None` (omitted from payload when `None`).

### `mockup_generator/db/mockups_repo.py`
- `set_base_mockup(client, productid, value=True)` — update the product's row (`base_mockup = value`). Row exists per product; plain update.

### `mockup_generator/db/productimages_repo.py` (new)
- `insert(client, *, productid, imageurl, caption=None, displayorder=None)` — when `displayorder` is `None`, compute next = current count for the product (avoids all-zeros).

### `mockup_generator/integrations/storage_client.py`
- `upload_mockup(productid, data, key, ...)` returns `(object_path, public_url)` via `get_public_url` (bucket now public) instead of a signed URL. Path: `{productid}/{key}.png`, key = `{color-slug}_{shorthex}`.
- Slug helper (lowercase, trim, non-alphanumeric → `-`, collapse repeats); `shorthex` = first 8 chars of a uuid4 hex (uniqueness, no overwrite on re-approve).
- `download_mockup` not needed (no proxy).

### Backend endpoints (in `routers/generate.py`)
- `GET /api/products/{productid}/colors` → `{ "colors": [...] }`.
- `POST /api/generate/image` — preview only:
  - `GenerateRequest += color: str | None = None`.
  - **400** if `image_ids` empty (`"Select at least one source image."`).
  - Generate PNG → respond `{ status, detail, image_b64 }` (base64/data URI). No storage, no DB.
- `POST /api/generate/approve` — multipart: `productid`, optional `color`, optional `prompt_text`, `source` (`"generated"|"corrected"`), file `image`.
  - PIL-validate (rejects non-images) → re-encode PNG → `upload_mockup` (public URL) → `mockup_variations_repo.insert(prompt_text, color, image_url=public_url, created_by)` → `mockups_repo.set_base_mockup(productid, True)` → `productimages_repo.insert(productid, imageurl=public_url, caption=color)`.
  - Returns `{ status, detail, image_url(public), variation_id }`.
  - Size guard on the upload.
- Disapprove: no endpoint — client discards the preview.

### Frontend — `ProductsTab.tsx` + `api.ts`
- `api.ts`: `getProductColors(productid)`; `generateImage` gains `color`, returns `{ image_b64 }`; `approveMockup(form)` → `POST /api/generate/approve`.
- `GenerationStage`:
  - Fetch colors on product load; **color dropdown** (optional, `— no color —` default).
  - **Generate button disabled until `pickedCount > 0`**.
  - Generate → show preview from base64 (held in state).
  - Result section actions: **Approve** (data-URI → Blob → `approveMockup`, `source=generated`), **Disapprove** (clear preview), **Download** (client-side from base64), **Upload corrected** (file input → `approveMockup`, `source=corrected`).
  - On approve success: show published public URL + confirmation; mark product done.

## Error handling

- Empty `image_ids` on `/image` → 400.
- Non-image / corrupt approve upload → 400 (PIL raises).
- Oversized upload → 413/400.
- Product not found → 404.
- Storage/Drive not configured → 503 (existing pattern).

## Security

- Public bucket: anonymous GET by object URL only. No anon `insert`/`update`/`delete`/`list` policy on `storage.objects` → shoppers cannot delete, replace, or enumerate bucket contents. All writes go through the backend service-role client.
- Generate/approve endpoints require an active profile (existing `get_current_user`).

## Testing

- `tests/test_generate_api.py`: empty `image_ids` → 400; generate returns base64 and writes nothing (mock storage/DB asserted not called).
- `tests/test_approve_publish.py` (new): approve uploads (public URL), inserts `mockup_variations` with color, flips `mockups.base_mockup`, inserts `productimages(imageurl, caption=color)`; corrected vs generated `source` both publish.
- `tests/test_variants_repo.py` (new): `list_colors` trims, dedups case-insensitively, drops empties, sorts.
- `tests/test_productimages_repo.py` (new): insert with computed `displayorder`.
- Frontend build clean.

## Verification

- Pending product → colors load → pick color → pick ≥1 source → Generate → preview shows, nothing persisted.
- Generate blocked with 0 source images.
- Disapprove → preview cleared, no DB/storage change.
- Approve generated → public URL returned; `mockup_variations` row has color; `mockups.base_mockup = true`; `productimages` row with `imageurl` + `caption = color`; shop page can render the public URL.
- Approve a corrected upload → same publish result with the edited image.
- Download saves the previewed image locally.
