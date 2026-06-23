from mockup_generator.db import prompts_repo


class FakeTable:
    def __init__(self, sink, rows):
        self.sink, self._rows = sink, rows
    def select(self, *a): self.sink.append(("select", a)); return self
    def insert(self, payload): self.sink.append(("insert", payload)); return self
    def update(self, payload): self.sink.append(("update", payload)); return self
    def delete(self): self.sink.append(("delete",)); return self
    def eq(self, c, v): self.sink.append(("eq", c, v)); return self
    def order(self, c, **k): return self
    def limit(self, n): return self
    def execute(self):
        class R: data = self._rows
        return R()


class FakeClient:
    def __init__(self, rows): self.sink, self._rows = [], rows
    def table(self, name): self.sink.append(("table", name)); return FakeTable(self.sink, self._rows)


def test_create_default_clears_siblings_first():
    inserted = [{"prompt_id": 1, "categoryid": "SA", "label": "Studio",
                 "body": "b", "is_default": True}]
    c = FakeClient(inserted)
    p = prompts_repo.create(c, categoryid="SA", label="Studio", body="b", is_default=True)
    assert p.prompt_id == 1 and p.is_default is True
    # clearing siblings (update is_default=False) must occur before insert
    kinds = [s[0] for s in c.sink]
    assert kinds.index("update") < kinds.index("insert")


def test_list_by_category_maps_rows():
    rows = [{"prompt_id": 5, "categoryid": "SA", "label": "Default",
             "body": "x", "is_default": True}]
    c = FakeClient(rows)
    out = prompts_repo.list_by_category(c, "SA")
    assert out[0].label == "Default" and out[0].prompt_id == 5
