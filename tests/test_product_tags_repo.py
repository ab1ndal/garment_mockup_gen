from mockup_generator.db import product_tags_repo as repo
from mockup_generator.db.product_tags_repo import TagRow


class FakeQuery:
    def __init__(self, sink, rows):
        self.sink, self._rows = sink, rows
    def select(self, cols): self.sink.append(("select", cols)); return self
    def eq(self, c, v): self.sink.append(("eq", c, v)); return self
    def execute(self):
        class R: data = self._rows
        return R()


class FakeClient:
    def __init__(self, rows):
        self.sink, self._rows = [], rows
    def table(self, name):
        self.sink.append(("table", name))
        return FakeQuery(self.sink, self._rows)
    def rpc(self, name, params):
        self.sink.append(("rpc", name, params))
        return FakeQuery(self.sink, self._rows)


def test_get_tags_maps_rows():
    c = FakeClient([{"tag_group": "occasion", "value": "wedding", "source": "manual", "confidence": None}])
    assert repo.get_tags(c, "BC1") == [TagRow("occasion", "wedding", "manual", None)]
    assert ("table", "product_tags") in c.sink and ("eq", "productid", "BC1") in c.sink


def test_replace_ai_tags_calls_rpc():
    c = FakeClient([{"productid": "BC1", "tag_group": "work", "value": "zari",
                     "source": "ai_reviewed", "confidence": 0.9}])
    out = repo.replace_ai_tags(c, "BC1", [TagRow("work", "zari", "ai_reviewed", 0.9),
                                          TagRow("occasion", "party", "manual", None)])
    assert ("rpc", "replace_product_tags", {
        "p_productid": "BC1", "p_scope": "ai",
        "p_tags": [{"tag_group": "work", "value": "zari", "source": "ai_reviewed", "confidence": 0.9},
                   {"tag_group": "occasion", "value": "party", "source": "manual", "confidence": None}],
    }) in c.sink
    assert out == [TagRow("work", "zari", "ai_reviewed", 0.9)]
