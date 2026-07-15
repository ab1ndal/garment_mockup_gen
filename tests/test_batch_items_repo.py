from mockup_generator.db import batch_items_repo as repo


class FakeResp:
    def __init__(self, data, count=None):
        self.data, self.count = data, count


class FakeQuery:
    def __init__(self, sink, resp):
        self.sink, self._resp = sink, resp

    def select(self, *a, **k): self.sink.append(("select", a, k)); return self
    def eq(self, c, v): self.sink.append(("eq", c, v)); return self
    def in_(self, c, v): self.sink.append(("in", c, v)); return self
    def order(self, c, **k): self.sink.append(("order", c, k)); return self
    def range(self, lo, hi): self.sink.append(("range", lo, hi)); return self
    def limit(self, n): self.sink.append(("limit", n)); return self
    def update(self, payload): self.sink.append(("update", payload)); return self
    def insert(self, rows): self.sink.append(("insert", rows)); return self
    def execute(self): return self._resp


class FakeClient:
    def __init__(self, resp):
        self.sink, self._resp = [], resp
    def table(self, name):
        self.sink.append(("table", name)); return FakeQuery(self.sink, self._resp)


def _raw(id=1, status="queued"):
    return {"id": id, "batch_id": "b1", "productid": "BC25001", "color": "Red",
            "image_ids": ["f1", "f2"], "prompt_text": "p", "status": status,
            "drive_file_id": None, "thumbnail_link": None, "error": None,
            "model": "m", "resolution": "4K", "aspect_ratio": "1:1"}


def test_page_filters_by_statuses_and_returns_total():
    c = FakeClient(FakeResp([_raw()], count=5))
    rows, total = repo.page(c, statuses=[repo.QUEUED, repo.GENERATING], offset=0, limit=20)
    assert total == 5 and rows[0].id == 1 and rows[0].image_ids == ["f1", "f2"]
    assert ("table", "batch_items") in c.sink
    assert ("in", "status", [repo.QUEUED, repo.GENERATING]) in c.sink
    assert ("range", 0, 19) in c.sink


def test_transition_guards_on_expected_status_and_merges_fields():
    c = FakeClient(FakeResp([{"id": 1}]))
    assert repo.transition(c, item_id=1, expect=repo.GENERATING, to=repo.READY,
                           drive_file_id="drv", thumbnail_link="lnk") is True
    assert ("eq", "id", 1) in c.sink
    assert ("eq", "status", repo.GENERATING) in c.sink
    upd = next(p for tag, p in [(s[0], s[1]) for s in c.sink if s[0] == "update"])
    assert upd["status"] == repo.READY and upd["drive_file_id"] == "drv"
    assert upd["thumbnail_link"] == "lnk"


def test_transition_returns_false_when_no_row():
    c = FakeClient(FakeResp([]))
    assert repo.transition(c, item_id=1, expect=repo.READY, to=repo.PUBLISHED) is False


def test_insert_many_empty_is_noop():
    c = FakeClient(FakeResp([]))
    assert repo.insert_many(c, []) == 0
    assert c.sink == []


def test_reset_orphaned_moves_generating_to_queued():
    c = FakeClient(FakeResp([{"id": 1}, {"id": 2}]))
    assert repo.reset_orphaned_generating(c) == 2
    upd = next(p for tag, p in [(s[0], s[1]) for s in c.sink if s[0] == "update"])
    assert upd["status"] == repo.QUEUED
    assert ("eq", "status", repo.GENERATING) in c.sink
