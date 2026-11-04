import pytest
from postgrest.exceptions import APIError

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
    def neq(self, c, v): self.sink.append(("neq", c, v)); return self
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


def test_page_embeds_product_name_in_the_same_query():
    rows = [
        {"file_id": "a", "productid": "BC25001", "alpha": None, "filename": "a.png",
         "thumbnail_link": None, "status": "pending", "products": {"name": "Saree"}},
        {"file_id": "b", "productid": None, "alpha": None, "filename": "b.png",
         "thumbnail_link": None, "status": "pending", "products": None},
    ]
    c = FakeClient(FakeResp(rows, count=2))
    out, _ = repo.page(c, status="pending", offset=0, limit=20)
    assert [x for x in c.sink if x[0] == "table"] == [("table", "backfill_items")]  # one query
    select = next(x for x in c.sink if x[0] == "select")
    assert "products(name, categoryid)" in select[1][0]
    assert (out[0].product_name, out[0].product_found) == ("Saree", True)
    assert (out[1].product_name, out[1].product_found) == (None, False)


class FakeWorklist:
    """In-memory PostgREST: applies the chain's filters, order and range, and caps
    a response at 1000 rows, so paging is exercised rather than asserted on calls."""

    MAX_ROWS = 1000

    def __init__(self, rows):
        self.rows, self.requests = rows, 0

    def table(self, name):
        assert name == "backfill_items"
        return _WorklistQuery(self)


class _WorklistQuery:
    def __init__(self, db):
        self.db, self.preds, self.inner, self.sort, self.window = db, [], False, None, (0, 10**9)

    def select(self, cols, **k):
        self.inner = "products!inner" in cols
        return self

    def eq(self, col, val):
        if col == "products.categoryid":
            self.preds.append(lambda r: (r.get("products") or {}).get("categoryid") == val)
        else:
            self.preds.append(lambda r: r.get(col) == val)
        return self

    def neq(self, col, val):
        self.preds.append(lambda r: r.get(col) != val)
        return self

    def order(self, col):
        self.sort = col
        return self

    def range(self, lo, hi):
        self.window = (lo, hi)
        return self

    def execute(self):
        self.db.requests += 1
        rows = [r for r in self.db.rows if all(p(r) for p in self.preds)]
        if self.inner:
            rows = [r for r in rows if r.get("products")]
        rows.sort(key=lambda r: r[self.sort])
        lo, hi = self.window
        if lo > len(rows):
            raise APIError({"message": "Requested range not satisfiable", "code": "PGRST103",
                            "hint": None, "details": None})
        return FakeResp(rows[lo:min(hi + 1, lo + FakeWorklist.MAX_ROWS)], count=len(rows))


def _item(i, status="pending", category="SA"):
    products = {"name": f"P{i}", "categoryid": category} if category != "no-product" else None
    return {"file_id": f"f{i:05}", "productid": None if products is None else f"BC{i}",
            "alpha": None, "filename": f"{i:05}.png", "thumbnail_link": None,
            "status": status, "products": products}


def test_page_stays_inside_the_category_across_pages():
    # Saree items are spread through the catalogue: pages must be pages of sarees.
    rows = [_item(i, category="SA" if i % 3 == 0 else "KUR") for i in range(150)]
    db = FakeWorklist(rows)
    seen = []
    for offset in range(0, 60, 20):
        got, total = repo.page(db, status="pending", offset=offset, limit=20, category="SA")
        assert total == 50                                # the category, not the worklist
        assert all(r.categoryid == "SA" for r in got)
        seen += [r.file_id for r in got]
    assert seen == [f"f{i:05}" for i in range(0, 150, 3)]  # all of them, in order, once


def test_page_past_the_end_returns_real_total_not_an_error():
    # Another reviewer emptied the category while this one sat on page 3.
    db = FakeWorklist([_item(i) for i in range(5)])
    got, total = repo.page(db, status="pending", offset=40, limit=20, category="SA")
    assert (got, total) == ([], 5)


def test_page_reraises_other_database_errors():
    class Broken(FakeWorklist):
        def table(self, name):
            q = super().table(name)
            def boom():
                raise APIError({"message": "nope", "code": "XX000", "hint": None, "details": None})
            q.execute = boom
            return q
    with pytest.raises(APIError):
        repo.page(Broken([]), status="pending", offset=0, limit=20)


def test_page_category_respects_status():
    rows = [_item(1), _item(2, status="skipped"), _item(3, category="KUR")]
    got, total = repo.page(FakeWorklist(rows), status="skipped", offset=0, limit=20, category="SA")
    assert total == 1 and [r.file_id for r in got] == ["f00002"]


def test_page_category_with_nothing_is_empty():
    got, total = repo.page(FakeWorklist([_item(1)]), status="pending", offset=0, limit=20, category="GWN")
    assert (got, total) == ([], 0)


def test_page_without_category_keeps_items_with_no_product():
    rows = [_item(1), _item(2, category="no-product"), _item(3, category=None)]
    got, total = repo.page(FakeWorklist(rows), status="pending", offset=0, limit=20)
    assert total == 3 and {r.file_id for r in got} == {"f00001", "f00002", "f00003"}


def test_category_counts_reads_past_the_row_cap():
    rows = [_item(i, category="SA" if i % 2 else "KUR") for i in range(2500)]
    db = FakeWorklist(rows)
    out = repo.category_counts(db)
    assert out["pending"] == {"SA": 1250, "KUR": 1250}
    assert db.requests == 3


def test_category_counts_groups_by_status_and_skips_published_and_uncategorised():
    rows = [_item(1), _item(2), _item(3, status="skipped"), _item(4, category="KUR"),
            _item(5, status="published"), _item(6, category="no-product"), _item(7, category=None)]
    out = repo.category_counts(FakeWorklist(rows))
    assert out == {"pending": {"SA": 2, "KUR": 1}, "skipped": {"SA": 1}, "edit": {}, "regenerate": {}}


def test_category_counts_empty_worklist_makes_one_request():
    db = FakeWorklist([])
    assert repo.category_counts(db) == {"pending": {}, "skipped": {}, "edit": {}, "regenerate": {}}
    assert db.requests == 1
