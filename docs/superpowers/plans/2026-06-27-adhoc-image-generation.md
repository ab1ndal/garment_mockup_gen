# Ad-hoc Image Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an ad-hoc "upload files → generate" path (new "Quick Generate" tab) that mirrors the regular image flow, exposes the full Gemini-3 image-generation option surface, and writes nothing to the catalog/DB/Drive.

**Architecture:** A new multipart route `POST /api/generate/image/upload` reuses the shared generation core (`generate_mockup_bytes` / `generate_with_retries`), which gains optional passthrough params (output mime, JPEG quality, person generation, thinking level). `GET /api/generate/options` publishes a per-model capability map; the new React `QuickGenerateTab` gates its selectors on it. No `productid`, no `get_db`, no Storage.

**Tech Stack:** FastAPI + Pydantic + `google-genai` 2.9.0 + Pillow (backend, pytest); React 18 + Vite + TypeScript + Tailwind (frontend, `npm run build` typecheck gate — no JS test runner in this repo).

## Global Constraints

- Python 3.10 (`>=3.10,<3.11`). Backend tests: `poetry run pytest`.
- `google-genai` 2.9.0. Image options come from `types.ImageConfig` /
  `types.ImageConfigImageOutputOptions` / `types.ThinkingConfig`.
- Gemini-3 image aspect ratios (full): `1:1, 16:9, 9:16, 4:3, 3:4, 5:4, 4:5, 3:2, 2:3, 1:4, 4:1, 1:8, 8:1`. `21:9` is **not** supported and must be removed.
- Image sizes: `1K/2K/4K` (3-Pro & 3.1-Flash); `512px` (3.1-Flash only); `2.5-Flash` has no 4K (`1K/2K`).
- `person_generation` values: `DONT_ALLOW, ALLOW_ADULT, ALLOW_ALL` (Vertex only — surface API errors, don't gate).
- `thinking_level` values: `minimal, high` (3.1-Flash only).
- Max references `_MAX_REFS = 14`; max upload `_MAX_UPLOAD_BYTES = 25 MB`.
- Do NOT change the return type of `generate_mockup_bytes` (stays `bytes`); the upload route derives the output mime from the returned bytes via PIL. This avoids churn in the existing `/image` route and its tests.
- All routes use `Depends(get_current_user)`. The upload route takes NO `get_db`.

---

## File Structure

- `mockup_generator/generation/common.py` — `generate_with_retries` gains output-mime / quality / thinking-level params; `first_image_bytes` preserves PNG vs JPEG.
- `mockup_generator/generation/service.py` — `generate_mockup_bytes` forwards the new params.
- `backend/schemas.py` — add `GenerateUploadPreview`.
- `backend/routers/generate.py` — capability map + caps helper, corrected `ALLOWED_ASPECTS`, extended `/options`, new `POST /image/upload`.
- `tests/test_generation_service.py` — core param + format-preservation tests.
- `tests/test_generate_api.py` — options-map + upload-route tests; fix 2 existing tests affected by the aspect-list change.
- `frontend/src/api.ts` — `GenUploadPreview`, `ImageCaps`, extended `GenOptions`, `generateImageUpload`.
- `frontend/src/components/QuickGenerateTab.tsx` — new tab component.
- `frontend/src/App.tsx` — register the 4th tab.

---

## Task 1: Core engine — output mime, JPEG quality, thinking level, format preservation

**Files:**
- Modify: `mockup_generator/generation/common.py`
- Modify: `mockup_generator/generation/service.py`
- Test: `tests/test_generation_service.py`

**Interfaces:**
- Produces:
  - `common.generate_with_retries(model_name, contents, *, aspect_ratio="1:1", resolution="4K", person_generation=None, system_instruction=None, output_mime_type=None, output_compression_quality=None, thinking_level=None, max_attempts=5)`
  - `common.first_image_bytes(response) -> bytes | None` (now preserves JPEG vs PNG)
  - `service.generate_mockup_bytes(images, prompt, *, model=None, resolution=None, aspect_ratio=None, output_mime_type=None, output_compression_quality=None, person_generation=None, thinking_level=None) -> bytes`

- [ ] **Step 1: Write failing tests for the new engine params + format preservation**

Add to `tests/test_generation_service.py`:

```python
def test_generate_with_retries_threads_output_options_and_thinking(monkeypatch):
    captured = {}
    _capture_client(monkeypatch, captured)

    common.generate_with_retries(
        "m", ["p"],
        output_mime_type="image/jpeg", output_compression_quality=80,
        thinking_level="high",
    )

    ic = captured["config"].image_config
    assert ic.image_output_options is not None
    assert ic.image_output_options.mime_type == "image/jpeg"
    assert ic.image_output_options.compression_quality == 80
    assert captured["config"].thinking_config is not None


def test_generate_with_retries_omits_output_options_and_thinking_by_default(monkeypatch):
    captured = {}
    _capture_client(monkeypatch, captured)

    common.generate_with_retries("m", ["p"])

    ic = captured["config"].image_config
    assert ic.image_output_options is None
    assert captured["config"].thinking_config is None


def test_first_image_bytes_preserves_jpeg():
    from io import BytesIO as _B
    buf = _B()
    _png_image().save(buf, "JPEG")
    blob = _FakeBlob(buf.getvalue(), mime_type="image/jpeg")
    part = type("P", (), {"inline_data": blob})()
    resp = _FakeResponse([part])

    data = common.first_image_bytes(resp)
    assert Image.open(_B(data)).format == "JPEG"


def test_generate_mockup_bytes_threads_output_and_thinking(monkeypatch):
    captured = {}

    def fake_retries(model_name, contents, **kwargs):
        captured["kwargs"] = kwargs
        return _FakeResponse([_FakePart(_png_image())])

    monkeypatch.setattr(service, "generate_with_retries", fake_retries)
    service.generate_mockup_bytes(
        [_png_image()], "p",
        output_mime_type="image/jpeg", output_compression_quality=70,
        person_generation="ALLOW_ADULT", thinking_level="high",
    )
    kw = captured["kwargs"]
    assert kw["output_mime_type"] == "image/jpeg"
    assert kw["output_compression_quality"] == 70
    assert kw["person_generation"] == "ALLOW_ADULT"
    assert kw["thinking_level"] == "high"
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `poetry run pytest tests/test_generation_service.py -k "output_options or preserves_jpeg or output_and_thinking" -v`
Expected: FAIL (TypeError: unexpected keyword argument / AttributeError on `image_output_options` / format == "PNG").

- [ ] **Step 3: Implement the engine changes**

In `mockup_generator/generation/common.py`, replace `first_image_bytes` body's encode block to preserve format:

```python
def first_image_bytes(response) -> bytes | None:
    """Return image bytes of the first image part, preserving the model's
    format (JPEG stays JPEG, everything else normalized to PNG)."""
    for part in response.candidates[0].content.parts:
        blob = getattr(part, "inline_data", None)
        data = getattr(blob, "data", None)
        if not data:
            continue
        mime = getattr(blob, "mime_type", "") or ""
        if mime and not mime.startswith("image/"):
            continue
        img = Image.open(BytesIO(data))
        out_fmt = "JPEG" if (img.format or "PNG").upper() in ("JPEG", "JPG") else "PNG"
        buf = BytesIO()
        img.convert("RGB").save(buf, format=out_fmt)
        return buf.getvalue()
    return None
```

Replace the `generate_with_retries` signature and config build:

```python
def generate_with_retries(
    model_name: str,
    contents,
    *,
    aspect_ratio: str = "1:1",
    resolution: str = "4K",
    person_generation: str | None = None,
    system_instruction: str | None = None,
    output_mime_type: str | None = None,
    output_compression_quality: int | None = None,
    thinking_level: str | None = None,
    max_attempts: int = 5,
):
    image_config = types.ImageConfig(aspect_ratio=aspect_ratio, image_size=resolution)
    if person_generation is not None:
        image_config.person_generation = person_generation
    if output_mime_type is not None or output_compression_quality is not None:
        image_config.image_output_options = types.ImageConfigImageOutputOptions(
            mime_type=output_mime_type,
            compression_quality=output_compression_quality,
        )
    thinking_config = (
        types.ThinkingConfig(thinking_level=thinking_level) if thinking_level else None
    )
    client = get_genai_client()
    if system_instruction is None:
        system_instruction = (
            "You are a professional fashion editor for Bindal's Creation. "
            "Always produce high-end, editorial quality images. Garments must "
            "be wrinkle-free and tailored."
        )
    safety_settings = [
        types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
        types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
    ]
    wait = 8
    for attempt in range(1, max_attempts + 1):
        try:
            return client.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_modalities=["IMAGE"],
                    safety_settings=safety_settings,
                    image_config=image_config,
                    thinking_config=thinking_config,
                ),
            )
        except errors.ClientError as e:
            if getattr(e, "status_code", None) == 429 and attempt < max_attempts:
                time.sleep(wait)
                wait = min(wait * 2, 60)
                continue
            raise
        except errors.ServerError:
            if attempt < max_attempts:
                time.sleep(int(wait * (1 + random())))
                wait = min(wait * 2, 60)
                continue
            raise
```

In `mockup_generator/generation/service.py`, extend `generate_mockup_bytes`:

```python
def generate_mockup_bytes(
    images: list[Image.Image],
    prompt: str,
    *,
    model: str | None = None,
    resolution: str | None = None,
    aspect_ratio: str | None = None,
    output_mime_type: str | None = None,
    output_compression_quality: int | None = None,
    person_generation: str | None = None,
    thinking_level: str | None = None,
) -> bytes:
    """Generate one mockup from reference ``images`` + ``prompt`` → image bytes
    (PNG by default, JPEG when ``output_mime_type='image/jpeg'``)."""
    model_name = model or settings.gemini_image_model
    parts = []
    for im in images:
        im = im.convert("RGB")
        im.thumbnail((MAX_SIDE, MAX_SIDE))
        parts.append(part_from_pil(im))

    contents = [prompt, *parts]
    response = generate_with_retries(
        model_name, contents,
        aspect_ratio=aspect_ratio or ASPECT_RATIO,
        resolution=resolution or RESOLUTION,
        output_mime_type=output_mime_type,
        output_compression_quality=output_compression_quality,
        person_generation=person_generation,
        thinking_level=thinking_level,
    )
    data = first_image_bytes(response)
    if data is None:
        raise NoImageReturned("Gemini returned no image part")
    return data
```

- [ ] **Step 4: Run the full service test file to verify pass (incl. existing PNG tests)**

Run: `poetry run pytest tests/test_generation_service.py -v`
Expected: PASS (new tests pass; existing `*_returns_png` / `first_image_bytes_*` still pass — defaults keep PNG).

- [ ] **Step 5: Commit**

```bash
git add mockup_generator/generation/common.py mockup_generator/generation/service.py tests/test_generation_service.py
git commit -m "feat(generation): output mime/quality + thinking level, preserve image format"
```

---

## Task 2: Capability map + corrected aspects + extended /options

**Files:**
- Modify: `backend/routers/generate.py`
- Test: `tests/test_generate_api.py`

**Interfaces:**
- Produces (module-level in `generate.py`):
  - `ASPECTS_FULL`, `ASPECTS_25`, `PERSON_VALUES`, `MIME_TYPES`, `IMAGE_CAPS: dict`, `COMPRESSION_BOUNDS`
  - `_caps_for(model: str | None) -> dict`
  - `ALLOWED_ASPECTS` corrected to `ASPECTS_FULL`
  - `/api/generate/options` response gains `image_caps`, `image_compression`

- [ ] **Step 1: Fix the two existing tests broken by the aspect-list change, and add an options-map test**

In `tests/test_generate_api.py`, update `test_generation_options_lists_choices_and_defaults`:

```python
def test_generation_options_lists_choices_and_defaults(client):
    r = client.get("/api/generate/options")
    assert r.status_code == 200
    body = r.json()
    assert "gemini-3-pro-image" in body["models"]
    assert body["resolutions"] == ["1K", "2K", "4K"]
    assert "1:1" in body["aspect_ratios"] and "3:4" in body["aspect_ratios"]
    assert "4:5" in body["aspect_ratios"]       # now supported
    assert "21:9" not in body["aspect_ratios"]  # removed — unsupported by the model
    assert body["defaults"]["resolution"] in body["resolutions"]
    assert body["defaults"]["aspect_ratio"] in body["aspect_ratios"]
    # per-model capability map
    caps = body["image_caps"]["gemini-3-pro-image"]
    assert "4K" in caps["image_sizes"]
    assert caps["thinking_levels"] == []
    flash = body["image_caps"]["gemini-3.1-flash-image"]
    assert "512px" in flash["image_sizes"] and "high" in flash["thinking_levels"]
    assert "4K" not in body["image_caps"]["gemini-2.5-flash-image"]["image_sizes"]
    # video options (unchanged)
    assert "veo-3.1-generate-preview" in body["video_models"]
    assert body["video_resolutions"] == ["720p", "1080p"]
    assert body["video_aspect_ratios"] == ["9:16", "16:9"]
    assert body["video_durations"] == [4, 6, 8]
    assert body["video_defaults"]["resolution"] in body["video_resolutions"]
```

Update `test_generate_image_rejects_bad_aspect` (4:5 is now valid → use the now-removed 21:9):

```python
def test_generate_image_rejects_bad_aspect(client, monkeypatch):
    _wire_happy(monkeypatch, calls={})
    r = client.post("/api/generate/image", json={
        "productid": "BC25001", "prompt": "p", "image_ids": ["f1"], "aspect_ratio": "21:9"})
    assert r.status_code == 400
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/test_generate_api.py -k "options_lists or rejects_bad_aspect" -v`
Expected: FAIL (`image_caps` KeyError; `21:9` currently still accepted so the reject test fails).

- [ ] **Step 3: Add the capability map + caps helper, correct ALLOWED_ASPECTS, extend /options**

In `backend/routers/generate.py`, replace the `ALLOWED_ASPECTS` / `_DEFAULTS` block (lines ~55–58) with:

```python
ALLOWED_MODELS = ["gemini-3-pro-image", "gemini-3.1-flash-image", "gemini-2.5-flash-image"]
ALLOWED_RESOLUTIONS = ["1K", "2K", "4K"]          # 1K and 2K cost the same → default 2K

# Gemini-3 image models support this full aspect set; 2.5-Flash a subset. 21:9 is NOT supported.
ASPECTS_FULL = ["1:1", "16:9", "9:16", "4:3", "3:4", "5:4", "4:5",
                "3:2", "2:3", "1:4", "4:1", "1:8", "8:1"]
ASPECTS_25 = ["1:1", "16:9", "9:16", "4:3", "3:4", "5:4", "4:5", "3:2", "2:3"]
PERSON_VALUES = ["DONT_ALLOW", "ALLOW_ADULT", "ALLOW_ALL"]
MIME_TYPES = ["image/png", "image/jpeg"]
COMPRESSION_BOUNDS = {"min": 1, "max": 100, "default": 90}

# Per-model capability map. thinking_levels == [] means the control is hidden in the UI.
IMAGE_CAPS = {
    "gemini-3-pro-image": {
        "aspect_ratios": ASPECTS_FULL, "image_sizes": ["1K", "2K", "4K"],
        "mime_types": MIME_TYPES, "person_generation": PERSON_VALUES, "thinking_levels": [],
    },
    "gemini-3.1-flash-image": {
        "aspect_ratios": ASPECTS_FULL, "image_sizes": ["512px", "1K", "2K", "4K"],
        "mime_types": MIME_TYPES, "person_generation": PERSON_VALUES,
        "thinking_levels": ["minimal", "high"],
    },
    "gemini-2.5-flash-image": {
        "aspect_ratios": ASPECTS_25, "image_sizes": ["1K", "2K"],
        "mime_types": MIME_TYPES, "person_generation": PERSON_VALUES, "thinking_levels": [],
    },
}
_DEFAULT_CAPS_MODEL = "gemini-3-pro-image"
ALLOWED_ASPECTS = ASPECTS_FULL  # legacy flat list for /image + flat /options
_DEFAULTS = {"model": "gemini-3-pro-image", "resolution": "2K", "aspect_ratio": "1:1"}


def _caps_for(model: str | None) -> dict:
    """Capability set for a model, falling back to the 3-Pro set for unknowns
    (e.g. an env-configured default not in IMAGE_CAPS)."""
    return IMAGE_CAPS.get(model or settings.gemini_image_model, IMAGE_CAPS[_DEFAULT_CAPS_MODEL])
```

In `generation_options(...)`, add the new fields to the returned dict (after `"defaults": ...`):

```python
        "image_caps": IMAGE_CAPS,
        "image_compression": COMPRESSION_BOUNDS,
```

- [ ] **Step 4: Run to verify pass**

Run: `poetry run pytest tests/test_generate_api.py -k "options_lists or rejects_bad_aspect" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/generate.py tests/test_generate_api.py
git commit -m "feat(generate): per-model capability map in /options, drop unsupported 21:9"
```

---

## Task 3: New `POST /api/generate/image/upload` route

**Files:**
- Modify: `backend/schemas.py`
- Modify: `backend/routers/generate.py`
- Test: `tests/test_generate_api.py`

**Interfaces:**
- Consumes: `_caps_for`, `ALLOWED_MODELS`, `_MAX_REFS`, `_MAX_UPLOAD_BYTES`, `_decode_b64_image`, `service.generate_mockup_bytes`.
- Produces:
  - `schemas.GenerateUploadPreview{status, detail, image_b64, mime_type}`
  - `POST /api/generate/image/upload` (multipart) → `GenerateUploadPreview`

- [ ] **Step 1: Write failing tests for the upload route**

Add to `tests/test_generate_api.py` (helpers `_png_bytes`, `client`, `gen` are already imported at the top of the file):

```python
def _jpeg_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (4, 4), (9, 9, 9)).save(buf, format="JPEG")
    return buf.getvalue()


def _wire_upload(monkeypatch, *, calls, returns=None):
    def fake_generate(images, prompt, **kw):
        calls["gen"] = {"n_images": len(images), "prompt": prompt, "kw": kw}
        return returns if returns is not None else _png_bytes()
    monkeypatch.setattr(gen.service, "generate_mockup_bytes", fake_generate)


def _upload(client, *, fields=None, files=None):
    files = files if files is not None else [("files", ("a.png", _png_bytes(), "image/png"))]
    return client.post("/api/generate/image/upload",
                       data={"prompt": "a luxe saree", **(fields or {})}, files=files)


def test_upload_success_single_file(client, monkeypatch):
    calls = {}
    _wire_upload(monkeypatch, calls=calls)
    r = _upload(client)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and len(body["image_b64"]) > 0
    assert body["mime_type"] == "image/png"
    assert calls["gen"]["n_images"] == 1 and calls["gen"]["prompt"] == "a luxe saree"


def test_upload_multiple_files(client, monkeypatch):
    calls = {}
    _wire_upload(monkeypatch, calls=calls)
    files = [("files", (f"{i}.png", _png_bytes(), "image/png")) for i in range(3)]
    r = _upload(client, files=files)
    assert r.status_code == 200
    assert calls["gen"]["n_images"] == 3


def test_upload_refine_only_no_files(client, monkeypatch):
    calls = {}
    _wire_upload(monkeypatch, calls=calls)
    refine = base64.b64encode(_png_bytes()).decode("ascii")
    r = _upload(client, fields={"refine_image_b64": refine}, files=[])
    assert r.status_code == 200
    assert calls["gen"]["n_images"] == 1


def test_upload_requires_source_or_refine_400(client, monkeypatch):
    _wire_upload(monkeypatch, calls={})
    r = _upload(client, files=[])
    assert r.status_code == 400


def test_upload_invalid_image_400(client, monkeypatch):
    _wire_upload(monkeypatch, calls={})
    r = _upload(client, files=[("files", ("x.png", b"not-an-image", "image/png"))])
    assert r.status_code == 400


def test_upload_too_large_413(client, monkeypatch):
    _wire_upload(monkeypatch, calls={})
    monkeypatch.setattr(gen, "_MAX_UPLOAD_BYTES", 8)
    r = _upload(client)
    assert r.status_code == 413


def test_upload_caps_references_at_14(client, monkeypatch):
    calls = {}
    _wire_upload(monkeypatch, calls=calls)
    files = [("files", (f"{i}.png", _png_bytes(), "image/png")) for i in range(20)]
    r = _upload(client, files=files)
    assert r.status_code == 200
    assert calls["gen"]["n_images"] == 14


def test_upload_rejects_bad_model(client, monkeypatch):
    _wire_upload(monkeypatch, calls={})
    r = _upload(client, fields={"model": "gpt-image-1"})
    assert r.status_code == 400


def test_upload_rejects_4k_on_25_flash(client, monkeypatch):
    _wire_upload(monkeypatch, calls={})
    r = _upload(client, fields={"model": "gemini-2.5-flash-image", "resolution": "4K"})
    assert r.status_code == 400


def test_upload_rejects_thinking_on_pro(client, monkeypatch):
    _wire_upload(monkeypatch, calls={})
    r = _upload(client, fields={"model": "gemini-3-pro-image", "thinking_level": "high"})
    assert r.status_code == 400


def test_upload_rejects_512px_on_pro(client, monkeypatch):
    _wire_upload(monkeypatch, calls={})
    r = _upload(client, fields={"model": "gemini-3-pro-image", "resolution": "512px"})
    assert r.status_code == 400


def test_upload_rejects_quality_without_jpeg(client, monkeypatch):
    _wire_upload(monkeypatch, calls={})
    r = _upload(client, fields={"compression_quality": "80"})
    assert r.status_code == 400


def test_upload_rejects_quality_out_of_range(client, monkeypatch):
    _wire_upload(monkeypatch, calls={})
    r = _upload(client, fields={"mime_type": "image/jpeg", "compression_quality": "200"})
    assert r.status_code == 400


def test_upload_jpeg_request_returns_jpeg_mime(client, monkeypatch):
    calls = {}
    _wire_upload(monkeypatch, calls=calls, returns=_jpeg_bytes())
    r = _upload(client, fields={"mime_type": "image/jpeg", "compression_quality": "80"})
    assert r.status_code == 200
    assert r.json()["mime_type"] == "image/jpeg"
    assert calls["gen"]["kw"]["output_mime_type"] == "image/jpeg"
    assert calls["gen"]["kw"]["output_compression_quality"] == 80


def test_upload_gemini_failure_502(client, monkeypatch):
    monkeypatch.setattr(gen.service, "generate_mockup_bytes",
                        lambda *a, **k: (_ for _ in ()).throw(NoImageReturned("nope")))
    r = _upload(client)
    assert r.status_code == 502
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/test_generate_api.py -k upload -v`
Expected: FAIL with 404 (route not defined yet).

- [ ] **Step 3: Add the schema**

In `backend/schemas.py`, after `GeneratePreview`:

```python
class GenerateUploadPreview(BaseModel):
    status: str
    detail: str
    image_b64: str
    mime_type: str
```

- [ ] **Step 4: Implement the route**

In `backend/routers/generate.py`, add `GenerateUploadPreview` to the `backend.schemas` import, then add this route after `generate_image` (before `approve_mockup`):

```python
@router.post("/image/upload", response_model=GenerateUploadPreview)
async def generate_image_upload(
    prompt: str = Form(...),
    model: str | None = Form(None),
    resolution: str | None = Form(None),
    aspect_ratio: str | None = Form(None),
    mime_type: str | None = Form(None),
    compression_quality: int | None = Form(None),
    person_generation: str | None = Form(None),
    thinking_level: str | None = Form(None),
    refine_image_b64: str | None = Form(None),
    files: list[UploadFile] = File(default=[]),
    user: CurrentUser = Depends(get_current_user),
):
    """Ad-hoc, catalog-free generation: uploaded reference images + prompt →
    preview bytes (PNG or JPEG). Writes nothing — no productid, no DB, no Drive."""
    model_name = model or settings.gemini_image_model
    if model is not None and model not in ALLOWED_MODELS:
        raise HTTPException(status_code=400, detail=f"Unsupported model: {model}")
    caps = _caps_for(model_name)
    if aspect_ratio and aspect_ratio not in caps["aspect_ratios"]:
        raise HTTPException(status_code=400, detail=f"Unsupported aspect ratio for {model_name}: {aspect_ratio}")
    if resolution and resolution not in caps["image_sizes"]:
        raise HTTPException(status_code=400, detail=f"Unsupported image size for {model_name}: {resolution}")
    if mime_type and mime_type not in caps["mime_types"]:
        raise HTTPException(status_code=400, detail=f"Unsupported output format: {mime_type}")
    if person_generation and person_generation not in caps["person_generation"]:
        raise HTTPException(status_code=400, detail=f"Unsupported person_generation: {person_generation}")
    if thinking_level and thinking_level not in caps["thinking_levels"]:
        raise HTTPException(status_code=400, detail=f"thinking_level not supported for {model_name}")
    if compression_quality is not None:
        if mime_type != "image/jpeg":
            raise HTTPException(status_code=400, detail="compression_quality applies only to image/jpeg.")
        if not 1 <= compression_quality <= 100:
            raise HTTPException(status_code=400, detail="compression_quality must be 1–100.")

    # Decode the refine reference first so bad input fails fast as a 400.
    refine_img = _decode_b64_image(refine_image_b64) if refine_image_b64 else None

    images: list[Image.Image] = []
    for f in files[:_MAX_REFS]:
        raw = await f.read()
        if len(raw) > _MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="An uploaded image is too large.")
        try:
            images.append(Image.open(BytesIO(raw)).convert("RGB"))
        except Exception as exc:
            raise HTTPException(status_code=400, detail="An uploaded file is not a valid image.") from exc

    if not images and refine_img is None:
        raise HTTPException(status_code=400, detail="Select or upload at least one source image.")

    # Uploads own the reference budget; the refine image is appended only if room.
    if refine_img is not None and len(images) < _MAX_REFS:
        images.append(refine_img)

    try:
        out = service.generate_mockup_bytes(
            images, prompt,
            model=model, resolution=resolution, aspect_ratio=aspect_ratio,
            output_mime_type=mime_type, output_compression_quality=compression_quality,
            person_generation=person_generation or None, thinking_level=thinking_level or None,
        )
    except service.NoImageReturned as exc:
        raise HTTPException(status_code=502, detail="The model returned no image. Try again.") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Image generation failed: {exc}") from exc

    out_fmt = (Image.open(BytesIO(out)).format or "PNG").upper()
    out_mime = "image/jpeg" if out_fmt in ("JPEG", "JPG") else "image/png"
    return GenerateUploadPreview(
        status="ok", detail="Preview generated.",
        image_b64=base64.b64encode(out).decode("ascii"), mime_type=out_mime,
    )
```

- [ ] **Step 5: Run to verify pass + full backend suite green**

Run: `poetry run pytest tests/test_generate_api.py -v && poetry run pytest`
Expected: PASS (all upload tests + whole suite).

- [ ] **Step 6: Commit**

```bash
git add backend/schemas.py backend/routers/generate.py tests/test_generate_api.py
git commit -m "feat(api): add POST /api/generate/image/upload for ad-hoc generation"
```

---

## Task 4: Frontend API client — upload + capability types

**Files:**
- Modify: `frontend/src/api.ts`

**Interfaces:**
- Produces:
  - `interface ImageCaps { aspect_ratios; image_sizes; mime_types; person_generation; thinking_levels: string[] }`
  - `GenOptions` gains `image_caps: Record<string, ImageCaps>` and `image_compression: { min; max; default }`
  - `interface GenUploadPreview extends GenPreview { mime_type: string }`
  - `generateImageUpload(files: File[], fields): Promise<GenUploadPreview>`

- [ ] **Step 1: Add the capability types to `GenOptions`**

In `frontend/src/api.ts`, replace the `GenOptions` interface (lines ~255–265) with:

```typescript
export interface ImageCaps {
  aspect_ratios: string[];
  image_sizes: string[];
  mime_types: string[];
  person_generation: string[];
  thinking_levels: string[];
}

export interface GenOptions {
  models: string[];
  resolutions: string[];
  aspect_ratios: string[];
  defaults: { model: string; resolution: string; aspect_ratio: string };
  image_caps: Record<string, ImageCaps>;
  image_compression: { min: number; max: number; default: number };
  video_models: string[];
  video_resolutions: string[];
  video_aspect_ratios: string[];
  video_durations: number[];
  video_defaults: { model: string; resolution: string; aspect_ratio: string; duration: number };
}
```

- [ ] **Step 2: Add `GenUploadPreview` + `generateImageUpload`**

In `frontend/src/api.ts`, after the `generateImage` definition (line ~292), add:

```typescript
export interface GenUploadPreview extends GenPreview {
  mime_type: string;
}

/** Ad-hoc generation from uploaded files (no product, no DB write). */
export function generateImageUpload(
  files: File[],
  fields: {
    prompt: string;
    model?: string;
    resolution?: string;
    aspect_ratio?: string;
    mime_type?: string;
    compression_quality?: number;
    person_generation?: string;
    thinking_level?: string;
    refine_image_b64?: string;
  },
): Promise<GenUploadPreview> {
  const fd = new FormData();
  fd.append("prompt", fields.prompt);
  if (fields.model) fd.append("model", fields.model);
  if (fields.resolution) fd.append("resolution", fields.resolution);
  if (fields.aspect_ratio) fd.append("aspect_ratio", fields.aspect_ratio);
  if (fields.mime_type) fd.append("mime_type", fields.mime_type);
  if (fields.compression_quality != null)
    fd.append("compression_quality", String(fields.compression_quality));
  if (fields.person_generation) fd.append("person_generation", fields.person_generation);
  if (fields.thinking_level) fd.append("thinking_level", fields.thinking_level);
  if (fields.refine_image_b64) fd.append("refine_image_b64", fields.refine_image_b64);
  files.forEach((f) => fd.append("files", f));
  return apiUpload<GenUploadPreview>("/api/generate/image/upload", fd);
}
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npm run build`
Expected: PASS (no type errors). The `image_caps` field is required, but the only consumer so far (`ProductsTab`) reads only the flat fields — that still typechecks since the backend always returns it.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api.ts
git commit -m "feat(frontend-api): generateImageUpload + per-model capability types"
```

---

## Task 5: Quick Generate tab UI

**Files:**
- Create: `frontend/src/components/QuickGenerateTab.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `getGenerationOptions`, `generateImageUpload`, `GenOptions`, `ImageCaps`, `GenUploadPreview` (api.ts); `RefineButton`; `useImageLightbox` (Lightbox).
- Produces: default-exported `QuickGenerateTab` React component; a `quickgen` tab in `App.tsx`.

- [ ] **Step 1: Create the component**

Create `frontend/src/components/QuickGenerateTab.tsx`:

```tsx
import { useEffect, useMemo, useState } from "react";
import {
  getGenerationOptions, generateImageUpload,
  type GenOptions, type ImageCaps,
} from "../api";
import RefineButton from "./RefineButton";
import { useImageLightbox } from "./Lightbox";

const RES_LABEL: Record<string, string> = {
  "512px": "0.5K", "1K": "1K", "2K": "2K · web", "4K": "4K · print",
};
const MIME_LABEL: Record<string, string> = {
  "image/png": "PNG · lossless", "image/jpeg": "JPEG · smaller",
};
const MAX_FILES = 14;

type Variation = { b64: string; mime: string; promptUsed: string; mode: "fresh" | "refine" };

const extOf = (mime: string) => (mime === "image/jpeg" ? "jpg" : "png");

export default function QuickGenerateTab() {
  const [opts, setOpts] = useState<GenOptions | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("");
  const [aspect, setAspect] = useState("");
  const [imageSize, setImageSize] = useState("");
  const [mimeType, setMimeType] = useState("image/png");
  const [quality, setQuality] = useState(90);
  const [personGen, setPersonGen] = useState("");
  const [thinking, setThinking] = useState("");
  const [variations, setVariations] = useState<Variation[]>([]);
  const [activeIdx, setActiveIdx] = useState(0);
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "info" | "error"; text: string } | null>(null);
  const lightbox = useImageLightbox();

  const active = variations[activeIdx] ?? null;
  const caps: ImageCaps | null = useMemo(
    () => (opts && model ? opts.image_caps[model] ?? null : null),
    [opts, model],
  );

  // Object-URL previews for uploaded files; revoke on change/unmount.
  const previews = useMemo(() => files.map((f) => URL.createObjectURL(f)), [files]);
  useEffect(() => () => previews.forEach((u) => URL.revokeObjectURL(u)), [previews]);

  useEffect(() => {
    getGenerationOptions().then((o) => {
      setOpts(o);
      setModel(o.defaults.model);
      setAspect(o.defaults.aspect_ratio);
      setImageSize(o.defaults.resolution);
      setQuality(o.image_compression.default);
    }).catch((e: Error) => setMsg({ kind: "error", text: e.message }));
  }, []);

  // Clamp selections to the chosen model's capabilities whenever it changes.
  useEffect(() => {
    if (!caps) return;
    if (!caps.aspect_ratios.includes(aspect)) setAspect(caps.aspect_ratios[0]);
    if (!caps.image_sizes.includes(imageSize)) setImageSize(caps.image_sizes[0]);
    if (!caps.mime_types.includes(mimeType)) setMimeType(caps.mime_types[0]);
    if (personGen && !caps.person_generation.includes(personGen)) setPersonGen("");
    if (thinking && !caps.thinking_levels.includes(thinking)) setThinking("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caps]);

  const addFiles = (e: React.ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(e.target.files ?? []);
    setFiles((prev) => {
      const next = [...prev, ...picked].slice(0, MAX_FILES);
      if (prev.length + picked.length > MAX_FILES)
        setMsg({ kind: "info", text: `Limited to ${MAX_FILES} reference images.` });
      return next;
    });
    e.target.value = "";
  };
  const removeFile = (i: number) => setFiles((prev) => prev.filter((_, idx) => idx !== i));

  const pushVariation = (b64: string, mime: string, promptUsed: string, mode: "fresh" | "refine") => {
    setVariations((prev) => {
      const next = [...prev, { b64, mime, promptUsed, mode }];
      setActiveIdx(next.length - 1);
      return next;
    });
    setFeedback("");
  };

  const composePrompt = () =>
    feedback.trim() ? `${prompt}\n\nRevision note: ${feedback.trim()}` : prompt;

  const generate = (refine: boolean) => {
    if (refine && !active) return;
    setBusy(true);
    setMsg(null);
    const promptUsed = composePrompt();
    generateImageUpload(files, {
      prompt: promptUsed,
      model: model || undefined,
      resolution: imageSize || undefined,
      aspect_ratio: aspect || undefined,
      mime_type: mimeType || undefined,
      compression_quality: mimeType === "image/jpeg" ? quality : undefined,
      person_generation: personGen || undefined,
      thinking_level: thinking || undefined,
      refine_image_b64: refine && active ? active.b64 : undefined,
    })
      .then((r) => {
        setMsg({ kind: "info", text: r.detail });
        pushVariation(r.image_b64, r.mime_type, promptUsed, refine ? "refine" : "fresh");
      })
      .catch((e: Error) => setMsg({ kind: "error", text: e.message.replace(/^\d+:\s*/, "") }))
      .finally(() => setBusy(false));
  };

  const download = () => {
    if (!active) return;
    const a = document.createElement("a");
    a.href = `data:${active.mime};base64,${active.b64}`;
    a.download = `mockup_${aspect.replace(":", "x")}.${extOf(active.mime)}`;
    a.click();
  };

  const canGenerate = files.length > 0 && prompt.trim().length > 0 && !busy;

  return (
    <div className="stack">
      <section>
        <h2 className="font-display tracking-tight">Quick Generate</h2>
        <p className="text-subtle text-sm">
          Upload reference images and generate a mockup — nothing is saved to the catalog.
        </p>
      </section>

      {/* Upload */}
      <section className="mt-4">
        <p className="section-label mt-0!">Reference images</p>
        <input type="file" accept="image/*" multiple onChange={addFiles} aria-label="Upload reference images" />
        {files.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {previews.map((src, i) => (
              <div key={i} className="relative h-20 w-20 overflow-hidden rounded-md border border-line">
                <img src={src} alt={files[i].name} className="h-full w-full object-cover" />
                <button
                  type="button"
                  onClick={() => removeFile(i)}
                  aria-label={`Remove ${files[i].name}`}
                  className="absolute right-0.5 top-0.5 rounded-full bg-black/60 px-1.5 text-xs text-white"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Prompt */}
      <section className="mt-5">
        <div className="field">
          <div className="flex items-center justify-between gap-3">
            <label htmlFor="qg-prompt" className="text-xs font-semibold text-subtle">Prompt</label>
            <RefineButton
              kind="image"
              instruction={prompt}
              onRefined={setPrompt}
              onError={(m) => setMsg({ kind: "error", text: m })}
            />
          </div>
          <textarea id="qg-prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={6} />
        </div>
      </section>

      {/* Options (model-gated) */}
      {opts && caps && (
        <section className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <label className="field mb-0!">
            <span className="text-xs font-semibold text-subtle">Model</span>
            <select aria-label="Model" value={model} onChange={(e) => setModel(e.target.value)}>
              {opts.models.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
          <label className="field mb-0!">
            <span className="text-xs font-semibold text-subtle">Aspect ratio</span>
            <select aria-label="Aspect ratio" value={aspect} onChange={(e) => setAspect(e.target.value)}>
              {caps.aspect_ratios.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
          </label>
          <label className="field mb-0!">
            <span className="text-xs font-semibold text-subtle">Image size</span>
            <select aria-label="Image size" value={imageSize} onChange={(e) => setImageSize(e.target.value)}>
              {caps.image_sizes.map((s) => <option key={s} value={s}>{RES_LABEL[s] ?? s}</option>)}
            </select>
          </label>
          <label className="field mb-0!">
            <span className="text-xs font-semibold text-subtle">Output format</span>
            <select aria-label="Output format" value={mimeType} onChange={(e) => setMimeType(e.target.value)}>
              {caps.mime_types.map((m) => <option key={m} value={m}>{MIME_LABEL[m] ?? m}</option>)}
            </select>
          </label>
          {mimeType === "image/jpeg" && (
            <label className="field mb-0!">
              <span className="text-xs font-semibold text-subtle">JPEG quality · {quality}</span>
              <input
                type="range" aria-label="JPEG quality"
                min={opts.image_compression.min} max={opts.image_compression.max}
                value={quality} onChange={(e) => setQuality(Number(e.target.value))}
              />
            </label>
          )}
          {caps.person_generation.length > 0 && (
            <label className="field mb-0!">
              <span className="text-xs font-semibold text-subtle">People</span>
              <select aria-label="Person generation" value={personGen} onChange={(e) => setPersonGen(e.target.value)}>
                <option value="">— model default —</option>
                {caps.person_generation.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </label>
          )}
          {caps.thinking_levels.length > 0 && (
            <label className="field mb-0!">
              <span className="text-xs font-semibold text-subtle">Thinking</span>
              <select aria-label="Thinking level" value={thinking} onChange={(e) => setThinking(e.target.value)}>
                <option value="">— default —</option>
                {caps.thinking_levels.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </label>
          )}
        </section>
      )}

      <button
        className="btn-primary mt-4 w-full text-[15px] shadow-card"
        style={{ minHeight: 52 }}
        onClick={() => generate(false)}
        disabled={!canGenerate}
      >
        {busy && <span className="spinner" aria-hidden />}
        {busy ? "Generating…" : "Generate Image"}
      </button>
      {files.length === 0 && (
        <p className="mt-2 text-xs text-subtle">Upload at least one reference image to generate.</p>
      )}

      {msg && (
        <p
          className={`mt-4 ${msg.kind === "error" ? "alert alert-error" : "alert alert-info"}`}
          role={msg.kind === "error" ? "alert" : "status"}
          aria-live="polite"
        >
          {msg.text}
        </p>
      )}

      {/* Review & iterate */}
      {active && (
        <section className="mt-5">
          <div className="flex items-center justify-between">
            <p className="section-label mt-0!">
              Review · <span className="tabular-nums">{activeIdx + 1} of {variations.length}</span>
            </p>
            <span className={`pill ${active.mode === "refine" ? "pill-done" : "pill-pending"}`}>
              {active.mode === "refine" ? "refined" : "fresh"}
            </span>
          </div>

          <button
            type="button"
            className="img-zoom mt-2 block w-full overflow-hidden rounded-md! border! border-line! p-0!"
            onClick={() => lightbox.show(`data:${active.mime};base64,${active.b64}`, "Generated mockup")}
            aria-label="Enlarge generated mockup"
          >
            <img src={`data:${active.mime};base64,${active.b64}`} alt="Generated mockup" className="w-full" />
          </button>

          {variations.length > 1 && (
            <div className="mt-2 flex gap-2 overflow-x-auto">
              {variations.map((v, i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() => setActiveIdx(i)}
                  aria-label={`View variation ${i + 1}`}
                  aria-current={i === activeIdx}
                  className={`h-16 w-16 shrink-0 overflow-hidden rounded-md border p-0 ${i === activeIdx ? "border-accent" : "border-line"}`}
                >
                  <img src={`data:${v.mime};base64,${v.b64}`} alt={`Variation ${i + 1}`} className="h-full w-full object-cover" />
                </button>
              ))}
            </div>
          )}

          <div className="field mt-3">
            <div className="flex items-center justify-between gap-3">
              <label htmlFor="qg-feedback" className="text-xs font-semibold text-subtle">
                Feedback (folds into the prompt on refine)
              </label>
              <RefineButton
                kind="image"
                instruction={feedback}
                onRefined={setFeedback}
                onError={(m) => setMsg({ kind: "error", text: m })}
              />
            </div>
            <textarea
              id="qg-feedback"
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              rows={3}
              placeholder="e.g. longer sleeves, warmer background"
            />
          </div>

          <div className="mt-3 flex flex-wrap gap-2">
            <button className="btn-primary" onClick={() => generate(true)} disabled={busy}>
              {busy && <span className="spinner" aria-hidden />} Refine
            </button>
            <button onClick={() => generate(false)} disabled={busy}>Try again</button>
            <button onClick={download} disabled={busy}>Download</button>
          </div>
        </section>
      )}

      {lightbox.node}
    </div>
  );
}
```

- [ ] **Step 2: Register the tab in `App.tsx`**

In `frontend/src/App.tsx`, add the import (after the `BackfillTab` import on line 7):

```tsx
import QuickGenerateTab from "./components/QuickGenerateTab";
```

Replace the `TABS` array (lines 99–103) with:

```tsx
const TABS = [
  { id: "products", label: "Products" },
  { id: "quickgen", label: "Quick Generate" },
  { id: "prompts", label: "Prompts" },
  { id: "backfill", label: "Backfill" },
] as const;
```

Replace the tabpanel render line (line 138) with:

```tsx
        {tab === "products" ? (
          <ProductsTab />
        ) : tab === "quickgen" ? (
          <QuickGenerateTab />
        ) : tab === "prompts" ? (
          <PromptsTab />
        ) : (
          <BackfillTab />
        )}
```

- [ ] **Step 3: Typecheck + build**

Run: `cd frontend && npm run build`
Expected: PASS (no type errors; bundle builds).

- [ ] **Step 4: Manual smoke (Playwright CLI)**

Start backend + frontend dev servers, then verify in a browser (use the Playwright CLI, `npx playwright`, per repo convention — NOT MCP tools):
- The "Quick Generate" tab appears and opens.
- Selecting a file + typing a prompt enables Generate.
- Switching model to `gemini-2.5-flash-image` removes `4K` from Image size; switching to `gemini-3.1-flash-image` shows the Thinking control and `512px`.
- Choosing JPEG reveals the quality slider.
- After a successful generate, the variation renders, Download saves a file with the correct extension, and Refine produces a second variation.

(If servers/keys aren't available in the execution environment, record this step as "verified by build + unit-tested backend" and leave the manual check for the reviewer.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/QuickGenerateTab.tsx frontend/src/App.tsx
git commit -m "feat(frontend): Quick Generate tab for ad-hoc image generation"
```

---

## Self-Review

**Spec coverage:**
- New Quick Generate tab → Task 5. ✅
- Multi-file upload (1–14) → Task 3 (cap) + Task 5 (UI). ✅
- Free-text prompt + refine on prompt and feedback → Task 5 (two `RefineButton`s). ✅
- Full Gemini option surface (aspect/size/mime/quality/person/thinking), per-model gating → Tasks 1–3 (backend) + Task 5 (UI). ✅
- 21:9 removal (shared) → Task 2. ✅
- Format preservation (PNG/JPEG) + correct download extension → Task 1 + Task 5. ✅
- No productid / no DB / no Drive / no publish → Task 3 (no `get_db`, no publish). ✅
- Capability map in `/options` → Task 2. ✅
- person_generation expose + surface errors (no Vertex gating) → Task 3 (validated against values only; API error surfaces via existing 502/400 mapping). ✅
- Tests: backend route + core + options; existing-test fixes for the aspect change → Tasks 1–3. ✅

**Placeholder scan:** No TBD/TODO; every code step has complete code; commands have expected output. ✅

**Type consistency:** `generate_mockup_bytes` returns `bytes` everywhere (route derives mime via PIL — consistent with the Global Constraint). `GenerateUploadPreview`/`GenUploadPreview` fields match (`status, detail, image_b64, mime_type`). `ImageCaps` shape identical in backend `IMAGE_CAPS` and frontend interface (`aspect_ratios, image_sizes, mime_types, person_generation, thinking_levels`). `generateImageUpload` field names match the route's `Form(...)` names. ✅

## Deferred (not in this plan)
Ad-hoc video generation — a later change makes `productid` optional on `POST /api/generate/video`, adds `image_b64`, and adds a video section to the Quick Generate tab that animates the active variation.
