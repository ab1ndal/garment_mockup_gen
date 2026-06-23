from mockup_generator.db import products_repo


class FakeQuery:
    def __init__(self, sink, rows):
        self.sink, self._rows = sink, rows
    def select(self, *a, **k): self.sink.append(("select", a)); return self
    def eq(self, c, v): self.sink.append(("eq", c, v)); return self
    def gte(self, c, v): self.sink.append(("gte", c, v)); return self
    def lte(self, c, v): self.sink.append(("lte", c, v)); return self
    def order(self, c, **k): self.sink.append(("order", c)); return self
    def range(self, lo, hi): self.sink.append(("range", lo, hi)); return self
    def limit(self, n): self.sink.append(("limit", n)); return self
    def execute(self):
        class R: data = self._rows
        return R()


class FakeClient:
    def __init__(self, rows): self.sink, self._rows = [], rows
    def table(self, name): self.sink.append(("table", name)); return FakeQuery(self.sink, self._rows)


def test_list_products_pending_and_range_filters():
    rows = [{"productid": "BC25001", "name": "Silk-Saree", "categoryid": "SA",
             "category_name": "Saree", "producturl": "http://d", "base_mockup": False}]
    c = FakeClient(rows)
    out = products_repo.list_products(c, category="SA", id_start="BC25001",
                                      id_end="BC251000", pending=True, limit=20, offset=0)
    assert out[0].productid == "BC25001"
    assert ("table", "product_browse") in c.sink
    assert ("eq", "categoryid", "SA") in c.sink
    assert ("eq", "base_mockup", False) in c.sink
    assert ("gte", "id_key", 25_000_001) in c.sink
    assert ("lte", "id_key", 25_001_000) in c.sink
    assert ("range", 0, 19) in c.sink


def test_list_products_single_id_exact():
    c = FakeClient([])
    products_repo.list_products(c, product_id="BC25007", pending=False)
    assert ("eq", "productid", "BC25007") in c.sink
    # pending=False must NOT add a base_mockup filter
    assert all(s[0] != "eq" or s[1] != "base_mockup" for s in c.sink)
