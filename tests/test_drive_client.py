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
