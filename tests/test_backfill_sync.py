from mockup_generator.integrations import drive_client
from mockup_generator.services import backfill_sync


def _item(fid, name="BC25001.png", pid="BC25001"):
    return {"productid": pid, "alpha": None, "file_id": fid, "name": name,
            "subfolder_id": None, "subfolder_name": None, "thumbnail_link": f"l-{fid}"}


def test_scan_maps_root_and_reserved_folders_to_status(monkeypatch):
    monkeypatch.setattr(drive_client, "scan_folder_of_folders",
                        lambda root: [_item("a"), _item("b", "BC25002.png", "BC25002")])

    def fake_bucket(root, name):
        return {
            drive_client.SKIPPED_FOLDER: [_item("s", "BC25003.png", "BC25003")],
            drive_client.EDIT_FOLDER: [_item("e", "BC25004.png", "BC25004")],
            drive_client.REJECTED_FOLDER: [_item("r", "BC25005.png", "BC25005")],
            drive_client.ARCHIVE_FOLDER: [_item("p", "BC25006.png", "BC25006")],
        }.get(name, [])

    monkeypatch.setattr(drive_client, "list_bucket", fake_bucket)

    rows = backfill_sync.scan("ROOT")
    by_id = {r["file_id"]: r for r in rows}
    assert by_id["a"]["status"] == "pending" and by_id["a"]["thumbnail_link"] == "l-a"
    assert by_id["s"]["status"] == "skipped"
    assert by_id["e"]["status"] == "edit"
    assert by_id["r"]["status"] == "regenerate"
    assert by_id["p"]["status"] == "published"
    assert len(rows) == 6


def test_rescan_upserts_scanned_rows(monkeypatch):
    monkeypatch.setattr(backfill_sync, "scan", lambda root: [{"file_id": "a"}, {"file_id": "b"}])
    captured = {}
    monkeypatch.setattr(backfill_sync.repo, "upsert_many",
                        lambda client, rows: captured.update(rows=rows) or len(rows))
    n = backfill_sync.rescan(object(), "ROOT")
    assert n == 2 and len(captured["rows"]) == 2
