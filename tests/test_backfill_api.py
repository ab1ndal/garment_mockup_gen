from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.main import app
from backend.auth import get_current_user, CurrentUser
from backend.deps import get_db
from backend.routers import backfill as bf
from mockup_generator.db.backfill_items_repo import BackfillRow
from mockup_generator.db.profiles_repo import Profile
from mockup_generator.db.products_repo import Product  # used as a simple stub


def _png(w=4, h=4) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (w, h), (5, 5, 5)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def client():
    u = CurrentUser(id="u1", email="a@b.c", role="user",
                    profile=Profile(id="u1", email="a@b.c", role="user", is_active=True))
    app.dependency_overrides[get_current_user] = lambda: u
    app.dependency_overrides[get_db] = lambda: object()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _row(fid, pid="BC25001"):
    return BackfillRow(file_id=fid, productid=pid, alpha=None,
                       filename=f"{pid}.png", thumbnail_link=f"l-{fid}", status="pending")


# ----- reads ---------------------------------------------------------------

def test_items_paginates_and_enriches(client, monkeypatch):
    monkeypatch.setattr(bf.items_repo, "page",
                        lambda db, *, status, offset, limit: ([_row("a"), _row("b", "BCBAD")], 2))
    monkeypatch.setattr(bf.products_repo, "names_for",
                        lambda db, pids: {"BC25001": "Saree"})
    monkeypatch.setattr(bf.drive_client, "thumbnails_for",
                        lambda items: {i["file_id"]: f"data:{i['file_id']}" for i in items})

    r = client.get("/api/backfill/items?status=pending&offset=0&limit=20")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2 and body["offset"] == 0 and body["limit"] == 20
    a = next(i for i in body["items"] if i["file_id"] == "a")
    assert a["product_name"] == "Saree" and a["unknown_product"] is False
    assert a["thumbnail_url"] == "data:a"
    bad = next(i for i in body["items"] if i["file_id"] == "b")
    assert bad["unknown_product"] is True   # productid present but no product row


def test_items_rejects_unknown_status(client):
    r = client.get("/api/backfill/items?status=bogus")
    assert r.status_code == 400


def test_counts_returns_per_status(client, monkeypatch):
    monkeypatch.setattr(bf.items_repo, "counts",
                        lambda db: {"pending": 3, "skipped": 1, "edit": 0, "regenerate": 2})
    r = client.get("/api/backfill/counts")
    assert r.status_code == 200
    assert r.json()["counts"]["pending"] == 3


# ----- approve -------------------------------------------------------------

def test_approve_claims_publishes_then_archives(client, monkeypatch):
    calls = {}
    monkeypatch.setattr(bf.items_repo, "transition",
                        lambda db, *, file_id, expect, to: calls.setdefault("claims", []).append((expect, to)) or True)
    monkeypatch.setattr(bf.drive_client, "download_file", lambda fid: _png())
    monkeypatch.setattr(bf.publish, "publish_image",
                        lambda db, **kw: (calls.update(kw) or {"image_url": "https://pub/x.png", "variation_id": 9}))
    monkeypatch.setattr(bf.drive_client, "ensure_subfolder",
                        lambda root, name: calls.__setitem__("archive_into", name) or "ARCHIVE")
    monkeypatch.setattr(bf.drive_client, "move_file",
                        lambda fid, parent: calls.__setitem__("moved", (fid, parent)))

    r = client.post("/api/backfill/approve",
                    json={"file_id": "a", "productid": "BC25001", "color": "Red"})
    assert r.status_code == 200
    body = r.json()
    assert body["image_url"] == "https://pub/x.png" and body["variation_id"] == 9
    assert calls["claims"] == [("pending", "published")]   # claimed, not reverted
    assert calls["archive_into"] == "published"
    assert calls["moved"] == ("a", "ARCHIVE")


def test_approve_409_when_already_handled(client, monkeypatch):
    monkeypatch.setattr(bf.items_repo, "transition", lambda db, **kw: False)
    r = client.post("/api/backfill/approve",
                    json={"file_id": "a", "productid": "BC25001", "color": "Red"})
    assert r.status_code == 409


def test_approve_reverts_claim_on_publish_failure(client, monkeypatch):
    claims = []
    monkeypatch.setattr(bf.items_repo, "transition",
                        lambda db, *, file_id, expect, to: claims.append((expect, to)) or True)
    monkeypatch.setattr(bf.drive_client, "download_file", lambda fid: _png())
    monkeypatch.setattr(bf.publish, "publish_image",
                        lambda db, **kw: (_ for _ in ()).throw(RuntimeError("publish boom")))

    r = client.post("/api/backfill/approve",
                    json={"file_id": "a", "productid": "BC25001", "color": "Red"})
    assert r.status_code == 502
    # claimed pending->published, then reverted published->pending
    assert claims == [("pending", "published"), ("published", "pending")]


# ----- flag / flag-edit ----------------------------------------------------

def test_flag_claims_sets_pending_flag_and_moves(client, monkeypatch):
    calls = {}
    monkeypatch.setattr(bf.items_repo, "transition",
                        lambda db, *, file_id, expect, to: calls.__setitem__("claim", (expect, to)) or True)
    monkeypatch.setattr(bf.mockups_repo, "set_base_mockup",
                        lambda db, pid, value: calls.__setitem__("flag", (pid, value)))
    monkeypatch.setattr(bf.drive_client, "ensure_subfolder", lambda root, name: "REJECTED")
    monkeypatch.setattr(bf.drive_client, "move_file",
                        lambda fid, parent: calls.__setitem__("moved", (fid, parent)))

    r = client.post("/api/backfill/flag", json={"file_id": "a", "productid": "BC25001"})
    assert r.status_code == 200
    assert calls["claim"] == ("pending", "regenerate")
    assert calls["flag"] == ("BC25001", False)
    assert calls["moved"] == ("a", "REJECTED")


def test_flag_409(client, monkeypatch):
    monkeypatch.setattr(bf.items_repo, "transition", lambda db, **kw: False)
    r = client.post("/api/backfill/flag", json={"file_id": "a", "productid": "BC25001"})
    assert r.status_code == 409


def test_flag_edit_claims_moves_and_records(client, monkeypatch):
    calls = {}
    monkeypatch.setattr(bf.items_repo, "transition",
                        lambda db, *, file_id, expect, to: calls.__setitem__("claim", (expect, to)) or True)
    monkeypatch.setattr(bf.drive_client, "ensure_subfolder", lambda root, name: "EDIT")
    monkeypatch.setattr(bf.drive_client, "move_file",
                        lambda fid, parent: calls.__setitem__("moved", (fid, parent)))
    monkeypatch.setattr(bf.backfill_edits_repo, "insert",
                        lambda db, **kw: calls.__setitem__("record", kw))

    r = client.post("/api/backfill/flag-edit",
                    json={"file_id": "a", "productid": "BC25001", "comment": "fix hem"})
    assert r.status_code == 200
    assert calls["claim"] == ("pending", "edit")
    assert calls["moved"] == ("a", "EDIT")
    assert calls["record"]["comment"] == "fix hem"


# ----- skip / unskip -------------------------------------------------------

def test_skip_claims_and_moves(client, monkeypatch):
    calls = {}
    monkeypatch.setattr(bf.items_repo, "transition",
                        lambda db, *, file_id, expect, to: calls.__setitem__("claim", (expect, to)) or True)
    monkeypatch.setattr(bf.drive_client, "ensure_subfolder",
                        lambda root, name: calls.__setitem__("into", name) or "SKIPPED")
    monkeypatch.setattr(bf.drive_client, "move_file",
                        lambda fid, parent: calls.__setitem__("moved", (fid, parent)))

    r = client.post("/api/backfill/skip", json={"file_id": "a", "productid": "BC25001"})
    assert r.status_code == 200
    assert calls["claim"] == ("pending", "skipped") and calls["into"] == "skipped"
    assert calls["moved"] == ("a", "SKIPPED")


def test_skip_409(client, monkeypatch):
    monkeypatch.setattr(bf.items_repo, "transition", lambda db, **kw: False)
    r = client.post("/api/backfill/skip", json={"file_id": "a"})
    assert r.status_code == 409


def test_unskip_claims_and_moves_to_root(client, monkeypatch):
    calls = {}
    monkeypatch.setattr(bf.items_repo, "transition",
                        lambda db, *, file_id, expect, to: calls.__setitem__("claim", (expect, to)) or True)
    monkeypatch.setattr(bf.drive_client, "move_file",
                        lambda fid, parent: calls.__setitem__("moved", (fid, parent)))

    r = client.post("/api/backfill/unskip", json={"file_id": "a"})
    assert r.status_code == 200
    assert calls["claim"] == ("skipped", "pending")
    assert calls["moved"] == ("a", bf.settings.generated_mockups_folder_id)


# ----- rescan --------------------------------------------------------------

def test_rescan_syncs_from_drive(client, monkeypatch):
    monkeypatch.setattr(bf.backfill_sync, "rescan", lambda db, root: 42)
    r = client.post("/api/backfill/rescan")
    assert r.status_code == 200
    assert r.json()["synced"] == 42


def test_approve_remove_watermark_routes_bytes_through_inpaint(client, monkeypatch):
    calls = {}
    monkeypatch.setattr(bf.items_repo, "transition", lambda db, **kw: True)
    monkeypatch.setattr(bf.drive_client, "download_file", lambda fid: b"RAW")
    monkeypatch.setattr(bf.watermark, "remove_corner_star",
                        lambda png: (calls.__setitem__("inpaint_in", png), b"CLEANED")[1])
    monkeypatch.setattr(bf.publish, "publish_image",
                        lambda db, **kw: calls.update(kw) or {"image_url": "u", "variation_id": 1})
    monkeypatch.setattr(bf.drive_client, "ensure_subfolder", lambda *a: "arch")
    monkeypatch.setattr(bf.drive_client, "move_file", lambda *a: None)

    r = client.post("/api/backfill/approve", json={
        "file_id": "f1", "productid": "BC25001", "color": "Red", "remove_watermark": True,
    })
    assert r.status_code == 200
    assert calls["inpaint_in"] == b"RAW"
    assert calls["png"] == b"CLEANED"


def test_approve_default_skips_inpaint(client, monkeypatch):
    calls = {}
    monkeypatch.setattr(bf.items_repo, "transition", lambda db, **kw: True)
    monkeypatch.setattr(bf.drive_client, "download_file", lambda fid: b"RAW")
    monkeypatch.setattr(bf.watermark, "remove_corner_star",
                        lambda png: (calls.__setitem__("inpaint", True), png)[1])
    monkeypatch.setattr(bf.publish, "publish_image",
                        lambda db, **kw: calls.update(kw) or {"image_url": "u", "variation_id": 1})
    monkeypatch.setattr(bf.drive_client, "ensure_subfolder", lambda *a: "arch")
    monkeypatch.setattr(bf.drive_client, "move_file", lambda *a: None)

    r = client.post("/api/backfill/approve", json={"file_id": "f1", "productid": "BC25001"})
    assert r.status_code == 200
    assert "inpaint" not in calls
    assert calls["png"] == b"RAW"
