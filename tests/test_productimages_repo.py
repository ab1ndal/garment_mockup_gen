from mockup_generator.db import productimages_repo


class _CountQ:
    def __init__(self, count):
        self._count = count

    def select(self, cols, count=None):
        return self

    def eq(self, col, val):
        return self

    def execute(self):
        return type("R", (), {"data": [], "count": self._count})()


class _InsertQ:
    def __init__(self, sink):
        self.sink = sink

    def insert(self, payload):
        self.sink["payload"] = payload
        return self

    def execute(self):
        return type("R", (), {"data": [{"imageid": 1, **self.sink["payload"]}]})()


class _InsertDb:
    """Two table() calls: first the count query, second the insert."""
    def __init__(self, sink, count):
        self.sink = sink
        self._count = count
        self._calls = 0

    def table(self, name):
        self.sink["table"] = name
        self._calls += 1
        return _CountQ(self._count) if self._calls == 1 else _InsertQ(self.sink)


def test_insert_computes_displayorder_from_count_and_sets_caption():
    sink = {}
    row = productimages_repo.insert(
        _InsertDb(sink, count=2), productid="BC1", imageurl="https://public/x.png", caption="Red"
    )
    assert sink["table"] == "productimages"
    p = sink["payload"]
    assert p == {"productid": "BC1", "imageurl": "https://public/x.png",
                 "displayorder": 2, "caption": "Red", "phototheme": "Default"}
    assert row["imageid"] == 1


def test_insert_omits_caption_when_none_and_honors_explicit_displayorder():
    sink = {}
    productimages_repo.insert(
        _InsertDb(sink, count=99), productid="BC1", imageurl="u", displayorder=5
    )
    assert sink["payload"] == {"productid": "BC1", "imageurl": "u",
                               "displayorder": 5, "phototheme": "Default"}


def test_insert_carries_explicit_theme():
    sink = {}
    productimages_repo.insert(
        _InsertDb(sink, count=0), productid="BC1", imageurl="u",
        caption="Red", theme="Studio·9:16",
    )
    assert sink["payload"]["phototheme"] == "Studio·9:16"
    assert sink["payload"]["caption"] == "Red"


# ---- list_for / delete_for: capture the query chain (eq vs is_ for NULL) ----

class _ChainQ:
    """Records select/eq/is_/delete calls; returns rows on execute."""
    def __init__(self, sink, rows):
        self.sink = sink
        self._rows = rows

    def select(self, cols):
        self.sink.setdefault("ops", []).append(("select", cols))
        return self

    def delete(self):
        self.sink.setdefault("ops", []).append(("delete",))
        return self

    def eq(self, col, val):
        self.sink.setdefault("filters", []).append(("eq", col, val))
        return self

    def is_(self, col, val):
        self.sink.setdefault("filters", []).append(("is_", col, val))
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


class _ChainDb:
    def __init__(self, sink, rows=None):
        self.sink = sink
        self._rows = rows or []

    def table(self, name):
        self.sink["table"] = name
        return _ChainQ(self.sink, self._rows)


def test_list_for_filters_by_productid_and_color():
    sink = {}
    rows = productimages_repo.list_for(
        _ChainDb(sink, rows=[{"imageid": 7, "imageurl": "https://public/old.png"}]),
        "BC1", "Red",
    )
    assert sink["table"] == "productimages"
    assert ("eq", "productid", "BC1") in sink["filters"]
    assert ("eq", "caption", "Red") in sink["filters"]
    assert ("eq", "phototheme", "Default") in sink["filters"]  # default theme
    assert rows[0]["imageurl"] == "https://public/old.png"


def test_list_for_uses_is_null_when_caption_none():
    sink = {}
    productimages_repo.list_for(_ChainDb(sink), "BC1", None)
    assert ("eq", "productid", "BC1") in sink["filters"]
    assert ("is_", "caption", "null") in sink["filters"]
    assert ("eq", "phototheme", "Default") in sink["filters"]


def test_list_for_filters_by_explicit_theme():
    sink = {}
    productimages_repo.list_for(_ChainDb(sink), "BC1", "Red", "Studio·9:16")
    assert ("eq", "caption", "Red") in sink["filters"]
    assert ("eq", "phototheme", "Studio·9:16") in sink["filters"]


def test_delete_for_filters_by_productid_and_color():
    sink = {}
    productimages_repo.delete_for(_ChainDb(sink), "BC1", "Red")
    assert ("delete",) in sink["ops"]
    assert ("eq", "productid", "BC1") in sink["filters"]
    assert ("eq", "caption", "Red") in sink["filters"]
    assert ("eq", "phototheme", "Default") in sink["filters"]


def test_delete_for_uses_is_null_when_caption_none():
    sink = {}
    productimages_repo.delete_for(_ChainDb(sink), "BC1", None)
    assert ("delete",) in sink["ops"]
    assert ("is_", "caption", "null") in sink["filters"]
    assert ("eq", "phototheme", "Default") in sink["filters"]


def test_delete_for_filters_by_explicit_theme():
    sink = {}
    productimages_repo.delete_for(_ChainDb(sink), "BC1", "Red", "Studio")
    assert ("eq", "phototheme", "Studio") in sink["filters"]
