"""Tests for the web-oriented, in-memory generation service + engine deltas.

All mock the GenAI client / retry helper — no network, no real key.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from mockup_generator.generation import common, service


def _png_image() -> Image.Image:
    return Image.new("RGB", (8, 8), (10, 20, 30))


class _FakePart:
    def __init__(self, image=None):
        self._image = image
        self.inline_data = object() if image is not None else None

    def as_image(self):
        return self._image


class _FakeResponse:
    """Mimics a Gemini response whose parts interleave thought/text + image."""

    def __init__(self, parts):
        candidate = type("C", (), {"content": type("Ct", (), {"parts": parts})()})()
        self.candidates = [candidate]


# --- engine delta: person_generation + configurable model in generate_with_retries ---

def _capture_client(monkeypatch, captured):
    class _FakeModels:
        def generate_content(self, *, model, contents, config):
            captured["model"] = model
            captured["config"] = config
            return _FakeResponse([_FakePart(_png_image())])

    monkeypatch.setattr(common, "get_genai_client",
                        lambda: type("C", (), {"models": _FakeModels()})())


def test_generate_with_retries_omits_person_generation_by_default(monkeypatch):
    """Gemini Developer API (api-key) rejects person_generation at call time."""
    captured = {}
    _capture_client(monkeypatch, captured)

    common.generate_with_retries("my-model", ["prompt"], aspect_ratio="1:1", resolution="4K")

    assert captured["model"] == "my-model"
    ic = captured["config"].image_config
    assert ic.aspect_ratio == "1:1"
    assert ic.image_size == "4K"
    assert ic.person_generation is None  # not sent on the Developer API path


def test_generate_with_retries_threads_person_generation_when_set(monkeypatch):
    """Vertex callers can still opt in explicitly."""
    captured = {}
    _capture_client(monkeypatch, captured)

    common.generate_with_retries("m", ["p"], person_generation="ALLOW_ADULT")

    assert captured["config"].image_config.person_generation == "ALLOW_ADULT"


# --- image extraction helper: skip thought/text parts, return PNG bytes ---

def test_first_image_bytes_skips_non_image_parts():
    resp = _FakeResponse([_FakePart(None), _FakePart(None), _FakePart(_png_image())])
    data = common.first_image_bytes(resp)
    assert data is not None
    # round-trips as a PNG
    im = Image.open(BytesIO(data))
    assert im.format == "PNG"


def test_first_image_bytes_returns_none_when_no_image():
    resp = _FakeResponse([_FakePart(None)])
    assert common.first_image_bytes(resp) is None


# --- service orchestration ---

def test_generate_mockup_bytes_uses_configured_model_and_returns_png(monkeypatch):
    captured = {}

    def fake_retries(model_name, contents, **kwargs):
        captured["model"] = model_name
        captured["contents"] = contents
        captured["kwargs"] = kwargs
        return _FakeResponse([_FakePart(_png_image())])

    monkeypatch.setattr(service, "generate_with_retries", fake_retries)
    monkeypatch.setenv("GEMINI_IMAGE_MODEL", "gemini-3-pro-image")

    data = service.generate_mockup_bytes([_png_image(), _png_image()], "a luxe saree")

    assert captured["model"] == "gemini-3-pro-image"
    # prompt first, then one part per image
    assert captured["contents"][0] == "a luxe saree"
    assert len(captured["contents"]) == 3
    im = Image.open(BytesIO(data))
    assert im.format == "PNG"


def test_generate_mockup_bytes_threads_model_resolution_aspect(monkeypatch):
    captured = {}

    def fake_retries(model_name, contents, **kwargs):
        captured["model"] = model_name
        captured["kwargs"] = kwargs
        return _FakeResponse([_FakePart(_png_image())])

    monkeypatch.setattr(service, "generate_with_retries", fake_retries)

    service.generate_mockup_bytes(
        [_png_image()], "p",
        model="gemini-3.1-flash-image", resolution="2K", aspect_ratio="3:4",
    )

    assert captured["model"] == "gemini-3.1-flash-image"
    assert captured["kwargs"]["resolution"] == "2K"
    assert captured["kwargs"]["aspect_ratio"] == "3:4"


def test_generate_mockup_bytes_raises_when_no_image_returned(monkeypatch):
    monkeypatch.setattr(
        service, "generate_with_retries",
        lambda *a, **k: _FakeResponse([_FakePart(None)]),
    )
    with pytest.raises(service.NoImageReturned):
        service.generate_mockup_bytes([_png_image()], "prompt")
