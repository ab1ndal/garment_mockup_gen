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
    def generate_videos(self, *, model, prompt, image, config):
        self._captured["model"] = model
        self._captured["prompt"] = prompt
        self._captured["image"] = image
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


def _patch_client(monkeypatch, client):
    monkeypatch.setattr(video_service, "get_genai_client", lambda: client)


def test_generate_video_bytes_returns_mp4(monkeypatch):
    captured = {}
    op = _FakeOperation(videos=[_FakeGenerated(_FakeVideo(b"MP4DATA"))], flips_after=0)
    _patch_client(monkeypatch, _FakeClient(op, captured=captured))

    out = video_service.generate_video_bytes(
        b"imgbytes", "a slow pan", model="veo-3.1-generate-preview",
        aspect_ratio="9:16", resolution="720p", duration=4,
    )

    assert out == b"MP4DATA"
    assert captured["model"] == "veo-3.1-generate-preview"
    cfg = captured["config"]
    assert cfg.aspect_ratio == "9:16"
    assert cfg.resolution == "720p"
    assert cfg.duration_seconds == 4


def test_generate_video_bytes_polls_until_done(monkeypatch):
    op = _FakeOperation(videos=[_FakeGenerated(_FakeVideo(b"V"))], flips_after=3)
    _patch_client(monkeypatch, _FakeClient(op))

    out = video_service.generate_video_bytes(b"i", "p", poll_interval=1, poll_timeout=100)
    assert out == b"V"


def test_generate_video_bytes_uses_configured_model(monkeypatch):
    captured = {}
    op = _FakeOperation(videos=[_FakeGenerated(_FakeVideo(b"V"))])
    _patch_client(monkeypatch, _FakeClient(op, captured=captured))
    monkeypatch.setenv("VEO_MODEL", "veo-3.1-fast-generate-preview")

    video_service.generate_video_bytes(b"i", "p")
    assert captured["model"] == "veo-3.1-fast-generate-preview"


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
