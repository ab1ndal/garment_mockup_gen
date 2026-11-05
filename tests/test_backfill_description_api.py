import inspect
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.auth import CurrentUser, get_current_user
from backend.deps import get_db
from backend.main import app
from backend.routers import backfill as bf
from mockup_generator.db.product_descriptions_repo import CurrentDescription
from mockup_generator.db.profiles_repo import Profile


def _png() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (4, 4), (5, 5, 5)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def client():
    u = CurrentUser(id="u1", email="a@b.c", role="user",
                    profile=Profile(id="u1", email="a@b.c", role="user", is_active=True))
    app.dependency_overrides[get_current_user] = lambda: u
    app.dependency_overrides[get_db] = lambda: object()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.fixture
def approving(monkeypatch):
    calls = {}
    monkeypatch.setattr(bf.items_repo, "transition", lambda db, **kw: True)
    monkeypatch.setattr(bf.productimages_repo, "next_display_order", lambda db, pid: 0)
    monkeypatch.setattr(bf.drive_client, "download_file", lambda fid: _png())
    monkeypatch.setattr(bf.publish, "publish_image",
                        lambda db, **kw: calls.setdefault("published", True) and {"image_url": "u", "variation_id": 1})
    monkeypatch.setattr(bf.drive_client, "ensure_subfolder", lambda root, name: "ARCHIVE")
    monkeypatch.setattr(bf.drive_client, "move_file", lambda fid, parent: None)

    def save(db, pid, text, *, expected_description, expected_reviewed_at):
        calls["saved"] = (pid, text, expected_description, expected_reviewed_at)
        return calls.get("save_result", True)
    monkeypatch.setattr(bf.descriptions_repo, "save_reviewed", save)
    monkeypatch.setattr(bf.descriptions_repo, "get_current",
                        lambda db, pid: CurrentDescription(None, None, None))
    return calls


def _approve(client, **extra):
    return client.post("/api/backfill/approve",
                       json={"file_id": "f1", "productid": "BC25001", **extra})


def test_get_description(client, monkeypatch):
    monkeypatch.setattr(bf.descriptions_repo, "get_current",
                        lambda db, pid: CurrentDescription("d", "manual", None))
    r = client.get("/api/backfill/description/BC25001")
    assert r.status_code == 200
    assert r.json() == {"description": "d", "description_source": "manual",
                        "description_reviewed_at": None, "replaceable": False}


def test_get_description_404(client, monkeypatch):
    monkeypatch.setattr(bf.descriptions_repo, "get_current", lambda db, pid: None)
    assert client.get("/api/backfill/description/BC9").status_code == 404


def test_approve_without_description_does_not_touch_it(client, approving):
    r = _approve(client)
    assert r.status_code == 200 and r.json()["description_warning"] is None
    assert "saved" not in approving


def test_approve_saves_description(client, approving):
    r = _approve(client, description="  New text \n", expected_description="old",
                 expected_reviewed_at="2026-10-24T10:00:00+00:00")
    assert r.status_code == 200 and r.json()["description_warning"] is None
    assert approving["saved"] == ("BC25001", "New text", "old", "2026-10-24T10:00:00+00:00")


def test_approve_saves_empty_draft(client, approving):
    _approve(client, description="", expected_description=None, expected_reviewed_at=None)
    assert approving["saved"] == ("BC25001", "", None, None)


def test_approve_description_conflict_warns(client, approving):
    approving["save_result"] = False
    r = _approve(client, description="New text")
    assert r.status_code == 200 and approving["published"]
    assert r.json()["description_warning"] == (
        "Image approved, but the description changed since you opened it. Not saved.")


def test_approve_description_error_warns(client, approving, monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(bf.descriptions_repo, "save_reviewed", boom)
    caplog.set_level("WARNING")
    r = _approve(client, description="New text")
    assert r.status_code == 200
    assert r.json()["description_warning"] == "Image approved, but the description could not be saved."
    assert any("description save failed" in rec.getMessage() for rec in caplog.records)


def test_approve_description_over_cap_still_approves(client, approving):
    r = _approve(client, description="x" * 601)
    assert r.status_code == 200 and approving["published"]
    assert "saved" not in approving
    assert r.json()["description_warning"] == (
        "Image approved, but the description is over 600 characters. Not saved.")


def test_approve_description_cap_counts_trimmed_text(client, approving):
    r = _approve(client, description=" " * 10 + "x" * 600 + "\n")
    assert r.status_code == 200 and r.json()["description_warning"] is None
    assert approving["saved"][1] == "x" * 600


def test_description_routes_are_sync():
    for fn in (bf.get_description, bf.approve):
        assert not inspect.iscoroutinefunction(fn)
