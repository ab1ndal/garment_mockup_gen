import json
from pathlib import Path

import pytest
from anthropic.types import Message, TextBlock, Usage
from fastapi.testclient import TestClient

from backend.auth import CurrentUser, get_current_user
from backend.deps import get_db
from backend.main import app
from backend.routers import backfill as bf
from backend.routers import batch as batch_router
from backend.routers import generate as generate_router
from backend.routers import titles as titles_router
from backend.services import title_detail as service
from mockup_generator.db import product_titles_repo as repo
from mockup_generator.db.product_titles_repo import CurrentTitle, TitlePreview
from mockup_generator.db.profiles_repo import Profile
from mockup_generator.describe import draft as d
from mockup_generator.describe.title_detail import MAX_TITLE_DETAIL_CHARS, normalize, should_save
from mockup_generator.prompts.describe_prompt import ProductFacts, build_prompt, load_schema

_FACTS = ProductFacts("BC1", "Silk-Saree", "SA", "Saree", "Silk", ["Red"], ["Free Size"], None)
_PROMPT = Path(__file__).resolve().parents[1] / "mockup_generator" / "prompts" / "title_detail.md"


# --- the rules ---

@pytest.mark.parametrize("raw,out", [
    ("Zari Border", "Zari Border"),
    ("with Zari Border", "Zari Border"),
    ("WITH  Zari   Border ", "Zari Border"),
    ("with", ""),
    ("Without Border", "Without Border"),  # only the word "with"
    (None, ""),
])
def test_normalize(raw, out):
    assert normalize(raw) == out


@pytest.mark.parametrize("detail,expected,edited,saves", [
    ("AI Text", None, False, True),         # AI fills a blank
    ("AI Text", "", False, True),
    ("AI Text", "   ", False, True),
    ("AI Text", "Human Text", False, False),  # AI never replaces existing text
    ("Edited", "Human Text", True, True),   # the reviewer's edit does
    ("", "Human Text", True, True),         # including clearing it
    ("", "Human Text", False, False),       # an unedited empty value never clears
    ("Human Text", "Human Text", True, False),  # nothing changed
    ("", None, True, False),
])
def test_should_save(detail, expected, edited, saves):
    assert should_save(detail, expected, edited) is saves


# --- repo ---

class FakeQuery:
    def __init__(self, sink, rows):
        self.sink, self._rows = sink, rows
    def select(self, cols): self.sink.append(("select", cols)); return self
    def update(self, payload): self.sink.append(("update", payload)); return self
    def eq(self, c, v): self.sink.append(("eq", c, v)); return self
    def is_(self, c, v): self.sink.append(("is_", c, v)); return self
    def limit(self, n): return self
    def execute(self):
        class R: data = self._rows
        return R()


class FakeClient:
    def __init__(self, rows=(), rpc_data=None):
        self.sink, self._rows, self._rpc_data = [], list(rows), rpc_data
        self.rpc_calls = []
    def table(self, name):
        self.sink.append(("table", name))
        return FakeQuery(self.sink, self._rows)
    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        return FakeQuery(self.sink, self._rpc_data)


def test_save_writes_only_title_detail_and_guards_on_expected():
    c = FakeClient(rows=[{"productid": "BC1"}])
    assert repo.save_reviewed(c, "BC1", "Zari Border", expected="Old") is True
    assert ("update", {"title_detail": "Zari Border"}) in c.sink
    assert ("eq", "productid", "BC1") in c.sink
    assert ("eq", "title_detail", "Old") in c.sink


def test_save_null_expected_uses_is_null():
    c = FakeClient(rows=[{"productid": "BC1"}])
    repo.save_reviewed(c, "BC1", "Zari Border", expected=None)
    assert ("is_", "title_detail", "null") in c.sink


def test_save_empty_clears_to_null():
    c = FakeClient(rows=[{"productid": "BC1"}])
    repo.save_reviewed(c, "BC1", "", expected="Old")
    assert ("update", {"title_detail": None}) in c.sink


def test_save_returns_false_when_someone_changed_it():
    assert repo.save_reviewed(FakeClient(rows=[]), "BC1", "x", expected=None) is False


def test_preview_asks_the_database():
    c = FakeClient(rpc_data={"title": "Red Silk Saree with Zari", "duplicate": True})
    assert repo.preview(c, _FACTS, "Zari") == TitlePreview("Red Silk Saree with Zari", True)
    assert c.rpc_calls == [("preview_product_title", {
        "p_fabric": "Silk", "p_categoryid": "SA", "p_detail": "Zari",
        "p_colours": ["Red"], "p_exclude_productid": "BC1"})]


def test_preview_without_detail_is_the_name_and_skips_the_rpc():
    c = FakeClient()
    assert repo.preview(c, _FACTS, "") == TitlePreview("Silk Saree", False)
    assert c.rpc_calls == []


# --- saving after a publish ---

@pytest.fixture
def saved(monkeypatch):
    calls = {"result": True}

    def save(db, pid, text, *, expected):
        calls["saved"] = (pid, text, expected)
        return calls["result"]
    monkeypatch.setattr(service.titles_repo, "save_reviewed", save)
    return calls


def test_ai_draft_fills_an_empty_detail(saved):
    assert service.save_title_detail(None, "BC1", "with Zari Border", None, False) is None
    assert saved["saved"] == ("BC1", "Zari Border", None)


def test_ai_draft_never_overwrites_a_human_detail(saved):
    assert service.save_title_detail(None, "BC1", "AI Text", "Human Text", False) is None
    assert "saved" not in saved


def test_reviewer_edit_overwrites(saved):
    assert service.save_title_detail(None, "BC1", "Edited", "Human Text", True) is None
    assert saved["saved"] == ("BC1", "Edited", "Human Text")


def test_change_since_load_warns_and_keeps_the_text(saved):
    saved["result"] = False
    warning = service.save_title_detail(None, "BC1", "Edited", "Old", True)
    assert "changed since you opened it" in warning and "Edited" in warning


def test_no_field_sent_saves_nothing(saved):
    assert service.save_title_detail(None, "BC1", None, "Human Text", True) is None
    assert "saved" not in saved


def test_over_limit_warns_and_does_not_save(saved):
    text = "x" * (MAX_TITLE_DETAIL_CHARS + 1)
    assert "over 60" in service.save_title_detail(None, "BC1", text, None, True)
    assert "saved" not in saved


def test_exactly_at_limit_saves(saved):
    text = "x" * MAX_TITLE_DETAIL_CHARS
    assert service.save_title_detail(None, "BC1", text, None, True) is None
    assert saved["saved"][1] == text


def test_database_failure_is_a_warning(monkeypatch):
    def boom(*a, **k): raise RuntimeError("down")
    monkeypatch.setattr(service.titles_repo, "save_reviewed", boom)
    assert "could not be saved" in service.save_title_detail(None, "BC1", "Zari", None, True)


def test_missing_warning_only_without_a_detail(monkeypatch):
    monkeypatch.setattr(service.titles_repo, "get_current",
                        lambda db, pid: CurrentTitle(None, "Silk Saree"))
    assert '"Silk Saree"' in service.missing_title_warning(None, "BC1")
    monkeypatch.setattr(service.titles_repo, "get_current",
                        lambda db, pid: CurrentTitle("Zari", "Silk Saree with Zari"))
    assert service.missing_title_warning(None, "BC1") is None


# --- draft output ---

def _msg(data):
    return Message.model_construct(
        id="m", type="message", role="assistant", model="claude-opus-5-5",
        content=[TextBlock(type="text", text=json.dumps(data))], stop_reason="end_turn",
        stop_sequence=None, usage=Usage(input_tokens=1, output_tokens=1))


class FakeAnthropic:
    def __init__(self, data):
        self._data = data
        self.messages = self
    def create(self, **kw):
        return _msg(self._data)


def _draft(**fields):
    data = {"description": "Flowing drape.", "includes": "saree", "uncertain": [], "conflicts": [], **fields}
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", (4, 4)).save(buf, format="PNG")
    return d.draft_description(_FACTS, [buf.getvalue()], None, client=FakeAnthropic(data))


def test_draft_reads_title_fields():
    out = _draft(title_detail="with Zari Border", colour_mismatch="Recorded Off White, photo is magenta.")
    assert out.title_detail == "Zari Border"
    assert out.title_detail_note == ""
    assert out.colour_mismatch == "Recorded Off White, photo is magenta."


def test_draft_over_limit_is_left_out_with_a_note_but_keeps_the_description():
    long = "Zari " * 13
    out = _draft(title_detail=long)
    assert out.title_detail == ""
    assert "over 60" in out.title_detail_note
    assert out.description == "Flowing drape."


# --- prompt and schema ---

def test_schema_requires_title_fields_without_max_length():
    schema = load_schema()
    for name in ("title_detail", "colour_mismatch"):
        assert name in schema["required"]
        assert schema["properties"][name] == {"type": "string"}


def test_prompt_has_title_rules_for_any_category():
    for cat in (dict(categoryid="SA", category_name="Saree"), dict(categoryid="C-KP", category_name="Kids Kurta Pajama")):
        p = build_prompt(ProductFacts("BC1", "n", cat["categoryid"], cat["category_name"], "Silk", [], [], None))
        assert 'TITLE DETAIL ("title_detail")' in p and 'COLOUR CHECK ("colour_mismatch")' in p


def test_prompt_examples_are_within_limit():
    examples_line = _PROMPT.read_text("utf-8").split("use only terms that fit this garment.\n")[1].split("\n")[0]
    examples = [e.strip() for e in examples_line.split(";")]
    assert len(examples) > 50
    assert all(0 < len(e) <= MAX_TITLE_DETAIL_CHARS and not e.lower().startswith("with") for e in examples)


# --- routes ---

@pytest.fixture
def client():
    u = CurrentUser(id="u1", email="a@b.c", role="user",
                    profile=Profile(id="u1", email="a@b.c", role="user", is_active=True))
    app.dependency_overrides[get_current_user] = lambda: u
    app.dependency_overrides[get_db] = lambda: object()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def test_get_title(client, monkeypatch):
    monkeypatch.setattr(titles_router.titles_repo, "get_current",
                        lambda db, pid: CurrentTitle("Zari", "Silk Saree with Zari"))
    assert client.get("/api/titles/BC1").json() == {"title_detail": "Zari", "display_title": "Silk Saree with Zari"}


def test_get_title_404(client, monkeypatch):
    monkeypatch.setattr(titles_router.titles_repo, "get_current", lambda db, pid: None)
    assert client.get("/api/titles/BC9").status_code == 404


def test_preview_route_normalizes(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(titles_router.descriptions_repo, "get_facts", lambda db, pid: _FACTS)

    def preview(db, facts, detail):
        seen["detail"] = detail
        return TitlePreview("t", True)
    monkeypatch.setattr(titles_router.titles_repo, "preview", preview)
    r = client.get("/api/titles/BC1/preview", params={"detail": "with Zari"})
    assert r.json() == {"title": "t", "duplicate": True}
    assert seen["detail"] == "Zari"


def _png():
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", (4, 4)).save(buf, format="PNG")
    return buf.getvalue()


def test_backfill_approve_saves_title_without_a_description_draft(client, monkeypatch, saved):
    monkeypatch.setattr(bf.items_repo, "transition", lambda db, **kw: True)
    monkeypatch.setattr(bf.productimages_repo, "next_display_order", lambda db, pid: 0)
    monkeypatch.setattr(bf.drive_client, "download_file", lambda fid: _png())
    monkeypatch.setattr(bf.publish, "publish_image", lambda db, **kw: {"image_url": "u", "variation_id": 1})
    monkeypatch.setattr(bf.drive_client, "ensure_subfolder", lambda root, name: "A")
    monkeypatch.setattr(bf.drive_client, "move_file", lambda fid, parent: None)
    r = client.post("/api/backfill/approve", json={
        "file_id": "f1", "productid": "BC1", "title_detail": "Zari", "expected_title_detail": None})
    assert r.status_code == 200 and r.json()["title_warning"] is None
    assert saved["saved"] == ("BC1", "Zari", None)


def test_backfill_title_saves_when_the_description_is_stale(client, monkeypatch, saved):
    monkeypatch.setattr(bf.items_repo, "transition", lambda db, **kw: True)
    monkeypatch.setattr(bf.productimages_repo, "next_display_order", lambda db, pid: 0)
    monkeypatch.setattr(bf.drive_client, "download_file", lambda fid: _png())
    monkeypatch.setattr(bf.publish, "publish_image", lambda db, **kw: {"image_url": "u", "variation_id": 1})
    monkeypatch.setattr(bf.drive_client, "ensure_subfolder", lambda root, name: "A")
    monkeypatch.setattr(bf.drive_client, "move_file", lambda fid, parent: None)
    monkeypatch.setattr(bf.descriptions_repo, "save_reviewed", lambda *a, **k: False)
    r = client.post("/api/backfill/approve", json={
        "file_id": "f1", "productid": "BC1", "description": "d", "title_detail": "Zari"})
    assert r.json()["description_warning"] and saved["saved"] == ("BC1", "Zari", None)


@pytest.fixture
def batch_accepting(monkeypatch):
    row = type("R", (), {"productid": "BC1", "storage_path": "p", "color": None, "aspect_ratio": "1:1",
                         "prompt_text": "t"})()
    monkeypatch.setattr(batch_router.items_repo, "get", lambda db, i: row)
    monkeypatch.setattr(batch_router.items_repo, "transition", lambda db, **kw: True)
    monkeypatch.setattr(batch_router.storage_client, "download_mockup", lambda *a, **k: _png())
    monkeypatch.setattr(batch_router.storage_client, "delete_object", lambda *a, **k: None)
    monkeypatch.setattr(batch_router.publish, "publish_image", lambda db, **kw: {"image_url": "u"})


def test_batch_review_accept_saves_title(client, batch_accepting, saved):
    r = client.post("/api/batch/1/accept", json={"title_detail": "Zari", "expected_title_detail": None})
    assert r.status_code == 200 and r.json()["warning"] is None
    assert saved["saved"] == ("BC1", "Zari", None)


def test_batch_one_click_accept_warns_when_no_detail(client, batch_accepting, saved, monkeypatch):
    monkeypatch.setattr(service.titles_repo, "get_current", lambda db, pid: CurrentTitle(None, "Silk Saree"))
    r = client.post("/api/batch/1/accept", json={})
    assert r.status_code == 200 and "no title detail" in r.json()["warning"]
    assert "saved" not in saved


@pytest.fixture
def generate_publishing(monkeypatch):
    monkeypatch.setattr(generate_router.publish, "publish_image",
                        lambda db, **kw: {"image_url": "u", "variation_id": 1})


def test_generate_approve_saves_title(client, generate_publishing, saved):
    r = client.post("/api/generate/approve",
                    data={"productid": "BC1", "title_detail": "Zari", "title_detail_edited": "false"},
                    files={"image": ("m.png", _png(), "image/png")})
    assert r.status_code == 200 and r.json()["title_warning"] is None
    assert saved["saved"] == ("BC1", "Zari", None)


def test_generate_approve_form_can_clear_a_detail(client, generate_publishing, saved):
    r = client.post("/api/generate/approve",
                    data={"productid": "BC1", "title_detail": "", "expected_title_detail": "Old",
                          "title_detail_edited": "true"},
                    files={"image": ("m.png", _png(), "image/png")})
    assert r.status_code == 200
    assert saved["saved"] == ("BC1", "", "Old")


def test_generate_approve_without_title_fields_saves_nothing(client, generate_publishing, saved):
    r = client.post("/api/generate/approve", data={"productid": "BC1"},
                    files={"image": ("m.png", _png(), "image/png")})
    assert r.status_code == 200
    assert "saved" not in saved
