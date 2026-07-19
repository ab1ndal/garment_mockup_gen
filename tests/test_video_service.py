"""Tests for the web-oriented, in-memory VEO video service.

All mock the GenAI client — no network, no real key, no real sleeps.
"""

from __future__ import annotations

import pytest

from mockup_generator.generation import video_service


class _FakeVideo:
    def __init__(self, data: bytes | None):
        self.video_bytes = data


class _FakeGenerated:
    def __init__(self, video):
        self.video = video


class _FakeResponse:
    def __init__(self, videos):
        self.generated_videos = videos


class _FakeOperation:
    """`done` flips True after `flips_after` polls; carries a response/error."""

    def __init__(self, *, videos=None, error=None, flips_after=0):
        self._videos = videos
        self.error = error
        self._flips_after = flips_after
        self.name = "op/123"

    @property
    def done(self):
        return self._flips_after <= 0

    @property
    def response(self):
        return _FakeResponse(self._videos) if self._videos is not None else None


class _FakeClient:
    def __init__(self, operation, *, captured=None):
        self._op = operation
        self._captured = captured if captured is not None else {}
        self.models = self
        self.operations = self
        self.files = self

    # models.generate_videos
    def generate_videos(self, *, model, prompt, config, image=None, video=None):
        self._captured["model"] = model
        self._captured["prompt"] = prompt
        self._captured["image"] = image
        self._captured["video"] = video
        self._captured["config"] = config
        return self._op

    # operations.get — count down flips_after each poll
    def get(self, op):
        op._flips_after -= 1
        return op

    # files.download — no-op (bytes already inline on the fake video)
    def download(self, *, file):
        return None


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(video_service.time, "sleep", lambda *_a, **_k: None)


def _patch_client(monkeypatch, client, seen=None):
    # Production passes the VEO region (VEO isn't served on `global`); the stub
    # records the location so a test can assert the regional client is used.
    def _factory(location=None):
        if seen is not None:
            seen["location"] = location
        return client
    monkeypatch.setattr(video_service, "get_genai_client", _factory)


def test_generate_video_bytes_returns_mp4(monkeypatch):
    captured = {}
    op = _FakeOperation(videos=[_FakeGenerated(_FakeVideo(b"MP4DATA"))], flips_after=0)
    _patch_client(monkeypatch, _FakeClient(op, captured=captured))

    out = video_service.generate_video_bytes(
        b"imgbytes", "a slow pan", model="veo-3.1-generate-001",
        aspect_ratio="9:16", resolution="720p", duration=4,
    )

    assert out == b"MP4DATA"
    assert captured["model"] == "veo-3.1-generate-001"
    cfg = captured["config"]
    assert cfg.aspect_ratio == "9:16"
    assert cfg.resolution == "720p"
    assert cfg.duration_seconds == 4


def test_generate_video_bytes_uses_veo_region_not_global(monkeypatch):
    """VEO 404s on the `global` endpoint the image models use, so the video
    client must be built for the configured VEO region."""
    seen = {}
    op = _FakeOperation(videos=[_FakeGenerated(_FakeVideo(b"V"))], flips_after=0)
    _patch_client(monkeypatch, _FakeClient(op), seen=seen)

    video_service.generate_video_bytes(b"i", "p")
    assert seen["location"] == "us-central1"
    assert seen["location"] != "global"


def test_generate_video_bytes_polls_until_done(monkeypatch):
    op = _FakeOperation(videos=[_FakeGenerated(_FakeVideo(b"V"))], flips_after=3)
    _patch_client(monkeypatch, _FakeClient(op))

    out = video_service.generate_video_bytes(b"i", "p", poll_interval=1, poll_timeout=100)
    assert out == b"V"


def test_generate_video_bytes_uses_configured_model(monkeypatch):
    captured = {}
    op = _FakeOperation(videos=[_FakeGenerated(_FakeVideo(b"V"))])
    _patch_client(monkeypatch, _FakeClient(op, captured=captured))
    monkeypatch.setenv("VEO_MODEL", "veo-3.1-fast-generate-001")

    video_service.generate_video_bytes(b"i", "p")
    assert captured["model"] == "veo-3.1-fast-generate-001"


def test_generate_video_bytes_raises_when_no_video(monkeypatch):
    op = _FakeOperation(videos=[], flips_after=0)
    _patch_client(monkeypatch, _FakeClient(op))
    with pytest.raises(video_service.NoVideoReturned):
        video_service.generate_video_bytes(b"i", "p")


def test_generate_video_bytes_times_out(monkeypatch):
    # never finishes; monotonic clock jumps past the timeout on the 2nd read
    ticks = iter([0.0, 0.0, 1000.0, 2000.0, 3000.0])
    monkeypatch.setattr(video_service.time, "monotonic", lambda: next(ticks))
    op = _FakeOperation(videos=None, flips_after=9999)
    _patch_client(monkeypatch, _FakeClient(op))
    with pytest.raises(video_service.VideoTimeout):
        video_service.generate_video_bytes(b"i", "p", poll_interval=1, poll_timeout=10)


def test_generate_video_bytes_wires_last_frame_and_reference(monkeypatch):
    captured = {}
    op = _FakeOperation(videos=[_FakeGenerated(_FakeVideo(b"V"))], flips_after=0)
    _patch_client(monkeypatch, _FakeClient(op, captured=captured))

    video_service.generate_video_bytes(
        b"start", "p", duration=8,
        last_frame_bytes=b"end",
        reference_image_bytes=[b"r1", b"r2"],
        person_generation="allow_adult",
        generate_audio=True,
    )
    cfg = captured["config"]
    assert cfg.last_frame is not None
    assert len(cfg.reference_images) == 2
    assert cfg.reference_images[0].reference_type == video_service.types.VideoGenerationReferenceType.ASSET
    assert cfg.person_generation == "allow_adult"
    assert cfg.generate_audio is True
    assert captured["image"] is not None
    assert captured["video"] is None


def test_generate_video_bytes_extension_uses_video_not_image(monkeypatch):
    captured = {}
    op = _FakeOperation(videos=[_FakeGenerated(_FakeVideo(b"V"))], flips_after=0)
    _patch_client(monkeypatch, _FakeClient(op, captured=captured))

    video_service.generate_video_bytes(
        None, "extend it", resolution="720p", extend_video_bytes=b"PRIORMP4",
    )
    assert captured["video"] is not None
    assert captured["image"] is None


def test_generate_video_bytes_text_to_video_no_media(monkeypatch):
    captured = {}
    op = _FakeOperation(videos=[_FakeGenerated(_FakeVideo(b"V"))], flips_after=0)
    _patch_client(monkeypatch, _FakeClient(op, captured=captured))

    video_service.generate_video_bytes(None, "a model walks", aspect_ratio="9:16")
    assert captured["image"] is None and captured["video"] is None
