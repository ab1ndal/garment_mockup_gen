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

    def get_public_url(self, path):  # unused here; present for parity
        return f"https://public/{path}"


class _FakeStorage:
    def __init__(self, sink):
        self.sink = sink

    def from_(self, bucket):
        self.sink["bucket"] = bucket
        return _FakeBucket(self.sink)


class _FakeServiceClient:
    def __init__(self, sink):
        self.storage = _FakeStorage(sink)


def test_upload_mockup_uploads_png_and_returns_path_and_signed_url(monkeypatch):
    sink = {}
    monkeypatch.setattr(storage_client, "service_client", lambda: _FakeServiceClient(sink))

    path, url = storage_client.upload_mockup("BC25001", b"PNGDATA", "abc123")

    assert sink["bucket"] == "mockups"
    assert path == "BC25001/abc123.png"
    assert sink["upload"]["path"] == "BC25001/abc123.png"
    assert sink["upload"]["file"] == b"PNGDATA"
    assert sink["upload"]["opts"]["content-type"] == "image/png"
    assert url == "https://signed/BC25001/abc123.png?t=604800"


def test_upload_mockup_raises_when_no_service_client(monkeypatch):
    monkeypatch.setattr(storage_client, "service_client", lambda: None)
    with pytest.raises(storage_client.StorageNotConfigured):
        storage_client.upload_mockup("BC25001", b"x", "k")


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
