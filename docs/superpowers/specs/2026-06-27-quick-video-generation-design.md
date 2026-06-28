# Quick Video Generation — Design Spec

**Date:** 2026-06-27
**Status:** Approved (brainstorm)
**Author:** Claude + abindal

## Summary

Add a **Quick Video Generation** tab to the mockup-generator React/FastAPI app, exposing the full Google VEO 3.1 capability surface in a single catalog-free studio. Mirrors the existing Quick Generate (image) tab's patterns: upload references, write/refine a prompt, generate, review, iterate — but produces short garment videos via VEO.

The tab is multi-mode (text→video, image→video, first+last frame interpolation, reference images for product/face consistency, video extension), with a constraint-aware control surface so illegal parameter combinations are un-submittable rather than failing after a multi-minute job.

## Goals

- Unlock VEO 3.1 capabilities currently unused by the backend (today: single-image→video only).
- Catalog-free "quick" workflow — nothing saved to Supabase/Drive, same posture as the image Quick Generate tab.
- Prompt-refinement assist: expand a short description into a detailed cinematic VEO prompt.
- Premium, accessible UX matching the app's existing design system.

## Non-Goals

- No persistence to catalog/Supabase/Drive (videos download or live in-session only).
- No upload of arbitrary external MP4s to extend (extend chains from in-session clips only).
- No batch/folder workflows (that lives in ProductsTab).

## VEO 3.1 capability reference (from Gemini API docs)

| Capability | 3.1 | 3.1-fast | 3.1-lite |
|---|---|---|---|
| Text→Video | ✓ | ✓ | ✓ |
| Image→Video (start frame) | ✓ | ✓ | ✓ |
| First+Last frame interpolation | ✓ | ✓ | ✓ |
| Reference images (up to 3) | ✓ | ✓ | ✗ |
| Video extension (+7s) | ✓ | ✓ | ✗ |
| Native audio | ✓ | ✓ | ✓ |
| 1080p | ✓ | ✓ | ✓ |

**Knobs:** `aspectRatio` (16:9 / 9:16), `resolution` (720p / 1080p), `durationSeconds` (4 / 6 / 8), `personGeneration` (allow_all for t2v, allow_adult for image modes), `numberOfVideos` = 1.

**Cross-field constraints:**
- 1080p → duration must be 8s.
- Reference images or first+last interpolation → duration must be 8s.
- Video extension → 720p only; input clip must be a prior VEO generation (≤141s, 9:16 or 16:9); output adds 7s.
- Native audio always on for VEO 3.x.

> **To verify in implementation:** the docs do not list a negative-prompt parameter for VEO 3.1, yet the current backend passes `negative_prompt`. Keep the field, verify against the live API, and drop it if the API rejects it. (Treat 4k as out of scope for v1 — 1080p is the ceiling we expose.)

## Architecture

### Modes

A mode selector (segmented control) at the top of the form reshapes the inputs below. Five modes:

| Mode | Inputs | VEO request |
|---|---|---|
| **Text → Video** | prompt | t2v, `personGeneration: allow_all` |
| **Image → Video** | 1 start frame + prompt | `image` |
| **Frames** (first+last) | start frame + end frame + prompt | `image` + `lastFrame`; forces 8s |
| **Reference** (consistency) | up to 3 reference images + prompt | `referenceImages` (`referenceType: "asset"`); forces 8s |
| **Extend** | prior in-session clip + prompt | `video` (extension input); forces 720p; appears only after a clip exists |

**Extend transport:** the GET `/video/{job_id}` poll evicts the job from server memory once the MP4 streams, so the server cannot hold a clip for later extension. Instead, the client already holds the generated clip (object URL / blob) and **re-submits those bytes as an `extend_video` multipart file**. The server stays stateless — no job-store dependency. The "in-session only, no arbitrary external MP4" guardrail is enforced at the UI level (Extend is only offered on a clip generated this session; there is no file picker for it).

### Frontend — `QuickVideoTab.tsx`

New component mirroring `QuickGenerateTab.tsx`. Layout top→bottom:

1. Heading + subtitle ("nothing is saved to the catalog").
2. **Mode selector** — segmented buttons, `aria-pressed`, disabled segments show reason when unsupported by the chosen model.
3. **Mode-specific upload zone(s)** — start frame, end frame, or up to 3 reference images (object-URL previews, revoke on cleanup; same pattern as image tab).
4. **Prompt** field with `RefineButton kind="video"`.
5. **Constraint-aware options grid** — model, aspect, resolution, duration, person generation, negative prompt. Selects clamp to the active model's caps + cross-field rules; locked fields show inline helper text explaining why.
6. **Primary button** with progress state (videos take minutes — show elapsed/progress, not just a spinner).
7. **Message** area (`alert`, `aria-live`).
8. **Result**: inline `<video controls>` player + **recent-clips history strip** (last ~4 as object URLs, click to switch active; `aria-pressed`). Active clip has **Download** and **Extend +7s** actions, plus a feedback field (folds a revision note into the next generation) with its own `RefineButton`.

State shape (mirrors image tab): `opts`, `mode`, `files`/`startFrame`/`lastFrame`/`refImages`, `prompt`, `model`, `aspect`, `resolution`, `duration`, `personGen`, `negativePrompt`, `clips[]` (object URLs + meta), `activeIdx`, `feedback`, `busy`, `progress`, `msg`.

Object-URL lifecycle: revoke clip URLs and preview URLs on change/unmount to avoid leaks; cap history so memory stays bounded.

### Constraint engine

Server returns a **`video_caps` map keyed by model** in `/api/generate/options` (parallel to `image_caps`):

```
video_caps[model] = {
  modes: string[]              // e.g. ["text","image","frames","reference","extend"]
  aspect_ratios: string[]
  resolutions: string[]
  durations: number[]
  person_generation: string[]
}
```

Lite drops `reference` + `extend` from `modes`. Client runs a clamp `useEffect` (same shape as the image tab's): on model/mode change, clamp every selection into the allowed set and apply cross-field rules:

- Reference / Frames / 1080p → force duration = 8 (lock select, helper text).
- Extend → force resolution = 720p.
- Mode unsupported by model → disable that segment, show reason (do not hide).

Server re-validates every rule before enqueue (never trust the client) → **400** with a clear message on any illegal combo (matches the existing `/video` endpoint's status convention).

### Backend

**New endpoint:** `POST /api/generate/video/upload` (multipart, catalog-free — mirrors `/api/generate/image/upload`).

Request fields:
- `mode`, `prompt`, `model`, `aspect_ratio`, `resolution`, `duration`, `negative_prompt`, `person_generation`
- files: `start_frame`, `last_frame`, `reference_images[]` (≤3), `extend_video` (the in-session clip bytes, for extend mode)

Behavior: validate (model in allow-list, mode supported by model, cross-field rules) → 400 on failure; otherwise enqueue a background job and return `VideoJobResponse { job_id, status: "pending" }`. Poll the **existing** `GET /api/generate/video/{job_id}` (reuse the job store, 30-min TTL, and thread runner) — returns JSON while pending/running, streams `video/mp4` when done.

**`video_service.generate_video_bytes` extended** to accept and pass the new genai knobs when present: `last_frame`, `reference_images` (mapped to `referenceImages` with `referenceType: "asset"`), `video` (extension input bytes), `person_generation`. The existing single-image path is unchanged when these are absent. Keep polling/timeout logic as-is.

**`/api/generate/options`** gains `video_caps` + per-model `video_defaults`.

**Prompt refine:** reuse `POST /api/prompts/refine` with `kind: "video"`. Tune the video-refine system prompt for VEO structure (subject + action + camera + lighting + mood + ambient/audio cue), constrained to one tight paragraph; garment-aware via `categoryid`.

**Extend edge case:** if `extend_job_id` is expired/evicted from the store, return a clear error and prompt the user to regenerate.

## Data flow

1. User picks mode, uploads frame(s)/references, writes prompt (optionally Refine).
2. Client clamps controls to model caps + cross-field rules.
3. `POST /api/generate/video/upload` (multipart) → `{ job_id }`.
4. Client polls `GET /api/generate/video/{job_id}` every ~5s, showing progress.
5. On done: MP4 blob → object URL → pushed into `clips[]`, set active, render in player.
6. User downloads, extends (+7s, chains job), or refines feedback → regenerate.

## Error handling

- Illegal combos blocked client-side (un-submittable) and server-side (400 with reason).
- Job errors (`VideoTimeout`, `NoVideoReturned`, generic) surfaced via the job-status `detail` → shown in the `alert` with a retry path.
- Expired extend source → clear message + regenerate prompt.
- Network/poll failure → timeout feedback with retry (per UX `timeout-feedback`).

## UX / design system

Match the app's existing tokens (`font-display`, `text-subtle`, `btn-primary`, `field`, `pill`, `alert`, `section-label`, `border-line`, `border-accent`, `spinner`, lightbox). No new font stack. Direction aligns with the luxury-fashion reference (elegant, premium, 400–600ms motion, dark overlay on media).

Accessibility/interaction rules applied:
- Touch targets ≥44px; primary button `min-height` 52 (matches image tab).
- Visible focus states; `aria-pressed` on mode + history toggles; `aria-live` on messages.
- Disabled segments explain why (no silent hiding).
- `prefers-reduced-motion` respected on any transitions.
- Progress feedback for >1s operations (these run minutes) — skeleton/progress, not a bare spinner.
- Video player has native `controls`; provide captions affordance later if audio dialogue is used.

## Testing

**Backend:**
- Constraint validator unit tests — every illegal combo → 422 (1080p+non-8s, reference/frames+non-8s, extend+1080p, mode unsupported by model).
- Upload endpoint — each mode builds the correct genai request (mock the client): verify `image`, `lastFrame`, `referenceImages`, `video`, `personGeneration` wiring.
- Extend mode requires an `extend_video` file → 400 when missing.
- Extend `test_video_service.py` for `last_frame` / `reference_images` / `video` (extension) / `person_generation` / `generate_audio` arguments.

**Frontend:** the repo has **no frontend test framework** (build = `tsc -b && vite build`). Verification is the TypeScript typecheck/build passing plus a manual smoke pass (each mode reshapes inputs; caps clamp on model change; locked-field helper text shows; disabled segments show reasons; player + history strip render; download + extend work). No unit-test framework is introduced (YAGNI — matches the existing codebase, which has zero frontend tests).

## Open items / to verify during implementation

- Negative-prompt support on VEO 3.1 (keep field, verify, drop if rejected).
- Exact genai SDK field names for `referenceImages` / `lastFrame` / extension `video` (confirm against installed SDK version).
- History-strip cap (start at 4) and object-URL memory ceiling.
