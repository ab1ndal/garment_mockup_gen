"""AI drafts: when they run, what they cost, and that nothing reaches products unapproved."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
from threading import Lock

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from postgrest.exceptions import APIError

from backend.auth import CurrentUser, get_current_user
from backend.deps import get_db
from backend.main import app
from backend.routers import batch as batch_router
from backend.routers import copy_drafts as drafts_router
from backend.services import copy_drafts as service
from backend.services import draft_worker as worker
from backend.services import reviewed_copy
from backend.services.copy_drafts import DraftImages
from mockup_generator.config import settings
from mockup_generator.db import copy_drafts_repo as repo
from mockup_generator.db.product_descriptions_repo import CurrentDescription
from mockup_generator.db.profiles_repo import Profile
from mockup_generator.describe import draft as draft_module
from mockup_generator.describe.draft import Draft, DraftFailed, ProposedTag, draft_cost_usd

_IMAGES = DraftImages(originals=("a", "b"), mockup_drive="m1")
_CARD = "backfill:m1"


class _Query:
    def __init__(self, db, table):
        self.db, self.table_name = db, table
        self.op, self.payload, self.filters, self.order_by, self.max = "select", None, [], None, None

    def select(self, cols): return self
    def insert(self, payload): self.op, self.payload = "insert", payload; return self
    def update(self, payload): self.op, self.payload = "update", payload; return self
    def eq(self, c, v): self.filters.append(lambda r: r.get(c) == v); return self
    def is_(self, c, v): self.filters.append(lambda r: r.get(c) is None); return self
    def in_(self, c, v): self.filters.append(lambda r: r.get(c) in v); return self
    def lt(self, c, v): self.filters.append(lambda r: r.get(c) is not None and r[c] < v); return self
    def gte(self, c, v): self.filters.append(lambda r: r.get(c) is not None and r[c] >= v); return self
    def order(self, c, desc=False): self.order_by = c; return self
    def limit(self, n): self.max = n; return self

    def execute(self):
        result = type("R", (), {})()
        result.data = self.db.run(self)
        return result


class FakeDb:
    """Enough of PostgREST for these tests. The rules that matter are enforced here the
    way the migration enforces them: one active row per product, by raising the real
    error class the client raises."""

    def __init__(self):
        self.rows, self.products, self.next_id, self.lock = [], {}, 1, Lock()
        self.product_writes = []

    def table(self, name):
        return _Query(self, name)

    def run(self, q):
        with self.lock:
            if q.table_name == "products":
                if q.op != "select":
                    self.product_writes.append(q.payload)
                return [r for r in self.products.values() if all(f(r) for f in q.filters)]
            if q.op == "insert":
                if any(r["productid"] == q.payload["productid"] and r["active"] for r in self.rows):
                    raise APIError({"message": "duplicate key", "code": "23505", "hint": None, "details": None})
                row = {"id": self.next_id, "active": True, "status": repo.QUEUED, "attempts": 0, "error": None,
                       "draft": None, "cost_usd": 0, "drafted_at": None, "updated_at": _now(), **q.payload}
                self.next_id += 1
                self.rows.append(row)
                return [dict(row)]
            matched = [r for r in self.rows if all(f(r) for f in q.filters)]
            if q.op == "update":
                for r in matched:
                    r.update({k: _now() if v == "now()" else v for k, v in q.payload.items()})
            matched = sorted(matched, key=lambda r: r["id"])
            return [dict(r) for r in matched[:q.max]]

    def rpc(self, name, params):
        class Call:
            def execute(self):
                r = type("R", (), {})()
                r.data = {"title": f"Silk Saree with {params['p_detail']}", "duplicate": params["p_detail"] == "Dup"}
                return r
        return Call()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _png():
    buf = BytesIO()
    Image.new("RGB", (4, 4)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def db(monkeypatch):
    """A blank product, images that always load, and a worker that does not start."""
    d = FakeDb()
    d.products["BC1"] = {"productid": "BC1", "name": "Silk Saree", "title_detail": None, "display_title": "Silk Saree",
                         "description": None, "description_source": None, "description_reviewed_at": None,
                         "categoryid": "SA", "fabric": "Silk", "categories": {"name": "Saree"}}
    monkeypatch.setattr(worker, "_spawn", lambda fn, *a: None)
    monkeypatch.setattr(worker, "_active", 0)
    monkeypatch.setattr(worker.drive_client, "download_file", lambda fid: _png())
    return d


@pytest.fixture
def paid(monkeypatch):
    """Counts model calls: each one is money."""
    calls = []

    def fake(facts, originals, mockup, note=None):
        calls.append({"originals": len(originals), "mockup": mockup is not None, "note": note})
        return Draft("Flowing drape.", "saree", [], [], [ProposedTag("work", "zari", 0.9)],
                     title_detail="Zari Border", colour_mismatch="", input_tokens=15000, output_tokens=500)
    monkeypatch.setattr(worker, "draft_description", fake)
    return calls


def _request(db, card=_CARD, images=_IMAGES, **kw):
    return service.request_draft(db, "BC1", card, lambda: images, **kw)


def _drain(db):
    while worker.run_one(db):
        pass


# --- never pay twice ---

def test_asking_repeatedly_pays_once(db, paid):
    first = _request(db)
    _drain(db)
    second = _request(db)
    third = _request(db)
    _drain(db)
    assert len(paid) == 1
    assert second.id == third.id == first.id and second.status == repo.READY


def test_concurrent_reviewers_share_one_draft(db, paid):
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda _: _request(db), range(8)))
    assert len({r.id for r in rows}) == 1
    assert len([r for r in db.rows if r["active"]]) == 1
    _drain(db)
    assert len(paid) == 1


def test_two_workers_cannot_claim_one_row(db):
    _request(db)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claimed = list(pool.map(lambda _: repo.claim_next_queued(db), range(4)))
    assert len([c for c in claimed if c]) == 1


def test_another_colours_card_reuses_the_draft_without_looking_at_images(db, paid):
    _request(db)
    _drain(db)

    def no_drive():
        raise AssertionError("must not load images")
    assert service.request_draft(db, "BC1", "backfill:m2", no_drive).status == repo.READY
    assert len(paid) == 1


def test_same_card_with_changed_images_drafts_again(db, paid):
    first = _request(db)
    _drain(db)
    second = _request(db, images=DraftImages(originals=("a", "c"), mockup_drive="m1"))
    assert second.id != first.id and second.status == repo.QUEUED
    _drain(db)
    assert len(paid) == 2


def test_image_order_does_not_count_as_a_change(db, paid):
    _request(db, images=DraftImages(originals=("a", "b"), mockup_drive="m1"))
    _drain(db)
    _request(db, images=DraftImages(originals=("b", "a"), mockup_drive="m1"))
    assert len(paid) == 1 and len(db.rows) == 1


def test_reviewer_can_ask_for_a_fresh_draft_with_a_note(db, paid):
    first = _request(db)
    _drain(db)
    second = _request(db, force=True, note="has mirror work")
    _drain(db)
    assert second.id != first.id and paid[-1]["note"] == "has mirror work"
    assert [r["active"] for r in db.rows] == [False, True]


def test_asking_again_while_it_runs_starts_nothing(db, paid):
    first = _request(db)
    assert _request(db, force=True).id == first.id
    assert len(db.rows) == 1


def test_failed_draft_is_not_retried_by_itself_but_retry_works(db, paid, monkeypatch):
    def boom(*a, **k):
        raise DraftFailed("Claude API error: overloaded")
    with monkeypatch.context() as m:
        m.setattr(worker, "draft_description", boom)
        first = _request(db)
        _drain(db)
    again = _request(db)
    assert again.id == first.id and again.status == repo.FAILED and "overloaded" in again.error
    _drain(db)
    assert paid == []
    retried = _request(db, force=True)
    assert retried.id == first.id and retried.status == repo.QUEUED and retried.attempts == 0
    _drain(db)
    assert len(paid) == 1 and repo.get_active(db, "BC1").status == repo.READY


def test_nothing_is_drafted_when_people_wrote_it_all(db, paid):
    db.products["BC1"].update(title_detail="Kantha Work", description="Hand stitched.", description_source="manual")
    assert _request(db) is None and db.rows == []
    assert _request(db, force=True) is not None


def test_no_images_is_an_error_not_a_row(db):
    with pytest.raises(service.NoImages):
        _request(db, images=DraftImages())
    assert db.rows == []


# --- nothing reaches products before Approve ---

def test_worker_never_writes_to_products(db, paid):
    _request(db)
    _drain(db)
    assert db.product_writes == []
    saved = repo.get_active(db, "BC1").draft
    assert saved["title_detail"] == "Zari Border" and saved["preview_title"] == "Silk Saree with Zari Border"
    assert saved["tags"] == [{"group": "work", "value": "zari", "confidence": 0.9}]


def test_failed_preview_keeps_the_paid_draft(db, paid, monkeypatch):
    def boom(*a):
        raise RuntimeError("rpc down")
    monkeypatch.setattr(worker.titles_repo, "preview", boom)
    _request(db)
    _drain(db)
    row = repo.get_active(db, "BC1")
    assert row.status == repo.READY and row.draft["preview_title"] is None


# --- failure modes ---

def test_restart_mid_queue_resumes_and_gives_up_after_two_tries(db, paid):
    _request(db)
    assert repo.claim_next_queued(db).attempts == 1
    assert worker.reset_orphaned(db) == 1           # restart
    assert repo.claim_next_queued(db).attempts == 2
    assert worker.reset_orphaned(db) == 1           # restarted again, maybe billed twice now
    assert worker.run_one(db) is True
    row = repo.get_active(db, "BC1")
    assert row.status == repo.FAILED and "Retry" in row.error
    assert paid == []


def test_stale_reclaim_leaves_live_rows_alone(db):
    _request(db)
    repo.claim_next_queued(db)
    assert repo.reset_stale(db, 900) == 0
    db.rows[0]["updated_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1000)).isoformat()
    assert repo.reset_stale(db, 900) == 1


def test_daily_cap_stops_new_calls_and_says_so(db, paid, monkeypatch):
    monkeypatch.setenv("DRAFT_DAILY_USD_CAP", "1")
    db.rows.append({"id": 99, "productid": "BC9", "active": True, "status": repo.READY, "source_card": "x",
                    "images_key": "k", "image_refs": {}, "attempts": 1, "cost_usd": 1.0, "drafted_at": _now()})
    db.next_id = 100
    _request(db)
    _drain(db)
    row = repo.get_active(db, "BC1")
    assert paid == [] and row.status == repo.FAILED and "limit" in row.error


def test_spend_older_than_a_day_does_not_count(db, paid, monkeypatch):
    monkeypatch.setenv("DRAFT_DAILY_USD_CAP", "1")
    old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    db.rows.append({"id": 99, "productid": "BC9", "active": True, "status": repo.READY, "source_card": "x",
                    "images_key": "k", "image_refs": {}, "attempts": 1, "cost_usd": 5.0, "drafted_at": old})
    db.next_id = 100
    _request(db)
    _drain(db)
    assert len(paid) == 1


def test_superseded_drafts_still_count_toward_the_cap(db, paid):
    _request(db)
    _drain(db)
    _request(db, force=True)
    _drain(db)
    assert repo.spent_last_24h(db) == pytest.approx(2 * draft_cost_usd(settings.description_model, 15000, 500), abs=0.001)


@pytest.mark.parametrize("value,expected", [("25", 25.0), ("garbage", 10.0)])
def test_cap_setting_is_read_from_the_environment(monkeypatch, value, expected):
    monkeypatch.setenv("DRAFT_DAILY_USD_CAP", value)
    assert settings.draft_daily_usd_cap == expected


def test_billed_failure_is_recorded(db, monkeypatch):
    def boom(*a, **k):
        raise DraftFailed("model stopped with max_tokens", input_tokens=15000, output_tokens=16000)
    monkeypatch.setattr(worker, "draft_description", boom)
    _request(db)
    _drain(db)
    assert repo.spent_last_24h(db) > 0.3


def test_staged_mockup_gone_drafts_from_originals(db, paid, monkeypatch):
    def gone(*a, **k):
        raise RuntimeError("object not found")
    monkeypatch.setattr(worker.storage_client, "download_mockup", gone)
    _request(db, card="batch:1", images=DraftImages(originals=("a",), mockup_storage="p"))
    _drain(db)
    assert paid == [{"originals": 1, "mockup": False, "note": None}]


def test_photo_failure_shows_plainly(db, paid, monkeypatch):
    def gone(fid):
        raise RuntimeError("drive 500")
    monkeypatch.setattr(worker.drive_client, "download_file", gone)
    _request(db)
    _drain(db)
    row = repo.get_active(db, "BC1")
    assert row.status == repo.FAILED and "drive 500" in row.error and paid == []


def test_pool_never_exceeds_the_limit(db, monkeypatch):
    started = []
    monkeypatch.setattr(worker, "_spawn", lambda fn, *a: started.append(fn))
    monkeypatch.setenv("DRAFT_CONCURRENCY", "2")
    for _ in range(5):
        worker.ensure_running(db)
    assert len(started) == 2


def test_cost_uses_the_model_price():
    assert draft_cost_usd("claude-opus-5-5", 1_000_000, 1_000_000) == 24.0
    assert draft_cost_usd("some-new-model", 1_000_000, 0) == 10.0


def test_real_tokens_reach_the_draft():
    class Client:
        messages = None

        def __init__(self):
            self.messages = self

        def create(self, **kw):
            from anthropic.types import Message, TextBlock, Usage
            return Message.model_construct(
                id="m", type="message", role="assistant", model="claude-opus-5-5",
                content=[TextBlock(type="text", text='{"description": "Drape."}')], stop_reason="end_turn",
                stop_sequence=None, usage=Usage(input_tokens=15000, output_tokens=400))
    facts = draft_module.ProductFacts("BC1", "n", "SA", "Saree", "Silk", [], [], None)
    d = draft_module.draft_description(facts, [_png()], None, client=Client())
    assert (d.input_tokens, d.output_tokens) == (15000, 400)


# --- human text ---

@pytest.mark.parametrize("text,source,human", [
    (None, None, False),
    ("   ", "manual", False),
    ("Hand stitched.", "manual", True),
    ("Reviewed.", "ai_reviewed", True),
    ("Short AI.", "ai", False),
    ("Short shop text.", None, True),
    ("**Markdown** caption", None, False),
    ("x" * 201, None, False),
])
def test_who_wrote_the_description(text, source, human):
    assert CurrentDescription(text, source, None).is_human is human


def _saved(monkeypatch):
    calls = []

    def save(db, pid, text, **kw):
        calls.append(text)
        return True
    monkeypatch.setattr(reviewed_copy.descriptions_repo, "save_reviewed", save)
    return calls


def test_unedited_ai_text_never_replaces_a_person(db, monkeypatch):
    calls = _saved(monkeypatch)
    db.products["BC1"].update(description="Hand stitched.", description_source="manual")
    warning = reviewed_copy.save_description(db, "BC1", "AI text", expected_description="Hand stitched.",
                                             expected_reviewed_at=None, edited=False)
    assert warning == reviewed_copy.DESCRIPTION_KEPT and calls == []


def test_reviewers_edit_can_replace_a_person(db, monkeypatch):
    calls = _saved(monkeypatch)
    db.products["BC1"].update(description="Hand stitched.", description_source="manual")
    assert reviewed_copy.save_description(db, "BC1", "Mine", expected_description="Hand stitched.",
                                          expected_reviewed_at=None, edited=True) is None
    assert calls == ["Mine"]


def test_unedited_ai_text_replaces_earlier_ai_text(db, monkeypatch):
    calls = _saved(monkeypatch)
    db.products["BC1"].update(description="**Old** caption", description_source=None)
    assert reviewed_copy.save_description(db, "BC1", "New", expected_description="**Old** caption",
                                          expected_reviewed_at=None, edited=False) is None
    assert calls == ["New"]


# --- the card shows what Accept saves ---

def _ready_draft(db, **draft):
    _request(db, force=True)
    row = db.rows[-1]
    row.update(status=repo.READY, draft={"title_detail": "Zari Border", "preview_title": "Silk Saree with Zari Border",
                                         "description": "Flowing drape.", "tags": [], **draft})
    return row["id"]


def test_card_without_a_draft_opens_review(db):
    assert reviewed_copy.card_previews(db, ["BC1"])["BC1"].can_accept is False


def test_card_with_failed_or_running_draft_opens_review(db):
    _request(db)
    assert reviewed_copy.card_previews(db, ["BC1"])["BC1"].can_accept is False
    db.rows[-1]["status"] = repo.FAILED
    assert reviewed_copy.card_previews(db, ["BC1"])["BC1"].can_accept is False


def test_card_with_a_ready_draft_shows_it(db):
    draft_id = _ready_draft(db, colour_mismatch="Recorded Red, photo is blue.", duplicate=True)
    c = reviewed_copy.card_previews(db, ["BC1"])["BC1"]
    assert (c.can_accept, c.draft_id, c.title, c.description) == (True, draft_id, "Silk Saree with Zari Border", "Flowing drape.")
    assert c.colour_mismatch == "Recorded Red, photo is blue." and c.duplicate is True


def test_card_draft_with_no_title_opens_review(db):
    _ready_draft(db, title_detail="")
    assert reviewed_copy.card_previews(db, ["BC1"])["BC1"].can_accept is False


def test_card_shows_the_persons_text_not_the_ai_text(db):
    db.products["BC1"].update(title_detail="Kantha Work", display_title="Silk Saree with Kantha Work",
                              description="Hand stitched.", description_source="manual")
    _ready_draft(db)
    c = reviewed_copy.card_previews(db, ["BC1"])["BC1"]
    assert c.title == "Silk Saree with Kantha Work" and c.description == "Hand stitched."


def test_card_where_people_wrote_everything_needs_no_draft(db):
    db.products["BC1"].update(title_detail="Kantha Work", display_title="Silk Saree with Kantha Work",
                              description="Hand stitched.", description_source="manual")
    c = reviewed_copy.card_previews(db, ["BC1"])["BC1"]
    assert c.can_accept is True and c.draft_id is None


def test_missing_table_means_review_not_an_error(db, monkeypatch):
    def missing(*a):
        raise APIError({"message": "relation does not exist", "code": "42P01", "hint": None, "details": None})
    monkeypatch.setattr(reviewed_copy.drafts_repo, "get_active_many", missing)
    assert reviewed_copy.card_previews(db, ["BC1"]) == {}


# --- routes ---

@pytest.fixture
def client(db):
    u = CurrentUser(id="u1", email="a@b.c", role="user",
                    profile=Profile(id="u1", email="a@b.c", role="user", is_active=True))
    app.dependency_overrides[get_current_user] = lambda: u
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def test_routes_report_a_missing_migration_plainly(client, monkeypatch):
    def missing(*a):
        raise APIError({"message": "relation does not exist", "code": "42P01", "hint": None, "details": None})
    monkeypatch.setattr(drafts_router.repo, "get_active", missing)
    assert client.get("/api/drafts/BC1").json()["status"] == "unavailable"
    r = client.post("/api/drafts/product/BC1", json={})
    assert r.status_code == 200 and r.json()["status"] == "unavailable"


def test_other_database_errors_are_not_called_a_missing_migration(client, monkeypatch):
    def down(*a):
        raise APIError({"message": "timeout", "code": "57014", "hint": None, "details": None})
    monkeypatch.setattr(drafts_router.repo, "get_active", down)
    assert client.get("/api/drafts/BC1").status_code == 502


def test_batch_route_uses_the_cards_images(client, db, paid, monkeypatch):
    card = type("C", (), {"productid": "BC1", "image_ids": ["a", "b", "c", "d"], "storage_path": "p"})()
    monkeypatch.setattr(drafts_router.batch_items_repo, "get", lambda d, i: card)
    r = client.post("/api/drafts/batch/7", json={})
    assert r.json()["status"] == "queued" and r.json()["source_card"] == "batch:7"
    assert db.rows[0]["image_refs"] == {"originals": ["a", "b", "c"], "mockup_drive": None, "mockup_storage": "p"}


def test_backfill_route_uses_the_folder_and_the_mockup(client, db, paid, monkeypatch):
    monkeypatch.setattr(drafts_router, "_first_original_ids", lambda d, pid: ["x", "y"])
    r = client.post("/api/drafts/backfill/f1?productid=BC1", json={})
    assert r.json()["source_card"] == "backfill:f1"
    assert db.rows[0]["image_refs"] == {"originals": ["x", "y"], "mockup_drive": "f1", "mockup_storage": None}


def test_both_image_sources_off_is_rejected(client):
    assert client.post("/api/drafts/backfill/f1?productid=BC1",
                       json={"use_originals": False, "use_mockup": False}).status_code == 400


def test_ready_draft_comes_back_with_its_text(client, db, paid):
    _request(db)
    _drain(db)
    body = client.get("/api/drafts/BC1").json()
    assert body["status"] == "ready" and body["title_detail"] == "Zari Border" and body["description"] == "Flowing drape."
    assert body["tags"] == [{"group": "work", "value": "zari", "confidence": 0.9}]


def test_note_over_the_limit_is_rejected(client):
    assert client.post("/api/drafts/product/BC1", json={"note": "x" * 501}).status_code == 422


def test_draft_routes_are_sync():
    import inspect
    for fn in (drafts_router.request_backfill, drafts_router.request_batch,
               drafts_router.request_product, drafts_router.get_draft):
        assert not inspect.iscoroutinefunction(fn)


@pytest.fixture
def accepting(db, monkeypatch):
    calls = {"published": 0, "tags": None}
    card = type("R", (), {"productid": "BC1", "storage_path": "p", "color": None, "aspect_ratio": "1:1", "prompt_text": "t"})()
    monkeypatch.setattr(batch_router.items_repo, "get", lambda d, i: card)
    monkeypatch.setattr(batch_router.items_repo, "transition", lambda d, **kw: True)
    monkeypatch.setattr(batch_router.storage_client, "download_mockup", lambda *a, **k: _png())
    monkeypatch.setattr(batch_router.storage_client, "delete_object", lambda *a, **k: None)

    def publish(d, **kw):
        calls["published"] += 1
        return {"image_url": "u"}
    monkeypatch.setattr(batch_router.publish, "publish_image", publish)
    monkeypatch.setattr(reviewed_copy.products_repo, "get_product",
                        lambda d, pid: type("P", (), {"categoryid": "SA"})())

    def tags(d, pid, rows):
        calls["tags"] = rows
        return rows
    monkeypatch.setattr(reviewed_copy.tags_repo, "replace_ai_tags", tags)
    return calls


def test_one_click_accept_saves_the_draft_the_card_showed(client, db, accepting):
    draft_id = _ready_draft(db, tags=[{"group": "work", "value": "zari", "confidence": 0.9}])
    r = client.post("/api/batch/1/accept", json={"approve_draft_id": draft_id})
    assert r.status_code == 200 and r.json()["warning"] is None
    assert accepting["published"] == 1
    assert {"title_detail": "Zari Border"} in db.product_writes
    assert any(w.get("description") == "Flowing drape." and w.get("description_source") == "ai_reviewed"
               for w in db.product_writes)
    assert [(t.group, t.value, t.source) for t in accepting["tags"]] == [("work", "zari", "ai_reviewed")]


def test_one_click_accept_keeps_what_a_person_wrote(client, db, accepting):
    db.products["BC1"].update(title_detail="Kantha Work", description="Hand stitched.", description_source="manual")
    draft_id = _ready_draft(db)
    r = client.post("/api/batch/1/accept", json={"approve_draft_id": draft_id})
    assert r.status_code == 200 and accepting["published"] == 1
    assert db.product_writes == [] and accepting["tags"] is None


@pytest.mark.parametrize("change", ["regenerated", "failed", "other product"])
def test_accept_of_text_no_longer_shown_publishes_nothing(client, db, accepting, change):
    draft_id = _ready_draft(db)
    row = db.rows[-1]
    if change == "regenerated":
        row["active"] = False
    elif change == "failed":
        row["status"] = repo.FAILED
    else:
        row["productid"] = "BC2"
    r = client.post("/api/batch/1/accept", json={"approve_draft_id": draft_id})
    assert r.status_code == 412 and accepting["published"] == 0 and db.product_writes == []


def test_review_accept_saves_description_and_tags(client, db, accepting):
    r = client.post("/api/batch/1/accept", json={
        "description": "Checked text.", "expected_description": None, "expected_reviewed_at": None,
        "description_edited": True, "title_detail": "Zari", "expected_title_detail": None, "title_detail_edited": True,
        "tags": [{"group": "work", "value": "zari", "source": "ai_reviewed", "confidence": 0.9}]})
    assert r.status_code == 200 and r.json()["warning"] is None
    assert any(w.get("description") == "Checked text." for w in db.product_writes)
    assert {"title_detail": "Zari"} in db.product_writes and accepting["tags"]


def test_slow_draft_never_blocks_approval(client, db, accepting):
    _request(db)    # still queued
    r = client.post("/api/batch/1/accept", json={"title_detail": "", "expected_title_detail": None})
    assert r.status_code == 200 and accepting["published"] == 1
