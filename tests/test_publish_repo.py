from mockup_generator.db import backfill_items_repo, publish_repo


class _Rpc:
    """Records the one rpc() call and returns ``data`` from execute()."""

    def __init__(self, data):
        self.data = data
        self.calls = []

    def rpc(self, fn, params=None):
        self.calls.append((fn, params))
        return self

    def execute(self):
        return type("Resp", (), {"data": self.data})()


def test_write_rows_is_one_rpc_with_every_column():
    db = _Rpc(1030)
    vid = publish_repo.write_rows(
        db, productid="BC1", imageurl="https://u/x.webp", color=None, theme="Default",
        displayorder=3, prompt_text=None, prompt_id=None, created_by="u1",
    )
    assert vid == 1030
    assert db.calls == [("publish_mockup_rows", {
        "p_productid": "BC1", "p_imageurl": "https://u/x.webp", "p_color": None,
        "p_theme": "Default", "p_displayorder": 3, "p_prompt_text": None,
        "p_prompt_id": None, "p_created_by": "u1",
    })]


def test_backfill_counts_is_one_rpc_and_fills_missing_statuses():
    db = _Rpc([{"status": "pending", "n": 5}, {"status": "published", "n": 9}])
    assert backfill_items_repo.counts(db) == {
        "pending": 5, "skipped": 0, "edit": 0, "regenerate": 0,  # published is not a tab
    }
    assert db.calls == [("backfill_status_counts", None)]
