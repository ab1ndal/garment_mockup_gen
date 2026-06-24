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


def test_ensure_subfolder_returns_existing(monkeypatch):
    files = _patch_files(monkeypatch, _RecordingFiles(
        list_result={"files": [{"id": "EXISTING", "name": "rejected"}]}))
    assert drive_client.ensure_subfolder("ROOT", "rejected") == "EXISTING"
    assert not any(name == "create" for name, _ in files.calls)


def test_ensure_subfolder_creates_when_absent(monkeypatch):
    files = _patch_files(monkeypatch, _RecordingFiles(list_result={"files": []}))
    assert drive_client.ensure_subfolder("ROOT", "rejected") == "NEW_FOLDER"
    assert any(name == "create" for name, _ in files.calls)
