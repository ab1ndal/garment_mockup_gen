# Existing-Mockup Import with Star Watermark Removal — Design

**Date:** 2026-07-12
**Status:** Approved

## Problem

Some products already have a finished mockup image sitting in Google Drive (either in the
product's Drive folder or in the backfill mockups worklist). These images may carry a small
Gemini "sparkle" star watermark near the bottom-right corner. Today the only way to get such
an image into Supabase as a generation is to run a fresh Gemini generation — wasteful and
unnecessary. We need to publish these existing images directly, with an optional local
cleanup step that removes the star. No AI generation involved.

## Decisions (from brainstorming)

- **Manual toggle**, not auto-detection: a "Remove star watermark" checkbox controls whether
  the inpaint runs. Off by default. No preview step.
- **Products tab source = product Drive folder** (images already listed in GenerationStage).
  The backfill mockups-folder case is handled by the existing Backfill tab flow.
- **Cleanup = star inpaint only.** No frame cropping, no white-balance/brightness touch-ups.
- **Publish semantics = full generation**: `publish.publish_image` path
  (`mockup_variations` row + `mockups.base_mockup` flag + `productimages` row) — not the
  Product-Shots `productimages`-only band.

## Architecture

### 1. Watermark removal utility (new, shared)

New module `mockup_generator/generation/watermark.py` with one public function:

```python
def remove_corner_star(png_bytes: bytes) -> bytes
```

- Decodes with PIL, operates in numpy, re-encodes PNG. No new dependencies.
- Fixed relative ROI at bottom-right. Measured from the reference sample (680×1082):
  star bbox 39×39 px at ~38 px from the right and bottom edges, i.e. spanning roughly
  x ∈ [0.887, 0.944]·w, y ∈ [0.930, 0.966]·h. ROI constants are slightly generous around
  that: **x ∈ [0.86, 0.97]·w, y ∈ [0.915, 0.98]·h**, defined as named module constants so
  they are tunable in one place.
- Fill method: estimate background from the ring of pixels immediately surrounding the ROI
  and replace ROI contents with a smooth blend (distance-weighted interpolation from the ROI
  border). Works because the star always sits on flat studio background (measured flat gray
  ~RGB 221). Pure numpy + PIL.
- Never fails soft: given any decodable image it returns a valid image. On an image without
  a star it repaints flat background over flat background — visually a no-op.
- **Verify against a real Drive mockup file during implementation** — the reference sample is
  a screenshot; real files may be larger and the star's pixel offset may not scale linearly.
  Adjust ROI constants if needed.

### 2. Backfill tab (existing flow, one new flag)

- `ApproveRequest` (backend/schemas.py) gains `remove_watermark: bool = False`.
- `backfill.approve` (backend/routers/backfill.py): after the Drive download and before
  `publish.publish_image`, apply `remove_corner_star` when the flag is set.
- Frontend `BackfillTab.tsx` ReviewPanel: "Remove star watermark" checkbox next to
  "Approve & publish", default off. `approveBackfill` in `frontend/src/api.ts` passes the flag.

### 3. Products tab (new action + endpoint)

- New endpoint `POST /api/generate/approve-existing` (backend/routers/generate.py), JSON body:
  `{ productid, file_id, color, theme_name?, remove_watermark }`.
  Flow: download Drive file by `file_id` (drive_client.download_file) → validate/normalize to
  PNG (same validation as existing `/approve`) → optional `remove_corner_star` →
  `publish.publish_image` with `prompt_text="(existing mockup import)"` so rows are
  distinguishable in `mockup_variations`.
- Frontend `ProductsTab.tsx` GenerationStage: each Drive image in the source grid gets a
  "Use as mockup" action. It opens a small confirm step: color picker (required, same options
  as the normal publish flow) + "Remove star watermark" checkbox → calls the new endpoint.
  On success, trigger the same `onPublished` flow as a normal approve.
- New `frontend/src/api.ts` function `approveExistingMockup(...)`.

## Error handling

- Drive download failure → 502 with detail.
- Invalid/undecodable image → 400.
- Storage/publish failures → same mapping as existing `/approve` (503 StorageNotConfigured,
  502 otherwise).
- Payload cap: reuse the existing `_MAX_UPLOAD_BYTES` limit on the downloaded bytes.

## Testing

- Unit tests for `remove_corner_star`:
  - Reference sample: star gone (no bright-above-background pixels in the star bbox), pixels
    outside the ROI byte-identical.
  - No-watermark image: no crash, output remains valid PNG, image outside ROI unchanged.
- Endpoint test for `approve-existing` with mocked Drive + storage: verifies publish called
  with processed bytes and correct prompt_text; flag off skips the inpaint.
- Backfill approve test: flag on routes bytes through `remove_corner_star`.

## Out of scope

- Auto-detection of the watermark.
- Frame/border cropping, brightness or white-balance touch-ups.
- Upload-from-computer path for existing mockups.
- Before/after preview UI.
