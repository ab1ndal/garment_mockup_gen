"""Tests for Supabase Storage upload + mockup_variations insert (no network)."""

from __future__ import annotations

import pytest

from mockup_generator.db import mockup_variations_repo
from mockup_generator.integrations import storage_client


# ---------------- storage_client.upload_mockup ----------------

class _FakeBucket:
    def __init__(self, sink):
        self.sink = sink

    def upload(self, path, file, file_options=None):
        self.sink["upload"] = {"path": path, "file": file, "opts": file_options}

    def create_signed_url(self, path, ttl):
        self.sink["signed"] = {"path": path, "ttl": ttl}
        return {"signedURL": f"https://signed/{path}?t={ttl}"}

    def get_public_url(self, path):
        return f"https://public/{path}"

    def remove(self, paths):
        self.sink["removed"] = paths


class _FakeStorage:
    def __init__(self, sink):
        self.sink = sink

    def from_(self, bucket):
        self.sink["bucket"] = bucket
        return _FakeBucket(self.sink)


class _FakeServiceClient:
    def __init__(self, sink):
        self.storage = _FakeStorage(sink)


def test_upload_mockup_uploads_png_and_returns_path_and_public_url(monkeypatch):
    sink = {}
    monkeypatch.setattr(storage_client, "service_client", lambda: _FakeServiceClient(sink))

    path, url = storage_client.upload_mockup("BC25001", b"PNGDATA", "abc123")

    assert sink["bucket"] == "mockups"
    assert path == "BC25001/abc123.png"
    assert sink["upload"]["path"] == "BC25001/abc123.png"
    assert sink["upload"]["file"] == b"PNGDATA"
    assert sink["upload"]["opts"]["content-type"] == "image/png"
    assert url == "https://public/BC25001/abc123.png"


def test_upload_mockup_raises_when_no_service_client(monkeypatch):
    monkeypatch.setattr(storage_client, "service_client", lambda: None)
    with pytest.raises(storage_client.StorageNotConfigured):
        storage_client.upload_mockup("BC25001", b"x", "k")


def test_slugify_normalizes():
    assert storage_client.slugify("Parrot Green") == "parrot-green"
    assert storage_client.slugify("  Light  Grey ") == "light-grey"
    assert storage_client.slugify("Navy_blue") == "navy-blue"
    assert storage_client.slugify(None) == ""


def test_short_hex_is_8_hex_chars():
    h = storage_client.short_hex()
    assert len(h) == 8
    int(h, 16)  # raises if not hex


def test_path_from_public_url_extracts_object_path():
    url = "https://proj.supabase.co/storage/v1/object/public/mockups/BC1/parrot-green_deadbeef.png"
    assert storage_client.path_from_public_url(url) == "BC1/parrot-green_deadbeef.png"
    # strips query string if present
    assert storage_client.path_from_public_url(url + "?t=1") == "BC1/parrot-green_deadbeef.png"
    # unrecognized URL -> None
    assert storage_client.path_from_public_url("https://example.com/x.png") is None


def test_delete_object_removes_path(monkeypatch):
    sink = {}
    monkeypatch.setattr(storage_client, "service_client", lambda: _FakeServiceClient(sink))
    storage_client.delete_object("BC1/old.png")
    assert sink["bucket"] == "mockups"
    assert sink["removed"] == ["BC1/old.png"]


# ---------------- mockup_variations_repo.insert ----------------

class _FakeTable:
    def __init__(self, sink):
        self.sink = sink

    def insert(self, payload):
        self.sink["payload"] = payload
        return self

    def execute(self):
        return type("R", (), {"data": [{"variation_id": 7, **self.sink["payload"]}]})()


class _FakeDb:
    def __init__(self, sink):
        self.sink = sink

    def table(self, name):
        self.sink["table"] = name
        return _FakeTable(self.sink)


def test_insert_writes_row_and_returns_it():
    sink = {}
    db = _FakeDb(sink)

    row = mockup_variations_repo.insert(
        db, productid="BC25001", prompt_text="a saree", image_url="u", created_by="user-1"
    )

    assert sink["table"] == "mockup_variations"
    p = sink["payload"]
    assert p["productid"] == "BC25001"
    assert p["prompt_text"] == "a saree"
    assert p["image_url"] == "u"
    assert p["kind"] == "image"
    assert p["created_by"] == "user-1"
    assert row["variation_id"] == 7


def test_insert_omits_none_optional_fields():
    sink = {}
    mockup_variations_repo.insert(
        _FakeDb(sink), productid="BC1", prompt_text="p", image_url="u"
    )
    # nullable cols not sent when unset, so DB defaults/nulls apply cleanly
    assert "prompt_id" not in sink["payload"]
    assert "created_by" not in sink["payload"]


def test_insert_includes_color_when_set():
    sink = {}
    mockup_variations_repo.insert(
        _FakeDb(sink), productid="BC1", prompt_text="p", image_url="u", color="Red"
    )
    assert sink["payload"]["color"] == "Red"


def test_insert_omits_color_when_none():
    sink = {}
    mockup_variations_repo.insert(
        _FakeDb(sink), productid="BC1", prompt_text="p", image_url="u"
    )
    assert "color" not in sink["payload"]
