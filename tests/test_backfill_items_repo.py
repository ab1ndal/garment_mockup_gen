from mockup_generator.db import backfill_items_repo as repo


class FakeResp:
    def __init__(self, data, count=None):
        self.data, self.count = data, count


class FakeQuery:
    """Records the supabase chain and returns a preset response on execute()."""

    def __init__(self, sink, resp):
        self.sink, self._resp = sink, resp

    def select(self, *a, **k): self.sink.append(("select", a, k)); return self
    def eq(self, c, v): self.sink.append(("eq", c, v)); return self
    def in_(self, c, v): self.sink.append(("in", c, v)); return self
    def order(self, c, **k): self.sink.append(("order", c)); return self
    def range(self, lo, hi): self.sink.append(("range", lo, hi)); return self
    def limit(self, n): self.sink.append(("limit", n)); return self
    def update(self, payload): self.sink.append(("update", payload)); return self
    def upsert(self, rows, **k): self.sink.append(("upsert", rows, k)); return self
    def execute(self): return self._resp


class FakeClient:
    def __init__(self, resp):
        self.sink, self._resp = [], resp
    def table(self, name):
        self.sink.append(("table", name)); return FakeQuery(self.sink, self._resp)


def test_page_filters_orders_and_returns_total():
    rows = [{"file_id": "a", "productid": "BC25001", "alpha": None,
             "filename": "BC25001.png", "thumbnail_link": "l-a", "status": "pending"}]
    c = FakeClient(FakeResp(rows, count=7))
    out, total = repo.page(c, status="pending", offset=0, limit=20)
    assert total == 7 and out[0].file_id == "a" and out[0].thumbnail_link == "l-a"
    assert ("table", "backfill_items") in c.sink
    assert ("eq", "status", "pending") in c.sink
    assert ("order", "filename") in c.sink
    assert ("range", 0, 19) in c.sink


def test_transition_returns_true_when_row_updated():
    c = FakeClient(FakeResp([{"file_id": "a"}]))
    assert repo.transition(c, file_id="a", expect="pending", to="skipped") is True
    # guarded on BOTH file_id and the expected current status
    assert ("eq", "file_id", "a") in c.sink
    assert ("eq", "status", "pending") in c.sink
    upd = next(p for tag, p in [(s[0], s[1]) for s in c.sink if s[0] == "update"])
    assert upd["status"] == "skipped"


def test_transition_returns_false_when_no_row():
    c = FakeClient(FakeResp([]))   # nobody matched expect status -> lost the race
    assert repo.transition(c, file_id="a", expect="pending", to="skipped") is False


def test_upsert_many_uses_file_id_conflict():
    c = FakeClient(FakeResp([{"file_id": "a"}, {"file_id": "b"}]))
    n = repo.upsert_many(c, [{"file_id": "a"}, {"file_id": "b"}])
    assert n == 2
    up = next(s for s in c.sink if s[0] == "upsert")
    assert up[2]["on_conflict"] == "file_id"


def test_upsert_many_empty_is_noop():
    c = FakeClient(FakeResp([]))
    assert repo.upsert_many(c, []) == 0
    assert c.sink == []   # no DB call at all
