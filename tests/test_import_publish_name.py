import pytest
from fastapi.testclient import TestClient

import backend.routers.import_shots as mod
from backend.main import app
from backend.auth import get_current_user, CurrentUser
from backend.deps import get_db
from mockup_generator.db.profiles_repo import Profile


@pytest.fixture
def client():
    u = CurrentUser(id="u1", email="a@b.c", role="user",
                    profile=Profile(id="u1", email="a@b.c", role="user", is_active=True))
    app.dependency_overrides[get_current_user] = lambda: u
    app.dependency_overrides[get_db] = lambda: object()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def test_published_key_contains_product_id(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(mod, "_render", lambda fid, params: b"webp-source")
    monkeypatch.setattr(mod.publish, "_encode_webp", lambda b: b)
    monkeypatch.setattr(mod.productimages_repo, "next_product_shot_order", lambda db, pid: 20)
    monkeypatch.setattr(mod.storage_client, "slugify", lambda c: "red")
    monkeypatch.setattr(mod.storage_client, "short_hex", lambda: "deadbeef")
    monkeypatch.setattr(mod.productimages_repo, "insert",
                        lambda db, **kw: None)

    def _fake_upload(productid, data, key, *, ext, content_type):
        captured["productid"] = productid
        captured["key"] = key
        return f"{productid}/{key}.{ext}", f"https://x/{productid}/{key}.{ext}"

    monkeypatch.setattr(mod.storage_client, "upload_mockup", _fake_upload)

    r = client.post("/api/import/publish",
                    json={"productid": "BC25001", "file_id": "f1", "color": "Red", "params": {}})
    assert r.status_code == 200
    assert captured["key"] == "BC25001_red_20_deadbeef"
