import pytest

from mockup_generator.integrations import drive_client


class FakeFiles:
    def __init__(self, recorder, create_ret=None, list_ret=None):
        self.rec, self._create_ret, self._list_ret = recorder, create_ret or {}, list_ret or {}

    def create(self, **kw):
        self.rec["create"] = kw
        return _Exec(self._create_ret)

    def list(self, **kw):
        self.rec.setdefault("list", []).append(kw)
        return _Exec(self._list_ret)


class _Exec:
    def __init__(self, ret): self._ret = ret
    def execute(self): return self._ret


class FakeSvc:
    def __init__(self, files): self._files = files
    def files(self): return self._files


def test_batch_folder_is_excluded_from_backfill_scan():
    assert drive_client.BATCH_STAGING_FOLDER in drive_client._RESERVED_SUBFOLDERS


def test_upload_image_creates_media_and_returns_id_and_thumb(monkeypatch):
    rec = {}
    files = FakeFiles(rec, create_ret={"id": "new123", "thumbnailLink": "http://t"})
    monkeypatch.setattr(drive_client, "_clients", lambda: (FakeSvc(files), object()))
    fid, thumb = drive_client.upload_image("parent1", "BC25001_red.png", b"PNGBYTES")
    assert fid == "new123" and thumb == "http://t"
    body = rec["create"]["body"]
    assert body["name"] == "BC25001_red.png" and body["parents"] == ["parent1"]


def test_list_folder_image_ids_returns_ids_capped(monkeypatch):
    monkeypatch.setattr(drive_client, "_clients", lambda: (object(), object()))

    # Stateless fake keyed on the folder in the query, so both calls below behave
    # identically: top level = 2 images + 1 subfolder; the subfolder = 1 image.
    def fake_paged(svc, q, fields):
        if "'folderX'" in q:
            return [
                {"id": "i1", "mimeType": "image/png"},
                {"id": "i2", "mimeType": "image/jpeg"},
                {"id": "sub", "mimeType": drive_client._FOLDER_MIME, "name": "Red"},
            ]
        return [{"id": "i3", "mimeType": "image/png"}]

    monkeypatch.setattr(drive_client, "_paged_files", fake_paged)
    ids = drive_client.list_folder_image_ids("folderX", limit=14)
    assert ids == ["i1", "i2", "i3"]
    capped = drive_client.list_folder_image_ids("folderX", limit=2)
    assert len(capped) == 2
