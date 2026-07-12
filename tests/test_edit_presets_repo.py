from mockup_generator.db import edit_presets_repo as repo


class _Q:
    def __init__(self, store, name):
        self.store, self.name = store, name
        self._filters, self._payload, self._op = [], None, None
    def select(self, *a, **k): self._op = "select"; return self
    def insert(self, payload): self._op, self._payload = "insert", payload; return self
    def update(self, payload): self._op, self._payload = "update", payload; return self
    def delete(self): self._op = "delete"; return self
    def eq(self, col, val): self._filters.append((col, val)); return self
    def order(self, *a, **k): return self
    def limit(self, n): self._filters.append(("_limit", n)); return self
    def execute(self):
        self.store.setdefault("calls", []).append((self._op, self._payload, self._filters))
        if self._op == "insert":
            row = {"preset_id": 1, **self._payload}
            self.store["rows"].append(row)
            return type("R", (), {"data": [row]})()
        if self._op == "select":
            rows = self.store["rows"]
            for col, val in self._filters:
                if col == "is_default":
                    rows = [r for r in rows if r.get("is_default") == val]
            return type("R", (), {"data": rows})()
        return type("R", (), {"data": []})()


class _Db:
    def __init__(self):
        self.store = {"rows": []}
    def table(self, name):
        assert name == "edit_presets"
        return _Q(self.store, name)


def test_insert_returns_row():
    db = _Db()
    row = repo.insert(db, name="Studio", params={"bg": "white"},
                      is_default=False, created_by="u1")
    assert row["name"] == "Studio" and row["params"] == {"bg": "white"}


def test_get_default_none_when_empty():
    assert repo.get_default(_Db()) is None


def test_set_default_clears_then_sets():
    db = _Db()
    repo.set_default(db, 5)
    ops = [c[0] for c in db.store["calls"]]
    assert ops == ["update", "update"]          # clear others, then set target
