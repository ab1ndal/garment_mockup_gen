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
    yield
    mod._CUTOUT_CACHE.clear()


def test_get_cutout_computes_once_per_file_id(monkeypatch):
    downloads, computes = [], []
    monkeypatch.setattr(mod, "_download", lambda fid: downloads.append(fid) or b"bytes")
    monkeypatch.setattr(mod.edit_pipeline, "compute_cutout",
                        lambda b: computes.append(b) or _rgba())

    a = mod._get_cutout("file-A")
    b = mod._get_cutout("file-A")
    assert a is b                      # same cached object
    assert downloads == ["file-A"]     # Drive hit exactly once
    assert len(computes) == 1          # BiRefNet ran exactly once


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
