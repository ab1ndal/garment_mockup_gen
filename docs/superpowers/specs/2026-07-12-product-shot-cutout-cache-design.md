# Product Shot cutout cache + Product ID display — design

**Date:** 2026-07-12
**Status:** Approved (design)
**Follows:** `2026-07-12-drive-product-shot-import-design.md`

## Problem

User feedback on the Product Shots import feature:

1. Background removal is re-initiated on every adjustment.
2. Adjustments should apply quickly.
3. The Product ID should be visible at the top when a shot is opened, and every Product Shot file should carry the Product ID in its name.
4. Occasional error: *"The server hit an unexpected error. Try again shortly."* (user assumed a rate limit).

## Root cause (verified)

Every adjustment fires a debounced `POST /api/import/preview` (`ProductShotsTab.tsx:173`), which calls `edit_pipeline.apply_edits` (`edit_pipeline.py:129`). That function **unconditionally** re-downloads the full-res source from Google Drive (`import_shots.py:35`) and re-runs the full pipeline **including BiRefNet background removal** (`_remove_background`, `edit_pipeline.py:99`). Nothing about the cutout is cached — only the rembg *session/model* is a module global; the cutout result is recomputed every call.

- **Comment 1** — cutout recomputed every adjustment. Confirmed.
- **Comment 2** — every adjustment = Drive download + CPU-bound BiRefNet inference + round-trip. Slow.
- **Comment 4** — the message is the generic 500 catch-all (`main.py:82`), **not** a rate limit. Leading real cause: **Google Drive API quota**, from re-downloading full-res on every slider move. Second candidate: BiRefNet failure (OOM / onnxruntime) on the HF Space. The actual exception type is logged at `main.py:84` in the Space logs; grabbing it is the only way to confirm a non-Drive cause. The fix below removes the Drive cause and cuts BiRefNet calls ~10–50×, so it is expected to resolve this regardless.
- **Comment 3** — Product ID appears only in the left product-picker list, not in the editor header. Output object path is `{productid}/{color}_{order}_{hex}.webp` — Product ID is the *folder*, absent from the *filename*.

## Solution overview

Separate the one expensive step (Drive download + BiRefNet cutout) from the cheap ones (rotate, straighten, brightness, saturation, autocontrast, white balance, bg color, shadow). Compute the cutout **once per Drive `file_id`**, cache it in-process, and re-apply only the cheap ops on every adjustment. Chosen approach: **server-side cutout cache** (preview stays pixel-identical to publish; one render path; smallest surgical change). Client-side canvas rendering was rejected (JS reimplementation risk, preview≠publish).

## Detailed design

### 1. Pipeline reorder (`mockup_generator/generation/edit_pipeline.py`) — pure, no I/O

Current order — `EXIF → quarter-rotate → colour/tonal → straighten → cutout → composite` — makes the cutout depend on geometry+colour, so it cannot be cached across adjustments.

New order — **`EXIF → cutout → quarter-rotate → colour/tonal → straighten → composite`**. The cutout now depends only on the source bytes, so it is cacheable by `file_id` alone.

Split `apply_edits` into two pure functions:

- `compute_cutout(src_bytes: bytes) -> Image.Image` — `Image.open` → `ImageOps.exif_transpose` → `convert("RGB")` → `_remove_background` → returns the RGBA cutout. This is the only rembg touch-point. Expensive; its result is what gets cached.
- `render(cutout: Image.Image, params: EditParams) -> bytes` — cheap ops on a given cutout:
  - Colour/tonal ops applied to the RGB channels with alpha preserved. `_gray_world` white balance receives the cutout's alpha as its `mask` so it balances on garment pixels only. `autocontrast` runs on the full RGB (matches current behavior closely enough; deterministic).
  - Quarter-rotate (`_QUARTER_CW` transpose) and straighten (`rotate`, transparent fill) applied to the RGBA.
  - Composite onto white/cream, optional drop shadow. Returns RGB PNG bytes.

`apply_edits(src_bytes, params)` is kept as a thin convenience wrapper (`render(compute_cutout(src_bytes), params)`) so any existing caller/tests still work.

**Known behavior change:** colour/tonal ops now run *after* segmentation instead of before. Output may differ subtly from the old pipeline. Acceptable: deterministic, and preview always equals publish because both go through `render`.

### 2. Cutout cache (side effect at the edge — lives in the import layer, not the pure pipeline)

A small in-process LRU in the import service/router:

- Keyed by Drive `file_id` (Drive files are immutable → key never goes stale).
- Value: the RGBA cutout `Image.Image` from `compute_cutout`.
- Capacity: **12 entries** (`OrderedDict`, move-to-end on hit, popitem(last=False) on overflow) — bounds memory on the single HF worker.
- Guarded by a `threading.Lock` (FastAPI sync endpoints run in a threadpool; concurrent access + duplicate-compute avoidance).
- `get_cutout(file_id) -> Image.Image`: on miss, `drive_client.download_file(file_id)` → `edit_pipeline.compute_cutout(...)` → store → return; on hit, return cached. Drive errors and `BackgroundRemovalUnavailable` map to the existing 502/503 as they do today.

Endpoints after change:
- `POST /api/import/preview` → `render(get_cutout(file_id), params)`. No Drive, no rembg on a cache hit.
- `POST /api/import/publish` → `_encode_webp(render(get_cutout(file_id), params))`. Same cutout, WEBP-encoded.
- **`POST /api/import/warm`** (new) → body `{file_id}` → `get_cutout(file_id)` → `{"status": "ok"}`. Pre-warms the cutout when the editor opens so the first adjustment is instant too.

### 3. Product ID (comment 3)

- **Editor header** (`ProductShotsTab.tsx`, editor pane ~line 398): show `Product ID: {selected.productid}` alongside the filename. Frontend change goes through the `ui-ux-pro-max` skill per project convention.
- **Filename** (`import_shots.py:80` `publish_shot`): prepend the product ID to the stem — `stem = "_".join(p for p in (req.productid, slug, str(order)) if p)`. Object path becomes `{productid}/{productid}_{color}_{order}_{hex}.webp`. Every downloaded file now carries its Product ID. **New shots only** — no rename/backfill of existing objects or `productimages.imageurl` rows.

### 4. Pre-warm wiring (frontend)

- `frontend/src/api.ts`: add `warmImportShot(file_id: string)` → `POST /api/import/warm`.
- `ProductShotsTab.tsx` `openEditor` (~line 162): fire-and-forget `warmImportShot(active.id)` when an image editor opens, so the cutout is computing while the user reads the controls.

## Data flow (after)

```
Open image  → POST /warm    → get_cutout(file_id): Drive download + BiRefNet (ONCE) → cached
Adjust      → POST /preview → render(cached cutout, params)   (no Drive, no rembg)  → PNG
Publish     → POST /publish → _encode_webp(render(cached cutout, params))           → WEBP
                              → path {productid}/{productid}_{color}_{order}_{hex}.webp
```

## Error handling

- Drive download failure → 502 "Could not load the image" (unchanged).
- `BackgroundRemovalUnavailable` → 503 (unchanged).
- Cache is best-effort: an eviction just means the next request recomputes. No correctness impact.

## Testing

- `compute_cutout` returns RGBA; `render` returns PNG bytes; `apply_edits` wrapper still works.
- `render` is deterministic and applies each param (rotate, straighten, brightness, saturation, autocontrast, white_balance, bg, shadow) — assert observable pixel effects.
- Cache: repeated `get_cutout(file_id)` computes the cutout exactly once (mock `compute_cutout` / `download_file`, assert call count == 1); LRU evicts the least-recently-used past capacity.
- `publish_shot` filename stem contains the product ID.
- `/warm` returns 200 and populates the cache (subsequent `/preview` does not call Drive/rembg).

## Out of scope

- Client-side canvas rendering.
- Crop control (does not exist today).
- Batch/bulk publish.
- Renaming/backfilling existing published Product Shot files.
- Changing the generic 500 handler.

## Files touched

- `mockup_generator/generation/edit_pipeline.py` — split into `compute_cutout` + `render`, reorder, keep `apply_edits` wrapper.
- `backend/routers/import_shots.py` — cutout cache, `/warm` endpoint, filename stem includes product ID.
- `backend/schemas.py` — request model for `/warm` (if not reusing an existing one).
- `frontend/src/api.ts` — `warmImportShot`.
- `frontend/src/components/ProductShotsTab.tsx` — Product ID in editor header, warm-on-open.
- Tests for the pipeline split, cache, and filename.
