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


class _FakeBlob:
    def __init__(self, data, mime_type="image/png"):
        self.data = data
        self.mime_type = mime_type


class _FakePart:
    """Mirrors a real google-genai part: image data lives in ``inline_data``
    as raw bytes, not behind ``as_image()`` (which returns a non-PIL type)."""

    def __init__(self, image=None):
        if image is not None:
            buf = BytesIO()
            image.save(buf, "PNG")
            self.inline_data = _FakeBlob(buf.getvalue())
        else:
            self.inline_data = None


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


def test_first_image_bytes_handles_real_genai_image_part():
    """Regression: google-genai 2.x ``Part.as_image()`` returns a
    ``types.Image`` (which has no ``.convert``), not a PIL image. The helper
    must read the raw ``inline_data`` bytes through PIL instead."""
    from google.genai import types

    buf = BytesIO()
    _png_image().save(buf, "PNG")
    img_part = types.Part(inline_data=types.Blob(data=buf.getvalue(), mime_type="image/png"))
    resp = _FakeResponse([types.Part(text="thinking…"), img_part])

    data = common.first_image_bytes(resp)
    assert data is not None
    assert Image.open(BytesIO(data)).format == "PNG"


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


def test_generate_with_retries_threads_output_options_and_thinking(monkeypatch):
    captured = {}
    _capture_client(monkeypatch, captured)

    common.generate_with_retries(
        "m", ["p"],
        output_mime_type="image/jpeg", output_compression_quality=80,
        thinking_level="high",
    )

    ic = captured["config"].image_config
    assert ic.image_output_options is not None
    assert ic.image_output_options.mime_type == "image/jpeg"
    assert ic.image_output_options.compression_quality == 80
    assert captured["config"].thinking_config is not None


def test_generate_with_retries_omits_output_options_and_thinking_by_default(monkeypatch):
    captured = {}
    _capture_client(monkeypatch, captured)

    common.generate_with_retries("m", ["p"])

    ic = captured["config"].image_config
    assert ic.image_output_options is None
    assert captured["config"].thinking_config is None


def test_first_image_bytes_preserves_jpeg():
    from io import BytesIO as _B
    buf = _B()
    _png_image().save(buf, "JPEG")
    blob = _FakeBlob(buf.getvalue(), mime_type="image/jpeg")
    part = type("P", (), {"inline_data": blob})()
    resp = _FakeResponse([part])

    data = common.first_image_bytes(resp)
    assert Image.open(_B(data)).format == "JPEG"


def test_generate_mockup_bytes_threads_output_and_thinking(monkeypatch):
    captured = {}

    def fake_retries(model_name, contents, **kwargs):
        captured["kwargs"] = kwargs
        return _FakeResponse([_FakePart(_png_image())])

    monkeypatch.setattr(service, "generate_with_retries", fake_retries)
    service.generate_mockup_bytes(
        [_png_image()], "p",
        output_mime_type="image/jpeg", output_compression_quality=70,
        person_generation="ALLOW_ADULT", thinking_level="high",
    )
    kw = captured["kwargs"]
    assert kw["output_mime_type"] == "image/jpeg"
    assert kw["output_compression_quality"] == 70
    assert kw["person_generation"] == "ALLOW_ADULT"
    assert kw["thinking_level"] == "high"
