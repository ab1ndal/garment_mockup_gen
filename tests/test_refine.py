"""Phase 6: prompt refinement (image + video meta-prompts, model call)."""
from types import SimpleNamespace

import pytest

from mockup_generator.prompts import refine


class _FakeModels:
    def __init__(self, text, sink):
        self._text, self._sink = text, sink

    def generate_content(self, *, model, contents, config):
        self._sink.append({"model": model, "contents": contents, "config": config})
        return SimpleNamespace(text=self._text)


class _FakeClient:
    def __init__(self, text, sink):
        self.models = _FakeModels(text, sink)


def _patch_client(monkeypatch, text="REFINED PROMPT BODY"):
    sink = []
    monkeypatch.setattr(refine, "get_genai_client", lambda: _FakeClient(text, sink))
    return sink


# --- meta-prompt builders -------------------------------------------------

def test_image_meta_has_house_markers_and_echoes_input():
    out = refine._image_meta("red silk saree, match the provided pattern details", "Saree").lower()
    assert "ultra-realistic" in out and "pixel" in out
    assert "do not" in out or "never" in out      # anti-hallucination directive
    assert "tag" in out                            # cleanup tail
    assert "match the provided pattern details" in out   # user directive preserved
    assert "saree" in out                          # category grounding


def test_video_meta_has_motion_markers_and_creativity():
    out = refine._video_meta("slow twirl, festive light, keep print exact", "Saree").lower()
    assert "camera" in out and "motion" in out
    assert "creative" in out                       # explicit creativity directive
    assert "pixel" in out or "exact" in out        # fidelity discipline kept
    assert "keep print exact" in out               # user directive preserved


# --- refine_prompt --------------------------------------------------------

def test_refine_returns_stripped_text(monkeypatch):
    _patch_client(monkeypatch, text="```text\n  A full prompt.\n```")
    assert refine.refine_prompt("thin", "Saree") == "A full prompt."


def test_image_kind_uses_lower_temperature_than_video(monkeypatch):
    sink = _patch_client(monkeypatch)
    refine.refine_prompt("x", "Saree", kind="image")
    refine.refine_prompt("x", "Saree", kind="video")
    img_temp = sink[0]["config"].temperature
    vid_temp = sink[1]["config"].temperature
    assert img_temp < vid_temp


def test_empty_instruction_raises():
    with pytest.raises(ValueError):
        refine.refine_prompt("   ")


def test_no_text_raises_refine_failed(monkeypatch):
    _patch_client(monkeypatch, text="")
    with pytest.raises(refine.RefineFailed):
        refine.refine_prompt("thin")


def test_video_meta_covers_veo_structure_and_is_one_paragraph():
    from mockup_generator.prompts.refine import _video_meta
    meta = _video_meta("model twirls in a red lehenga", "Lehenga")
    low = meta.lower()
    # VEO shot grammar + audio cue + single-paragraph instruction
    assert "camera" in low
    assert "audio" in low or "ambient" in low
    assert "one paragraph" in low or "single paragraph" in low
    # preserves the user's instruction and the category grounding
    assert "model twirls in a red lehenga" in meta
    assert "Lehenga" in meta
