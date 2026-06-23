from mockup_generator.db import mockups_repo


class _Upd:
    def __init__(self, sink):
        self.sink = sink

    def update(self, payload):
        self.sink["payload"] = payload
        return self

    def eq(self, col, val):
        self.sink["eq"] = (col, val)
        return self

    def execute(self):
        self.sink["executed"] = True
        return type("R", (), {"data": []})()


class _Db:
    def __init__(self, sink):
        self.sink = sink

    def table(self, name):
        self.sink["table"] = name
        return _Upd(self.sink)


def test_set_base_mockup_updates_flag():
    sink = {}
    mockups_repo.set_base_mockup(_Db(sink), "BC25001")
    assert sink["table"] == "mockups"
    assert sink["payload"] == {"base_mockup": True}
    assert sink["eq"] == ("productid", "BC25001")
    assert sink["executed"] is True
