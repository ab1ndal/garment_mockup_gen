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
        "productid": "BC25001", "prompt": "p", "image_ids": ["f1"], "aspect_ratio": "4:5"})
    assert r.status_code == 400


def test_generate_image_rejects_bad_model(client, monkeypatch):
    _wire_happy(monkeypatch, calls={})
    r = client.post("/api/generate/image", json={
        "productid": "BC25001", "prompt": "p", "image_ids": ["f1"], "model": "gpt-image-1"})
    assert r.status_code == 400


def test_generation_options_lists_choices_and_defaults(client):
    r = client.get("/api/generate/options")
    assert r.status_code == 200
    body = r.json()
    assert "gemini-3-pro-image" in body["models"]
    assert body["resolutions"] == ["1K", "2K", "4K"]
    assert "1:1" in body["aspect_ratios"] and "3:4" in body["aspect_ratios"]
    assert "4:5" not in body["aspect_ratios"]  # not supported by the model
    assert body["defaults"]["resolution"] in body["resolutions"]
    assert body["defaults"]["aspect_ratio"] in body["aspect_ratios"]
    # video options
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
