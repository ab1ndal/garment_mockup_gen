"""Unit tests for the Drive client's folder/variant grouping (no network).

Fakes the Drive ``files().list`` chain and stubs thumbnail fetching so the
grouping logic (loose images + one group per non-empty subfolder, depth 1) is
exercised without touching Google or the network.
"""

from mockup_generator.integrations import drive_client

_FOLDER = drive_client._FOLDER_MIME


def _file(i, *, folder=False):
    return {
        "id": i,
        "name": f"{i}",
        "mimeType": _FOLDER if folder else "image/jpeg",
        "thumbnailLink": None if folder else f"link-{i}",
    }


class _FakeFiles:
    def __init__(self, responses):
        self.responses = responses
        self.queries = []
        self._q = ""

    def list(self, *, q, **kwargs):
        self._q = q
        self.queries.append(q)
        return self

    def execute(self):
        for folder_id, files in self.responses.items():
            if f"'{folder_id}' in parents" in self._q:
                return {"files": files}
        return {"files": []}


class _FakeSvc:
    def __init__(self, responses):
        self._files = _FakeFiles(responses)

    def files(self):
        return self._files


def _patch(monkeypatch, responses):
    svc = _FakeSvc(responses)
    monkeypatch.setattr(drive_client, "_clients", lambda: (svc, object()))
    monkeypatch.setattr(drive_client, "_thumbnail_data_uri",
                        lambda session, link, fid: f"data:thumb:{fid}")
    return svc


def test_extract_folder_id_formats():
    assert drive_client.extract_folder_id("https://drive.google.com/drive/folders/ABC123") == "ABC123"
    assert drive_client.extract_folder_id("https://drive.google.com/open?id=XYZ789") == "XYZ789"
    assert drive_client.extract_folder_id("https://x/folders/ID_1-2?usp=sharing") == "ID_1-2"
    assert drive_client.extract_folder_id(None) is None
    assert drive_client.extract_folder_id("https://example.com/no-id-here") is None


def test_groups_split_loose_and_variant_subfolders(monkeypatch):
    responses = {
        "ROOT": [
            _file("a"), _file("b"),
            _file("RED", folder=True), _file("BLUE", folder=True), _file("EMPTY", folder=True),
        ],
        "RED": [_file("r1"), _file("r2")],
        "BLUE": [_file("b1")],
        "EMPTY": [],
    }
    _patch(monkeypatch, responses)

    out = drive_client.list_folder_image_groups("ROOT")

    assert [i["id"] for i in out["loose"]] == ["a", "b"]
    # one group per non-empty subfolder, in listing order; EMPTY omitted
    assert [(g["id"], g["name"]) for g in out["groups"]] == [("RED", "RED"), ("BLUE", "BLUE")]
    assert [i["id"] for i in out["groups"][0]["images"]] == ["r1", "r2"]
    assert [i["id"] for i in out["groups"][1]["images"]] == ["b1"]
    # thumbnails attached via the (stubbed) fetcher
    assert out["loose"][0]["thumbnail_url"] == "data:thumb:a"
    assert out["groups"][0]["images"][0]["thumbnail_url"] == "data:thumb:r1"


def test_groups_no_subfolders_is_all_loose(monkeypatch):
    _patch(monkeypatch, {"ROOT": [_file("a"), _file("b")]})
    out = drive_client.list_folder_image_groups("ROOT")
    assert [i["id"] for i in out["loose"]] == ["a", "b"]
    assert out["groups"] == []


def test_list_folder_images_flat(monkeypatch):
    _patch(monkeypatch, {"F": [_file("x"), _file("y")]})
    out = drive_client.list_folder_images("F")
    assert [i["id"] for i in out] == ["x", "y"]
    assert out[0]["thumbnail_url"] == "data:thumb:x"


def test_thumbnails_for_ids_keeps_input_order(monkeypatch):
    """Sources are fanned out across threads, so the results must be re-ordered
    back to the caller's ids — a review card's checkboxes are keyed on them."""
    monkeypatch.setattr(drive_client, "_clients", lambda: (object(), object()))
    monkeypatch.setattr(drive_client, "_thumbnail_link", lambda session, fid: f"link-{fid}")
    monkeypatch.setattr(drive_client, "_thumbnail_data_uri",
                        lambda session, link, fid: f"data:{link}")
    out = drive_client.thumbnails_for_ids(["a", "b", "c"])
    assert [t["id"] for t in out] == ["a", "b", "c"]
    assert out[1]["thumbnail_url"] == "data:link-b"


def test_thumbnails_for_ids_falls_back_when_metadata_fails(monkeypatch):
    """One unreadable file must not take down the whole review card."""
    monkeypatch.setattr(drive_client, "_clients", lambda: (object(), object()))
    def flaky(session, fid):
        if fid == "bad":
            raise RuntimeError("404")
        return f"link-{fid}"
    monkeypatch.setattr(drive_client, "_thumbnail_link", flaky)
    monkeypatch.setattr(drive_client, "_thumbnail_data_uri",
                        lambda session, link, fid: f"data:{link}" if link else f"public:{fid}")
    out = drive_client.thumbnails_for_ids(["ok", "bad"])
    assert out[0]["thumbnail_url"] == "data:link-ok"
    assert out[1]["thumbnail_url"] == "public:bad"


def test_thumbnails_for_ids_empty_makes_no_calls(monkeypatch):
    """ThreadPoolExecutor(max_workers=0) raises — the empty case must short-circuit."""
    def boom(): raise AssertionError("no Drive client needed for an empty list")
    monkeypatch.setattr(drive_client, "_clients", boom)
    assert drive_client.thumbnails_for_ids([]) == []


class _FakeDownloader:
    """Stand-in for MediaIoBaseDownload: writes the file's bytes in two chunks."""

    def __init__(self, buf, request):
        self.buf = buf
        self.request = request  # the get_media request object
        self._chunks = [b"PNG", b"DATA"]

    def next_chunk(self):
        self.buf.write(self._chunks.pop(0))
        return (None, len(self._chunks) == 0)


def test_download_file_streams_full_bytes(monkeypatch):
    captured = {}

    class _Files:
        def get_media(self, *, fileId, supportsAllDrives):
            captured["fileId"] = fileId
            captured["supportsAllDrives"] = supportsAllDrives
            return f"req:{fileId}"

    svc = type("Svc", (), {"files": lambda self: _Files()})()
    monkeypatch.setattr(drive_client, "_clients", lambda: (svc, object()))
    monkeypatch.setattr(drive_client, "MediaIoBaseDownload", _FakeDownloader)

    data = drive_client.download_file("FILE1")

    assert data == b"PNGDATA"
    assert captured == {"fileId": "FILE1", "supportsAllDrives": True}
