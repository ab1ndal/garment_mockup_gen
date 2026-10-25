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
from mockup_generator.describe.draft import Draft, DraftFailed
from mockup_generator.prompts.describe_prompt import ProductFacts

_FACTS = ProductFacts("BC25001", "Georgette Saree", "SA", "Saree", "Georgette", [], [], "old")


def _png() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (4, 4), (5, 5, 5)).save(buf, format="PNG")
    return buf.getvalue()


def _img(i):
    return {"id": i, "name": f"{i}.jpg", "mime_type": "image/jpeg", "thumbnail_url": "t"}


@pytest.fixture
def client():
    u = CurrentUser(id="u1", email="a@b.c", role="user",
                    profile=Profile(id="u1", email="a@b.c", role="user", is_active=True))
    app.dependency_overrides[get_current_user] = lambda: u
    app.dependency_overrides[get_db] = lambda: object()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.fixture
def drafting(monkeypatch):
    """Product with a Drive folder holding loose a,b and group images r1,r2."""
    calls = {"downloads": []}
    monkeypatch.setattr(bf.descriptions_repo, "get_facts", lambda db, pid: _FACTS)
    monkeypatch.setattr(bf.products_repo, "get_product", lambda db, pid: type("P", (), {
        "producturl": "https://drive.google.com/drive/folders/ROOT"})())
    monkeypatch.setattr(bf.drive_client, "list_folder_image_groups", lambda fid: {
        "loose": [_img("a"), _img("b")],
        "groups": [{"id": "G", "name": "Red", "images": [_img("r1"), _img("r2")]}]})
    monkeypatch.setattr(bf.drive_client, "download_file",
                        lambda fid: calls["downloads"].append(fid) or f"bytes:{fid}".encode())

    def fake_draft(facts, originals, mockup, owner_note=None):
        calls.update(originals=originals, mockup=mockup, note=owner_note)
        return Draft("New text", "saree", ["length"], [])
    monkeypatch.setattr(bf, "draft_description", fake_draft)
    return calls


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
                        "description_reviewed_at": None}


def test_get_description_404(client, monkeypatch):
    monkeypatch.setattr(bf.descriptions_repo, "get_current", lambda db, pid: None)
    assert client.get("/api/backfill/description/BC9").status_code == 404


def test_draft_uses_first_three_originals_and_mockup(client, drafting):
    r = client.post("/api/backfill/f1/description/draft", json={"productid": "BC25001"})
    assert r.status_code == 200
    assert r.json() == {"description": "New text", "includes": "saree",
                        "uncertain": ["length"], "conflicts": []}
    assert drafting["originals"] == [b"bytes:a", b"bytes:b", b"bytes:r1"]
    assert drafting["mockup"] == b"bytes:f1"


def test_draft_mockup_only(client, drafting):
    r = client.post("/api/backfill/f1/description/draft",
                    json={"productid": "BC25001", "use_originals": False})
    assert r.status_code == 200
    assert drafting["originals"] == [] and drafting["mockup"] == b"bytes:f1"


def test_draft_no_images_400(client, drafting, monkeypatch):
    monkeypatch.setattr(bf.drive_client, "list_folder_image_groups", lambda fid: {"loose": [], "groups": []})
    r = client.post("/api/backfill/f1/description/draft",
                    json={"productid": "BC25001", "use_mockup": False})
    assert r.status_code == 400


def test_draft_both_toggles_off_400(client, drafting):
    r = client.post("/api/backfill/f1/description/draft",
                    json={"productid": "BC25001", "use_originals": False, "use_mockup": False})
    assert r.status_code == 400


def test_draft_unknown_product_404(client, drafting, monkeypatch):
    monkeypatch.setattr(bf.descriptions_repo, "get_facts", lambda db, pid: None)
    assert client.post("/api/backfill/f1/description/draft",
                       json={"productid": "BC9"}).status_code == 404


def test_draft_failed_502(client, drafting, monkeypatch):
    def boom(*a, **k):
        raise DraftFailed("model stopped with refusal")
    monkeypatch.setattr(bf, "draft_description", boom)
    r = client.post("/api/backfill/f1/description/draft", json={"productid": "BC25001"})
    assert r.status_code == 502 and "Could not draft a description" in r.json()["detail"]


def test_draft_note_passed_through(client, drafting):
    client.post("/api/backfill/f1/description/draft",
                json={"productid": "BC25001", "note": "has mirror work"})
    assert drafting["note"] == "has mirror work"


def test_draft_note_over_500_chars_422(client, drafting):
    r = client.post("/api/backfill/f1/description/draft",
                    json={"productid": "BC25001", "note": "x" * 501})
    assert r.status_code == 422


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
    for fn in (bf.get_description, bf.draft_description_route, bf.approve):
        assert not inspect.iscoroutinefunction(fn)
