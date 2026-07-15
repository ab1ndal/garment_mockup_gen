import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.auth import get_current_user, CurrentUser
from backend.deps import get_db
from backend.routers import batch as bx
from mockup_generator.db.batch_items_repo import BatchRow
from mockup_generator.db.profiles_repo import Profile


@pytest.fixture
def client():
    u = CurrentUser(id="u1", email="a@b.c", role="user",
                    profile=Profile(id="u1", email="a@b.c", role="user", is_active=True))
    app.dependency_overrides[get_current_user] = lambda: u
    app.dependency_overrides[get_db] = lambda: object()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _row(id=1, status="ready", drv="BC25001/batch-1.png"):
    return BatchRow(id=id, batch_id="b1", productid="BC25001", color="Red",
                    image_ids=["a", "b"], prompt_text="p", status=status,
                    storage_path=drv, error=None,
                    model="m", resolution="4K", aspect_ratio="1:1")


def test_enqueue_plans_inserts_and_starts_worker(client, monkeypatch):
    monkeypatch.setattr(bx.enqueue, "plan_cards",
                        lambda db, **k: ([{"productid": "BC1"}], [{"productid": "BC2", "reason": "no images"}]))
    inserted = {}
    monkeypatch.setattr(bx.items_repo, "insert_many",
                        lambda db, rows: inserted.setdefault("rows", rows) or len(rows))
    started = {"n": 0}
    monkeypatch.setattr(bx.worker, "ensure_running", lambda db: started.__setitem__("n", started["n"] + 1))

    r = client.post("/api/batch", json={"category": "SA", "count": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["queued"] == 1 and body["skipped"][0]["reason"] == "no images"
    assert started["n"] == 1 and len(inserted["rows"]) == 1


def test_enqueue_rejects_out_of_range_count(client):
    assert client.post("/api/batch", json={"count": 0}).status_code == 422
    assert client.post("/api/batch", json={"count": 101}).status_code == 422


def test_items_ready_tab_enriches(client, monkeypatch):
    monkeypatch.setattr(bx.items_repo, "page",
                        lambda db, *, statuses, offset, limit: ([_row()], 1))
    monkeypatch.setattr(bx.products_repo, "names_for", lambda db, pids: {"BC25001": "Saree"})
    signed = {}
    def fake_sign(path, *, bucket, **k):
        signed.update({"path": path, "bucket": bucket})
        return f"https://signed/{path}"
    monkeypatch.setattr(bx.storage_client, "signed_url", fake_sign)
    r = client.get("/api/batch/items?tab=ready&offset=0&limit=20")
    assert r.status_code == 200
    it = r.json()["items"][0]
    assert it["product_name"] == "Saree"
    assert it["generated_thumb_url"] == "https://signed/BC25001/batch-1.png"
    assert it["color"] == "Red"
    # staged thumbs are signed from the private temp bucket, never public
    assert signed["bucket"] == bx.storage_client.TEMP_BUCKET


def test_items_in_progress_tab_queries_two_statuses(client, monkeypatch):
    seen = {}
    def fake_page(db, *, statuses, offset, limit):
        seen["statuses"] = statuses
        return [], 0
    monkeypatch.setattr(bx.items_repo, "page", fake_page)
    monkeypatch.setattr(bx.products_repo, "names_for", lambda db, pids: {})
    r = client.get("/api/batch/items?tab=in_progress")
    assert r.status_code == 200
    assert set(seen["statuses"]) == {"queued", "generating"}


def test_items_rejects_unknown_tab(client):
    assert client.get("/api/batch/items?tab=bogus").status_code == 400


def test_counts(client, monkeypatch):
    monkeypatch.setattr(bx.items_repo, "counts",
                        lambda db: {"ready": 2, "queued": 1, "generating": 0,
                                    "failed": 0, "published": 3, "rejected": 1})
    r = client.get("/api/batch/counts")
    assert r.status_code == 200 and r.json()["counts"]["ready"] == 2


def test_accept_publishes_from_temp_deletes_staged_and_marks_published(client, monkeypatch):
    monkeypatch.setattr(bx.items_repo, "get", lambda db, i: _row(id=i))
    monkeypatch.setattr(bx.items_repo, "transition",
                        lambda db, *, item_id, expect, to, **f: True)
    downloaded = {}
    def fake_dl(path, *, bucket, **k):
        downloaded.update({"path": path, "bucket": bucket})
        return b"PNG"
    monkeypatch.setattr(bx.storage_client, "download_mockup", fake_dl)
    published = {}
    monkeypatch.setattr(bx.publish, "publish_image",
                        lambda db, **k: published.update(k) or {"image_url": "u", "variation_id": 7})
    deleted = {}
    def fake_rm(path, *, bucket, **k): deleted.update({"path": path, "bucket": bucket})
    monkeypatch.setattr(bx.storage_client, "delete_object", fake_rm)
    r = client.post("/api/batch/1/accept", json={})
    assert r.status_code == 200 and r.json()["status"] == "ok"
    # read from temp, published via the unchanged publish path, temp copy removed
    assert downloaded["path"] == "BC25001/batch-1.png"
    assert downloaded["bucket"] == bx.storage_client.TEMP_BUCKET
    assert published["color"] == "Red"
    assert deleted["path"] == "BC25001/batch-1.png"
    assert deleted["bucket"] == bx.storage_client.TEMP_BUCKET


def test_accept_conflict_when_not_ready(client, monkeypatch):
    monkeypatch.setattr(bx.items_repo, "get", lambda db, i: _row(id=i))
    monkeypatch.setattr(bx.items_repo, "transition",
                        lambda db, *, item_id, expect, to, **f: False)  # lost the row
    r = client.post("/api/batch/1/accept", json={})
    assert r.status_code == 409


def test_reject_deletes_staged_and_marks_rejected(client, monkeypatch):
    monkeypatch.setattr(bx.items_repo, "get", lambda db, i: _row(id=i))
    moved = {"to": None}
    monkeypatch.setattr(bx.items_repo, "transition",
                        lambda db, *, item_id, expect, to, **f: moved.__setitem__("to", to) or True)
    deleted = {}
    monkeypatch.setattr(bx.storage_client, "delete_object",
                        lambda path, *, bucket, **k: deleted.update({"path": path}))
    r = client.post("/api/batch/1/reject")
    assert r.status_code == 200 and moved["to"] == "rejected"
    assert deleted["path"] == "BC25001/batch-1.png"


def test_accept_still_succeeds_with_warning_when_staged_delete_fails(client, monkeypatch):
    """The publish already happened; a leftover temp object is an orphan to clean
    up, not a reason to fail the reviewer's action."""
    monkeypatch.setattr(bx.items_repo, "get", lambda db, i: _row(id=i))
    monkeypatch.setattr(bx.items_repo, "transition", lambda db, *, item_id, expect, to, **f: True)
    monkeypatch.setattr(bx.storage_client, "download_mockup", lambda path, *, bucket, **k: b"PNG")
    monkeypatch.setattr(bx.publish, "publish_image",
                        lambda db, **k: {"image_url": "u", "variation_id": 7})
    def _boom(path, *, bucket, **k): raise RuntimeError("storage refused")
    monkeypatch.setattr(bx.storage_client, "delete_object", _boom)
    r = client.post("/api/batch/1/accept", json={})
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert "could not be removed" in r.json()["warning"]


def test_edit_requeues_with_note_and_clears_drive(client, monkeypatch):
    monkeypatch.setattr(bx.items_repo, "get", lambda db, i: _row(id=i))
    captured = {}
    monkeypatch.setattr(bx.items_repo, "transition",
                        lambda db, *, item_id, expect, to, **f: captured.update({"to": to, **f}) or True)
    monkeypatch.setattr(bx.storage_client, "delete_object", lambda path, *, bucket, **k: None)
    started = {"n": 0}
    monkeypatch.setattr(bx.worker, "ensure_running", lambda db: started.__setitem__("n", 1))
    r = client.post("/api/batch/1/edit", json={"prompt_note": "brighter", "image_ids": ["a"]})
    assert r.status_code == 200
    assert captured["to"] == "queued" and "brighter" in captured["prompt_text"]
    assert captured["image_ids"] == ["a"] and captured["storage_path"] is None
    assert started["n"] == 1


def test_retry_requeues_failed(client, monkeypatch):
    monkeypatch.setattr(bx.items_repo, "get", lambda db, i: _row(id=i, status="failed", drv=None))
    captured = {}
    monkeypatch.setattr(bx.items_repo, "transition",
                        lambda db, *, item_id, expect, to, **f: captured.update({"expect": expect, "to": to}) or True)
    monkeypatch.setattr(bx.worker, "ensure_running", lambda db: None)
    r = client.post("/api/batch/1/retry")
    assert r.status_code == 200 and captured["expect"] == "failed" and captured["to"] == "queued"


def test_router_is_mounted(client):
    # /counts requires the router to be registered; 200 (not 404) proves it.
    import backend.routers.batch as bx2
    from unittest.mock import patch
    with patch.object(bx2.items_repo, "counts", lambda db: {s: 0 for s in bx2.items_repo.ALL_STATUSES}):
        assert client.get("/api/batch/counts").status_code == 200
