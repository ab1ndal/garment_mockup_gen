from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.main import app
from backend.auth import get_current_user, CurrentUser
from backend.deps import get_db
from backend.routers import backfill as bf
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


def _item(fid, pid="BC25001"):
    return {"productid": pid, "alpha": None, "file_id": fid, "name": f"{pid}.png",
            "subfolder_id": None, "subfolder_name": None, "thumbnail_link": f"l-{fid}"}


def test_items_paginates_and_enriches(client, monkeypatch):
    monkeypatch.setattr(bf.backfill_service, "get_index",
                        lambda root, refresh=False: [_item("a"), _item("b", "BCBAD")])
    monkeypatch.setattr(bf.drive_client, "thumbnails_for",
                        lambda items: {i["file_id"]: f"data:{i['file_id']}" for i in items})

    def fake_get_product(db, pid):
        return Product(productid=pid, name="Saree", categoryid="c1",
                       category_name="Sarees", base_mockup=False, producturl="u") if pid == "BC25001" else None

    monkeypatch.setattr(bf.products_repo, "get_product", fake_get_product)
    monkeypatch.setattr(bf.variants_repo, "list_colors", lambda db, pid: ["Red", "Blue"])

    r = client.get("/api/backfill/items?offset=0&limit=10")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    a = next(i for i in body["items"] if i["file_id"] == "a")
    assert a["product_name"] == "Saree" and a["unknown_product"] is False
    assert a["colors"] == ["Red", "Blue"] and a["thumbnail_url"] == "data:a"
    bad = next(i for i in body["items"] if i["file_id"] == "b")
    assert bad["unknown_product"] is True and bad["colors"] == []


def test_approve_publishes_then_archives_drive(client, monkeypatch):
    calls = {}
    monkeypatch.setattr(bf.drive_client, "download_file", lambda fid: _png())
    monkeypatch.setattr(bf.publish, "publish_image",
                        lambda db, **kw: (calls.update(kw) or {"image_url": "https://pub/x.png", "variation_id": 9}))
    monkeypatch.setattr(bf.drive_client, "ensure_subfolder",
                        lambda root, name: calls.__setitem__("archive_into", name) or "ARCHIVE")
    monkeypatch.setattr(bf.drive_client, "move_file",
                        lambda fid, parent: calls.__setitem__("moved", (fid, parent)))
    monkeypatch.setattr(bf.backfill_service, "evict", lambda fid, **kw: calls.__setitem__("evicted", fid))

    r = client.post("/api/backfill/approve",
                    json={"file_id": "a", "productid": "BC25001", "color": "Red"})
    assert r.status_code == 200
    body = r.json()
    assert body["image_url"] == "https://pub/x.png" and body["variation_id"] == 9
    assert calls["productid"] == "BC25001" and calls["color"] == "Red"
    assert calls["prompt_text"] is None
    assert calls["archive_into"] == "published"          # SA can't delete → archive instead
    assert calls["moved"] == ("a", "ARCHIVE") and calls["evicted"] == "a"


def test_approve_warns_when_drive_archive_fails(client, monkeypatch):
    monkeypatch.setattr(bf.drive_client, "download_file", lambda fid: _png())
    monkeypatch.setattr(bf.publish, "publish_image",
                        lambda db, **kw: {"image_url": "https://pub/x.png", "variation_id": 1})
    monkeypatch.setattr(bf.drive_client, "ensure_subfolder", lambda root, name: "ARCHIVE")
    monkeypatch.setattr(bf.drive_client, "move_file",
                        lambda fid, parent: (_ for _ in ()).throw(RuntimeError("drive boom")))
    monkeypatch.setattr(bf.backfill_service, "evict", lambda fid, **kw: None)

    r = client.post("/api/backfill/approve",
                    json={"file_id": "a", "productid": "BC25001", "color": "Red"})
    assert r.status_code == 200
    assert r.json()["warning"]                    # published, but Drive archive failed


def test_flag_sets_pending_and_moves(client, monkeypatch):
    calls = {}
    monkeypatch.setattr(bf.mockups_repo, "set_base_mockup",
                        lambda db, pid, value: calls.__setitem__("flag", (pid, value)))
    monkeypatch.setattr(bf.drive_client, "ensure_subfolder", lambda root, name: "REJECTED")
    monkeypatch.setattr(bf.drive_client, "move_file",
                        lambda fid, parent: calls.__setitem__("moved", (fid, parent)))
    monkeypatch.setattr(bf.backfill_service, "evict", lambda fid, **kw: calls.__setitem__("evicted", fid))

    r = client.post("/api/backfill/flag", json={"file_id": "a", "productid": "BC25001"})
    assert r.status_code == 200
    assert calls["flag"] == ("BC25001", False)
    assert calls["moved"][1] == "REJECTED"
    assert calls["evicted"] == "a"


def test_flag_unknown_product_moves_only(client, monkeypatch):
    calls = {}
    monkeypatch.setattr(bf.mockups_repo, "set_base_mockup",
                        lambda db, pid, value: calls.__setitem__("flag", True))
    monkeypatch.setattr(bf.drive_client, "ensure_subfolder", lambda root, name: "REJECTED")
    monkeypatch.setattr(bf.drive_client, "move_file", lambda fid, parent: calls.__setitem__("moved", fid))
    monkeypatch.setattr(bf.backfill_service, "evict", lambda fid, **kw: None)

    r = client.post("/api/backfill/flag", json={"file_id": "a"})   # no productid
    assert r.status_code == 200
    assert "flag" not in calls and calls["moved"] == "a"
