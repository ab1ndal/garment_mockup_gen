"""On-demand prompt refinement.

Turns a freeform instruction (a thin keyword, a themed brief, or one carrying
explicit directives) into a full house-style, Gemini-optimized prompt. Two
contracts: image (faithful, structured expansion) and video (richer, more
creative motion direction). Stateless — calls the configured text model via the
shared genai client and returns text. Persists nothing.
"""

from __future__ import annotations

import time
from random import random

from google.genai import errors, types

from mockup_generator.config import settings
from mockup_generator.generation.common import get_genai_client

_IMAGE_TEMPERATURE = 0.4
_VIDEO_TEMPERATURE = 0.9
_MAX_ATTEMPTS = 4

_SYSTEM = (
    "You are a senior prompt engineer for Bindal's Creation, a luxury Indian "
    "ethnic-wear brand. You rewrite a user's rough instruction into a single, "
    "production-ready generation prompt. Output ONLY the final prompt text — no "
    "preamble, no explanation, no markdown fences."
)

# One shipped exemplar keeps the model anchored to the house structure without
# bloating the request. Imported lazily to avoid a heavy import at module load.

def _image_exemplar() -> str:
    from mockup_generator.prompts.defaults import SAREE_PROMPT
    return SAREE_PROMPT


class RefineFailed(RuntimeError):
    """The text model returned no usable prompt text."""


def _category_line(category_name: str | None) -> str:
    if category_name:
        return f"The garment category is: {category_name}. Ground every detail in this garment type.\n"
    return ""


def _image_meta(instruction: str, category_name: str | None) -> str:
    return (
        "Rewrite the user's instruction into ONE ultra-realistic, hyper-detailed "
        "image-generation prompt in the house style.\n"
        + _category_line(category_name)
        + "Required structure: garment specs -> model requirements -> "
        "technical/aesthetic specs -> anti-hallucination and final cleanup tail.\n"
        "Hard rules:\n"
        "- Demand pixel-for-pixel fidelity to the uploaded reference; DO NOT invent "
        "motifs, colors, prints, or silhouettes.\n"
        "- End with a cleanup directive removing all tags, pins, stands, and labels.\n"
        "- Preserve EVERY explicit instruction the user gave, verbatim in intent "
        "(mood, must-keep details, length, color). Drop nothing.\n"
        "- Output the prompt text only.\n\n"
        "Follow the structure and tone of this shipped example:\n"
        f"<<<EXAMPLE>>>\n{_image_exemplar()}\n<<<END EXAMPLE>>>\n\n"
        f"User instruction:\n{instruction.strip()}"
    )


def _video_meta(instruction: str, category_name: str | None) -> str:
    return (
        "Rewrite the user's instruction into ONE short product video-generation "
        "prompt for Google VEO. Expand a thin description into a vivid, creative, "
        "shot-described clip while keeping the garment pixel-faithful to the "
        "reference.\n"
        + _category_line(category_name)
        + "Write it as one single paragraph (NOT a list), and cover, in a "
        "natural order: subject and wardrobe; the action/motion (fabric flow and "
        "drape, a turn, twirl, or step); clear camera and shot language (slow "
        "push-in, gentle dolly, orbit, or pan) with pacing for a few-second clip "
        "and a loop-friendly resolve; lighting and mood; and a brief ambient "
        "audio cue (the soft rustle of fabric, gentle room tone — VEO renders "
        "native audio).\n"
        "Hard rules:\n"
        "- Keep the garment pixel-faithful: DO NOT invent motifs, colors, or "
        "change the silhouette.\n"
        "- Preserve EVERY explicit instruction the user gave, verbatim in intent. "
        "Drop nothing.\n"
        "- Keep it to one paragraph; output the prompt text only.\n\n"
        f"User instruction:\n{instruction.strip()}"
    )


def _strip(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        # drop the opening fence (``` or ```lang) and the trailing fence
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1 :]
        if text.endswith("```"):
            text = text[: -3]
    return text.strip()


def _generate_text(contents: str, temperature: float) -> str:
    client = get_genai_client()
    config = types.GenerateContentConfig(
        system_instruction=_SYSTEM,
        temperature=temperature,
    )
    wait = 8
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = client.models.generate_content(
                model=settings.gemini_text_model,
                contents=contents,
                config=config,
            )
            return getattr(resp, "text", "") or ""
        except errors.ClientError as e:
            # ``code``, not ``status_code`` — see generation/common.py.
            if getattr(e, "code", None) == 429 and attempt < _MAX_ATTEMPTS:
                time.sleep(wait)
                wait = min(wait * 2, 60)
                continue
            raise
        except errors.ServerError:
            if attempt < _MAX_ATTEMPTS:
                time.sleep(int(wait * (1 + random())))
                wait = min(wait * 2, 60)
                continue
            raise
    raise RefineFailed("exhausted retries without a response")


def refine_prompt(
    instruction: str,
    category_name: str | None = None,
    *,
    kind: str = "image",
) -> str:
    """Expand a freeform instruction into a full house-style prompt.

    ``kind`` selects the image or video contract (and temperature). Raises
    ``ValueError`` on an empty instruction and ``RefineFailed`` when the model
    returns no usable text.
    """
    if not instruction or not instruction.strip():
        raise ValueError("instruction is empty")
    if kind == "video":
        contents = _video_meta(instruction, category_name)
        temperature = _VIDEO_TEMPERATURE
    else:
        contents = _image_meta(instruction, category_name)
        temperature = _IMAGE_TEMPERATURE
    text = _strip(_generate_text(contents, temperature))
    if not text:
        raise RefineFailed("model returned no text")
    return text
