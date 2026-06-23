import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.auth import get_current_user, CurrentUser
from backend.deps import get_db
from mockup_generator.db import prompts_repo
from mockup_generator.db.profiles_repo import Profile


@pytest.fixture
def client():
    u = CurrentUser(id="u1", email="a@b.c", role="user",
                    profile=Profile(id="u1", email="a@b.c", role="user", is_active=True))
    app.dependency_overrides[get_current_user] = lambda: u
    app.dependency_overrides[get_db] = lambda: object()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_list_prompts(client, monkeypatch):
    monkeypatch.setattr(prompts_repo, "list_by_category",
                        lambda *a, **k: [prompts_repo.Prompt(1, "SA", "Default", "b", True)])
    r = client.get("/api/prompts?categoryid=SA")
    assert r.status_code == 200 and r.json()[0]["label"] == "Default"


def test_create_prompt(client, monkeypatch):
    monkeypatch.setattr(prompts_repo, "create",
                        lambda *a, **k: prompts_repo.Prompt(2, "SA", "Studio", "b", False))
    r = client.post("/api/prompts", json={"categoryid": "SA", "label": "Studio", "body": "b"})
    assert r.status_code == 201 and r.json()["prompt_id"] == 2


def test_delete_prompt(client, monkeypatch):
    called = {}
    monkeypatch.setattr(prompts_repo, "delete", lambda c, pid: called.setdefault("pid", pid))
    r = client.delete("/api/prompts/7")
    assert r.status_code == 204 and called["pid"] == 7


def test_update_prompt(client, monkeypatch):
    monkeypatch.setattr(prompts_repo, "update",
                        lambda *a, **k: prompts_repo.Prompt(5, "SA", "X", "Y", True))
    r = client.patch("/api/prompts/5", json={"label": "X", "body": "Y", "is_default": True})
    assert r.status_code == 200
    body = r.json()
    assert body["prompt_id"] == 5
    assert body["label"] == "X"
    assert body["body"] == "Y"
    assert body["is_default"] is True
