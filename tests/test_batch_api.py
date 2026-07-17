import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.auth import get_current_user, CurrentUser
from backend.deps import get_db
from backend.routers import batch as bx
from backend.routers.generate import _DEFAULTS as GEN_DEFAULTS
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


def test_enqueue_passes_generation_options_through_to_every_card(client, monkeypatch):
    planned = {}
    monkeypatch.setattr(bx.enqueue, "plan_cards", lambda db, **k: planned.update(k) or ([], []))
    monkeypatch.setattr(bx.items_repo, "insert_many", lambda db, rows: 0)
    r = client.post("/api/batch", json={"count": 5, "model": "gemini-3.1-flash-image",
                                        "resolution": "2K", "aspect_ratio": "1:1"})
    assert r.status_code == 200
    assert planned["model"] == "gemini-3.1-flash-image"
    assert planned["resolution"] == "2K" and planned["aspect_ratio"] == "1:1"


def test_enqueue_defaults_match_single_image_generation(client, monkeypatch):
    """Batch used to default a null resolution to 4K while the portal's own
    default was 2K, so batches silently ran slower and at print quality."""
    planned = {}
    monkeypatch.setattr(bx.enqueue, "plan_cards", lambda db, **k: planned.update(k) or ([], []))
    monkeypatch.setattr(bx.items_repo, "insert_many", lambda db, rows: 0)
    assert client.post("/api/batch", json={"count": 5}).status_code == 200
    assert planned["resolution"] == GEN_DEFAULTS["resolution"]
    assert planned["aspect_ratio"] == GEN_DEFAULTS["aspect_ratio"]


def test_enqueue_rejects_unsupported_generation_options(client):
    """Options are stamped onto every card, so a bad value would surface as N
    failed generations rather than one bad request."""
    assert client.post("/api/batch", json={"count": 1, "model": "gpt-image-1"}).status_code == 400
    assert client.post("/api/batch", json={"count": 1, "resolution": "8K"}).status_code == 400
    assert client.post("/api/batch", json={"count": 1, "aspect_ratio": "21:9"}).status_code == 400


def test_items_ready_tab_enriches(client, monkeypatch):
    monkeypatch.setattr(bx.items_repo, "page",
                        lambda db, *, statuses, offset, limit, sort_by_product=False, categoryid=None, productid=None: ([_row()], 1))
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


def test_items_handled_card_never_signs_a_deleted_staged_file(client, monkeypatch):
    """Accept and reject both delete the staged object and null the path, so a
    History row has nothing to sign — signing it produced a broken image."""
    monkeypatch.setattr(bx.items_repo, "page",
                        lambda db, **k: ([_row(status="published", drv=None)], 1))
    monkeypatch.setattr(bx.products_repo, "names_for", lambda db, pids: {"BC25001": "Saree"})
    def boom(*a, **k): raise AssertionError("handled cards have no staged file to sign")
    monkeypatch.setattr(bx.storage_client, "signed_url", boom)
    it = client.get("/api/batch/items?tab=history").json()["items"][0]
    assert it["generated_thumb_url"] is None
    assert it["status"] == "published" and it["color"] == "Red"


def test_items_in_progress_tab_queries_two_statuses(client, monkeypatch):
    seen = {}
    def fake_page(db, *, statuses, offset, limit, sort_by_product=False, categoryid=None, productid=None):
        seen["statuses"] = statuses
        seen["categoryid"] = categoryid
        seen["productid"] = productid
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


def test_accept_clears_staged_path(client, monkeypatch):
    """The staged object is deleted here, so the card must stop pointing at it —
    a dangling path is what made History try to sign a file that was gone."""
    monkeypatch.setattr(bx.items_repo, "get", lambda db, i: _row(id=i))
    updates = []
    monkeypatch.setattr(bx.items_repo, "transition",
                        lambda db, *, item_id, expect, to, **f: updates.append((expect, to, f)) or True)
    monkeypatch.setattr(bx.storage_client, "download_mockup", lambda p, *, bucket, **k: b"PNG")
    monkeypatch.setattr(bx.publish, "publish_image",
                        lambda db, **k: {"image_url": "https://public/m.png", "variation_id": 7})
    monkeypatch.setattr(bx.storage_client, "delete_object", lambda p, *, bucket, **k: None)
    assert client.post("/api/batch/1/accept", json={}).status_code == 200
    assert updates[-1] == ("published", "published", {"storage_path": None})


def test_reject_clears_staged_path(client, monkeypatch):
    monkeypatch.setattr(bx.items_repo, "get", lambda db, i: _row(id=i))
    updates = []
    monkeypatch.setattr(bx.items_repo, "transition",
                        lambda db, *, item_id, expect, to, **f: updates.append((expect, to, f)) or True)
    monkeypatch.setattr(bx.storage_client, "delete_object", lambda p, *, bucket, **k: None)
    assert client.post("/api/batch/1/reject").status_code == 200
    assert updates[-1] == ("rejected", "rejected", {"storage_path": None})


def test_sources_returns_parallel_thumbnails_not_full_downloads(client, monkeypatch):
    """The review pane opened slowly because it streamed every full-resolution
    source and the 4K mockup, base64'd into one JSON body. Thumbnails only."""
    monkeypatch.setattr(bx.items_repo, "get", lambda db, i: _row(id=i))
    monkeypatch.setattr(bx.variants_repo, "list_colors", lambda db, pid: ["Red", "Blue"])
    monkeypatch.setattr(bx.drive_client, "thumbnails_for_ids",
                        lambda ids: [{"id": i, "thumbnail_url": f"data:image/jpeg;base64,{i}"} for i in ids])
    def boom(*a, **k): raise AssertionError("review must not stream full-resolution files")
    monkeypatch.setattr(bx.drive_client, "download_file", boom)
    monkeypatch.setattr(bx.storage_client, "download_mockup", boom)
    body = client.get("/api/batch/1/sources").json()
    assert [s["id"] for s in body["sources"]] == ["a", "b"]
    assert body["sources"][0]["thumb_url"] == "data:image/jpeg;base64,a"
    assert body["colors"] == ["Red", "Blue"] and body["color"] == "Red"


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
