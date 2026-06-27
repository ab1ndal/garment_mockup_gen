# Ad-hoc Image Generation — Design

**Date:** 2026-06-27
**Status:** Approved (pending spec review)

## Problem

The legacy Streamlit app let a user upload arbitrary garment images and generate a
mockup without any catalog entry. The React/FastAPI app only generates for a
catalog product using Google Drive source images keyed by `productid`. There is no
way to do a quick, throwaway ("random") generation from images on your machine.

This adds an **ad-hoc image generation** path: upload files → generate → preview →
refine → download. It is fully decoupled from the product catalog — no `productid`,
no Supabase, no Drive, no publish.

Video generation is explicitly **out of scope** for this iteration (planned as a
follow-up).

## Scope

### In scope
- New **"Quick Generate"** tab in the React app.
- Multi-file image upload (1–14 references) as the generation source.
- Free-text prompt with AI refine.
- Same image generation controls as the regular flow: model, resolution, aspect ratio.
- Variation history: fresh generation, refine (regenerate using the active variation
  as an extra reference), and try-again.
- Refine capability on **every prompt-style text input** (the prompt field and the
  iteration feedback field).
- Download the active variation as PNG.

### Out of scope
- Video generation (deferred to a follow-up).
- Publish / approve to Supabase Storage or `productimages` (no DB writes at all).
- Color variants (no product → no variant data).
- Catalog linkage of any kind (`productid`).
- Legacy local-folder and folder-of-folders batch modes (obsolete for a web app).

## Backend

All changes are additive and reuse the existing generation core. No existing route
behavior changes.

### New route: `POST /api/generate/image/upload`

`backend/routers/generate.py`. Multipart/form-data (because it carries file uploads),
mirroring the validation of the existing `POST /api/generate/image` but with no
`productid` and no Drive download.

**Form fields:**
- `prompt: str` (required)
- `model: str | None`
- `resolution: str | None`
- `aspect_ratio: str | None`
- `refine_image_b64: str | None` — the active variation, sent back for refine/try-again
- `files: list[UploadFile]` — 0–14 uploaded reference images

**Validation (reuses existing constants/helpers):**
- `model` / `resolution` / `aspect_ratio` validated against the existing
  `ALLOWED_MODELS` / `ALLOWED_RESOLUTIONS` / `ALLOWED_ASPECTS` allowlists → 400 on mismatch.
- At least one of `files` or `refine_image_b64` must be present → 400 otherwise
  ("Select or upload at least one source image.").
- Each uploaded file ≤ `_MAX_UPLOAD_BYTES` (25 MB) → 413 otherwise.
- Each file must be a valid image (PIL open/verify) → 400 otherwise.
- Total references capped at `_MAX_REFS` (14); the refine image is appended only if
  there is room, matching the existing `/image` behavior (warn-and-drop on overflow).

**Flow:**
1. Decode `refine_image_b64` first (reuse `_decode_b64_image`) so bad input fails fast.
2. Read each `UploadFile`, validate size + format, open as RGB PIL image.
3. Build the `images` list (uploads first, then refine image if room).
4. `service.generate_mockup_bytes(images, prompt, model=, resolution=, aspect_ratio=)`
   — **unchanged**; it already takes a list of PIL images and is source-agnostic.
5. Return `GeneratePreview{status, detail, image_b64}` — the **existing** response model.

**Auth:** `Depends(get_current_user)` like every other route. No `get_db` dependency
(nothing touches the database).

**Errors:** reuse the existing mapping — `service.NoImageReturned` → 502 ("The model
returned no image. Try again."), other generation failures → 502.

### Refine

No change. The existing `POST /api/prompts/refine` already accepts an optional
`categoryid` and a `kind`, so it works for ad-hoc prompts with no category. The
frontend calls it with `kind: "image"` and no `categoryid`.

### Schemas

No new response schema (reuse `GeneratePreview`). The upload request is expressed as
FastAPI `Form(...)` / `File(...)` parameters rather than a Pydantic body, matching the
existing `/approve` route's pattern.

## Frontend

`frontend/src/`.

### Navigation
- `App.tsx` — add a 4th tab, **"Quick Generate"**, alongside Products / Prompts /
  Backfill, rendering a new `QuickGenerateTab` component.

### New component: `components/QuickGenerateTab.tsx`
Standalone (no product context). Reuses existing building blocks: `RefineButton`,
`Lightbox`, and `getGenerationOptions()`.

**State:**
- `files: File[]` — uploaded references (+ object-URL previews).
- `prompt: string`.
- `model` / `resolution` / `aspect` — seeded from `getGenerationOptions()` defaults.
- `variations: { b64, promptUsed, feedback?, mode }[]` and an active index — same shape
  as the regular flow.
- `feedback: string`.
- `busy` / `error` state.

**Sections (top to bottom):**
1. **Upload** — `<input type="file" accept="image/*" multiple>`; thumbnail grid of
   chosen files with a remove control on each; enforce the 14-file cap with a clear
   message when exceeded.
2. **Prompt** — textarea + `RefineButton` (`kind="image"`, no `categoryid`).
3. **Options** — model / resolution / aspect `<select>`s (no color selector).
4. **Generate** — disabled until ≥1 file and a non-empty prompt; calls the new upload
   endpoint; pushes the result as a `"fresh"` variation.
5. **Variations** — active preview (click-to-enlarge via `Lightbox`) + numbered
   filmstrip; a **feedback** textarea with its own `RefineButton`; **Refine** (regenerate
   sending `refine_image_b64 = active.b64`, mode `"refined"`) and **Try Again** (regenerate
   from the uploaded files only, discarding feedback); **Download** the active variation
   as a PNG.

Filenames for download: a generic stem (e.g. `mockup_<aspect>.png`) since there is no
product id.

### API client: `api.ts`
- Add `generateImageUpload(files: File[], fields: {prompt, model?, resolution?,
  aspect_ratio?, refine_image_b64?}): Promise<GenPreview>` built on the existing
  `apiUpload` (multipart, injects the Bearer token). Returns the existing `GenPreview`
  type.
- No change to `refinePrompt` / `RefineButton` usage.

## Data flow

```
Quick Generate tab
  upload files ──┐
  prompt ────────┤
  options ───────┴─► generateImageUpload (multipart) ─► POST /api/generate/image/upload
                                                          └─► generate_mockup_bytes ─► PNG
                          ◄── GenPreview{image_b64} ◄────────────────────────────────┘
  variation pushed ("fresh")
  refine: + refine_image_b64 = active.b64 ─► same endpoint ─► variation ("refined")
  download: active.b64 ─► PNG file (client-side)
```

Nothing is persisted server-side; everything lives in component state until the user
downloads or leaves the tab.

## Error handling
- Backend returns the same HTTP error codes/messages as the existing `/image` and
  `/approve` routes (400 validation, 413 too large, 502 generation failure).
- Frontend surfaces `error` inline in the tab (same pattern as `ProductsTab`), and
  disables the generate/refine buttons while `busy`.

## Testing
- **Backend:** unit/integration tests for `POST /api/generate/image/upload` covering:
  happy path (1 file), multiple files, refine-only (no files), missing-everything →
  400, oversized file → 413, invalid image → 400, bad model/resolution/aspect → 400,
  `>14` files capped. Mock `service.generate_mockup_bytes` to avoid real model calls,
  following existing generate-route test patterns.
- **Frontend:** component-level check that upload + prompt enables Generate, that a
  successful response renders a variation and enables Download, and that Refine sends
  `refine_image_b64`.

## Risks / notes
- Reuses `service.generate_mockup_bytes` and the existing option allowlists verbatim,
  so the ad-hoc path stays consistent with the catalog path automatically.
- The upload route is the first generate route with no `get_db` dependency — confirm
  no shared helper assumes a db handle.
- Designed so the deferred video work slots in cleanly: a later change makes
  `productid` optional on `POST /api/generate/video` and adds an `image_b64` field, with
  the Quick Generate tab gaining a video section that animates the active variation.
