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
    monkeypatch.setattr(gen.storage_client, "short_hex", lambda: "deadbeef")
    monkeypatch.setattr(gen.storage_client, "upload_mockup",
                        lambda pid, data, key, **kw: (calls.__setitem__("key", key)
                                                      or (f"{pid}/{key}.png", f"https://public/{pid}/{key}.png")))
    monkeypatch.setattr(gen.mockup_variations_repo, "insert",
                        lambda db, **kw: (calls.__setitem__("variation", kw) or {"variation_id": 42}))
    monkeypatch.setattr(gen.mockups_repo, "set_base_mockup",
                        lambda db, pid, value=True: calls.__setitem__("flag", (pid, value)))
    monkeypatch.setattr(gen.productimages_repo, "insert",
                        lambda db, **kw: (calls.__setitem__("image", kw) or {"imageid": 1}))
    # no-redundancy seam: prior rows (default none), delete row, delete object
    monkeypatch.setattr(gen.productimages_repo, "list_for",
                        lambda db, pid, cap: calls.get("existing", []))
    monkeypatch.setattr(gen.productimages_repo, "delete_for",
                        lambda db, pid, cap: calls.__setitem__("deleted_for", (pid, cap)))
    monkeypatch.setattr(gen.storage_client, "delete_object",
                        lambda path, **kw: calls.setdefault("removed", []).append(path))
    # path_from_public_url is the real pure function (not mocked)


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
    assert body["image_url"] == "https://public/BC25001/parrot-green_deadbeef.png"
    assert body["variation_id"] == 42
    assert calls["key"] == "parrot-green_deadbeef"
    assert calls["variation"]["color"] == "Parrot Green"
    assert calls["variation"]["prompt_text"] == "a saree"
    assert calls["variation"]["image_url"] == "https://public/BC25001/parrot-green_deadbeef.png"
    assert calls["flag"] == ("BC25001", True)
    assert calls["image"]["imageurl"] == "https://public/BC25001/parrot-green_deadbeef.png"
    assert calls["image"]["caption"] == "Parrot Green"
    assert calls["deleted_for"] == ("BC25001", "Parrot Green")
    assert "removed" not in calls  # no prior row -> nothing deleted from storage


def test_approve_replaces_existing_row_and_deletes_old_object(client, monkeypatch):
    calls = {}
    _wire(monkeypatch, calls)
    calls["existing"] = [{
        "imageid": 5,
        "imageurl": "https://proj.supabase.co/storage/v1/object/public/mockups/BC25001/old_cafef00d.png",
    }]
    r = client.post("/api/generate/approve",
                    data={"productid": "BC25001", "color": "Red", "source": "generated"},
                    files={"image": ("m.png", _png_bytes(), "image/png")})
    assert r.status_code == 200
    assert calls["removed"] == ["BC25001/old_cafef00d.png"]   # old object cleaned up
    assert calls["deleted_for"] == ("BC25001", "Red")          # old row replaced


def test_approve_corrected_defaults_prompt_text(client, monkeypatch):
    calls = {}
    _wire(monkeypatch, calls)
    r = client.post("/api/generate/approve",
                    data={"productid": "BC1", "source": "corrected"},
                    files={"image": ("m.png", _png_bytes(), "image/png")})
    assert r.status_code == 200
    assert calls["variation"]["prompt_text"] == "(manual upload)"
    assert calls["variation"]["color"] is None  # repo omits None from the payload
    assert calls["key"] == "deadbeef"  # no color -> hex only


def test_approve_rejects_non_image_400(client, monkeypatch):
    _wire(monkeypatch, calls={})
    r = client.post("/api/generate/approve",
                    data={"productid": "BC1", "source": "generated"},
                    files={"image": ("x.txt", b"not an image", "text/plain")})
    assert r.status_code == 400


def test_approve_storage_not_configured_503(client, monkeypatch):
    calls = {}
    _wire(monkeypatch, calls)
    monkeypatch.setattr(gen.storage_client, "upload_mockup",
                        lambda *a, **k: (_ for _ in ()).throw(StorageNotConfigured("no key")))
    r = client.post("/api/generate/approve",
                    data={"productid": "BC1", "source": "generated"},
                    files={"image": ("m.png", _png_bytes(), "image/png")})
    assert r.status_code == 503
