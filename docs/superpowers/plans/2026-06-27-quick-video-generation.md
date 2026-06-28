# Quick Video Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a catalog-free Quick Video Generation tab exposing the full VEO 3.1 capability surface (text→video, image→video, first+last frame interpolation, reference images, video extension) with constraint-aware controls, prompt-refine assist, and an inline player + recent-clips history.

**Architecture:** Extend the existing in-memory VEO service + background-job machinery rather than adding new infrastructure. A new multipart endpoint `POST /api/generate/video/upload` mirrors `/image/upload` (catalog-free, no DB/Drive/Storage). The frontend adds a `QuickVideoTab` mirroring `QuickGenerateTab`, driven by a server `video_caps` map plus a client cross-field clamp. The server is stateless for extension — the client re-submits the in-session clip bytes it already holds.

**Tech Stack:** FastAPI, Pydantic, `google-genai` SDK (`types.GenerateVideosConfig`, `types.VideoGenerationReferenceImage`, `types.Video`), React 18 + Vite + TypeScript + Tailwind. Backend tests: pytest + FastAPI `TestClient`. Frontend has **no test framework** — verification is `npm run build` (typecheck) + manual smoke.

## Global Constraints

- Python `>=3.10,<3.11`. Run backend tests with `poetry run pytest`.
- VEO models allowed: `veo-3.1-generate-preview`, `veo-3.1-fast-generate-preview`, `veo-3.1-lite-generate-preview`.
- Cross-field rules (enforced server-side, status **400**): 1080p ⇒ duration 8; mode `reference`/`frames` ⇒ duration 8; mode `extend` ⇒ resolution 720p; mode must be in the chosen model's caps. Lite drops modes `reference` + `extend`.
- Catalog-free: the new endpoint writes nothing — no productid, no DB, no Drive, no Storage.
- Confirmed SDK field names: config `last_frame` (`types.Image`), `reference_images` (`list[types.VideoGenerationReferenceImage]` with `reference_type=types.VideoGenerationReferenceType.ASSET`), `person_generation`, `generate_audio`, `negative_prompt`; extension input passed as `generate_videos(video=types.Video(video_bytes=..., mime_type="video/mp4"))`.
- Match existing UI tokens (`field`, `btn-primary`, `pill`, `alert`, `section-label`, `border-line`, `border-accent`, `spinner`, `font-display`, `text-subtle`). No new fonts.

---

### Task 1: Extend `generate_video_bytes` for all VEO modes

**Files:**
- Modify: `mockup_generator/generation/video_service.py:40-92`
- Test: `tests/test_video_service.py`

**Interfaces:**
- Produces: `generate_video_bytes(image_bytes: bytes | None = None, prompt: str = "", *, model=None, aspect_ratio=None, resolution=None, duration=None, negative_prompt=None, person_generation: str | None = None, generate_audio: bool | None = None, last_frame_bytes: bytes | None = None, reference_image_bytes: list[bytes] | None = None, extend_video_bytes: bytes | None = None, poll_timeout=None, poll_interval=None) -> bytes`
- Consumes: `google.genai.types` (`Image`, `Video`, `GenerateVideosConfig`, `VideoGenerationReferenceImage`, `VideoGenerationReferenceType`).

- [ ] **Step 1: Update the fake client in the test file to accept the new kwargs**

In `tests/test_video_service.py`, replace the `generate_videos` method of `_FakeClient` (currently lines 55-60) so it accepts optional `image`/`video` and captures everything:

```python
    # models.generate_videos
    def generate_videos(self, *, model, prompt, config, image=None, video=None):
        self._captured["model"] = model
        self._captured["prompt"] = prompt
        self._captured["image"] = image
        self._captured["video"] = video
        self._captured["config"] = config
        return self._op
```

- [ ] **Step 2: Write failing tests for the new wiring**

Append to `tests/test_video_service.py`:

```python
def test_generate_video_bytes_wires_last_frame_and_reference(monkeypatch):
    captured = {}
    op = _FakeOperation(videos=[_FakeGenerated(_FakeVideo(b"V"))], flips_after=0)
    _patch_client(monkeypatch, _FakeClient(op, captured=captured))

    video_service.generate_video_bytes(
        b"start", "p", duration=8,
        last_frame_bytes=b"end",
        reference_image_bytes=[b"r1", b"r2"],
        person_generation="allow_adult",
        generate_audio=True,
    )
    cfg = captured["config"]
    assert cfg.last_frame is not None
    assert len(cfg.reference_images) == 2
    assert cfg.reference_images[0].reference_type == video_service.types.VideoGenerationReferenceType.ASSET
    assert cfg.person_generation == "allow_adult"
    assert cfg.generate_audio is True
    assert captured["image"] is not None
    assert captured["video"] is None


def test_generate_video_bytes_extension_uses_video_not_image(monkeypatch):
    captured = {}
    op = _FakeOperation(videos=[_FakeGenerated(_FakeVideo(b"V"))], flips_after=0)
    _patch_client(monkeypatch, _FakeClient(op, captured=captured))

    video_service.generate_video_bytes(
        None, "extend it", resolution="720p", extend_video_bytes=b"PRIORMP4",
    )
    assert captured["video"] is not None
    assert captured["image"] is None


def test_generate_video_bytes_text_to_video_no_media(monkeypatch):
    captured = {}
    op = _FakeOperation(videos=[_FakeGenerated(_FakeVideo(b"V"))], flips_after=0)
    _patch_client(monkeypatch, _FakeClient(op, captured=captured))

    video_service.generate_video_bytes(None, "a model walks", aspect_ratio="9:16")
    assert captured["image"] is None and captured["video"] is None
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `poetry run pytest tests/test_video_service.py -k "wires_last_frame or extension_uses_video or text_to_video_no_media" -v`
Expected: FAIL (e.g. `TypeError: generate_video_bytes() got an unexpected keyword argument 'last_frame_bytes'`).

- [ ] **Step 4: Implement the extended service**

Replace the body of `generate_video_bytes` in `mockup_generator/generation/video_service.py` (the function spanning lines 40-92). Keep the docstring/poll loop; change the signature and request-building block:

```python
def generate_video_bytes(
    image_bytes: bytes | None = None,
    prompt: str = "",
    *,
    model: str | None = None,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    duration: int | None = None,
    negative_prompt: str | None = None,
    person_generation: str | None = None,
    generate_audio: bool | None = None,
    last_frame_bytes: bytes | None = None,
    reference_image_bytes: list[bytes] | None = None,
    extend_video_bytes: bytes | None = None,
    poll_timeout: int | None = None,
    poll_interval: int | None = None,
) -> bytes:
    """Generate an mp4 with VEO under ``prompt`` → mp4 bytes.

    Mode is inferred from the inputs: ``extend_video_bytes`` extends a prior
    clip; otherwise ``image_bytes`` is the first frame (and ``last_frame_bytes``
    the last, for interpolation); ``reference_image_bytes`` supply consistency
    assets; with none of these it is text-to-video. Blocks (polling) until the
    job finishes, the timeout elapses, or the model returns nothing.
    """
    model_name = model or settings.veo_model
    timeout = poll_timeout if poll_timeout is not None else settings.veo_poll_timeout_sec
    interval = poll_interval if poll_interval is not None else settings.veo_poll_interval_sec

    cfg_kwargs: dict = dict(
        aspect_ratio=aspect_ratio or ASPECT_RATIO,
        resolution=resolution or RESOLUTION,
        duration_seconds=duration if duration is not None else DURATION_SEC,
        number_of_videos=1,
        negative_prompt=negative_prompt if negative_prompt is not None else DEFAULT_NEGATIVE,
    )
    if person_generation:
        cfg_kwargs["person_generation"] = person_generation
    if generate_audio is not None:
        cfg_kwargs["generate_audio"] = generate_audio
    if last_frame_bytes:
        cfg_kwargs["last_frame"] = types.Image(image_bytes=last_frame_bytes, mime_type="image/png")
    if reference_image_bytes:
        cfg_kwargs["reference_images"] = [
            types.VideoGenerationReferenceImage(
                image=types.Image(image_bytes=b, mime_type="image/png"),
                reference_type=types.VideoGenerationReferenceType.ASSET,
            )
            for b in reference_image_bytes
        ]

    call_kwargs: dict = dict(
        model=model_name, prompt=prompt, config=types.GenerateVideosConfig(**cfg_kwargs),
    )
    if extend_video_bytes:
        call_kwargs["video"] = types.Video(video_bytes=extend_video_bytes, mime_type="video/mp4")
    elif image_bytes:
        call_kwargs["image"] = types.Image(image_bytes=image_bytes, mime_type="image/png")

    client = get_genai_client()
    operation = client.models.generate_videos(**call_kwargs)

    start = time.monotonic()
    while not operation.done:
        if time.monotonic() - start > timeout:
            raise VideoTimeout(f"VEO job exceeded {timeout}s (op={operation.name})")
        time.sleep(interval)
        operation = client.operations.get(operation)

    result = getattr(operation, "response", None)
    videos = getattr(result, "generated_videos", None) if result else None
    if not videos:
        raise NoVideoReturned(f"VEO returned no video (op={operation.name}, error={operation.error})")

    generated = videos[0]
    client.files.download(file=generated.video)
    data = getattr(generated.video, "video_bytes", None)
    if not data:
        raise NoVideoReturned("VEO video had no bytes after download")
    return data
```

- [ ] **Step 5: Run the full video-service test file to verify pass (incl. the existing 5 tests)**

Run: `poetry run pytest tests/test_video_service.py -v`
Expected: PASS (all tests, including the pre-existing `returns_mp4`, `polls_until_done`, `uses_configured_model`, `raises_when_no_video`, `times_out`).

- [ ] **Step 6: Commit**

```bash
git add mockup_generator/generation/video_service.py tests/test_video_service.py
git commit -m "feat(video-service): support last-frame, reference images, extension, audio"
```

---

### Task 2: Video capability map, cross-field validator, options endpoint

**Files:**
- Modify: `backend/routers/generate.py:93-101` (video constants), `:162-184` (`generation_options`), `:363-379` (refactor `/video` to use the validator)
- Test: `tests/test_generate_api.py`

**Interfaces:**
- Produces: module-level `VIDEO_CAPS: dict[str, dict]`, `_video_caps_for(model: str | None) -> dict`, and `_validate_video_params(*, model: str | None, mode: str, aspect_ratio: str | None, resolution: str | None, duration: int | None) -> None` (raises `HTTPException(400)`); `/options` JSON gains `"video_caps"`.
- Consumes: existing `ALLOWED_VEO_MODELS`, `ALLOWED_VIDEO_RESOLUTIONS`, `ALLOWED_VIDEO_ASPECTS`, `ALLOWED_VIDEO_DURATIONS`, `_VIDEO_DEFAULTS`, `settings.veo_model`.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_generate_api.py`:

```python
def test_options_includes_video_caps(client):
    body = client.get("/api/generate/options").json()
    caps = body["video_caps"]
    full = caps["veo-3.1-generate-preview"]
    assert set(full["modes"]) == {"text", "image", "frames", "reference", "extend"}
    lite = caps["veo-3.1-lite-generate-preview"]
    assert "reference" not in lite["modes"] and "extend" not in lite["modes"]
    assert full["resolutions"] == ["720p", "1080p"]
    assert full["durations"] == [4, 6, 8]


def test_validate_video_params_rules():
    from fastapi import HTTPException
    import pytest as _pytest
    # reference mode requires 8s
    with _pytest.raises(HTTPException) as e1:
        gen._validate_video_params(model="veo-3.1-generate-preview", mode="reference",
                                   aspect_ratio="9:16", resolution="720p", duration=4)
    assert e1.value.status_code == 400
    # extend requires 720p
    with _pytest.raises(HTTPException) as e2:
        gen._validate_video_params(model="veo-3.1-generate-preview", mode="extend",
                                   aspect_ratio="9:16", resolution="1080p", duration=8)
    assert e2.value.status_code == 400
    # lite does not support reference mode
    with _pytest.raises(HTTPException) as e3:
        gen._validate_video_params(model="veo-3.1-lite-generate-preview", mode="reference",
                                   aspect_ratio="9:16", resolution="720p", duration=8)
    assert e3.value.status_code == 400
    # 1080p requires 8s
    with _pytest.raises(HTTPException) as e4:
        gen._validate_video_params(model="veo-3.1-generate-preview", mode="image",
                                   aspect_ratio="9:16", resolution="1080p", duration=6)
    assert e4.value.status_code == 400
    # valid combo: no raise
    gen._validate_video_params(model="veo-3.1-generate-preview", mode="frames",
                               aspect_ratio="16:9", resolution="720p", duration=8)
```

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/test_generate_api.py -k "video_caps or validate_video_params" -v`
Expected: FAIL (`AttributeError: module ... has no attribute '_validate_video_params'`; `KeyError: 'video_caps'`).

- [ ] **Step 3: Add the caps map + validator**

In `backend/routers/generate.py`, after the existing `_VIDEO_DEFAULTS` block (line 101), add:

```python
_VIDEO_MODES_FULL = ["text", "image", "frames", "reference", "extend"]
_VIDEO_MODES_LITE = ["text", "image", "frames"]
_VIDEO_PERSON_VALUES = ["allow_all", "allow_adult"]

VIDEO_CAPS = {
    "veo-3.1-generate-preview": {
        "modes": _VIDEO_MODES_FULL, "aspect_ratios": ALLOWED_VIDEO_ASPECTS,
        "resolutions": ALLOWED_VIDEO_RESOLUTIONS, "durations": ALLOWED_VIDEO_DURATIONS,
        "person_generation": _VIDEO_PERSON_VALUES,
    },
    "veo-3.1-fast-generate-preview": {
        "modes": _VIDEO_MODES_FULL, "aspect_ratios": ALLOWED_VIDEO_ASPECTS,
        "resolutions": ALLOWED_VIDEO_RESOLUTIONS, "durations": ALLOWED_VIDEO_DURATIONS,
        "person_generation": _VIDEO_PERSON_VALUES,
    },
    "veo-3.1-lite-generate-preview": {
        "modes": _VIDEO_MODES_LITE, "aspect_ratios": ALLOWED_VIDEO_ASPECTS,
        "resolutions": ALLOWED_VIDEO_RESOLUTIONS, "durations": ALLOWED_VIDEO_DURATIONS,
        "person_generation": _VIDEO_PERSON_VALUES,
    },
}
_DEFAULT_VIDEO_CAPS_MODEL = "veo-3.1-generate-preview"


def _video_caps_for(model: str | None) -> dict:
    return VIDEO_CAPS.get(model or settings.veo_model, VIDEO_CAPS[_DEFAULT_VIDEO_CAPS_MODEL])


def _validate_video_params(*, model: str | None, mode: str,
                           aspect_ratio: str | None, resolution: str | None,
                           duration: int | None) -> None:
    """Enforce model allow-list, per-model mode support, and VEO cross-field
    constraints. Raises HTTPException(400) on any violation."""
    if model is not None and model not in ALLOWED_VEO_MODELS:
        raise HTTPException(status_code=400, detail=f"Unsupported video model: {model}")
    caps = _video_caps_for(model)
    if mode not in caps["modes"]:
        raise HTTPException(status_code=400,
                            detail=f"Mode '{mode}' is not supported by {model or settings.veo_model}.")
    if aspect_ratio and aspect_ratio not in caps["aspect_ratios"]:
        raise HTTPException(status_code=400, detail=f"Unsupported video aspect ratio: {aspect_ratio}")
    if resolution and resolution not in caps["resolutions"]:
        raise HTTPException(status_code=400, detail=f"Unsupported video resolution: {resolution}")
    if duration is not None and duration not in caps["durations"]:
        raise HTTPException(status_code=400, detail=f"Unsupported video duration: {duration}")

    eff_resolution = resolution or _VIDEO_DEFAULTS["resolution"]
    eff_duration = duration if duration is not None else _VIDEO_DEFAULTS["duration"]
    if eff_resolution == "1080p" and eff_duration != 8:
        raise HTTPException(status_code=400, detail="1080p video requires an 8-second duration.")
    if mode in ("reference", "frames") and eff_duration != 8:
        raise HTTPException(status_code=400,
                            detail=f"{mode.capitalize()} mode requires an 8-second duration.")
    if mode == "extend" and eff_resolution != "720p":
        raise HTTPException(status_code=400, detail="Video extension is 720p only.")
```

- [ ] **Step 4: Surface `video_caps` in `/options`**

In `generation_options` (the return dict at lines 172-184), add one line after `"video_defaults": ...`:

```python
        "video_caps": VIDEO_CAPS,
```

- [ ] **Step 5: Refactor `/video` to reuse the validator (DRY)**

In `generate_video` replace the four inline checks at lines 369-379 (the model / resolution / aspect / duration `if` blocks and the 1080p-requires-8s block) with a single call (the existing `/video` animates a published still → mode `"image"`):

```python
    _validate_video_params(model=req.model, mode="image", aspect_ratio=req.aspect_ratio,
                           resolution=req.resolution, duration=req.duration)
```

- [ ] **Step 6: Run tests to verify pass (new + existing video/options tests)**

Run: `poetry run pytest tests/test_generate_api.py -v`
Expected: PASS — including the pre-existing `test_generation_options_lists_choices_and_defaults`, `test_generate_video_rejects_bad_model`, and `test_generate_video_1080p_requires_8s` (now served by the validator).

- [ ] **Step 7: Commit**

```bash
git add backend/routers/generate.py tests/test_generate_api.py
git commit -m "feat(generate-api): video_caps map + cross-field validator; reuse in /video"
```

---

### Task 3: `POST /api/generate/video/upload` endpoint

**Files:**
- Modify: `backend/routers/generate.py` (add a video upload constant, a job runner, and the endpoint)
- Test: `tests/test_generate_api.py`

**Interfaces:**
- Consumes: `_validate_video_params` (Task 2), `video_service.generate_video_bytes` (Task 1), existing `_VideoJob`, `_video_jobs`, `_video_jobs_lock`, `_reap_video_jobs`, `_spawn`, `_MAX_UPLOAD_BYTES`.
- Produces: `POST /api/generate/video/upload` → `VideoJobResponse`; `_run_video_upload_job(job_id, prompt, model, aspect_ratio, resolution, duration, negative_prompt, person_generation, generate_audio, start_bytes, last_bytes, ref_bytes, ext_bytes)`.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_generate_api.py`:

```python
def _wire_video_upload(monkeypatch, *, calls):
    monkeypatch.setattr(gen, "_spawn", lambda fn, *a: fn(*a))

    def fake_video(image_bytes=None, prompt="", **kw):
        calls["video"] = {"image_bytes": image_bytes, "prompt": prompt, "kw": kw}
        return b"MP4BYTES"

    monkeypatch.setattr(gen.video_service, "generate_video_bytes", fake_video)


def _png_upload(name):
    return (name, (f"{name}.png", _png_bytes(), "image/png"))


def test_video_upload_text_mode_no_files(client, monkeypatch):
    calls = {}
    _wire_video_upload(monkeypatch, calls=calls)
    r = client.post("/api/generate/video/upload",
                    data={"mode": "text", "prompt": "a model walks the ramp"})
    assert r.status_code == 200
    assert r.json()["status"] in ("pending", "running", "done")
    assert calls["video"]["image_bytes"] is None
    assert calls["video"]["prompt"] == "a model walks the ramp"


def test_video_upload_image_mode_wires_start_frame(client, monkeypatch):
    calls = {}
    _wire_video_upload(monkeypatch, calls=calls)
    r = client.post("/api/generate/video/upload",
                    data={"mode": "image", "prompt": "slow pan"},
                    files=[_png_upload("start_frame")])
    assert r.status_code == 200
    assert calls["video"]["image_bytes"] is not None


def test_video_upload_frames_mode_wires_last_frame(client, monkeypatch):
    calls = {}
    _wire_video_upload(monkeypatch, calls=calls)
    r = client.post("/api/generate/video/upload",
                    data={"mode": "frames", "prompt": "reveal", "duration": "8"},
                    files=[_png_upload("start_frame"), _png_upload("last_frame")])
    assert r.status_code == 200
    assert calls["video"]["kw"]["last_frame_bytes"] is not None


def test_video_upload_reference_mode_wires_refs(client, monkeypatch):
    calls = {}
    _wire_video_upload(monkeypatch, calls=calls)
    files = [("reference_images", (f"r{i}.png", _png_bytes(), "image/png")) for i in range(3)]
    r = client.post("/api/generate/video/upload",
                    data={"mode": "reference", "prompt": "consistency", "duration": "8"},
                    files=files)
    assert r.status_code == 200
    assert len(calls["video"]["kw"]["reference_image_bytes"]) == 3


def test_video_upload_extend_mode_wires_video(client, monkeypatch):
    calls = {}
    _wire_video_upload(monkeypatch, calls=calls)
    r = client.post("/api/generate/video/upload",
                    data={"mode": "extend", "prompt": "more", "resolution": "720p"},
                    files=[("extend_video", ("clip.mp4", b"PRIORMP4", "video/mp4"))])
    assert r.status_code == 200
    assert calls["video"]["kw"]["extend_video_bytes"] == b"PRIORMP4"


def test_video_upload_image_mode_requires_start_frame_400(client, monkeypatch):
    _wire_video_upload(monkeypatch, calls={})
    r = client.post("/api/generate/video/upload", data={"mode": "image", "prompt": "p"})
    assert r.status_code == 400


def test_video_upload_extend_requires_video_400(client, monkeypatch):
    _wire_video_upload(monkeypatch, calls={})
    r = client.post("/api/generate/video/upload",
                    data={"mode": "extend", "prompt": "p", "resolution": "720p"})
    assert r.status_code == 400


def test_video_upload_rejects_reference_on_lite_400(client, monkeypatch):
    _wire_video_upload(monkeypatch, calls={})
    files = [("reference_images", ("r.png", _png_bytes(), "image/png"))]
    r = client.post("/api/generate/video/upload",
                    data={"mode": "reference", "prompt": "p", "duration": "8",
                          "model": "veo-3.1-lite-generate-preview"},
                    files=files)
    assert r.status_code == 400


def test_video_upload_then_streams_mp4(client, monkeypatch):
    calls = {}
    _wire_video_upload(monkeypatch, calls=calls)
    started = client.post("/api/generate/video/upload",
                          data={"mode": "text", "prompt": "p"})
    job_id = started.json()["job_id"]
    r = client.get(f"/api/generate/video/{job_id}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/mp4"
    assert r.content == b"MP4BYTES"
```

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/test_generate_api.py -k video_upload -v`
Expected: FAIL with 404 (route not yet defined) on the happy-path tests.

- [ ] **Step 3: Add the video-upload size cap constant**

In `backend/routers/generate.py`, just below `_MAX_UPLOAD_BYTES = 25 * 1024 * 1024` (line 53), add:

```python
_MAX_VIDEO_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB — an extension source clip
```

- [ ] **Step 4: Add the job runner**

In `backend/routers/generate.py`, after `_run_video_job` (ends line 159), add:

```python
def _run_video_upload_job(
    job_id, prompt, model, aspect_ratio, resolution, duration,
    negative_prompt, person_generation, generate_audio,
    start_bytes, last_bytes, ref_bytes, ext_bytes,
) -> None:
    _set_job(job_id, status="running")
    try:
        mp4 = video_service.generate_video_bytes(
            start_bytes, prompt, model=model, aspect_ratio=aspect_ratio,
            resolution=resolution, duration=duration, negative_prompt=negative_prompt,
            person_generation=person_generation, generate_audio=generate_audio,
            last_frame_bytes=last_bytes, reference_image_bytes=ref_bytes,
            extend_video_bytes=ext_bytes,
        )
        _set_job(job_id, status="done", data=mp4)
    except video_service.VideoTimeout:
        _set_job(job_id, status="error", detail="Video generation timed out. Try again.")
    except video_service.NoVideoReturned:
        _set_job(job_id, status="error", detail="The model returned no video. Try again.")
    except Exception as exc:  # noqa: BLE001 - surface any failure to the poller
        _set_job(job_id, status="error", detail=f"Video generation failed: {exc}")
```

- [ ] **Step 5: Add the endpoint**

In `backend/routers/generate.py`, after `generate_video_upload`'s sibling `generate_video` route (after line 410, before the `@router.get("/video/{job_id}")` route), add:

```python
@router.post("/video/upload", response_model=VideoJobResponse)
async def generate_video_upload(
    mode: str = Form(...),
    prompt: str = Form(...),
    model: str | None = Form(None),
    aspect_ratio: str | None = Form(None),
    resolution: str | None = Form(None),
    duration: int | None = Form(None),
    negative_prompt: str | None = Form(None),
    person_generation: str | None = Form(None),
    generate_audio: bool | None = Form(None),
    start_frame: UploadFile | None = File(None),
    last_frame: UploadFile | None = File(None),
    reference_images: list[UploadFile] = File(default=[]),
    extend_video: UploadFile | None = File(None),
    user: CurrentUser = Depends(get_current_user),
):
    """Ad-hoc, catalog-free VEO render. Mode-specific uploads + prompt → enqueued
    job; poll ``/video/{job_id}`` for the mp4. Writes nothing — no productid, no
    DB, no Storage. Extension re-uses the in-session clip the client re-submits."""
    _validate_video_params(model=model, mode=mode, aspect_ratio=aspect_ratio,
                           resolution=resolution, duration=duration)

    async def _read(f: UploadFile, *, cap: int) -> bytes:
        raw = await f.read()
        if len(raw) > cap:
            raise HTTPException(status_code=413, detail="An uploaded file is too large.")
        return raw

    start_bytes = await _read(start_frame, cap=_MAX_UPLOAD_BYTES) if start_frame else None
    last_bytes = await _read(last_frame, cap=_MAX_UPLOAD_BYTES) if last_frame else None
    ref_bytes = [await _read(f, cap=_MAX_UPLOAD_BYTES) for f in reference_images[:3]] or None
    ext_bytes = await _read(extend_video, cap=_MAX_VIDEO_UPLOAD_BYTES) if extend_video else None

    if mode == "image" and not start_bytes:
        raise HTTPException(status_code=400, detail="Image-to-video needs a start frame.")
    if mode == "frames" and (not start_bytes or not last_bytes):
        raise HTTPException(status_code=400, detail="Frames mode needs a start and an end frame.")
    if mode == "reference" and not ref_bytes:
        raise HTTPException(status_code=400, detail="Reference mode needs at least one reference image.")
    if mode == "extend" and not ext_bytes:
        raise HTTPException(status_code=400, detail="Extend mode needs a source clip.")

    _reap_video_jobs()
    job_id = uuid.uuid4().hex
    with _video_jobs_lock:
        _video_jobs[job_id] = _VideoJob(status="pending", filename="quick_video.mp4")
    _spawn(_run_video_upload_job, job_id, prompt, model, aspect_ratio, resolution, duration,
           negative_prompt, person_generation, generate_audio,
           start_bytes, last_bytes, ref_bytes, ext_bytes)
    return VideoJobResponse(job_id=job_id, status="pending")
```

- [ ] **Step 6: Run tests to verify pass**

Run: `poetry run pytest tests/test_generate_api.py -k video_upload -v`
Expected: PASS (all 9 video_upload tests).

- [ ] **Step 7: Run the whole backend suite to confirm nothing regressed**

Run: `poetry run pytest -q`
Expected: PASS (full suite).

- [ ] **Step 8: Commit**

```bash
git add backend/routers/generate.py tests/test_generate_api.py
git commit -m "feat(generate-api): catalog-free /video/upload endpoint with mode-aware inputs"
```

---

### Task 4: Tune the video refine prompt for VEO structure

**Files:**
- Modify: `mockup_generator/prompts/refine.py:69-89` (`_video_meta`)
- Test: `tests/test_refine.py`

**Interfaces:**
- Produces: `_video_meta(instruction: str, category_name: str | None) -> str` (unchanged signature; richer content).

- [ ] **Step 1: Write a failing test**

Append to `tests/test_refine.py`:

```python
def test_video_meta_covers_veo_structure_and_is_one_paragraph():
    from mockup_generator.prompts.refine import _video_meta
    meta = _video_meta("model twirls in a red lehenga", "Lehenga")
    low = meta.lower()
    # VEO shot grammar + audio cue + single-paragraph instruction
    assert "camera" in low
    assert "audio" in low or "ambient" in low
    assert "one paragraph" in low or "single paragraph" in low
    # preserves the user's instruction and the category grounding
    assert "model twirls in a red lehenga" in meta
    assert "Lehenga" in meta
```

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/test_refine.py -k video_meta_covers -v`
Expected: FAIL (current `_video_meta` has no "audio"/"one paragraph" text).

- [ ] **Step 3: Update `_video_meta`**

Replace `_video_meta` (lines 69-89) in `mockup_generator/prompts/refine.py`:

```python
def _video_meta(instruction: str, category_name: str | None) -> str:
    return (
        "Rewrite the user's instruction into ONE short product video-generation "
        "prompt for Google VEO. Expand a thin description into a vivid, "
        "shot-described clip while keeping the garment pixel-faithful to the "
        "reference.\n"
        + _category_line(category_name)
        + "Write it as a single flowing paragraph (NOT a list), and cover, in a "
        "natural order: subject and wardrobe; the action/motion (fabric flow and "
        "drape, a turn, twirl, or step); clear camera and shot language (slow "
        "push-in, gentle dolly, orbit, or pan) with pacing for a few-second clip "
        "and a loop-friendly resolve; lighting and mood; and a brief ambient "
        "audio cue (the soft rustle of fabric, gentle room tone — VEO renders "
        "native audio).\n"
        "Hard rules:\n"
        "- Keep the garment pixel-faithful: DO NOT invent motifs, colors, or "
        "change the silhouette.\n"
        "- Preserve EVERY explicit instruction the user gave, verbatim in intent. "
        "Drop nothing.\n"
        "- Keep it to one tight paragraph; output the prompt text only.\n\n"
        f"User instruction:\n{instruction.strip()}"
    )
```

- [ ] **Step 4: Run to verify pass + the rest of the refine suite**

Run: `poetry run pytest tests/test_refine.py -v`
Expected: PASS (new test + all pre-existing refine tests).

- [ ] **Step 5: Commit**

```bash
git add mockup_generator/prompts/refine.py tests/test_refine.py
git commit -m "feat(refine): VEO-structured single-paragraph video prompt with audio cue"
```

---

### Task 5: Frontend API client — video caps type + `startVideoUpload`

**Files:**
- Modify: `frontend/src/api.ts:255-275` (types), and add a function after `startVideo` (after line 357)

**Interfaces:**
- Produces: `interface VideoCaps`; `GenOptions.video_caps: Record<string, VideoCaps>`; `startVideoUpload(fields, files): Promise<VideoJob>`.
- Consumes: existing `apiUpload`, `VideoJob`, `getVideoResult`.

- [ ] **Step 1: Add the `VideoCaps` interface and extend `GenOptions`**

In `frontend/src/api.ts`, immediately before `export interface GenOptions {` (line 263), add:

```typescript
export interface VideoCaps {
  modes: string[];
  aspect_ratios: string[];
  resolutions: string[];
  durations: number[];
  person_generation: string[];
}
```

Then add one field inside `GenOptions` (after `video_defaults: ...;`, line 274):

```typescript
  video_caps: Record<string, VideoCaps>;
```

- [ ] **Step 2: Add `startVideoUpload`**

In `frontend/src/api.ts`, after the `startVideo` arrow function (line 357), add:

```typescript
/** Ad-hoc, catalog-free VEO render from uploaded media + prompt. Returns a
 *  job_id to poll with getVideoResult(). */
export function startVideoUpload(
  fields: {
    mode: string;
    prompt: string;
    model?: string;
    aspect_ratio?: string;
    resolution?: string;
    duration?: number;
    negative_prompt?: string;
    person_generation?: string;
    generate_audio?: boolean;
  },
  files: {
    startFrame?: File;
    lastFrame?: File;
    referenceImages?: File[];
    extendVideo?: Blob;
  },
): Promise<VideoJob> {
  const fd = new FormData();
  fd.append("mode", fields.mode);
  fd.append("prompt", fields.prompt);
  if (fields.model) fd.append("model", fields.model);
  if (fields.aspect_ratio) fd.append("aspect_ratio", fields.aspect_ratio);
  if (fields.resolution) fd.append("resolution", fields.resolution);
  if (fields.duration != null) fd.append("duration", String(fields.duration));
  if (fields.negative_prompt) fd.append("negative_prompt", fields.negative_prompt);
  if (fields.person_generation) fd.append("person_generation", fields.person_generation);
  if (fields.generate_audio != null) fd.append("generate_audio", String(fields.generate_audio));
  if (files.startFrame) fd.append("start_frame", files.startFrame);
  if (files.lastFrame) fd.append("last_frame", files.lastFrame);
  (files.referenceImages ?? []).forEach((f) => fd.append("reference_images", f));
  if (files.extendVideo) fd.append("extend_video", files.extendVideo, "clip.mp4");
  return apiUpload<VideoJob>("/api/generate/video/upload", fd);
}
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npm run build`
Expected: PASS (no TypeScript errors).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api.ts
git commit -m "feat(frontend-api): VideoCaps type + startVideoUpload"
```

---

### Task 6: `QuickVideoTab` component

**Files:**
- Create: `frontend/src/components/QuickVideoTab.tsx`

**Interfaces:**
- Consumes: `getGenerationOptions`, `startVideoUpload`, `getVideoResult`, `GenOptions`, `VideoCaps`, `VideoJob` (from `../api`); `RefineButton` (from `./RefineButton`).
- Produces: `export default function QuickVideoTab()`.

- [ ] **Step 1: Create the component**

Create `frontend/src/components/QuickVideoTab.tsx` with the full content below. (No test framework — verification is the build in Step 2 + manual smoke in Task 7.)

```tsx
import { useEffect, useMemo, useRef, useState } from "react";
import {
  getGenerationOptions, startVideoUpload, getVideoResult,
  type GenOptions, type VideoCaps, type VideoJob,
} from "../api";
import RefineButton from "./RefineButton";

type Mode = "text" | "image" | "frames" | "reference" | "extend";

const MODE_LABELS: Record<Mode, string> = {
  text: "Text → Video",
  image: "Image → Video",
  frames: "First + Last",
  reference: "Reference",
  extend: "Extend +7s",
};
const MODE_HINT: Record<Mode, string> = {
  text: "Generate from the prompt alone — no upload needed.",
  image: "Animate a single start frame.",
  frames: "Interpolate motion between a start and an end frame.",
  reference: "Up to 3 reference images keep the garment and model consistent.",
  extend: "Extend the active clip by 7 seconds.",
};
const MAX_REFS = 3;
const POLL_MS = 5000;

type Clip = { url: string; promptUsed: string; mode: Mode };

export default function QuickVideoTab() {
  const [opts, setOpts] = useState<GenOptions | null>(null);
  const [mode, setMode] = useState<Mode>("image");
  const [model, setModel] = useState("");
  const [aspect, setAspect] = useState("");
  const [resolution, setResolution] = useState("");
  const [duration, setDuration] = useState<number>(4);
  const [personGen, setPersonGen] = useState("");
  const [negative, setNegative] = useState("");
  const [prompt, setPrompt] = useState("");

  const [startFrame, setStartFrame] = useState<File | null>(null);
  const [lastFrame, setLastFrame] = useState<File | null>(null);
  const [refImages, setRefImages] = useState<File[]>([]);

  const [clips, setClips] = useState<Clip[]>([]);
  const [activeIdx, setActiveIdx] = useState(0);
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [msg, setMsg] = useState<{ kind: "info" | "error"; text: string } | null>(null);

  const active = clips[activeIdx] ?? null;
  const caps: VideoCaps | null = useMemo(
    () => (opts && model ? opts.video_caps[model] ?? null : null),
    [opts, model],
  );

  // Object-URL previews for image uploads; revoke on change/unmount.
  const startUrl = useMemo(() => (startFrame ? URL.createObjectURL(startFrame) : ""), [startFrame]);
  const lastUrl = useMemo(() => (lastFrame ? URL.createObjectURL(lastFrame) : ""), [lastFrame]);
  const refUrls = useMemo(() => refImages.map((f) => URL.createObjectURL(f)), [refImages]);
  useEffect(() => () => { if (startUrl) URL.revokeObjectURL(startUrl); }, [startUrl]);
  useEffect(() => () => { if (lastUrl) URL.revokeObjectURL(lastUrl); }, [lastUrl]);
  useEffect(() => () => refUrls.forEach((u) => URL.revokeObjectURL(u)), [refUrls]);
  // Revoke clip URLs on unmount.
  useEffect(() => () => clips.forEach((c) => URL.revokeObjectURL(c.url)), [clips]);

  useEffect(() => {
    getGenerationOptions().then((o) => {
      setOpts(o);
      const d = o.video_defaults;
      setModel(d.model);
      setAspect(d.aspect_ratio);
      setResolution(d.resolution);
      setDuration(d.duration);
    }).catch((e: Error) => setMsg({ kind: "error", text: e.message }));
  }, []);

  // If the chosen model can't do the current mode, fall back to "image".
  useEffect(() => {
    if (caps && !caps.modes.includes(mode)) setMode("image");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caps]);

  // Clamp aspect/resolution/duration to caps; apply VEO cross-field rules.
  useEffect(() => {
    if (!caps) return;
    if (!caps.aspect_ratios.includes(aspect)) setAspect(caps.aspect_ratios[0]);
    let res = caps.resolutions.includes(resolution) ? resolution : caps.resolutions[0];
    if (mode === "extend") res = "720p";                       // extension is 720p only
    if (res !== resolution) setResolution(res);
    const needs8 = mode === "reference" || mode === "frames" || res === "1080p";
    if (needs8 && duration !== 8) setDuration(8);
    else if (!caps.durations.includes(duration)) setDuration(caps.durations[0]);
    if (personGen && !caps.person_generation.includes(personGen)) setPersonGen("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caps, mode, resolution]);

  const durationLocked = mode === "reference" || mode === "frames" || resolution === "1080p";
  const resolutionLocked = mode === "extend";

  const composePrompt = () =>
    feedback.trim() ? `${prompt}\n\nRevision note: ${feedback.trim()}` : prompt;

  const inputsReady = () => {
    if (mode === "image") return !!startFrame;
    if (mode === "frames") return !!startFrame && !!lastFrame;
    if (mode === "reference") return refImages.length > 0;
    if (mode === "extend") return !!active;
    return true; // text
  };
  const canGenerate = prompt.trim().length > 0 && inputsReady() && !busy;

  const poll = async (jobId: string): Promise<Blob> =>
    new Promise<Blob>((resolve, reject) => {
      const tick = async () => {
        try {
          const r = await getVideoResult(jobId);
          if (r instanceof Blob) return resolve(r);
          if ((r as VideoJob).status === "error")
            return reject(new Error((r as VideoJob).detail || "Video generation failed."));
          setTimeout(tick, POLL_MS);
        } catch (e) {
          reject(e as Error);
        }
      };
      tick();
    });

  const generate = async () => {
    setBusy(true);
    setMsg(null);
    setElapsed(0);
    const timer = setInterval(() => setElapsed((s) => s + 1), 1000);
    const promptUsed = composePrompt();
    try {
      let extendVideo: Blob | undefined;
      if (mode === "extend") {
        if (!active) throw new Error("Generate a clip first, then extend it.");
        extendVideo = await (await fetch(active.url)).blob();
      }
      const { job_id } = await startVideoUpload(
        {
          mode, prompt: promptUsed, model: model || undefined,
          aspect_ratio: aspect || undefined, resolution: resolution || undefined,
          duration, negative_prompt: negative || undefined,
          person_generation: personGen || undefined,
        },
        {
          startFrame: mode === "image" || mode === "frames" ? startFrame ?? undefined : undefined,
          lastFrame: mode === "frames" ? lastFrame ?? undefined : undefined,
          referenceImages: mode === "reference" ? refImages : undefined,
          extendVideo,
        },
      );
      const blob = await poll(job_id);
      const url = URL.createObjectURL(blob);
      setClips((prev) => {
        const next = [...prev, { url, promptUsed, mode }];
        setActiveIdx(next.length - 1);
        return next;
      });
      setFeedback("");
      setMsg({ kind: "info", text: "Video ready." });
    } catch (e) {
      setMsg({ kind: "error", text: (e as Error).message.replace(/^\d+:\s*/, "") });
    } finally {
      clearInterval(timer);
      setBusy(false);
    }
  };

  const download = () => {
    if (!active) return;
    const a = document.createElement("a");
    a.href = active.url;
    a.download = `quick_video_${aspect.replace(":", "x")}.mp4`;
    a.click();
  };

  const startExtend = () => { setMode("extend"); setFeedback(""); };

  return (
    <div className="stack">
      <section>
        <h2 className="font-display tracking-tight">Quick Video</h2>
        <p className="text-subtle text-sm">
          Generate a short garment video with VEO — nothing is saved to the catalog.
        </p>
      </section>

      {/* Mode selector */}
      <section className="mt-4">
        <p className="section-label mt-0!">Mode</p>
        <div className="flex flex-wrap gap-2" role="group" aria-label="Generation mode">
          {(Object.keys(MODE_LABELS) as Mode[]).map((m) => {
            const supported = !caps || caps.modes.includes(m);
            const isExtend = m === "extend";
            const disabled = !supported || (isExtend && !active);
            const reason = !supported
              ? "Not available on this model"
              : isExtend && !active
                ? "Generate a clip first"
                : undefined;
            return (
              <button
                key={m}
                type="button"
                className={`pill ${mode === m ? "pill-done" : "pill-pending"}`}
                aria-pressed={mode === m}
                disabled={disabled}
                title={reason}
                onClick={() => setMode(m)}
              >
                {MODE_LABELS[m]}
              </button>
            );
          })}
        </div>
        <p className="mt-2 text-xs text-subtle">{MODE_HINT[mode]}</p>
      </section>

      {/* Mode-specific uploads */}
      {(mode === "image" || mode === "frames") && (
        <section className="mt-4">
          <p className="section-label mt-0!">{mode === "frames" ? "Start frame" : "Source frame"}</p>
          <input
            type="file" accept="image/*" aria-label="Start frame"
            onChange={(e) => setStartFrame(e.target.files?.[0] ?? null)}
          />
          {startUrl && (
            <img src={startUrl} alt="Start frame preview"
                 className="mt-2 h-24 w-24 rounded-md border border-line object-cover" />
          )}
        </section>
      )}
      {mode === "frames" && (
        <section className="mt-4">
          <p className="section-label mt-0!">End frame</p>
          <input
            type="file" accept="image/*" aria-label="End frame"
            onChange={(e) => setLastFrame(e.target.files?.[0] ?? null)}
          />
          {lastUrl && (
            <img src={lastUrl} alt="End frame preview"
                 className="mt-2 h-24 w-24 rounded-md border border-line object-cover" />
          )}
        </section>
      )}
      {mode === "reference" && (
        <section className="mt-4">
          <p className="section-label mt-0!">Reference images · up to {MAX_REFS}</p>
          <input
            type="file" accept="image/*" multiple aria-label="Reference images"
            onChange={(e) => {
              const picked = Array.from(e.target.files ?? []);
              setRefImages((prev) => [...prev, ...picked].slice(0, MAX_REFS));
              e.target.value = "";
            }}
          />
          {refUrls.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-2">
              {refUrls.map((src, i) => (
                <div key={i} className="relative h-20 w-20 overflow-hidden rounded-md border border-line">
                  <img src={src} alt={`Reference ${i + 1}`} className="h-full w-full object-cover" />
                  <button
                    type="button"
                    onClick={() => setRefImages((prev) => prev.filter((_, idx) => idx !== i))}
                    aria-label={`Remove reference ${i + 1}`}
                    className="absolute right-0.5 top-0.5 rounded-full bg-black/60 px-1.5 text-xs text-white"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          )}
        </section>
      )}
      {mode === "extend" && (
        <section className="mt-4">
          <p className="section-label mt-0!">Source clip</p>
          <p className="text-xs text-subtle">
            {active ? "Extending the active clip below by 7 seconds." : "Generate a clip first to extend it."}
          </p>
        </section>
      )}

      {/* Prompt */}
      <section className="mt-5">
        <div className="field">
          <div className="flex items-center justify-between gap-3">
            <label htmlFor="qv-prompt" className="text-xs font-semibold text-subtle">Prompt</label>
            <RefineButton
              kind="video"
              instruction={prompt}
              onRefined={setPrompt}
              onError={(m) => setMsg({ kind: "error", text: m })}
            />
          </div>
          <textarea id="qv-prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={5} />
        </div>
      </section>

      {/* Options (model-gated) */}
      {opts && caps && (
        <section className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <label className="field mb-0!">
            <span className="text-xs font-semibold text-subtle">Model</span>
            <select aria-label="Model" value={model} onChange={(e) => setModel(e.target.value)}>
              {opts.video_models.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
          <label className="field mb-0!">
            <span className="text-xs font-semibold text-subtle">Aspect ratio</span>
            <select aria-label="Aspect ratio" value={aspect} onChange={(e) => setAspect(e.target.value)}>
              {caps.aspect_ratios.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
          </label>
          <label className="field mb-0!">
            <span className="text-xs font-semibold text-subtle">Resolution</span>
            <select
              aria-label="Resolution" value={resolution} disabled={resolutionLocked}
              onChange={(e) => setResolution(e.target.value)}
            >
              {caps.resolutions.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            {resolutionLocked && <span className="mt-1 text-xs text-subtle">720p only when extending.</span>}
          </label>
          <label className="field mb-0!">
            <span className="text-xs font-semibold text-subtle">Duration</span>
            <select
              aria-label="Duration" value={duration} disabled={durationLocked}
              onChange={(e) => setDuration(Number(e.target.value))}
            >
              {caps.durations.map((d) => <option key={d} value={d}>{d}s</option>)}
            </select>
            {durationLocked && <span className="mt-1 text-xs text-subtle">8s required for this mode/resolution.</span>}
          </label>
          {caps.person_generation.length > 0 && (
            <label className="field mb-0!">
              <span className="text-xs font-semibold text-subtle">People</span>
              <select aria-label="Person generation" value={personGen} onChange={(e) => setPersonGen(e.target.value)}>
                <option value="">— model default —</option>
                {caps.person_generation.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </label>
          )}
          <label className="field mb-0! sm:col-span-3">
            <span className="text-xs font-semibold text-subtle">Negative prompt (optional)</span>
            <input
              type="text" aria-label="Negative prompt" value={negative}
              onChange={(e) => setNegative(e.target.value)}
              placeholder="e.g. morphing faces, jerky camera"
            />
          </label>
        </section>
      )}

      <button
        className="btn-primary mt-4 w-full text-[15px] shadow-card"
        style={{ minHeight: 52 }}
        onClick={generate}
        disabled={!canGenerate}
      >
        {busy && <span className="spinner" aria-hidden />}
        {busy ? `Rendering… ${elapsed}s` : "Generate Video"}
      </button>
      {busy && (
        <p className="mt-2 text-xs text-subtle" aria-live="polite">
          VEO renders take a minute or two — keep this tab open.
        </p>
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
              Review · <span className="tabular-nums">{activeIdx + 1} of {clips.length}</span>
            </p>
            <span className="pill pill-done">{MODE_LABELS[active.mode]}</span>
          </div>

          <video
            key={active.url}
            src={active.url}
            controls
            className="mt-2 w-full rounded-md border border-line"
          />

          {clips.length > 1 && (
            <div className="mt-2 flex gap-2 overflow-x-auto">
              {clips.map((c, i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() => setActiveIdx(i)}
                  aria-label={`View clip ${i + 1}`}
                  aria-pressed={i === activeIdx}
                  className={`h-16 w-28 shrink-0 overflow-hidden rounded-md border p-0 ${i === activeIdx ? "border-accent" : "border-line"}`}
                >
                  <video src={c.url} muted className="h-full w-full object-cover" />
                </button>
              ))}
            </div>
          )}

          <div className="field mt-3">
            <div className="flex items-center justify-between gap-3">
              <label htmlFor="qv-feedback" className="text-xs font-semibold text-subtle">
                Feedback (folds into the prompt on the next render)
              </label>
              <RefineButton
                kind="video"
                instruction={feedback}
                onRefined={setFeedback}
                onError={(m) => setMsg({ kind: "error", text: m })}
              />
            </div>
            <textarea
              id="qv-feedback"
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              rows={3}
              placeholder="e.g. slower twirl, warmer light"
            />
          </div>

          <div className="mt-3 flex flex-wrap gap-2">
            <button onClick={download} disabled={busy}>Download</button>
            {caps && caps.modes.includes("extend") && (
              <button onClick={startExtend} disabled={busy}>Extend +7s</button>
            )}
          </div>
        </section>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npm run build`
Expected: PASS (no TypeScript errors).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/QuickVideoTab.tsx
git commit -m "feat(frontend): QuickVideoTab — multi-mode VEO studio with player + history"
```

---

### Task 7: Wire the tab into the app shell

**Files:**
- Modify: `frontend/src/App.tsx:8` (import), `:100-105` (TABS), `:139-149` (render switch)

**Interfaces:**
- Consumes: `QuickVideoTab` (default export from Task 6).

- [ ] **Step 1: Import the component**

In `frontend/src/App.tsx`, after the `QuickGenerateTab` import (line 8), add:

```typescript
import QuickVideoTab from "./components/QuickVideoTab";
```

- [ ] **Step 2: Add the tab entry**

In the `TABS` array (lines 100-105), add an entry after the `quickgen` row:

```typescript
  { id: "quickvideo", label: "Quick Video" },
```

- [ ] **Step 3: Render it in the panel switch**

In the `role="tabpanel"` block (lines 139-149), add a branch. Replace:

```typescript
        ) : tab === "quickgen" ? (
          <QuickGenerateTab />
        ) : tab === "prompts" ? (
```

with:

```typescript
        ) : tab === "quickgen" ? (
          <QuickGenerateTab />
        ) : tab === "quickvideo" ? (
          <QuickVideoTab />
        ) : tab === "prompts" ? (
```

- [ ] **Step 4: Typecheck / build**

Run: `cd frontend && npm run build`
Expected: PASS (no TypeScript errors; `TabId` union now includes `"quickvideo"`).

- [ ] **Step 5: Manual smoke pass**

Start the app (`poetry run uvicorn backend.main:app --reload` + `cd frontend && npm run dev`), sign in, open **Quick Video**, and verify:
- Switching modes reshapes the upload zone (text → none; image → 1 frame; frames → 2; reference → up to 3; extend → appears only after a clip exists).
- Changing model to `…-lite…` disables the Reference and Extend segments with a tooltip reason.
- Selecting Reference/Frames or 1080p locks duration to 8s with helper text; Extend locks resolution to 720p.
- Refine expands a short prompt; a render produces a clip that plays inline; a second render adds a thumbnail to the history strip; Download saves an mp4; Extend +7s chains the active clip.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(frontend): add Quick Video tab to the app shell"
```

---

## Self-Review

**Spec coverage:**
- Modes (text/image/frames/reference/extend) → Task 1 (service), Task 3 (endpoint mode→inputs), Task 6 (UI selector). ✓
- Constraint engine (`video_caps` + cross-field) → Task 2 (server caps + validator), Task 6 (client clamp + locks). ✓
- Prompt-refine assist → Task 4 (VEO-structured prompt), Task 6 (RefineButton on prompt + feedback). ✓
- Backend `/video/upload`, stateless extend via re-submitted bytes → Task 3 + Task 5 (`extend_video` field) + Task 6 (`fetch(active.url).blob()`). ✓
- Options `video_caps` → Task 2 + Task 5. ✓
- Player + history strip, progress for long jobs → Task 6. ✓
- Testing (backend pytest; frontend build + manual) → Tasks 1–4 tests; Tasks 5–7 build/manual. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code; every test step shows assertions; commands have expected output.

**Type consistency:** `startVideoUpload(fields, files)` signature in Task 5 matches its call in Task 6. `VideoCaps` fields (`modes`, `aspect_ratios`, `resolutions`, `durations`, `person_generation`) match `VIDEO_CAPS` in Task 2 and consumption in Task 6. `generate_video_bytes` kwargs (`last_frame_bytes`, `reference_image_bytes`, `extend_video_bytes`, `person_generation`, `generate_audio`) defined in Task 1, passed by `_run_video_upload_job` in Task 3, asserted in Task 1 + Task 3 tests. `_validate_video_params` keyword signature consistent across Task 2 definition, Task 2 `/video` refactor, and Task 3 endpoint.

**Open items (carried from spec):** negative-prompt support on VEO 3.1 is kept and passed through; if the live API rejects it, drop the field from `startVideoUpload`/endpoint and the service config — does not change the task structure.
