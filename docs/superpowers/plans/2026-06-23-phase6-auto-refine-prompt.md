# Phase 6 — Auto-refine Prompt Button Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an on-demand **Refine** button at every editable prompt box that rewrites a freeform instruction (thin keyword, themed brief, or explicit directives) into a full house-style, Gemini-optimized image **or** video prompt, filling the same box for review — no auto-save.

**Architecture:** Pure additive feature. A new stateless core module (`mockup_generator/prompts/refine.py`) builds image/video meta-prompts and calls the configured advanced text model through the existing `get_genai_client()`. One new stateless endpoint `POST /api/prompts/refine` in the existing prompts router exposes it. One shared React `RefineButton` (with a framing tooltip) wires into the three places a prompt is edited: the Prompts-tab editor, the generate image-prompt box, and the generate video-prompt box.

**Tech Stack:** Python 3.10, FastAPI, Poetry, pytest, `google-genai`; Vite + React + TypeScript.

**Design:** `docs/superpowers/specs/2026-06-23-phase6-auto-refine-prompt-design.md`

## Global Constraints

- Python 3.10 (`>=3.10,<3.11`). Backend tests: `poetry run pytest`. Frontend gate: `cd frontend && npm run build`.
- **Stateless feature:** refine writes nothing to the DB and adds no persisted entity. No schema change, no migration, no new dependency.
- Reuse the existing genai client (`mockup_generator/generation/common.get_genai_client`). Do not destabilize the image-generation path in `common.py`.
- New setting `GEMINI_TEXT_MODEL`, default `gemini-3-pro` (advanced text model — the text sibling of the image model, not Flash).
- Image refine uses **low** temperature (faithful expansion); video refine uses **higher** temperature (more creative). Image temp < video temp.
- Both meta-prompts must (a) emit the house structure, (b) **preserve every explicit user directive verbatim**, (c) ground in the category when provided, (d) output prompt text only — no preamble, no markdown fences.
- New endpoint is auth-gated exactly like its prompts-router siblings.
- Caveman mode is chat-only; code, comments, commit messages, and docs are normal prose.

---

### Task 1: Core refine module + meta-prompt builders + setting

**Files:**
- Modify: `mockup_generator/config.py` (add `gemini_text_model` property near `gemini_image_model`, ~line 84)
- Create: `mockup_generator/prompts/refine.py`
- Create: `tests/test_refine.py`
- Modify: `.env.example` (add `GEMINI_TEXT_MODEL` under the AI providers block)

**Interfaces:**
- Consumes: `mockup_generator.generation.common.get_genai_client`, `google.genai.types`, `google.genai.errors`, `mockup_generator.config.settings`.
- Produces:
  - `settings.gemini_text_model -> str`
  - `refine.RefineFailed` (RuntimeError subclass)
  - `refine._image_meta(instruction: str, category_name: str | None) -> str`
  - `refine._video_meta(instruction: str, category_name: str | None) -> str`
  - `refine.refine_prompt(instruction: str, category_name: str | None = None, *, kind: str = "image") -> str`

- [ ] **Step 1: Add the `gemini_text_model` setting**

In `mockup_generator/config.py`, immediately after the `gemini_image_model` property (~line 84), add:

```python
    @property
    def gemini_text_model(self) -> str:
        """Advanced Gemini text model used to refine/expand prompts.
        Text sibling of the image model (not Flash); override per deploy."""
        return _get("GEMINI_TEXT_MODEL", default="gemini-3-pro")  # type: ignore[return-value]
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_refine.py`:

```python
"""Phase 6: prompt refinement (image + video meta-prompts, model call)."""
from types import SimpleNamespace

import pytest

from mockup_generator.prompts import refine


class _FakeModels:
    def __init__(self, text, sink):
        self._text, self._sink = text, sink

    def generate_content(self, *, model, contents, config):
        self._sink.append({"model": model, "contents": contents, "config": config})
        return SimpleNamespace(text=self._text)


class _FakeClient:
    def __init__(self, text, sink):
        self.models = _FakeModels(text, sink)


def _patch_client(monkeypatch, text="REFINED PROMPT BODY"):
    sink = []
    monkeypatch.setattr(refine, "get_genai_client", lambda: _FakeClient(text, sink))
    return sink


# --- meta-prompt builders -------------------------------------------------

def test_image_meta_has_house_markers_and_echoes_input():
    out = refine._image_meta("red silk saree, match the provided pattern details", "Saree").lower()
    assert "ultra-realistic" in out and "pixel" in out
    assert "do not" in out or "never" in out      # anti-hallucination directive
    assert "tag" in out                            # cleanup tail
    assert "match the provided pattern details" in out   # user directive preserved
    assert "saree" in out                          # category grounding


def test_video_meta_has_motion_markers_and_creativity():
    out = refine._video_meta("slow twirl, festive light, keep print exact", "Saree").lower()
    assert "camera" in out and "motion" in out
    assert "creative" in out                       # explicit creativity directive
    assert "pixel" in out or "exact" in out        # fidelity discipline kept
    assert "keep print exact" in out               # user directive preserved


# --- refine_prompt --------------------------------------------------------

def test_refine_returns_stripped_text(monkeypatch):
    _patch_client(monkeypatch, text="```text\n  A full prompt.\n```")
    assert refine.refine_prompt("thin", "Saree") == "A full prompt."


def test_image_kind_uses_lower_temperature_than_video(monkeypatch):
    sink = _patch_client(monkeypatch)
    refine.refine_prompt("x", "Saree", kind="image")
    refine.refine_prompt("x", "Saree", kind="video")
    img_temp = sink[0]["config"].temperature
    vid_temp = sink[1]["config"].temperature
    assert img_temp < vid_temp


def test_empty_instruction_raises():
    with pytest.raises(ValueError):
        refine.refine_prompt("   ")


def test_no_text_raises_refine_failed(monkeypatch):
    _patch_client(monkeypatch, text="")
    with pytest.raises(refine.RefineFailed):
        refine.refine_prompt("thin")
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `poetry run pytest tests/test_refine.py -v`
Expected: FAIL — `mockup_generator.prompts.refine` does not exist (ImportError / ModuleNotFoundError).

- [ ] **Step 4: Implement `mockup_generator/prompts/refine.py`**

Create `mockup_generator/prompts/refine.py`:

```python
"""On-demand prompt refinement.

Turns a freeform instruction (a thin keyword, a themed brief, or one carrying
explicit directives) into a full house-style, Gemini-optimized prompt. Two
contracts: image (faithful, structured expansion) and video (richer, more
creative motion direction). Stateless — calls the configured text model via the
shared genai client and returns text. Persists nothing.
"""

from __future__ import annotations

import time
from random import random

from google.genai import errors, types

from mockup_generator.config import settings
from mockup_generator.generation.common import get_genai_client

_IMAGE_TEMPERATURE = 0.4
_VIDEO_TEMPERATURE = 0.9
_MAX_ATTEMPTS = 4

_SYSTEM = (
    "You are a senior prompt engineer for Bindal's Creation, a luxury Indian "
    "ethnic-wear brand. You rewrite a user's rough instruction into a single, "
    "production-ready generation prompt. Output ONLY the final prompt text — no "
    "preamble, no explanation, no markdown fences."
)

# One shipped exemplar keeps the model anchored to the house structure without
# bloating the request. Imported lazily to avoid a heavy import at module load.

def _image_exemplar() -> str:
    from mockup_generator.prompts.defaults import SAREE_PROMPT
    return SAREE_PROMPT


class RefineFailed(RuntimeError):
    """The text model returned no usable prompt text."""


def _category_line(category_name: str | None) -> str:
    if category_name:
        return f"The garment category is: {category_name}. Ground every detail in this garment type.\n"
    return ""


def _image_meta(instruction: str, category_name: str | None) -> str:
    return (
        "Rewrite the user's instruction into ONE ultra-realistic, hyper-detailed "
        "image-generation prompt in the house style.\n"
        + _category_line(category_name)
        + "Required structure: garment specs -> model requirements -> "
        "technical/aesthetic specs -> anti-hallucination and final cleanup tail.\n"
        "Hard rules:\n"
        "- Demand pixel-for-pixel fidelity to the uploaded reference; DO NOT invent "
        "motifs, colors, prints, or silhouettes.\n"
        "- End with a cleanup directive removing all tags, pins, stands, and labels.\n"
        "- Preserve EVERY explicit instruction the user gave, verbatim in intent "
        "(mood, must-keep details, length, color). Drop nothing.\n"
        "- Output the prompt text only.\n\n"
        "Follow the structure and tone of this shipped example:\n"
        f"<<<EXAMPLE>>>\n{_image_exemplar()}\n<<<END EXAMPLE>>>\n\n"
        f"User instruction:\n{instruction.strip()}"
    )


def _video_meta(instruction: str, category_name: str | None) -> str:
    return (
        "Rewrite the user's instruction into ONE short vertical (9:16) product "
        "video-generation prompt for VEO. Be more creative than a still image "
        "prompt while keeping the garment pixel-faithful to the reference.\n"
        + _category_line(category_name)
        + "Cover, in order:\n"
        "- Opening framing and a clear camera / shot language (slow push-in, gentle "
        "dolly, orbit, or pan) with pacing for a few-second clip and a loop-friendly "
        "resolve.\n"
        "- Motion: fabric flow and drape, subtle model motion (a turn, twirl, or "
        "step), and a lighting or mood shift.\n"
        "- An evocative, creative mood and atmosphere.\n"
        "Hard rules:\n"
        "- Keep the garment pixel-faithful: DO NOT invent motifs, colors, or change "
        "the silhouette.\n"
        "- Preserve EVERY explicit instruction the user gave, verbatim in intent. "
        "Drop nothing.\n"
        "- Output the prompt text only.\n\n"
        f"User instruction:\n{instruction.strip()}"
    )


def _strip(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        # drop the opening fence (``` or ```lang) and the trailing fence
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1 :]
        if text.endswith("```"):
            text = text[: -3]
    return text.strip()


def _generate_text(contents: str, temperature: float) -> str:
    client = get_genai_client()
    config = types.GenerateContentConfig(
        system_instruction=_SYSTEM,
        temperature=temperature,
    )
    wait = 8
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = client.models.generate_content(
                model=settings.gemini_text_model,
                contents=contents,
                config=config,
            )
            return getattr(resp, "text", "") or ""
        except errors.ClientError as e:
            if getattr(e, "status_code", None) == 429 and attempt < _MAX_ATTEMPTS:
                time.sleep(wait)
                wait = min(wait * 2, 60)
                continue
            raise
        except errors.ServerError:
            if attempt < _MAX_ATTEMPTS:
                time.sleep(int(wait * (1 + random())))
                wait = min(wait * 2, 60)
                continue
            raise
    raise RefineFailed("exhausted retries without a response")


def refine_prompt(
    instruction: str,
    category_name: str | None = None,
    *,
    kind: str = "image",
) -> str:
    """Expand a freeform instruction into a full house-style prompt.

    ``kind`` selects the image or video contract (and temperature). Raises
    ``ValueError`` on an empty instruction and ``RefineFailed`` when the model
    returns no usable text.
    """
    if not instruction or not instruction.strip():
        raise ValueError("instruction is empty")
    if kind == "video":
        contents = _video_meta(instruction, category_name)
        temperature = _VIDEO_TEMPERATURE
    else:
        contents = _image_meta(instruction, category_name)
        temperature = _IMAGE_TEMPERATURE
    text = _strip(_generate_text(contents, temperature))
    if not text:
        raise RefineFailed("model returned no text")
    return text
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `poetry run pytest tests/test_refine.py -v`
Expected: PASS — all six tests green.

- [ ] **Step 6: Add `GEMINI_TEXT_MODEL` to `.env.example`**

In `.env.example`, under the `# --- AI providers ---` block, add:

```bash
GEMINI_TEXT_MODEL=gemini-3-pro            # advanced text model for prompt refinement
```

- [ ] **Step 7: Run the full suite for regressions**

Run: `poetry run pytest -q`
Expected: PASS — all prior tests still green plus the 6 new ones.

- [ ] **Step 8: Commit**

```bash
git add mockup_generator/prompts/refine.py mockup_generator/config.py tests/test_refine.py .env.example
git commit -m "feat(prompts): core prompt-refine module (image + video contracts)

Stateless refine_prompt() expands a freeform instruction into a full
house-style Gemini prompt via the advanced text model (GEMINI_TEXT_MODEL).
Image contract is faithful/structured (low temperature); video contract is
richer and more creative (higher temperature). Preserves explicit user
directives and grounds in the category. Writes nothing."
```

---

### Task 2: `POST /api/prompts/refine` endpoint + schemas

**Files:**
- Modify: `backend/schemas.py` (add `RefineRequest`, `RefineResponse` after `PromptUpdate`, ~line 60)
- Modify: `backend/routers/prompts.py` (add the `refine` route + imports)
- Modify: `tests/test_prompts_api.py` (append refine endpoint tests)

**Interfaces:**
- Consumes: `mockup_generator.prompts.refine.refine_prompt` (Task 1), `mockup_generator.db.products_repo.list_categories(client) -> list[tuple[str, str]]` (existing — `(categoryid, name)` pairs), `backend.auth.get_current_user`, `backend.deps.get_db`.
- Produces: `POST /api/prompts/refine` accepting `{instruction, categoryid?, kind}` → `{refined}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_prompts_api.py`:

```python
def test_refine_returns_text(client, monkeypatch):
    from mockup_generator.prompts import refine as refine_mod
    from backend.routers import prompts as prompts_router

    seen = {}

    def fake_refine(instruction, category_name=None, *, kind="image"):
        seen.update(instruction=instruction, category_name=category_name, kind=kind)
        return "EXPANDED PROMPT"

    monkeypatch.setattr(refine_mod, "refine_prompt", fake_refine)
    # category name resolution: SA -> "Saree"
    monkeypatch.setattr(prompts_router.products_repo, "list_categories",
                        lambda db: [("SA", "Saree"), ("CRD", "Cord Set")])

    r = client.post("/api/prompts/refine",
                    json={"instruction": "red saree", "categoryid": "SA", "kind": "image"})
    assert r.status_code == 200
    assert r.json()["refined"] == "EXPANDED PROMPT"
    assert seen == {"instruction": "red saree", "category_name": "Saree", "kind": "image"}


def test_refine_video_kind(client, monkeypatch):
    from mockup_generator.prompts import refine as refine_mod
    from backend.routers import prompts as prompts_router
    monkeypatch.setattr(refine_mod, "refine_prompt",
                        lambda instruction, category_name=None, *, kind="image": f"V:{kind}")
    monkeypatch.setattr(prompts_router.products_repo, "list_categories", lambda db: [])
    r = client.post("/api/prompts/refine", json={"instruction": "twirl", "kind": "video"})
    assert r.status_code == 200 and r.json()["refined"] == "V:video"


def test_refine_empty_instruction_is_400(client):
    r = client.post("/api/prompts/refine", json={"instruction": "   ", "kind": "image"})
    assert r.status_code == 400


def test_refine_bad_kind_is_422(client):
    r = client.post("/api/prompts/refine", json={"instruction": "x", "kind": "audio"})
    assert r.status_code == 422
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/test_prompts_api.py -k refine -v`
Expected: FAIL — route does not exist yet (404), so the 200/400 assertions fail.

- [ ] **Step 3: Add the schemas**

In `backend/schemas.py`, immediately after the `PromptUpdate` class (~line 60), add:

```python
from typing import Literal


class RefineRequest(BaseModel):
    instruction: str
    categoryid: str | None = None
    kind: Literal["image", "video"] = "image"


class RefineResponse(BaseModel):
    refined: str
```

(If `from typing import Literal` is cleaner at the top of the file, move it there with the other imports; do not duplicate it.)

- [ ] **Step 4: Add the endpoint**

In `backend/routers/prompts.py`, extend the imports and add the route. Update the schema import line to include the new names, and add the products_repo + refine imports:

```python
from backend.schemas import PromptCreate, PromptOut, PromptUpdate, RefineRequest, RefineResponse
from mockup_generator.db import products_repo, prompts_repo
from mockup_generator.prompts.refine import RefineFailed, refine_prompt
```

Then add the route (e.g. after `create_prompt`):

```python
@router.post("/prompts/refine", response_model=RefineResponse)
def refine(payload: RefineRequest,
           user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    if not payload.instruction or not payload.instruction.strip():
        raise HTTPException(status_code=400, detail="Instruction is empty.")
    category_name = None
    if payload.categoryid:
        category_name = next(
            (name for cid, name in products_repo.list_categories(db) if cid == payload.categoryid),
            None,
        )
    try:
        refined = refine_prompt(payload.instruction, category_name, kind=payload.kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RefineFailed as exc:
        raise HTTPException(status_code=502, detail="Refine produced no text.") from exc
    return RefineResponse(refined=refined)
```

Add `HTTPException` to the `fastapi` import at the top of the file:

```python
from fastapi import APIRouter, Depends, HTTPException, status
```

- [ ] **Step 5: Run the refine tests to verify they pass**

Run: `poetry run pytest tests/test_prompts_api.py -k refine -v`
Expected: PASS — all four refine tests green (200 image, 200 video, 400 empty, 422 bad kind).

- [ ] **Step 6: Run the full suite for regressions**

Run: `poetry run pytest -q`
Expected: PASS — all tests green.

- [ ] **Step 7: Commit**

```bash
git add backend/schemas.py backend/routers/prompts.py tests/test_prompts_api.py
git commit -m "feat(api): POST /api/prompts/refine endpoint

Stateless, auth-gated endpoint that maps categoryid -> name and calls
refine_prompt (image or video kind). 400 on empty instruction, 422 on bad
kind, 502 when the model returns no text. Writes nothing."
```

---

### Task 3: Frontend — `refinePrompt` API + shared `RefineButton` + wiring

**Files:**
- Modify: `frontend/src/api.ts` (add `refinePrompt` next to the other prompt fns, ~line 236)
- Create: `frontend/src/components/RefineButton.tsx`
- Modify: `frontend/src/components/PromptsTab.tsx` (image refine in `PromptEditor`)
- Modify: `frontend/src/components/ProductsTab.tsx` (image refine on `promptText` box, video refine on `videoPrompt` box)

**Interfaces:**
- Consumes: `apiFetch` (existing in `api.ts`), the `POST /api/prompts/refine` endpoint (Task 2).
- Produces: `refinePrompt(instruction, categoryid?, kind?) => Promise<{ refined: string }>` and a `RefineButton` React component with props `{ kind, instruction, categoryid?, onRefined, onError }`.

- [ ] **Step 1: Add the API function**

In `frontend/src/api.ts`, after `deletePrompt` (~line 236), add:

```typescript
export const refinePrompt = (
  instruction: string,
  categoryid?: string,
  kind: "image" | "video" = "image",
) =>
  apiFetch<{ refined: string }>("/api/prompts/refine", {
    method: "POST",
    body: JSON.stringify({ instruction, categoryid, kind }),
  });
```

- [ ] **Step 2: Create the `RefineButton` component**

Create `frontend/src/components/RefineButton.tsx`:

```tsx
import { useState } from "react";
import { refinePrompt } from "../api";

const HINTS: Record<"image" | "video", string> = {
  image:
    "Describe what you want — garment, mood, any must-keep details. " +
    "e.g. 'Festive Diwali saree, warm mood — match the provided pattern details.'",
  video:
    "Describe the clip — motion, camera, mood, must-keep details. " +
    "e.g. 'Slow elegant twirl, soft festive light, fabric flowing — keep the print exact.'",
};

export default function RefineButton({
  kind, instruction, categoryid, onRefined, onError,
}: {
  kind: "image" | "video";
  instruction: string;
  categoryid?: string;
  onRefined: (text: string) => void;
  onError: (msg: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const empty = !instruction.trim();

  const run = () => {
    setBusy(true);
    refinePrompt(instruction, categoryid, kind)
      .then((r) => onRefined(r.refined))
      .catch((e) => onError(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <div className="toolbar" style={{ alignItems: "center", gap: "var(--sp-2)" }}>
      <button className="btn-primary" onClick={run} disabled={busy || empty}>
        {busy && <span className="spinner" aria-hidden />}
        {busy ? "Refining…" : "✨ Refine"}
      </button>
      <span
        className="hint"
        role="img"
        aria-label="How to write a refine instruction"
        title={HINTS[kind]}
        style={{ cursor: "help" }}
      >
        ⓘ
      </span>
    </div>
  );
}
```

- [ ] **Step 3: Wire image refine into `PromptsTab` `PromptEditor`**

In `frontend/src/components/PromptsTab.tsx`, add the import at the top:

```tsx
import RefineButton from "./RefineButton";
```

Then inside `PromptEditor`, in the prompt-body `field` block (just above the `<textarea>` at ~line 119), add the button — it refines the current `body` and writes the result back into `body`:

```tsx
      <div className="field">
        <div className="toolbar" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <label htmlFor={`body-${prompt.prompt_id}`}>Prompt body</label>
          <RefineButton
            kind="image"
            instruction={body}
            categoryid={prompt.categoryid}
            onRefined={setBody}
            onError={onError}
          />
        </div>
        <textarea id={`body-${prompt.prompt_id}`} value={body}
                  onChange={(e) => setBody(e.target.value)} rows={6} />
      </div>
```

(Note: `prompt.categoryid` exists on the `Prompt` type used here. After refine, the existing `dirty` check turns Save active.)

- [ ] **Step 4: Wire image + video refine into `ProductsTab`**

In `frontend/src/components/ProductsTab.tsx`, add the import at the top:

```tsx
import RefineButton from "./RefineButton";
```

Find the image-prompt textarea bound to `promptText` / `setPromptText` and add, directly above it:

```tsx
          <RefineButton
            kind="image"
            instruction={promptText}
            categoryid={product.categoryid}
            onRefined={setPromptText}
            onError={(m) => setMsg({ kind: "error", text: m })}
          />
```

Find the video-prompt textarea bound to `videoPrompt` / `setVideoPrompt` and add, directly above it:

```tsx
          <RefineButton
            kind="video"
            instruction={videoPrompt}
            categoryid={product.categoryid}
            onRefined={setVideoPrompt}
            onError={(m) => setMsg({ kind: "error", text: m })}
          />
```

(`product` is the prop in scope in this child component — confirm the exact in-scope name when editing; `setMsg` is the existing message setter at ~line 152.)

- [ ] **Step 5: Run the frontend build to verify it compiles**

Run: `cd frontend && npm run build`
Expected: PASS — `tsc -b` clean, Vite build succeeds (no type errors on the new props/usages).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api.ts frontend/src/components/RefineButton.tsx frontend/src/components/PromptsTab.tsx frontend/src/components/ProductsTab.tsx
git commit -m "feat(ui): shared RefineButton with framing tooltip

Adds refinePrompt() and a reusable ✨ Refine control (image/video kinds, info
tooltip) wired into the Prompts-tab editor and the generate image + video
prompt boxes. Fill-only: refine writes into the box; the user saves manually."
```

---

### Task 4: End-to-end verification

**Files:** none (verification only).

- [ ] **Step 1: Backend suite green**

Run: `poetry run pytest -q`
Expected: PASS — full suite including `tests/test_refine.py` and the new refine API tests.

- [ ] **Step 2: Frontend build clean**

Run: `cd frontend && npm run build`
Expected: PASS — clean `tsc` + Vite build.

- [ ] **Step 3: Live smoke (with a filled `.env` incl. `GEMINI_TEXT_MODEL`)**

Run: `poetry run uvicorn backend.main:app --reload`, then `cd frontend && npm run dev`. In the UI:
- Prompts tab: type `red silk saree, festive Diwali mood — match the provided pattern details` into a body, click **✨ Refine** → box fills with a full house-style image prompt that keeps the festive mood and the "match the provided pattern details" directive; Save persists it.
- Generate screen image box: thin instruction → Refine → expanded image prompt.
- Generate screen video box: `slow twirl, soft festive light, keep print exact` → Refine (`kind=video`) → prompt with camera/motion language that keeps the garment exact.
- Hover the ⓘ next to a Refine button → framing tooltip shows (image vs video copy).

Expected: all four behaviors hold; refine never writes to the DB on its own (only Save / generate does).

- [ ] **Step 4: Mark Phase 6 done in the roadmap and commit**

In `docs/plans/2026-06-21-implementation-plan.md`, update the Phase 6 heading + checkbox:

```markdown
## Phase 6 — Auto-refine prompt button  ← ✅ DONE
**Design:** `docs/superpowers/specs/2026-06-23-phase6-auto-refine-prompt-design.md`. **Plan:** `docs/superpowers/plans/2026-06-23-phase6-auto-refine-prompt.md`.
- [x] On-demand button that turns a freeform instruction into a full Gemini-optimized image or video prompt (only when the user asks). Stateless `POST /api/prompts/refine` + shared `RefineButton`; advanced `GEMINI_TEXT_MODEL`; fill-only, no auto-save.
```

```bash
git add docs/plans/2026-06-21-implementation-plan.md
git commit -m "docs: mark Phase 6 (auto-refine prompt button) done"
```

---

## Self-Review

**Spec coverage:**
- §3.1 core `refine_prompt` + model setting + retry + output hygiene → Task 1 (`refine.py`, `gemini_text_model`, `_generate_text` backoff, `_strip`). ✓
- §3.2 image + video meta-prompt contracts (house structure, directive preservation, category grounding, text-only, video creativity) → Task 1 `_image_meta`/`_video_meta` + `test_image_meta_*`/`test_video_meta_*`. ✓
- §3.3 endpoint (`{instruction, categoryid?, kind}` → `{refined}`, 400/422/502, category-name resolution, writes nothing) → Task 2. ✓
- §3.4 frontend (`refinePrompt`, shared `RefineButton` with kind-specific tooltip, three surfaces, fill-only) → Task 3. ✓
- §4 data flow / §5 error handling → Task 2 endpoint + Task 3 `onError` wiring; full table covered (empty→400, bad kind→422, no text→502, model retry, UI alert). ✓
- §6 testing (image+video markers, fake-client parse, kind→temperature, empty raises, router 400/422, frontend build, manual smoke) → Tasks 1, 2, 4. ✓
- §7 open items resolved: model id default `gemini-3-pro` (Task 1); generate boxes pinned to `promptText`/`videoPrompt` in `ProductsTab` (Task 3); temperatures `_IMAGE_TEMPERATURE=0.4` < `_VIDEO_TEMPERATURE=0.9` (Task 1). ✓

**Placeholder scan:** No TBD/TODO. Every code step shows complete code; every command shows expected output. The two "confirm the in-scope name" notes (Task 3 Steps 3–4) point at verified existing identifiers (`prompt.categoryid`, `product.categoryid`, `setMsg`), not unwritten code. ✓

**Type consistency:** `refine_prompt(instruction, category_name=None, *, kind="image") -> str` identical across Task 1 def, Task 2 call, and Task 2 test fake. `RefineRequest{instruction, categoryid?, kind}` / `RefineResponse{refined}` match the endpoint, the api.ts `refinePrompt`, and `RefineButton` usage. `RefineFailed` raised in Task 1 and caught in Task 2. `products_repo.list_categories(db) -> list[tuple[str,str]]` matches the existing repo signature read during planning. `RefineButton` props (`kind, instruction, categoryid?, onRefined, onError`) match all three call sites. ✓
```
