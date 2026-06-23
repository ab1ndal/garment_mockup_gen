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
from mockup_generator.integrations.storage_client import StorageNotConfigured


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
    monkeypatch.setattr(gen.storage_client, "upload_mockup",
                        lambda pid, data, key, **kw: (f"{pid}/{key}.png", "https://signed/x"))
    monkeypatch.setattr(gen.mockup_variations_repo, "insert",
                        lambda db, **kw: {"variation_id": 99, **kw})


def test_generate_image_success_with_explicit_ids(client, monkeypatch):
    calls = {}
    _wire_happy(monkeypatch, calls=calls)

    r = client.post("/api/generate/image",
                    json={"productid": "BC25001", "prompt": "a luxe saree",
                          "image_ids": ["f1", "f2"]})

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["image_url"] == "https://signed/x"
    assert body["variation_id"] == 99
    assert calls["downloaded"] == ["f1", "f2"]
    assert calls["gen"]["n_images"] == 2
    assert calls["gen"]["prompt"] == "a luxe saree"


def test_generate_image_falls_back_to_folder_listing(client, monkeypatch):
    calls = {}
    _wire_happy(monkeypatch, calls=calls)
    # no image_ids -> list folder; loose + one variant group
    monkeypatch.setattr(gen.drive_client, "list_folder_image_groups", lambda fid: {
        "loose": [{"id": "L1"}],
        "groups": [{"id": "g", "name": "RED", "images": [{"id": "R1"}, {"id": "R2"}]}],
    })

    r = client.post("/api/generate/image", json={"productid": "BC25001", "prompt": "p"})

    assert r.status_code == 200
    assert calls["downloaded"] == ["L1", "R1", "R2"]


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
    r = client.post("/api/generate/image", json={"productid": "X", "prompt": "p"})
    assert r.status_code == 404


def test_generate_image_no_folder_400(client, monkeypatch):
    monkeypatch.setattr(gen.products_repo, "get_product", lambda db, pid: _product(url=None))
    r = client.post("/api/generate/image", json={"productid": "BC25001", "prompt": "p"})
    assert r.status_code == 400


def test_generate_image_no_images_400(client, monkeypatch):
    monkeypatch.setattr(gen.products_repo, "get_product", lambda db, pid: _product())
    monkeypatch.setattr(gen.drive_client, "list_folder_image_groups",
                        lambda fid: {"loose": [], "groups": []})
    r = client.post("/api/generate/image", json={"productid": "BC25001", "prompt": "p"})
    assert r.status_code == 400


def test_generate_image_gemini_failure_502(client, monkeypatch):
    calls = {}
    _wire_happy(monkeypatch, calls=calls)
    monkeypatch.setattr(gen.service, "generate_mockup_bytes",
                        lambda *a, **k: (_ for _ in ()).throw(NoImageReturned("nope")))
    r = client.post("/api/generate/image",
                    json={"productid": "BC25001", "prompt": "p", "image_ids": ["f1"]})
    assert r.status_code == 502


def test_generate_image_storage_not_configured_503(client, monkeypatch):
    calls = {}
    _wire_happy(monkeypatch, calls=calls)
    monkeypatch.setattr(gen.storage_client, "upload_mockup",
                        lambda *a, **k: (_ for _ in ()).throw(StorageNotConfigured("no key")))
    r = client.post("/api/generate/image",
                    json={"productid": "BC25001", "prompt": "p", "image_ids": ["f1"]})
    assert r.status_code == 503


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


def test_generate_video_still_stub_501(client):
    r = client.post("/api/generate/video", json={"productid": "BC25001", "prompt": "x"})
    assert r.status_code == 501
