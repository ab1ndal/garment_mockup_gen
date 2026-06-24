from mockup_generator.services import backfill_service as svc


def _item(fid, pid="BC25001"):
    return {"productid": pid, "alpha": None, "file_id": fid, "name": f"{pid}.png",
            "subfolder_id": None, "subfolder_name": None, "thumbnail_link": f"l-{fid}"}


def setup_function():
    svc.clear_cache()


def test_get_index_scans_once_and_caches(monkeypatch):
    scans = {"n": 0}

    def fake_scan(root):
        scans["n"] += 1
        return [_item("a"), _item("b")]

    monkeypatch.setattr(svc.drive_client, "scan_folder_of_folders", fake_scan)
    first = svc.get_index("ROOT")
    second = svc.get_index("ROOT")
    assert [i["file_id"] for i in first] == ["a", "b"]
    assert second == first
    assert scans["n"] == 1                      # cached, not re-scanned


def test_refresh_forces_rescan(monkeypatch):
    scans = {"n": 0}
    monkeypatch.setattr(svc.drive_client, "scan_folder_of_folders",
                        lambda root: scans.__setitem__("n", scans["n"] + 1) or [_item("a")])
    svc.get_index("ROOT")
    svc.get_index("ROOT", refresh=True)
    assert scans["n"] == 2


def test_paginate():
    items = [_item(str(i)) for i in range(5)]
    assert [i["file_id"] for i in svc.paginate(items, 2, 2)] == ["2", "3"]


def test_evict_removes_item(monkeypatch):
    monkeypatch.setattr(svc.drive_client, "scan_folder_of_folders",
                        lambda root: [_item("a"), _item("b")])
    svc.get_index("ROOT")
    svc.evict("a", root_id="ROOT")
    assert [i["file_id"] for i in svc.get_index("ROOT")] == ["b"]   # served from cache, a gone
