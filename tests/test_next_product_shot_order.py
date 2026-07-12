from mockup_generator.db.productimages_repo import next_product_shot_order


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def execute(self):
        return type("R", (), {"data": self._rows})()


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows
    def table(self, name):
        assert name == "productimages"
        return _FakeQuery(self._rows)


def test_empty_band_defaults_to_20():
    assert next_product_shot_order(_FakeDb([]), "P1") == 20


def test_appends_after_existing_band_max():
    assert next_product_shot_order(_FakeDb([{"displayorder": 21}]), "P1") == 22


def test_null_displayorder_is_safe():
    assert next_product_shot_order(_FakeDb([{"displayorder": None}]), "P1") == 20
