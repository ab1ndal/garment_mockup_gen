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
- **The full Gemini-3 image-generation option surface** (see "Gemini option surface"
  below), gated per-model in the UI: model, aspect ratio, image size, output format
  (mime type) + JPEG compression quality, person generation, thinking level.
- Variation history: fresh generation, refine (regenerate using the active variation
  as an extra reference), and try-again.
- Refine capability on **every prompt-style text input** (the prompt field and the
  iteration feedback field).
- Download the active variation in the generated format (PNG or JPEG).

### Out of scope
- Video generation (deferred to a follow-up).
- Publish / approve to Supabase Storage or `productimages` (no DB writes at all).
- Color variants (no product → no variant data).
- Catalog linkage of any kind (`productid`).
- Legacy local-folder and folder-of-folders batch modes (obsolete for a web app).
- `candidate_count`/multi-image, `temperature`, `seed` — not supported by the Gemini
  image models per the API docs; deliberately not exposed.

## Gemini option surface

Verified against `google-genai` 2.9.0 (`types.ImageConfig` /
`types.ImageConfigImageOutputOptions` / `types.ThinkingConfig` /
`types.PersonGeneration`) and the official image-generation docs. Options are
**model-specific**, so the server publishes a per-model capability map and the UI gates
on it.

| Option | SDK field | Values | Model support |
|---|---|---|---|
| aspect ratio | `ImageConfig.aspect_ratio` | `1:1, 16:9, 9:16, 4:3, 3:4, 5:4, 4:5, 3:2, 2:3, 1:4, 4:1, 1:8, 8:1` | full list: 3-Pro, 3.1-Flash; 2.5-Flash: common subset (`1:1, 16:9, 9:16, 4:3, 3:4, 5:4, 4:5, 3:2, 2:3`) |
| image size | `ImageConfig.image_size` | `512px, 1K, 2K, 4K` | `1K/2K/4K`: 3-Pro & 3.1-Flash; `512px`: 3.1-Flash only; 2.5-Flash: `1K/2K` (no 4K) |
| output format | `ImageConfig.image_output_options.mime_type` | `image/png` (default), `image/jpeg` | all |
| compression quality | `ImageConfig.image_output_options.compression_quality` | int 1–100 (default 90) | JPEG only |
| person generation | `ImageConfig.person_generation` | `DONT_ALLOW, ALLOW_ADULT, ALLOW_ALL` (default: model default / unset) | all models, **Vertex only** — the Developer API (api key) returns 400; we surface that error inline rather than gating |
| thinking level | `ThinkingConfig.thinking_level` | `minimal, high` | 3.1-Flash only |

**Notable correctness fix (shared):** the current `ALLOWED_ASPECTS` includes `21:9`,
which the Gemini 3 image models do **not** support. It is removed and replaced with the
documented set above.

### Capability map (returned by `/api/generate/options`)

Per image model, the endpoint returns `aspect_ratios`, `image_sizes`, `mime_types`,
`compression_quality` bounds, `person_generation` values, and `thinking_levels` (empty
list = unsupported → UI hides that control). The env-configured default model is merged
in if not already present (existing behavior), defaulting to the 3-Pro capability set.

## Backend

Changes are mostly additive. The shared generation core gains a few passthrough
params (used by the new route; the regular `/image` route keeps its current behavior
via defaults) and the `/options` endpoint grows the capability map. The `21:9` removal
is the one shared behavior change.

### Shared core changes

**`mockup_generator/generation/common.py` — `generate_with_retries(...)`** gains
keyword params (all optional, defaulting to today's behavior):
- `output_mime_type: str | None` and `output_compression_quality: int | None` → set on
  `ImageConfig.image_output_options` (`types.ImageConfigImageOutputOptions`).
- `thinking_level: str | None` → set on `GenerateContentConfig.thinking_config`
  (`types.ThinkingConfig(thinking_level=...)`).
- `person_generation` already supported.

**`first_image_bytes` (format preservation).** Today it always re-encodes the returned
image to PNG, which would silently defeat a JPEG request. It is updated to **preserve
the model's returned format** (or honor the requested `output_mime_type`): return JPEG
bytes for a JPEG request, PNG otherwise. The PNG-only callers (catalog approve path) are
unaffected because they keep requesting PNG (the default).

**`mockup_generator/generation/service.py` — `generate_mockup_bytes(...)`** gains the
same passthrough params (`output_mime_type`, `output_compression_quality`,
`thinking_level`, `person_generation`) and forwards them to `generate_with_retries`. It
returns `(bytes, mime_type)` so callers know how to label/encode the result (the regular
catalog caller ignores the mime and keeps treating it as PNG).

### Options endpoint

`GET /api/generate/options` is extended to return the **per-model capability map**
described in "Gemini option surface" (in addition to the existing flat lists, kept for
backward compatibility with `ProductsTab`). `ALLOWED_ASPECTS` is corrected (drop `21:9`,
add the documented values).

### New route: `POST /api/generate/image/upload`

`backend/routers/generate.py`. Multipart/form-data (carries file uploads), with no
`productid`, no Drive download, and no DB.

**Form fields:**
- `prompt: str` (required)
- `model: str | None`
- `resolution: str | None` (image size)
- `aspect_ratio: str | None`
- `mime_type: str | None` (`image/png` | `image/jpeg`)
- `compression_quality: int | None` (JPEG only)
- `person_generation: str | None`
- `thinking_level: str | None`
- `refine_image_b64: str | None` — the active variation, sent back for refine/try-again
- `files: list[UploadFile]` — 0–14 uploaded reference images

**Validation (against the per-model capability map):**
- `model` against `ALLOWED_MODELS`; `aspect_ratio` / `resolution` / `mime_type` /
  `person_generation` / `thinking_level` validated against the **selected model's**
  capabilities → 400 on mismatch (e.g. `4K` on 2.5-Flash, `thinking_level` on 3-Pro,
  `512px` on a non-3.1-Flash model).
- `compression_quality` in 1–100 and only meaningful with `image/jpeg` → 400 otherwise.
- At least one of `files` or `refine_image_b64` present → 400 otherwise.
- Each uploaded file ≤ `_MAX_UPLOAD_BYTES` (25 MB) → 413; must be a valid image → 400.
- Total references capped at `_MAX_REFS` (14); refine image appended only if room
  (warn-and-drop), matching the existing `/image` behavior.

**Flow:**
1. Decode `refine_image_b64` first (reuse `_decode_b64_image`) so bad input fails fast.
2. Read each `UploadFile`, validate size + format, open as RGB PIL image.
3. Build the `images` list (uploads first, then refine image if room).
4. `service.generate_mockup_bytes(images, prompt, model=, resolution=, aspect_ratio=,
   output_mime_type=, output_compression_quality=, person_generation=, thinking_level=)`
   → `(bytes, mime)`.
5. Return `GenerateUploadPreview{status, detail, image_b64, mime_type}` — base64 of the
   returned bytes plus the mime so the client renders/downloads with the right type.

**Auth:** `Depends(get_current_user)` like every other route. No `get_db` dependency
(nothing touches the database).

**person_generation note:** sent through to the model when set. On a non-Vertex
(api-key) deploy the API returns 400 ("only supported in Gemini Enterprise Agent
Platform mode"); that error is surfaced inline to the user rather than gated server-side.

**Errors:** reuse the existing mapping — `service.NoImageReturned` → 502, other
generation failures → 502, validation → 400, oversized → 413.

### Refine

No change. The existing `POST /api/prompts/refine` already accepts an optional
`categoryid` and a `kind`, so it works for ad-hoc prompts with no category. The
frontend calls it with `kind: "image"` and no `categoryid`.

### Schemas

One new response schema `GenerateUploadPreview` = `GeneratePreview` + `mime_type: str`
(so the client knows whether the base64 payload is PNG or JPEG). The upload request is
expressed as FastAPI `Form(...)` / `File(...)` parameters rather than a Pydantic body,
matching the existing `/approve` route's pattern.

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
- `model` and the per-option selections: `aspect`, `imageSize`, `mimeType`,
  `compressionQuality`, `personGeneration`, `thinkingLevel` — seeded from
  `getGenerationOptions()` defaults for the chosen model.
- `caps` — the capability map for the selected model, driving which controls render and
  what each `<select>` offers.
- `variations: { b64, mime, promptUsed, feedback?, mode }[]` and an active index — same
  shape as the regular flow, plus the per-variation mime.
- `feedback: string`.
- `busy` / `error` state.

**Per-model gating:** when `model` changes, recompute `caps` and clamp any now-invalid
selection back to the model's default (e.g. switching to 2.5-Flash drops a `4K` size to
`2K`; switching off 3.1-Flash hides the thinking-level control). Controls whose
capability list is empty are not rendered. The compression-quality control only shows
when `mimeType === "image/jpeg"`.

**Sections (top to bottom):**
1. **Upload** — `<input type="file" accept="image/*" multiple>`; thumbnail grid of
   chosen files with a remove control on each; enforce the 14-file cap with a clear
   message when exceeded.
2. **Prompt** — textarea + `RefineButton` (`kind="image"`, no `categoryid`).
3. **Options** — model selector, then the model-gated controls: aspect ratio, image
   size, output format (mime), JPEG quality (conditional), person generation, thinking
   level. No color selector (no product).
4. **Generate** — disabled until ≥1 file and a non-empty prompt; calls the new upload
   endpoint; pushes the result (with its mime) as a `"fresh"` variation.
5. **Variations** — active preview (click-to-enlarge via `Lightbox`) + numbered
   filmstrip; a **feedback** textarea with its own `RefineButton`; **Refine** (regenerate
   sending `refine_image_b64 = active.b64`, mode `"refined"`) and **Try Again** (regenerate
   from the uploaded files only, discarding feedback); **Download** the active variation
   in its generated format.

Filenames for download: a generic stem with the correct extension from the variation
mime (e.g. `mockup_<aspect>.png` / `.jpg`) since there is no product id.

### API client: `api.ts`
- Add `generateImageUpload(files: File[], fields: {prompt, model?, resolution?,
  aspect_ratio?, mime_type?, compression_quality?, person_generation?, thinking_level?,
  refine_image_b64?}): Promise<GenUploadPreview>` built on the existing `apiUpload`
  (multipart, injects the Bearer token). `GenUploadPreview` = `GenPreview` + `mime_type`.
- `getGenerationOptions()` return type grows the per-model capability map (additive; the
  existing flat fields stay for `ProductsTab`).
- No change to `refinePrompt` / `RefineButton` usage.

## Data flow

```
Quick Generate tab
  upload files ──┐
  prompt ────────┤
  options ───────┴─► generateImageUpload (multipart) ─► POST /api/generate/image/upload
                                                          └─► generate_mockup_bytes ─► (bytes, mime)
                  ◄── GenUploadPreview{image_b64, mime_type} ◄──────────────────────┘
  variation pushed ("fresh", with mime)
  refine: + refine_image_b64 = active.b64 ─► same endpoint ─► variation ("refined")
  download: active.b64 ─► PNG/JPEG file (extension from mime, client-side)
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
  400, oversized file → 413, invalid image → 400, `>14` files capped, and the per-model
  capability validations — bad model → 400, `4K` on 2.5-Flash → 400, `thinking_level` on
  3-Pro → 400, `512px` on a non-3.1-Flash model → 400, `compression_quality` out of
  range or with PNG → 400, JPEG request returns `mime_type: image/jpeg`. Mock
  `service.generate_mockup_bytes` to avoid real model calls, following existing
  generate-route test patterns.
- **Core:** tests that `generate_with_retries` builds the expected `ImageConfig`
  (output options, thinking config, person generation) from the new params, and that
  `first_image_bytes` preserves JPEG vs PNG.
- **Options:** `GET /api/generate/options` returns a capability map for each model and no
  longer offers `21:9`.
- **Frontend:** component-level check that upload + prompt enables Generate, that a
  successful response renders a variation and enables Download with the right extension,
  that switching model clamps invalid selections and hides unsupported controls, and that
  Refine sends `refine_image_b64`.

## Risks / notes
- The shared `first_image_bytes` change (format preservation) touches the catalog path.
  Mitigation: catalog callers keep requesting PNG (the default), and a regression test
  pins PNG output for the existing flow.
- Reuses `service.generate_mockup_bytes` and the shared option plumbing, so the ad-hoc
  and catalog paths stay consistent; the `21:9` removal corrects both at once.
- The upload route is the first generate route with no `get_db` dependency — confirm
  no shared helper assumes a db handle.
- Option values are validated against the SDK-verified capability map; if Google adds
  models/values later, the map is the single place to update.
- Designed so the deferred video work slots in cleanly: a later change makes
  `productid` optional on `POST /api/generate/video` and adds an `image_b64` field, with
  the Quick Generate tab gaining a video section that animates the active variation.
