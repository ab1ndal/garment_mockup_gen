import base64
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.main import app
from backend.auth import get_current_user, CurrentUser
from backend.deps import get_db
from backend.routers import generate as gen
from mockup_generator.db.profiles_repo import Profile
from mockup_generator.db.products_repo import Product
from mockup_generator.generation.service import NoImageReturned


def _png_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (4, 4), (1, 2, 3)).save(buf, format="PNG")
    return buf.getvalue()


def _product(url="https://drive.google.com/drive/folders/FOLDER1"):
    return Product(productid="BC25001", name="Saree", categoryid="SAREE",
                   category_name="Saree", producturl=url, base_mockup=False)


@pytest.fixture
def client(monkeypatch):
    u = CurrentUser(id="u1", email="a@b.c", role="user",
                    profile=Profile(id="u1", email="a@b.c", role="user", is_active=True))
    app.dependency_overrides[get_current_user] = lambda: u
    app.dependency_overrides[get_db] = lambda: object()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _wire_happy(monkeypatch, *, calls):
    monkeypatch.setattr(gen.products_repo, "get_product", lambda db, pid: _product())
    monkeypatch.setattr(gen.drive_client, "download_file",
                        lambda fid: (calls.setdefault("downloaded", []).append(fid), _png_bytes())[1])

    def fake_generate(images, prompt, **kw):
        calls["gen"] = {"n_images": len(images), "prompt": prompt, "kw": kw}
        return _png_bytes()

    monkeypatch.setattr(gen.service, "generate_mockup_bytes", fake_generate)


def test_generate_image_success_with_explicit_ids(client, monkeypatch):
    calls = {}
    _wire_happy(monkeypatch, calls=calls)

    r = client.post("/api/generate/image",
                    json={"productid": "BC25001", "prompt": "a luxe saree",
                          "image_ids": ["f1", "f2"]})

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert isinstance(body["image_b64"], str) and len(body["image_b64"]) > 0
    assert "image_url" not in body
    assert calls["downloaded"] == ["f1", "f2"]
    assert calls["gen"]["n_images"] == 2
    assert calls["gen"]["prompt"] == "a luxe saree"


def test_generate_image_caps_references_at_14(client, monkeypatch):
    calls = {}
    _wire_happy(monkeypatch, calls=calls)
    many = [f"id{i}" for i in range(20)]

    r = client.post("/api/generate/image",
                    json={"productid": "BC25001", "prompt": "p", "image_ids": many})

    assert r.status_code == 200
    assert len(calls["downloaded"]) == 14


def test_generate_image_product_not_found_404(client, monkeypatch):
    monkeypatch.setattr(gen.products_repo, "get_product", lambda db, pid: None)
    r = client.post("/api/generate/image",
                    json={"productid": "X", "prompt": "p", "image_ids": ["f1"]})
    assert r.status_code == 404


def test_generate_image_no_folder_400(client, monkeypatch):
    monkeypatch.setattr(gen.products_repo, "get_product", lambda db, pid: _product(url=None))
    r = client.post("/api/generate/image",
                    json={"productid": "BC25001", "prompt": "p", "image_ids": ["f1"]})
    assert r.status_code == 400


def test_generate_image_requires_source_images_400(client, monkeypatch):
    monkeypatch.setattr(gen.products_repo, "get_product", lambda db, pid: _product())
    r = client.post("/api/generate/image",
                    json={"productid": "BC25001", "prompt": "p", "image_ids": []})
    assert r.status_code == 400


def test_generate_image_gemini_failure_502(client, monkeypatch):
    calls = {}
    _wire_happy(monkeypatch, calls=calls)
    monkeypatch.setattr(gen.service, "generate_mockup_bytes",
                        lambda *a, **k: (_ for _ in ()).throw(NoImageReturned("nope")))
    r = client.post("/api/generate/image",
                    json={"productid": "BC25001", "prompt": "p", "image_ids": ["f1"]})
    assert r.status_code == 502


def test_generate_image_threads_model_resolution_aspect(client, monkeypatch):
    calls = {}
    _wire_happy(monkeypatch, calls=calls)
    r = client.post("/api/generate/image", json={
        "productid": "BC25001", "prompt": "p", "image_ids": ["f1"],
        "model": "gemini-3.1-flash-image", "resolution": "2K", "aspect_ratio": "3:4",
    })
    assert r.status_code == 200
    kw = calls["gen"]["kw"]
    assert kw["model"] == "gemini-3.1-flash-image"
    assert kw["resolution"] == "2K"
    assert kw["aspect_ratio"] == "3:4"


def test_generate_image_rejects_bad_resolution(client, monkeypatch):
    _wire_happy(monkeypatch, calls={})
    r = client.post("/api/generate/image", json={
        "productid": "BC25001", "prompt": "p", "image_ids": ["f1"], "resolution": "8K"})
    assert r.status_code == 400


def test_generate_image_rejects_bad_aspect(client, monkeypatch):
    _wire_happy(monkeypatch, calls={})
    r = client.post("/api/generate/image", json={
        "productid": "BC25001", "prompt": "p", "image_ids": ["f1"], "aspect_ratio": "21:9"})
    assert r.status_code == 400


def test_generate_image_rejects_bad_model(client, monkeypatch):
    _wire_happy(monkeypatch, calls={})
    r = client.post("/api/generate/image", json={
        "productid": "BC25001", "prompt": "p", "image_ids": ["f1"], "model": "gpt-image-1"})
    assert r.status_code == 400


def _refine_b64() -> str:
    return base64.b64encode(_png_bytes()).decode("ascii")


def test_generate_image_refine_appends_prior_output(client, monkeypatch):
    calls = {}
    _wire_happy(monkeypatch, calls=calls)
    r = client.post("/api/generate/image", json={
        "productid": "BC25001", "prompt": "p", "image_ids": ["f1", "f2"],
        "refine_image_b64": _refine_b64(),
    })
    assert r.status_code == 200
    assert calls["downloaded"] == ["f1", "f2"]
    # 2 downloaded sources + 1 refine reference
    assert calls["gen"]["n_images"] == 3


def test_generate_image_refine_only_no_sources(client, monkeypatch):
    calls = {}
    _wire_happy(monkeypatch, calls=calls)
    r = client.post("/api/generate/image", json={
        "productid": "BC25001", "prompt": "p", "image_ids": [],
        "refine_image_b64": _refine_b64(),
    })
    assert r.status_code == 200
    assert calls.get("downloaded") in (None, [])     # no Drive download needed
    assert calls["gen"]["n_images"] == 1             # the refine image alone


def test_generate_image_requires_source_or_refine_400(client, monkeypatch):
    _wire_happy(monkeypatch, calls={})
    r = client.post("/api/generate/image",
                    json={"productid": "BC25001", "prompt": "p", "image_ids": []})
    assert r.status_code == 400


def test_generate_image_bad_refine_400(client, monkeypatch):
    _wire_happy(monkeypatch, calls={})
    r = client.post("/api/generate/image", json={
        "productid": "BC25001", "prompt": "p", "image_ids": [],
        "refine_image_b64": "!!!not-base64!!!",
    })
    assert r.status_code == 400


def test_generate_image_refine_dropped_when_sources_at_cap(client, monkeypatch):
    calls = {}
    _wire_happy(monkeypatch, calls=calls)
    many = [f"id{i}" for i in range(15)]
    r = client.post("/api/generate/image", json={
        "productid": "BC25001", "prompt": "p", "image_ids": many,
        "refine_image_b64": _refine_b64(),
    })
    assert r.status_code == 200
    assert len(calls["downloaded"]) == 14            # sources capped at _MAX_REFS
    assert calls["gen"]["n_images"] == 14            # refine dropped, not 15


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


_PUB_URL = "https://x.supabase.co/storage/v1/object/public/mockups/BC25001/blue_abcd1234.png"


def _wire_video(monkeypatch, *, calls, url=_PUB_URL):
    # run the background job inline so polling is deterministic in tests
    monkeypatch.setattr(gen, "_spawn", lambda fn, *a: fn(*a))
    monkeypatch.setattr(gen.productimages_repo, "list_for",
                        lambda db, pid, color: [{"imageid": "1", "imageurl": url}] if url else [])
    monkeypatch.setattr(gen.storage_client, "download_mockup",
                        lambda path: (calls.setdefault("path", path), _png_bytes())[1])

    def fake_video(image_bytes, prompt, **kw):
        calls["video"] = {"len": len(image_bytes), "prompt": prompt, "kw": kw}
        return b"MP4BYTES"

    monkeypatch.setattr(gen.video_service, "generate_video_bytes", fake_video)


def _start_video(client, payload):
    r = client.post("/api/generate/video", json=payload)
    return r


def test_generate_video_enqueues_then_streams_mp4(client, monkeypatch):
    calls = {}
    _wire_video(monkeypatch, calls=calls)
    started = _start_video(client, {"productid": "BC25001", "prompt": "slow pan", "color": "blue"})
    assert started.status_code == 200
    job_id = started.json()["job_id"]
    assert started.json()["status"] in ("pending", "running", "done")
    assert calls["path"] == "BC25001/blue_abcd1234.png"
    assert calls["video"]["prompt"] == "slow pan"

    r = client.get(f"/api/generate/video/{job_id}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/mp4"
    assert "attachment" in r.headers["content-disposition"]
    assert r.content == b"MP4BYTES"

    # job evicted after download
    assert client.get(f"/api/generate/video/{job_id}").status_code == 404


def test_generate_video_uses_explicit_image_url(client, monkeypatch):
    calls = {}
    _wire_video(monkeypatch, calls=calls, url=None)  # no productimages fallback
    r = _start_video(client, {"productid": "BC25001", "prompt": "p", "image_url": _PUB_URL})
    assert r.status_code == 200
    assert calls["path"] == "BC25001/blue_abcd1234.png"


def test_generate_video_threads_options(client, monkeypatch):
    calls = {}
    _wire_video(monkeypatch, calls=calls)
    r = _start_video(client, {
        "productid": "BC25001", "prompt": "p",
        "model": "veo-3.1-fast-generate-preview", "resolution": "720p",
        "aspect_ratio": "16:9", "duration": 6,
    })
    assert r.status_code == 200
    kw = calls["video"]["kw"]
    assert kw["model"] == "veo-3.1-fast-generate-preview"
    assert kw["aspect_ratio"] == "16:9"
    assert kw["duration"] == 6


def test_generate_video_no_published_mockup_400(client, monkeypatch):
    calls = {}
    _wire_video(monkeypatch, calls=calls, url=None)
    r = _start_video(client, {"productid": "BC25001", "prompt": "p"})
    assert r.status_code == 400


def test_generate_video_rejects_bad_model(client):
    r = _start_video(client, {"productid": "BC25001", "prompt": "p", "model": "sora"})
    assert r.status_code == 400


def test_generate_video_1080p_requires_8s(client, monkeypatch):
    calls = {}
    _wire_video(monkeypatch, calls=calls)
    r = _start_video(client, {
        "productid": "BC25001", "prompt": "p", "resolution": "1080p", "duration": 4})
    assert r.status_code == 400


def test_generate_video_job_error_surfaced_on_poll(client, monkeypatch):
    calls = {}
    _wire_video(monkeypatch, calls=calls)
    monkeypatch.setattr(gen.video_service, "generate_video_bytes",
                        lambda *a, **k: (_ for _ in ()).throw(gen.video_service.VideoTimeout("slow")))
    started = _start_video(client, {"productid": "BC25001", "prompt": "p"})
    assert started.status_code == 200
    job_id = started.json()["job_id"]
    r = client.get(f"/api/generate/video/{job_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "error"
    assert "timed out" in body["detail"].lower()


def test_video_job_unknown_404(client):
    assert client.get("/api/generate/video/nope").status_code == 404


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


def test_upload_bad_refine_400(client, monkeypatch):
    _wire_upload(monkeypatch, calls={})
    r = _upload(client, fields={"refine_image_b64": "!!!not-base64!!!"}, files=[])
    assert r.status_code == 400


def test_upload_refine_dropped_when_files_at_cap(client, monkeypatch):
    calls = {}
    _wire_upload(monkeypatch, calls=calls)
    files = [("files", (f"{i}.png", _png_bytes(), "image/png")) for i in range(14)]
    refine = base64.b64encode(_png_bytes()).decode("ascii")
    r = _upload(client, fields={"refine_image_b64": refine}, files=files)
    assert r.status_code == 200
    assert calls["gen"]["n_images"] == 14  # refine dropped — uploads already at the 14 cap


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
