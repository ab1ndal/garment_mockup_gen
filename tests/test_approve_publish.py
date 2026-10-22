# tests/test_approve_publish.py
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.main import app
from backend.auth import get_current_user, CurrentUser
from backend.deps import get_db
from backend.routers import generate as gen
from mockup_generator.db.profiles_repo import Profile
from mockup_generator.integrations.storage_client import StorageNotConfigured


def _png_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (4, 4), (9, 9, 9)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def client():
    u = CurrentUser(id="u1", email="a@b.c", role="user",
                    profile=Profile(id="u1", email="a@b.c", role="user", is_active=True))
    app.dependency_overrides[get_current_user] = lambda: u
    app.dependency_overrides[get_db] = lambda: object()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _wire(monkeypatch, calls):
    monkeypatch.setattr(gen.publish.storage_client, "short_hex", lambda: "deadbeef")
    monkeypatch.setattr(gen.publish.storage_client, "upload_mockup",
                        # First upload is the PNG; web WEBP + size variants follow.
                        lambda pid, data, key, **kw: (calls.setdefault("key", key) and None
                                                      or (f"{pid}/{key}.png", f"https://public/{pid}/{key}.png")))
    monkeypatch.setattr(gen.publish.mockup_variations_repo, "insert",
                        lambda db, **kw: (calls.__setitem__("variation", kw) or {"variation_id": 42}))
    monkeypatch.setattr(gen.publish.mockups_repo, "set_base_mockup",
                        lambda db, pid, value=True: calls.__setitem__("flag", (pid, value)))
    monkeypatch.setattr(gen.publish.productimages_repo, "insert",
                        lambda db, **kw: (calls.__setitem__("image", kw) or {"imageid": 1}))
    # Append model: the next display order is baked into the storage key.
    monkeypatch.setattr(gen.publish.productimages_repo, "next_display_order",
                        lambda db, pid: calls.get("order", 0))
    # Replace seams must never fire — guard against regressions.
    monkeypatch.setattr(gen.publish.productimages_repo, "delete_for",
                        lambda db, pid, cap, theme="Default": calls.__setitem__("deleted_for", (pid, cap, theme)))
    monkeypatch.setattr(gen.publish.storage_client, "delete_object",
                        lambda path, **kw: calls.setdefault("removed", []).append(path))


def test_approve_generated_publishes(client, monkeypatch):
    calls = {}
    _wire(monkeypatch, calls)

    r = client.post("/api/generate/approve",
                    data={"productid": "BC25001", "color": "Parrot Green",
                          "prompt_text": "a saree", "source": "generated"},
                    files={"image": ("m.png", _png_bytes(), "image/png")})

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["image_url"] == "https://public/BC25001/parrot-green_0_deadbeef.png"
    assert body["variation_id"] == 42
    assert calls["key"] == "parrot-green_0_deadbeef"
    assert calls["variation"]["color"] == "Parrot Green"
    assert calls["variation"]["prompt_text"] == "a saree"
    assert calls["variation"]["image_url"] == "https://public/BC25001/parrot-green_0_deadbeef.png"
    assert calls["flag"] == ("BC25001", True)
    assert calls["image"]["imageurl"] == "https://public/BC25001/parrot-green_0_deadbeef.png"
    assert calls["image"]["productcolor"] == "Parrot Green"
    assert calls["image"]["theme"] == "Default"  # no theme/aspect sent -> Default
    assert calls["image"]["displayorder"] == 0
    assert "deleted_for" not in calls  # append model -> never replace
    assert "removed" not in calls      # no prior PNG deleted


def test_approve_appends_keeping_prior_design(client, monkeypatch):
    """A second design for the same color+theme is appended at the next display
    order, with its own storage object — the prior one is untouched."""
    calls = {}
    _wire(monkeypatch, calls)
    calls["order"] = 1
    r = client.post("/api/generate/approve",
                    data={"productid": "BC25001", "color": "Red", "source": "generated"},
                    files={"image": ("m.png", _png_bytes(), "image/png")})
    assert r.status_code == 200
    assert calls["key"] == "red_1_deadbeef"
    assert calls["image"]["displayorder"] == 1
    assert "deleted_for" not in calls  # old row kept
    assert "removed" not in calls      # old PNG kept


def test_approve_builds_phototheme_from_label_and_aspect(client, monkeypatch):
    calls = {}
    _wire(monkeypatch, calls)
    r = client.post("/api/generate/approve",
                    data={"productid": "BC1", "color": "Red", "source": "generated",
                          "theme_name": "Studio", "aspect_ratio": "9:16"},
                    files={"image": ("m.png", _png_bytes(), "image/png")})
    assert r.status_code == 200
    assert calls["image"]["theme"] == "Studio·9:16"      # non-1:1 -> aspect suffix


def test_approve_theme_label_without_aspect_suffix_at_1to1(client, monkeypatch):
    calls = {}
    _wire(monkeypatch, calls)
    r = client.post("/api/generate/approve",
                    data={"productid": "BC1", "color": "Red", "source": "generated",
                          "theme_name": "Studio", "aspect_ratio": "1:1"},
                    files={"image": ("m.png", _png_bytes(), "image/png")})
    assert r.status_code == 200
    assert calls["image"]["theme"] == "Studio"           # 1:1 -> no suffix


def test_approve_corrected_defaults_prompt_text(client, monkeypatch):
    calls = {}
    _wire(monkeypatch, calls)
    r = client.post("/api/generate/approve",
                    data={"productid": "BC1", "source": "corrected"},
                    files={"image": ("m.png", _png_bytes(), "image/png")})
    assert r.status_code == 200
    assert calls["variation"]["prompt_text"] == "(manual upload)"
    assert calls["variation"]["color"] is None  # repo omits None from the payload
    assert calls["key"] == "0_deadbeef"  # no color -> order + hex


def test_approve_rejects_non_image_400(client, monkeypatch):
    _wire(monkeypatch, calls={})
    r = client.post("/api/generate/approve",
                    data={"productid": "BC1", "source": "generated"},
                    files={"image": ("x.txt", b"not an image", "text/plain")})
    assert r.status_code == 400


def test_approve_storage_not_configured_503(client, monkeypatch):
    calls = {}
    _wire(monkeypatch, calls)
    monkeypatch.setattr(gen.publish.storage_client, "upload_mockup",
                        lambda *a, **k: (_ for _ in ()).throw(StorageNotConfigured("no key")))
    r = client.post("/api/generate/approve",
                    data={"productid": "BC1", "source": "generated"},
                    files={"image": ("m.png", _png_bytes(), "image/png")})
    assert r.status_code == 503


def test_approve_logs_every_stage(client, monkeypatch, caplog):
    """One approve must leave enough in the logs to say which step was slow:
    the request total, the route's own stages and each publish stage."""
    _wire(monkeypatch, {})
    caplog.set_level("INFO")

    r = client.post("/api/generate/approve", data={"productid": "BC25001"},
                    files={"image": ("m.png", _png_bytes(), "image/png")})

    assert r.status_code == 200
    lines = [rec.getMessage() for rec in caplog.records]
    approve = next(m for m in lines if m.startswith("approve timing"))
    for stage in ("read_upload", "decode", "encode_png", "publish"):
        assert f" {stage}=" in approve
    pub = next(m for m in lines if m.startswith("publish timing"))
    assert "outcome=ok" in pub
    for stage in ("next_order", "upload_png", "encode_webp", "upload_webp",
                  "upload_webp_150", "upload_webp_600", "upload_webp_1200",
                  "upload_webp_all", "wait_png", "insert_variation",
                  "set_base_mockup", "insert_productimage", "total"):
        assert f" {stage}=" in pub
    assert any(m.startswith("request method=POST path=/api/generate/approve status=200 ms=")
               for m in lines)
