# Phase 3 Design — Real Generation Engine (Drive → Gemini/VEO → Storage)

**Date:** 2026-06-23 (research refresh)
**Companion to:** `2026-06-22-phase2-design.md`, `2026-06-21-design.md`
**Status:** Design approved (image-gen slice). Video + review-UI scoped for future sessions.

> **Research refresh (2026-06-23):** Upgraded to **`google-genai` v2.9.0**
> (latest; was 1.56.0) and re-verified all fields via live introspection. Full
> test suite (31) passes on 2.x — no regression. `pyproject` bumped to
> `google-genai (>=2.9.0,<3.0.0)`. Key facts:
> 1. **`person_generation` IS valid on `ImageConfig` in 2.x** (`ALLOW_ALL` /
>    `ALLOW_ADULT` / `ALLOW_NONE`). It was absent in 1.56 (would have raised
>    `extra_forbidden`); the 2.x rewrite added it to the Gemini image path.
>    **Set `person_generation="ALLOW_ADULT"`** so fashion-figure generation is
>    never blocked. (Verify accepted at call-time once a real key is in `.env`.)
> 2. **GA image model `gemini-3-pro-image` is confirmed** (docs: GA/stable).
>    Current code still uses `-preview`; switch to env-configurable default
>    `gemini-3-pro-image`.
> 3. New 2.x `ImageConfig` fields: `prominent_people` (celebrity control — leave
>    unset, no celebrity intent), plus `output_mime_type` / `image_output_options`
>    which are **Vertex-only (not Gemini API)** — do not use on the api-key path.

## Goal

Replace the Phase-2 stub generation endpoints (`backend/routers/generate.py`,
currently returning `501 not_implemented`) with a real engine: download a
product's source images from Google Drive, generate a photorealistic mockup with
Gemini (and, later, a video with VEO), persist the output, and show it in the
web UI.

This session ships **image generation only**. Video and the review/approve UI
are designed here but deferred to later sessions.

---

## Research findings — latest Google Gemini / VEO guidelines

Verified against the installed `google-genai` Python SDK **v1.56.0** (live
`model_fields` introspection) and `ai.google.dev` docs (June 2026). Current code
is mostly aligned; deltas below.

### Image generation

| Aspect | Current code | Latest guideline | Action |
|---|---|---|---|
| Model | `gemini-3-pro-image-preview` | GA name **`gemini-3-pro-image`** confirmed live (docs: GA/stable, last updated Nov 2025) | Make model name **configurable via env** (`GEMINI_IMAGE_MODEL`), default `gemini-3-pro-image`. Keep `-preview` as fallback. |
| Reference images | up to 14 passed | `gemini-3-pro-image` cap = **14** | Aligned. Keep ≤14. |
| `image_config` fields | `aspect_ratio`, `image_size` | v2.9 `ImageConfig` = `aspect_ratio`, `image_size`, `person_generation`, `prominent_people`, `output_mime_type`, `output_compression_quality`, `image_output_options` | Add `person_generation`. Skip `output_mime_type`/`image_output_options` (Vertex-only). |
| `person_generation` | — | ✅ Valid in 2.x: `ALLOW_ALL` / `ALLOW_ADULT` / `ALLOW_NONE` | **Set `ALLOW_ADULT`** for fashion figures (belt-and-suspenders with `safety_settings=BLOCK_NONE`). |
| `prominent_people` | — | `ProminentPeople` enum (celebrity generation gate) | Leave unset — no celebrity intent. |
| Resolutions | `4K` | `1K` / `2K` / `4K`, default `1K` (per SDK field doc) | Keep `4K` (luxury quality intent). |
| Aspect ratios | `1:1` | `1:1`, `2:3`, `3:2`, `3:4`, `4:3`, `9:16`, `16:9`, `21:9` | Keep `1:1` for product mockups. |
| Extract image | manual `BytesIO(part.inline_data.data)` | SDK convenience **`part.as_image()`** (confirmed present in v1.56) | Use `part.as_image()` in new bytes helper. |
| Thinking | — | Gemini 3 image models are **thinking models**: reasoning on by default, **cannot be disabled** via API | Adds latency. Synchronous design still acceptable (~10–30 s); note for timeout budget. |
| Watermark | — | SynthID applied to **all** generated images | Note for users; no code change. |

Image call shape (2.x — `person_generation` set):
```python
client.models.generate_content(
    model=settings.gemini_image_model,   # env-configurable, default gemini-3-pro-image
    contents=[prompt, *image_parts],
    config=types.GenerateContentConfig(
        system_instruction=...,
        response_modalities=["IMAGE"],
        safety_settings=[...],           # BLOCK_NONE
        image_config=types.ImageConfig(
            aspect_ratio="1:1",
            image_size="4K",
            person_generation="ALLOW_ADULT",   # 2.x: valid on Gemini image path
        ),
    ),
)
```

**Live model verification (optional):** the `.env` `GOOGLE_API_KEY` value was
blank at refresh time, so `models.list()` could not run. GA name is confirmed
from docs instead. Re-run once a key is present:
```bash
poetry run python -c "from mockup_generator.config import settings; from google import genai; \
print([m.name for m in genai.Client(api_key=settings.google_api_key).models.list() if 'image' in m.name])"
```

### Hidden / advanced capabilities (from SDK code inspection, v2.9.0)

`client.models` in 2.x exposes image methods barely covered in the quickstart
docs. Availability differs by backend — our setup uses the **Gemini Developer
API (`api_key=`)**, so Vertex-only methods are out unless we switch backends.

| Method | What it does | Our (api-key) backend | Relevance |
|---|---|---|---|
| `generate_content` + `image_config` | gemini-3-pro-image, multi-ref, person_generation | ✅ | **Primary path — use.** |
| `recontext_image` | **Virtual Try-On — generate persons modeling fashion products** (`source={prompt, person_image, product_images}`) | ❌ **Vertex-only** ("Gemini Enterprise Agent Platform") | **Perfect domain fit.** Needs Vertex AI mode (project/location/ADC), not api-key. **Decision: adopt Vertex backend later?** |
| `upscale_image` | Imagen upscaler, `upscale_factor` `x2`/`x4` | ✅ (no hard guard) | Push 4K mockups higher for print/luxury. Verify Imagen upscale model id is enabled. |
| `edit_image` | Imagen mask/reference editing | ✅ (no hard guard) | Targeted edits (swap background, fix region) without full regen. |
| `generate_images` | Imagen text→image (no refs) | ✅ | Lower-priority alt; our refs-driven flow prefers `generate_content`. |
| `segment_image` | Mask a region | ❌ Vertex-only | Would pair with `edit_image`; Vertex-only. |
| `interactions.*` | New **GA Interactions API** (stateful create/get/cancel) | ✅ present | Higher-level orchestration; not needed for the synchronous slice. |

**Thinking-model parts:** gemini-3-pro-image is a thinking model — responses may
interleave `thought` / `thought_signature` / `text` parts with the image part.
The image extractor must iterate `response.candidates[0].content.parts`, skip
non-image parts, and pull the first part where `part.as_image()` is non-None
(do **not** assume `parts[0]` is the image).

**Net for this slice:** stay on `generate_content` (api-key). Log Virtual Try-On
as the highest-value future upgrade — it directly produces "model wearing the
garment," which is the product's whole point — gated on a Vertex AI migration.

### Video generation (VEO) — DEFERRED, documented for next session

| Aspect | Current code | Latest guideline (verified v1.56 + docs) |
|---|---|---|
| Model | `veo-3.1-generate-preview` | Still valid (Preview, **no official deprecation date** — the "Apr 2 2026 removal" was a 3rd-party blog, disregard). Siblings: `veo-3.1-fast-generate-preview`, `veo-3.1-lite-generate-preview`. Make env-configurable like the image model. |
| Duration / resolution | `4s` / `720p` | Docs: **1080p & 4K require `duration_seconds="8"`.** 720p = 4 / 6 / 8 s. |
| Audio | not set | `generate_audio` IS a valid `GenerateVideosConfig` field in v1.56. Veo 3.1 produces native synced audio. |
| Extras | `negative_prompt` set | Confirmed v1.56 fields: `seed`, `reference_images` (≤3), `last_frame`, `enhance_prompt`, `fps`, `compression_quality`. |
| Retention | downloads promptly ✓ | Server keeps generated video ~2 days — download right after the op completes. |
| Polling | `client.operations.get(operation)` loop ✓ | Confirmed correct. `operation.response.generated_videos[0].video`; `client.files.download(file=video.video)`. |

**Video async design (next session):** VEO jobs take minutes. Per decision, use
**background task + poll**: `/api/generate/video` enqueues a FastAPI
`BackgroundTask` (or a small job row), returns a `job_id` immediately; frontend
polls `/api/generate/video/{job_id}`. Job lifecycle: `pending → running →
done(url) | error`. On `done`, video already uploaded to Storage. Do not block
the request thread for minutes (HF Space / proxy timeouts).

---

## Image-gen slice — this session

### Decisions (locked)

1. **Output destination:** new **Supabase Storage bucket `mockups`**. Backend
   uploads the PNG via the service client, returns a public/signed URL. No Drive
   write scope needed (Drive stays `readonly`).
2. **`mockup_variations` table created now** (minimal), one row per generation.
   Review/approve UI stays out of scope.
3. **Apply engine deltas** (env-configurable model name, `part.as_image()`,
   `person_generation="ALLOW_ADULT"` — valid in google-genai 2.x).
4. **Synchronous** image generation (~10–30 s, acceptable). Only video needs async.
5. **Reference images:** if `GenerateRequest.image_ids` provided, download exactly
   those Drive files; otherwise download all images in the product folder
   (loose + variant subfolders), capped at 14.
6. One combined generation per request → one output image. (Legacy per-image
   `A/B/C` mode not exposed in the web UI this session.)

### Architecture — new web-oriented service layer

Existing `mockup_generator/generation/images.py` and `video.py` are
**filesystem-oriented** (read a dir of `Path`s, write `Path`s). Rather than
contort them, add a thin in-memory service that reuses the shared
`generation/common.py` helpers and leaves the legacy CLI path untouched.

**Chosen approach (A):** new orchestration module + new integration modules;
reuse `common.generate_with_retries`. Rejected: (B) refactor `images.py` to be
storage-agnostic — more risk to working legacy code, no benefit this session;
(C) put orchestration in the router — fat, untestable.

```
backend/routers/generate.py            # /image: orchestrate, return GenResult{image_url, variation_id}
mockup_generator/generation/service.py # generate_mockup_bytes(ref_images, prompt) -> PNG bytes (in-memory, reuses common)
mockup_generator/integrations/drive_client.py    # + download_file(file_id) -> bytes (full-res, readonly OK)
mockup_generator/integrations/storage_client.py  # NEW: upload_mockup(productid, data) -> url (service client)
mockup_generator/db/mockup_variations_repo.py     # NEW: insert(...) -> row; (list later)
```

### Data flow (`POST /api/generate/image`)

1. `get_current_user` (any active profile, as Phase 2).
2. Load product → `producturl` → `extract_folder_id`.
3. Resolve refs: `image_ids` if given, else list folder images; **download
   full-resolution bytes** for each (≤14) via `drive_client.download_file`.
4. Decode → PIL, thumbnail to `MAX_SIDE` (1024), build parts.
5. `service.generate_mockup_bytes(images, req.prompt)` → PNG bytes
   (`common.generate_with_retries` with the new `image_config`).
6. `storage_client.upload_mockup(productid, bytes)` → object path + URL.
7. `mockup_variations_repo.insert(productid, prompt_text, image_url, created_by)`.
8. Return `GenerateResponse{status:"ok", detail, image_url, variation_id}`.

Errors: no folder / no images → `400`; Gemini failure after retries → `502`;
Drive/Storage misconfig → `503`. All as JSON, surfaced in the UI message area.

### Drive download

Add `download_file(file_id) -> bytes` using `svc.files().get_media(fileId=...,
supportsAllDrives=True)` streamed via `MediaIoBaseDownload`. `drive.readonly`
scope is sufficient for download (thumbnails are only `w600` — too small to use
as references, so a real download is required).

### Supabase Storage

- Bucket `mockups` (private). Backend uploads with the **service client**
  (`service_client()`, needs `SUPABASE_SECRET_KEY` on the Space). Object key:
  `{productid}/{variation_id or timestamp}.png`.
- Return a signed URL (e.g. 7-day) or make the bucket public-read and return the
  public URL. Default: **signed URL** (private bucket).
- Migration creates the bucket + an RLS policy allowing authenticated reads
  consistent with the rest of the schema.

### `mockup_variations` table (additive migration via Supabase MCP)

```sql
create table public.mockup_variations (
  variation_id bigint generated always as identity primary key,
  productid    text not null references public.products(productid),
  prompt_id    bigint references public.prompts(prompt_id),  -- nullable; prompt may be ad-hoc/edited
  prompt_text  text not null,                                -- exact prompt used (audit)
  image_url    text not null,                                -- storage object path / signed-URL base
  kind         text not null default 'image',                -- 'image' | 'video' (video later)
  created_by   uuid references public.profiles(id),
  created_at   timestamptz not null default now()
);
create index mockup_variations_productid_idx on public.mockup_variations (productid);
-- RLS: enable; allow active profiles to select; server writes use the service key.
```

### Frontend (minimal)

- `api.ts`: extend `GenResult` with `image_url?: string` and
  `variation_id?: number`.
- `ProductsTab.tsx`: on image success, render the returned image with a
  download link instead of only showing `r.detail` text. Remove the Phase-3
  "not enabled" handling for the image button. Video button keeps the stub
  message until the video session.
- Gated on `npm run build`.

### Config / env

- `GEMINI_IMAGE_MODEL` (optional, default `gemini-3-pro-image` — GA confirmed)
  — add to `config.Settings`. Legacy `images.py` `MODEL_NAME` can read the same
  setting or stay on its hardcoded value (CLI path, out of scope).
- `SUPABASE_SECRET_KEY` — **required** this phase for Storage upload + writes
  (Phase 2 listed it optional). Document on the HF Space.
- `GOOGLE_DRIVE_SA_JSON` — already used for read; download uses same SA/scope.

### Testing (TDD)

- `drive_client.download_file` — mock the Drive service / media download.
- `storage_client.upload_mockup` — mock the Supabase client.
- `service.generate_mockup_bytes` — mock `common.get_genai_client` /
  `generate_with_retries`; assert `image_config` carries aspect, size, and
  `person_generation="ALLOW_ADULT"`; assert the env-configured model name is
  used; assert PNG bytes returned via `part.as_image()`.
- `mockup_variations_repo.insert` — mock client; assert payload shape.
- `routers/generate.py` `/image` — mock all of the above; assert flow, response
  shape, and error codes (400/502/503).
- Frontend gated on `npm run build`.

---

## Out of scope (future sessions)

- **Video generation** (`/api/generate/video`): background-task + poll design
  above; apply VEO deltas (8s for 1080p/4K, `generate_audio`, `seed`).
- **Review / approve UI**: list `mockup_variations` per product, approve →
  set `mockups.base_mockup = true` (and/or copy approved image to Drive).
- **In-app thumbnails of outputs** beyond the just-generated one.
- **Drive write-back** of approved mockups (would need SA scope upgrade to
  `drive.file` / `drive` + a destination-folder convention).
- Video prompt persistence (per Phase 2: video prompts never stored).

## Open items to confirm next session

- ~~Confirm GA model name `gemini-3-pro-image`~~ — **resolved**: GA per docs.
  (Optional live `models.list()` once a real `GOOGLE_API_KEY` is in `.env`.)
- Signed-URL TTL vs public bucket for `mockups`.
- Whether approved-image Drive write-back is wanted (drives the scope-upgrade decision).
- Video session: make VEO model env-configurable; set `duration_seconds="8"`
  if/when moving to 1080p/4K; decide on `generate_audio`.
- **Vertex AI backend decision** — unlocks Virtual Try-On (`recontext_image`,
  models-wearing-product) + `segment_image`. Cost: switch client to
  `vertexai=True` + project/location + ADC creds (vs current api-key). High
  product value; scope separately.
- Verify `upscale_image` (Imagen x2/x4) model id is enabled on the api key
  (optional 4K→8K post-step for print).
