from io import BytesIO

import pytest
from PIL import Image

import backend.routers.import_shots as mod
from backend.schemas import EditParamsModel


def _src_bytes(colour=(120, 60, 30), size=(40, 40)):
    buf = BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _clear_cache():
    mod._SOURCE_CACHE.clear()
    mod._INFLIGHT.clear()
    yield
    mod._SOURCE_CACHE.clear()
    mod._INFLIGHT.clear()


def test_get_source_downloads_once_per_file_id(monkeypatch):
    downloads = []
    monkeypatch.setattr(mod, "_download", lambda fid: downloads.append(fid) or _src_bytes())

    a = mod._get_source("file-A")
    b = mod._get_source("file-A")
    assert a.size == b.size             # cache hit decodes a fresh Image from bytes
    assert downloads == ["file-A"]      # Drive hit exactly once


def test_concurrent_get_source_downloads_once(monkeypatch):
    import threading, time
    downloads = []
    def _slow(fid):
        downloads.append(1)
        time.sleep(0.2)
        return _src_bytes()
    monkeypatch.setattr(mod, "_download", _slow)
    results = []
    threads = [threading.Thread(target=lambda: results.append(mod._get_source("file-C")))
               for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert len(downloads) == 1     # 4 concurrent misses -> ONE Drive download
    assert len(results) == 4


def test_cache_evicts_least_recently_used(monkeypatch):
    monkeypatch.setattr(mod, "_download", lambda fid: _src_bytes())
    for i in range(mod._CACHE_CAP + 3):
        mod._get_source(f"file-{i}")
    assert len(mod._SOURCE_CACHE) == mod._CACHE_CAP
    assert "file-0" not in mod._SOURCE_CACHE     # oldest evicted


def test_render_uses_cached_source(monkeypatch):
    downloads = []
    monkeypatch.setattr(mod, "_download", lambda fid: downloads.append(1) or _src_bytes())
    p = EditParamsModel()
    mod._render("file-X", p)
    mod._render("file-X", p)
    assert len(downloads) == 1          # second render is a cache hit


from fastapi.testclient import TestClient
from backend.main import app
from backend.auth import get_current_user, CurrentUser
from backend.deps import get_db
from mockup_generator.db.profiles_repo import Profile


@pytest.fixture
def client():
    u = CurrentUser(id="u1", email="a@b.c", role="user",
                    profile=Profile(id="u1", email="a@b.c", role="user", is_active=True))
    app.dependency_overrides[get_current_user] = lambda: u
    app.dependency_overrides[get_db] = lambda: object()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def test_warm_populates_cache(client, monkeypatch):
    monkeypatch.setattr(mod, "_download", lambda fid: _src_bytes())
    r = client.post("/api/import/warm", json={"file_id": "file-W"})
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert "file-W" in mod._SOURCE_CACHE


def test_release_evicts_cached_source(client, monkeypatch):
    monkeypatch.setattr(mod, "_download", lambda fid: _src_bytes())
    mod._get_source("file-R")                      # populate
    assert "file-R" in mod._SOURCE_CACHE
    r = client.post("/api/import/release", json={"file_id": "file-R"})
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert "file-R" not in mod._SOURCE_CACHE
