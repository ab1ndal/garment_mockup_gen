# Phase 6 — Auto-refine Prompt Button (Design)

**Date:** 2026-06-23
**Roadmap:** `docs/plans/2026-06-21-implementation-plan.md` → Phase 6
**Status:** Specced

## 1. Goal

Add an on-demand **Refine** button next to every editable prompt box. The user
writes a freeform instruction in the prompt body — anything from a thin keyword
("red silk saree") to a themed brief with explicit directives ("Festive Diwali
saree, warm mood — match the provided pattern details") — presses Refine, and a
text model rewrites it into a full, house-style, Gemini-optimized prompt that
fills the same box, editable and **not yet saved**. The user reviews and saves
with the existing Save control.

This works for **image** prompts (the default-prompt system from Phase 5) and
for **video** prompts (the VEO flow). Video prompt craft is more detailed, so it
gets its own, richer instruction contract and more creative latitude.

The whole feature is stateless: refine reads an instruction and returns text. It
writes nothing to the database. Persistence stays with the existing
create/update prompt flow, which is insert-only on seed and explicit on edit.

## 2. Non-goals

- No auto-save. Refine fills the editor; the user saves manually.
- No new persisted entity. Variants ("Festive Special", etc.) become normal
  prompt rows only when the user clicks Save in the Prompts tab.
- No batch / bulk refine.
- No schema change, no migration, no new dependency (reuses `google-genai`).

## 3. Architecture

Pure additive feature across three layers:

- **Core** — `mockup_generator/prompts/refine.py`: a `refine_prompt(...)`
  function plus two meta-prompt builders (image, video). Reuses the existing
  `get_genai_client()` and retry idiom from `mockup_generator/generation/common.py`.
- **Backend** — one new stateless endpoint `POST /api/prompts/refine` in the
  existing prompts router. Auth-gated like its siblings.
- **Frontend** — one shared `RefineButton` component wired into the existing
  prompt-editing surfaces; one `refinePrompt` API function.

### 3.1 Core: `mockup_generator/prompts/refine.py`

```python
def refine_prompt(
    instruction: str,
    category_name: str | None = None,
    *,
    kind: str = "image",   # "image" | "video"
) -> str:
    """Expand a freeform instruction into a full house-style Gemini prompt.

    Builds a meta-prompt (image or video contract), calls the configured text
    model via the shared genai client, and returns the model's text stripped of
    any markdown fences or preamble. Raises ValueError on empty instruction;
    raises RefineFailed when the model returns no usable text."""
```

- **Model:** new setting `settings.gemini_text_model`, read from
  `GEMINI_TEXT_MODEL`, default `gemini-3-pro` (advanced text model — confirm the
  exact published id at implementation time; this is the text sibling of the
  image model, not Flash). Chosen because prompt-craft rewriting — especially
  video — rewards a stronger model.
- **Client:** `get_genai_client()` (Vertex or AI-Studio per existing config). No
  new client code.
- **Generation config:** image kind uses default/low temperature for faithful,
  structured expansion. Video kind uses a higher temperature (more creative
  latitude) — exact value chosen at implementation, but image < video.
- **Retry:** reuse the existing backoff pattern for 429/5xx, mirroring
  `generate_with_retries`. Refactor only if cleanly shareable; otherwise a small
  local retry loop is acceptable — do not destabilize the image path.
- **Output hygiene:** strip leading/trailing whitespace and any ```/```text
  fences so the result drops straight into a prompt box.

### 3.2 Meta-prompt contracts (the core asset)

Two builders. Both are pure functions returning a string; both are unit-tested
on their markers so the contract is locked.

**`_image_meta(instruction, category_name)`** — instructs the model to output a
prompt in the shipped house structure, with 1–2 few-shot exemplars drawn from
the existing shipped prompts (`SAREE_PROMPT`, `CORD_SET_PROMPT`). Required rules:

1. Output the house structure: **garment specs → model requirements →
   technical/aesthetic specs → anti-hallucination + final cleanup tail** (remove
   tags/pins/stands, pixel-for-pixel fidelity, no mannequins).
2. **Preserve every explicit user directive verbatim** — mood ("festive
   Diwali"), constraints ("match the provided pattern details"), length /
   silhouette / color instructions. Drop nothing the user stated; weave each
   into the appropriate section.
3. Ground the output in the category (saree vs blazer vs jeans) when
   `category_name` is provided.
4. Output the prompt text **only** — no preamble, no explanation, no markdown.

**`_video_meta(instruction, category_name)`** — a separate, richer contract for
VEO. Same fidelity discipline, but explicitly invites creativity and covers
motion. Required rules:

1. Describe a short vertical (9:16) product clip: opening framing, **camera /
   shot language** (slow push-in, gentle dolly, orbit, pan), **pacing** for a
   few-second clip, and a clean resolve / loop-friendly ending.
2. Direct **motion**: fabric flow and drape movement, subtle model motion (a
   turn, a twirl, a step), and a lighting or mood shift.
3. **Be more creative** than the image contract — propose an evocative mood and
   atmosphere — while keeping the garment **pixel-faithful** to the reference
   (no invented motifs, colors, or silhouette changes) and preserving every
   explicit user directive.
4. Ground in the category and output the prompt text only.

### 3.3 Backend: `POST /api/prompts/refine`

In `backend/routers/prompts.py` (same `/api` prefix, same auth dependency):

- **Request** (`RefineRequest` in `backend/schemas.py`):
  `{ instruction: str, categoryid: str | None = None, kind: "image" | "video" = "image" }`.
- **Response** (`RefineResponse`): `{ refined: str }`.
- **Behavior:** validate `kind`; reject empty/whitespace `instruction` with
  **400**; resolve `categoryid` → category name via the existing categories repo
  (best-effort — unknown id just means no grounding, not an error); call
  `refine_prompt(...)`; return `{refined}`. Writes nothing.
- **Errors:** empty instruction → 400; `RefineFailed` (model returned nothing) →
  **502** "refine produced no text"; let the shared error handling cover the
  rest.

### 3.4 Frontend

- **`frontend/src/api.ts`:**
  `refinePrompt(instruction: string, categoryid?: string, kind?: "image" | "video") => Promise<{ refined: string }>`.
- **`frontend/src/components/RefineButton.tsx`:** shared control.
  Props: `{ kind: "image" | "video"; instruction: string; categoryid?: string; onRefined: (text: string) => void; onError: (msg: string) => void; }`.
  Renders a "✨ Refine" button (spinner while busy, matching the existing
  `.btn-primary` + `.spinner` idiom) and an info **tooltip** that teaches
  freeform framing, text varying by `kind`:
  - image: *"Describe what you want — garment, mood, any must-keep details. e.g. 'Festive Diwali saree, warm mood — match the provided pattern details.'"*
  - video: *"Describe the clip — motion, camera, mood, must-keep details. e.g. 'Slow elegant twirl, soft festive light, fabric flowing — keep the print exact.'"*
  Button is **disabled when `instruction` is empty/whitespace** (mirrors the
  existing dirty-guard). On success calls `onRefined(refined)`; on failure calls
  `onError`.
- **Wiring (all locations a prompt is passed/edited):**
  - **PromptsTab → PromptEditor** (`kind="image"`): button uses the current
    `body` as `instruction` and the selected `categoryid`; `onRefined` sets the
    `body` textarea. User then clicks the existing Save.
  - **Generate image-prompt box** (`kind="image"`): `onRefined` sets the
    generate screen's prompt state.
  - **Generate video-prompt box** (`kind="video"`): `onRefined` sets the video
    prompt state. (Exact component path confirmed during planning against the
    current generate/review screen.)

## 4. Data flow

1. User types a freeform instruction into a prompt box and clicks ✨ Refine.
2. `RefineButton` calls `refinePrompt(instruction, categoryid, kind)`.
3. `POST /api/prompts/refine` validates, resolves category name, calls
   `refine_prompt(instruction, category_name, kind=kind)`.
4. Core builds the image or video meta-prompt, calls the text model (with
   kind-appropriate temperature + retry), strips the output, returns it.
5. Endpoint returns `{refined}`; `onRefined` writes it into the same box.
6. User edits if desired and saves through the existing flow (or just uses it
   for that generation, on the generate screen).

## 5. Error handling

| Case | Handling |
|------|----------|
| Empty / whitespace instruction | Button disabled client-side; endpoint also returns 400. |
| Invalid `kind` | 422 via schema enum. |
| Unknown `categoryid` | No grounding; not an error. |
| Model returns no text | `RefineFailed` → 502 "refine produced no text". |
| 429 / 5xx from model | Retry with backoff (existing idiom); surface on exhaustion. |
| Any API error in UI | Existing `alert alert-error` surface via `onError`. |

## 6. Testing

- **`tests/test_refine.py`** (new):
  - `_image_meta` includes house markers (ultra-realistic, pixel, anti-
    hallucination directive, cleanup/tag tail) and echoes the user instruction +
    category name.
  - `_video_meta` includes motion/camera markers (camera/shot, motion, pacing),
    an explicit creativity directive, the fidelity tail, and echoes instruction
    + category.
  - `refine_prompt` returns stripped text from a **fake genai client** (no
    network); markdown fences and preamble are stripped.
  - `kind` routes to the correct builder and the video path uses the higher
    temperature (assert on the config passed to the fake client).
  - Empty instruction raises `ValueError`.
- **Router test** (append to the prompts-router test): 400 on empty instruction,
  200 returns `{refined}` for both kinds (refine monkeypatched), auth required.
- **Frontend:** `npm run build` clean. Manual smoke — thin → expanded; festive
  variant; a "match the provided pattern details" directive survives into the
  output; video kind yields motion/camera language.

## 7. Open items (resolve during planning)

- Confirm the exact advanced text model id for `GEMINI_TEXT_MODEL`
  (Gemini 3 Pro text sibling).
- Confirm the generate/review screen component paths for the image-prompt and
  video-prompt boxes.
- Pick concrete temperature values (image low, video higher).

## 8. Self-review

- **Placeholders:** none — open items are explicitly deferred to planning with
  named defaults, not TBDs in the contract.
- **Consistency:** stateless/no-persist stated in goal, non-goals, architecture,
  and data flow; image<video temperature stated consistently; error table
  matches §3.3.
- **Scope:** single implementation plan — one core module, one endpoint, one
  shared component wired into existing surfaces. No decomposition needed.
- **Ambiguity:** "all locations we pass the prompt" pinned to the three concrete
  boxes in §3.4; variant mechanism pinned to freeform body text (no separate
  field).
