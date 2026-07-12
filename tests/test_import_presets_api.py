import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.auth import CurrentUser, Profile, get_current_user
from backend.deps import get_db
from backend.routers import import_shots as im


@pytest.fixture
def client():
    u = CurrentUser(id="u1", email="a@b.c", role="user",
                    profile=Profile(id="u1", email="a@b.c", role="user", is_active=True))
    app.dependency_overrides[get_current_user] = lambda: u
    app.dependency_overrides[get_db] = lambda: object()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def test_list_presets(client, monkeypatch):
    monkeypatch.setattr(im.edit_presets_repo, "list_all", lambda db: [
        {"preset_id": 1, "name": "Studio", "params": {"bg": "white"}, "is_default": True}])
    r = client.get("/api/import/presets")
    assert r.status_code == 200
    assert r.json()["presets"][0]["name"] == "Studio"


def test_create_preset(client, monkeypatch):
    seen = {}
    def _insert(db, *, name, params, is_default, created_by):
        seen.update(name=name, is_default=is_default, created_by=created_by)
        return {"preset_id": 9, "name": name, "params": params, "is_default": is_default}
    monkeypatch.setattr(im.edit_presets_repo, "insert", _insert)
    r = client.post("/api/import/presets", json={
        "name": "Soft", "params": {"bg": "cream"}, "is_default": True})
    assert r.status_code == 200
    assert seen == {"name": "Soft", "is_default": True, "created_by": "u1"}


def test_mark_default(client, monkeypatch):
    marked = {}
    monkeypatch.setattr(im.edit_presets_repo, "set_default",
                        lambda db, pid: marked.update(pid=pid))
    r = client.put("/api/import/presets/7/default")
    assert r.status_code == 200 and marked["pid"] == 7


def test_delete_preset(client, monkeypatch):
    gone = {}
    monkeypatch.setattr(im.edit_presets_repo, "delete", lambda db, pid: gone.update(pid=pid))
    r = client.delete("/api/import/presets/3")
    assert r.status_code == 200 and gone["pid"] == 3
