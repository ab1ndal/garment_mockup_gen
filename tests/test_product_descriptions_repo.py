from mockup_generator.db import product_descriptions_repo as repo


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
    def __init__(self, rows_by_table):
        self.sink, self._rows = [], rows_by_table
    def table(self, name):
        self.sink.append(("table", name))
        return FakeQuery(self.sink, self._rows.get(name, []))


def _update(sink):
    return next(s[1] for s in sink if s[0] == "update")


def test_save_reviewed_filters_on_expected():
    c = FakeClient({"products": [{"productid": "BC1"}]})
    ok = repo.save_reviewed(c, "BC1", "New text", expected_description="Old",
                            expected_reviewed_at="2026-10-24T10:00:00+00:00")
    assert ok is True
    payload = _update(c.sink)
    assert payload["description"] == "New text"
    assert payload["description_source"] == "ai_reviewed"
    assert payload["description_reviewed_at"].endswith("+00:00")
    assert ("eq", "productid", "BC1") in c.sink
    assert ("eq", "description", "Old") in c.sink
    assert ("eq", "description_reviewed_at", "2026-10-24T10:00:00+00:00") in c.sink


def test_save_reviewed_null_expected_uses_is_null():
    c = FakeClient({"products": [{"productid": "BC1"}]})
    repo.save_reviewed(c, "BC1", "t", expected_description=None, expected_reviewed_at=None)
    assert ("is_", "description", "null") in c.sink
    assert ("is_", "description_reviewed_at", "null") in c.sink


def test_save_reviewed_returns_false_when_no_row():
    c = FakeClient({"products": []})
    assert repo.save_reviewed(c, "BC1", "t", expected_description=None,
                              expected_reviewed_at=None) is False


def test_get_current_missing_returns_none():
    assert repo.get_current(FakeClient({}), "BC1") is None


def test_get_current_maps_row():
    c = FakeClient({"products": [{"description": "d", "description_source": "manual",
                                  "description_reviewed_at": None}]})
    cur = repo.get_current(c, "BC1")
    assert (cur.description, cur.source, cur.reviewed_at) == ("d", "manual", None)


def test_get_facts_dedupes_colours_and_sizes():
    c = FakeClient({
        "products": [{"productid": "BC1", "name": "Georgette Saree", "fabric": "Georgette",
                      "description": None, "categories": {"name": "Saree"}}],
        "productsizecolors": [
            {"color": "Red ", "size": "Free Size"}, {"color": "red", "size": "free size"},
            {"color": "Blue", "size": None}, {"color": None, "size": "XL"},
        ],
    })
    f = repo.get_facts(c, "BC1")
    assert f.name == "Georgette Saree" and f.category_name == "Saree"
    assert f.colours == ["Blue", "Red"]
    assert f.sizes == ["Free Size", "XL"]


def test_get_facts_missing_returns_none():
    assert repo.get_facts(FakeClient({}), "BC1") is None
