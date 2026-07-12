from io import BytesIO

import pytest
from PIL import Image

import backend.routers.import_shots as mod
from backend.schemas import EditParamsModel


def _rgba(colour=(120, 60, 30), size=(40, 40)):
    return Image.new("RGBA", size, colour + (255,))


@pytest.fixture(autouse=True)
def _clear_cache():
    mod._CUTOUT_CACHE.clear()
    mod._INFLIGHT.clear()
    yield
    mod._CUTOUT_CACHE.clear()
    mod._INFLIGHT.clear()


def test_get_cutout_computes_once_per_file_id(monkeypatch):
    downloads, computes = [], []
    monkeypatch.setattr(mod, "_download", lambda fid: downloads.append(fid) or b"bytes")
    monkeypatch.setattr(mod.edit_pipeline, "compute_cutout",
                        lambda b: computes.append(b) or _rgba())

    a = mod._get_cutout("file-A")
    b = mod._get_cutout("file-A")
    assert a.size == b.size            # cache hit decodes a fresh Image from bytes
    assert downloads == ["file-A"]     # Drive hit exactly once
    assert len(computes) == 1          # BiRefNet ran exactly once


def test_concurrent_get_cutout_computes_once(monkeypatch):
    import threading, time
    computes = []
    def _slow(src_bytes):
        computes.append(1)
        time.sleep(0.2)
        return _rgba()
    monkeypatch.setattr(mod, "_download", lambda fid: b"bytes")
    monkeypatch.setattr(mod.edit_pipeline, "compute_cutout", _slow)
    results = []
    threads = [threading.Thread(target=lambda: results.append(mod._get_cutout("file-C")))
               for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert len(computes) == 1     # 4 concurrent misses -> ONE BiRefNet run
    assert len(results) == 4


def test_cache_evicts_least_recently_used(monkeypatch):
    monkeypatch.setattr(mod, "_download", lambda fid: b"bytes")
    monkeypatch.setattr(mod.edit_pipeline, "compute_cutout", lambda b: _rgba())
    for i in range(mod._CACHE_CAP + 3):
        mod._get_cutout(f"file-{i}")
    assert len(mod._CUTOUT_CACHE) == mod._CACHE_CAP
    assert "file-0" not in mod._CUTOUT_CACHE     # oldest evicted


def test_render_uses_cached_cutout(monkeypatch):
    computes = []
    monkeypatch.setattr(mod, "_download", lambda fid: b"bytes")
    monkeypatch.setattr(mod.edit_pipeline, "compute_cutout",
                        lambda b: computes.append(1) or _rgba())
    p = EditParamsModel()
    mod._render("file-X", p)
    mod._render("file-X", p)
    assert len(computes) == 1          # second render is a cache hit


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
    monkeypatch.setattr(mod, "_download", lambda fid: b"bytes")
    monkeypatch.setattr(mod.edit_pipeline, "compute_cutout", lambda b: _rgba())
    r = client.post("/api/import/warm", json={"file_id": "file-W"})
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert "file-W" in mod._CUTOUT_CACHE
