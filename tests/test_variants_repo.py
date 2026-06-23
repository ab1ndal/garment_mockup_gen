from mockup_generator.db import variants_repo


class _Q:
    def __init__(self, rows):
        self._rows = rows
        self.selected = None
        self.eqd = None

    def select(self, cols):
        self.selected = cols
        return self

    def eq(self, col, val):
        self.eqd = (col, val)
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


class _Db:
    def __init__(self, rows):
        self._q = _Q(rows)
        self.table_name = None

    def table(self, name):
        self.table_name = name
        return self._q


def test_list_colors_trims_dedups_drops_empty_and_sorts():
    rows = [
        {"color": "Grey "}, {"color": "grey"}, {"color": "Grey"},
        {"color": ""}, {"color": None}, {"color": "Black"},
        {"color": "  Red  "},
    ]
    db = _Db(rows)
    out = variants_repo.list_colors(db, "BC25001")
    assert db.table_name == "productsizecolors"
    assert db._q.eqd == ("productid", "BC25001")
    # case-insensitive dedup keeps first canonical spelling ("Grey "->"Grey")
    assert out == ["Black", "Grey", "Red"]
