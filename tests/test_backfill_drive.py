import pytest

from mockup_generator.config import settings
from mockup_generator.integrations import drive_client


def test_generated_folder_id_default():
    assert settings.generated_mockups_folder_id == "1FBDw_F40zDt4zvp6el3Ei50Nw8cOahm4"


def test_drive_scope_is_read_write():
    assert "https://www.googleapis.com/auth/drive" in drive_client._SCOPES


@pytest.mark.parametrize("name,expected", [
    ("BC25123.png", ("BC25123", None)),
    ("BC25123A.png", ("BC25123", "A")),
    ("BC25123_A.png", ("BC25123", "A")),
    ("BC25123_a.png", ("BC25123", "A")),      # alpha upper-cased
    ("BC25123AB.png", ("BC25123", "AB")),
    ("BC1234.jpg", ("BC1234", None)),
    ("BC25123.v2.png", (None, None)),          # extra dots -> malformed
    ("IMG_001.png", (None, None)),             # non-BC -> malformed
    ("notes.txt", (None, None)),
])
def test_parse_generated_name(name, expected):
    assert drive_client.parse_generated_name(name) == expected


class _RecordingFiles:
    """Records the Drive files() calls the mutation helpers make."""

    def __init__(self, list_result=None):
        self.calls = []
        self._list_result = list_result or {"files": []}

    def delete(self, **kw):
        self.calls.append(("delete", kw))
        return self

    def get(self, **kw):
        self.calls.append(("get", kw))
        self._last = ("get", kw)
        return self

    def update(self, **kw):
        self.calls.append(("update", kw))
        return self

    def list(self, **kw):
        self.calls.append(("list", kw))
        self._last = ("list", kw)
        return self

    def create(self, **kw):
        self.calls.append(("create", kw))
        self._last = ("create", kw)
        return self

    def execute(self):
        kind = self._last[0] if hasattr(self, "_last") else None
        if kind == "get":
            return {"parents": ["OLD_PARENT"]}
        if kind == "list":
            return self._list_result
        if kind == "create":
            return {"id": "NEW_FOLDER"}
        return {}


def _patch_files(monkeypatch, files):
    svc = type("Svc", (), {"files": lambda self: files})()
    monkeypatch.setattr(drive_client, "_clients", lambda: (svc, object()))
    return files


def test_delete_file(monkeypatch):
    files = _patch_files(monkeypatch, _RecordingFiles())
    drive_client.delete_file("F1")
    assert ("delete", {"fileId": "F1", "supportsAllDrives": True}) in files.calls


def test_move_file_swaps_parents(monkeypatch):
    files = _patch_files(monkeypatch, _RecordingFiles())
    drive_client.move_file("F1", "REJECTED")
    update = next(kw for name, kw in files.calls if name == "update")
    assert update["fileId"] == "F1"
    assert update["addParents"] == "REJECTED"
    assert update["removeParents"] == "OLD_PARENT"
    assert update["supportsAllDrives"] is True
    # Verify fetch-before-update: get() was called with correct params
    get = next(kw for name, kw in files.calls if name == "get")
    assert get["fileId"] == "F1"
    assert get["fields"] == "parents"
    assert get["supportsAllDrives"] is True


def test_ensure_subfolder_returns_existing(monkeypatch):
    files = _patch_files(monkeypatch, _RecordingFiles(
        list_result={"files": [{"id": "EXISTING", "name": "rejected"}]}))
    assert drive_client.ensure_subfolder("ROOT", "rejected") == "EXISTING"
    assert not any(name == "create" for name, _ in files.calls)


def test_ensure_subfolder_creates_when_absent(monkeypatch):
    files = _patch_files(monkeypatch, _RecordingFiles(list_result={"files": []}))
    assert drive_client.ensure_subfolder("ROOT", "rejected") == "NEW_FOLDER"
    assert any(name == "create" for name, _ in files.calls)


def test_move_file_joins_multiple_old_parents(monkeypatch):
    """Verify move_file correctly joins multiple parent IDs with commas."""
    files_recorder = _RecordingFiles()

    # Override execute to return two parents for get calls
    original_execute = files_recorder.execute
    def execute_with_multi_parents():
        kind = files_recorder._last[0] if hasattr(files_recorder, "_last") else None
        if kind == "get":
            return {"parents": ["P1", "P2"]}
        return original_execute()
    files_recorder.execute = execute_with_multi_parents

    _patch_files(monkeypatch, files_recorder)
    drive_client.move_file("F1", "NEW")
    update = next(kw for name, kw in files_recorder.calls if name == "update")
    assert update["removeParents"] == "P1,P2"


_FOLDER = drive_client._FOLDER_MIME


def _named(fid, name, *, folder=False):
    return {"id": fid, "name": name,
            "mimeType": _FOLDER if folder else "image/png",
            "thumbnailLink": None if folder else f"link-{fid}"}


class _ScanFiles:
    def __init__(self, responses):
        self.responses = responses
        self._q = ""

    def list(self, *, q, **kw):
        self._q = q
        return self

    def execute(self):
        for folder_id, files in self.responses.items():
            if f"'{folder_id}' in parents" in self._q:
                return {"files": files}
        return {"files": []}


def _patch_scan(monkeypatch, responses):
    svc = type("Svc", (), {"files": lambda self: _ScanFiles(responses)})()
    monkeypatch.setattr(drive_client, "_clients", lambda: (svc, object()))


def test_scan_folder_of_folders_flattens(monkeypatch):
    responses = {
        "ROOT": [_named("a", "BC25001.png"),
                 _named("S1", "group1", folder=True),
                 _named("S2", "group2", folder=True)],
        "S1": [_named("b", "BC25002.png"), _named("c", "BC25002_A.png")],
        "S2": [_named("d", "weird-name.png")],
    }
    _patch_scan(monkeypatch, responses)

    out = drive_client.scan_folder_of_folders("ROOT")
    by_id = {i["file_id"]: i for i in out}

    assert by_id["a"]["productid"] == "BC25001" and by_id["a"]["subfolder_name"] is None
    assert by_id["b"]["productid"] == "BC25002" and by_id["b"]["subfolder_name"] == "group1"
    assert by_id["c"]["alpha"] == "A"
    assert by_id["d"]["productid"] is None          # malformed name still listed
    assert by_id["a"]["thumbnail_link"] == "link-a"
    assert len(out) == 4


def test_scan_skips_reserved_subfolders(monkeypatch):
    # published/ (archived approvals) and rejected/ (flagged) live under root but
    # must not re-enter the worklist on rescan.
    responses = {
        "ROOT": [_named("a", "BC25001.png"),
                 _named("R", "rejected", folder=True),
                 _named("P", "published", folder=True),
                 _named("S1", "group1", folder=True)],
        "R": [_named("x", "BC25099.png")],
        "P": [_named("y", "BC25098.png")],
        "S1": [_named("b", "BC25002.png")],
    }
    _patch_scan(monkeypatch, responses)

    out = drive_client.scan_folder_of_folders("ROOT")
    assert {i["file_id"] for i in out} == {"a", "b"}   # reserved folders excluded


def test_thumbnails_for_uses_attach(monkeypatch):
    monkeypatch.setattr(drive_client, "_clients", lambda: (object(), object()))
    monkeypatch.setattr(drive_client, "_attach_thumbnails",
                        lambda session, files: {f["id"]: {"thumbnail_url": f"data:{f['id']}"} for f in files})
    out = drive_client.thumbnails_for([
        {"file_id": "a", "thumbnail_link": "link-a"},
        {"file_id": "b", "thumbnail_link": "link-b"},
    ])
    assert out == {"a": "data:a", "b": "data:b"}
