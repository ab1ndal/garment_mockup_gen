from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.auth import CurrentUser, get_current_user
from backend.deps import get_db
from backend.main import app
from backend.routers import backfill as bf
from mockup_generator.db.product_tags_repo import TagRow
from mockup_generator.db.profiles_repo import Profile


def _png() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (4, 4), (5, 5, 5)).save(buf, format="PNG")
    return buf.getvalue()


def _product(categoryid):
    return type("P", (), {"productid": "BC25001", "categoryid": categoryid,
                          "producturl": "https://drive.google.com/drive/folders/ROOT"})()


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
    calls = {"rpc": []}
    monkeypatch.setattr(bf.items_repo, "transition", lambda db, **kw: True)
    monkeypatch.setattr(bf.productimages_repo, "next_display_order", lambda db, pid: 0)
    monkeypatch.setattr(bf.drive_client, "download_file", lambda fid: _png())
    monkeypatch.setattr(bf.publish, "publish_image", lambda db, **kw: {"image_url": "u", "variation_id": 1})
    monkeypatch.setattr(bf.drive_client, "ensure_subfolder", lambda root, name: "ARCHIVE")
    monkeypatch.setattr(bf.drive_client, "move_file", lambda fid, parent: None)
    monkeypatch.setattr(bf.descriptions_repo, "save_reviewed",
                        lambda db, pid, text, **kw: calls.get("save_result", True))
    monkeypatch.setattr(bf.products_repo, "get_product", lambda db, pid: _product(calls.get("cat", "SA")))

    def replace(db, pid, tags):
        calls["rpc"].append((pid, tags))
        if "rpc_error" in calls:
            raise calls["rpc_error"]
        return calls.get("stored", tags)
    monkeypatch.setattr(bf.tags_repo, "replace_ai_tags", replace)
    return calls


_TAGS = [{"group": "work", "value": "zari", "source": "ai_reviewed", "confidence": 0.9},
         {"group": "occasion", "value": "party", "source": "manual", "confidence": None}]


def _approve(client, **extra):
    body = {"file_id": "f1", "productid": "BC25001", "description": "New text",
            "expected_description": "old", "expected_reviewed_at": None, **extra}
    return client.post("/api/backfill/approve", json=body)


def test_get_tags_returns_groups_and_current(client, monkeypatch):
    monkeypatch.setattr(bf.products_repo, "get_product", lambda db, pid: _product("LE"))
    monkeypatch.setattr(bf.tags_repo, "get_tags",
                        lambda db, pid: [TagRow("occasion", "wedding", "manual", None)])
    r = client.get("/api/backfill/tags/BC25001")
    assert r.status_code == 200
    body = r.json()
    assert body["family"] == "lehenga"
    occasion = next(g for g in body["groups"] if g["group"] == "occasion")
    assert occasion["single"] is False and occasion["label"] == "Occasion"
    assert {"value": "wedding", "label": "Wedding"} in occasion["values"]
    assert "office" not in [v["value"] for v in occasion["values"]]
    assert body["current"] == [{"group": "occasion", "value": "wedding", "source": "manual", "confidence": None}]


def test_get_tags_404(client, monkeypatch):
    monkeypatch.setattr(bf.products_repo, "get_product", lambda db, pid: None)
    assert client.get("/api/backfill/tags/BC9").status_code == 404


def test_approve_saves_tags_after_description(client, approving):
    r = _approve(client, tags=_TAGS)
    assert r.status_code == 200 and r.json()["tags_warning"] is None
    assert approving["rpc"] == [("BC25001", [TagRow("work", "zari", "ai_reviewed", 0.9),
                                             TagRow("occasion", "party", "manual", None)])]


def test_approve_without_tags_leaves_them(client, approving):
    r = _approve(client)
    assert r.status_code == 200 and r.json()["tags_warning"] is None
    assert approving["rpc"] == []


def test_approve_skips_tags_when_description_not_saved(client, approving):
    approving["save_result"] = False
    r = _approve(client, tags=_TAGS)
    assert r.status_code == 200
    assert r.json()["tags_warning"] == bf._TAGS_NOT_SAVED
    assert approving["rpc"] == []


def test_approve_skips_tags_without_description(client, approving):
    r = client.post("/api/backfill/approve", json={"file_id": "f1", "productid": "BC25001", "tags": _TAGS})
    assert r.status_code == 200 and r.json()["tags_warning"] == bf._TAGS_NOT_SAVED
    assert approving["rpc"] == []


def test_approve_rejects_disallowed_value(client, approving):
    approving["cat"] = "LE"
    r = _approve(client, tags=[{"group": "occasion", "value": "office", "source": "ai_reviewed", "confidence": None}])
    assert r.status_code == 200
    assert r.json()["tags_warning"].startswith("Tags not saved: occasion: office")
    assert approving["rpc"] == []


def test_approve_rejects_two_single_values(client, approving):
    approving["cat"] = "KUR"
    two = [{"group": "neck", "value": v, "source": "manual", "confidence": None} for v in ("round", "v-neck")]
    r = _approve(client, tags=two)
    assert r.status_code == 200 and r.json()["tags_warning"].startswith("Tags not saved:")
    assert approving["rpc"] == []


def test_approve_rpc_error_is_warning(client, approving):
    approving["rpc_error"] = RuntimeError("value office not allowed for LE")
    r = _approve(client, tags=_TAGS)
    assert r.status_code == 200
    assert r.json()["tags_warning"] == (
        "Image approved, but the tags could not be saved: value office not allowed for LE")


def test_approve_reports_skipped_tags(client, approving):
    approving["stored"] = [TagRow("work", "zari", "ai_reviewed", 0.9)]
    r = _approve(client, tags=_TAGS)
    assert r.json()["tags_warning"] == "Kept hand-entered tags; not saved: occasion: party."


@pytest.mark.parametrize("tag", [
    {"group": "work", "value": "zari", "source": "ai", "confidence": None},
    {"group": "work", "value": "zari", "source": "ai_reviewed", "confidence": 1.5},
], ids=["bad_source", "bad_confidence"])
def test_approve_bad_tag_fields_warn_not_block(client, approving, tag):
    r = _approve(client, tags=[tag])
    assert r.status_code == 200 and r.json()["tags_warning"].startswith("Tags not saved:")
    assert approving["rpc"] == []


def test_approve_no_family_does_not_wipe_ai_tags(client, approving):
    approving["cat"] = "NEW-CAT"
    r = _approve(client, tags=[])
    assert r.status_code == 200 and r.json()["tags_warning"] is None
    assert approving["rpc"] == []


def test_approve_rpc_postgrest_error_shows_message(client, approving):
    from postgrest.exceptions import APIError
    approving["rpc_error"] = APIError({"message": "value office not allowed for LE", "code": "P0001",
                                       "hint": None, "details": None})
    r = _approve(client, tags=_TAGS)
    assert r.json()["tags_warning"] == (
        "Image approved, but the tags could not be saved: value office not allowed for LE")
