"""Shared helpers for the generation engines.

Deduplicated from the former create_base / create_video / create_mockup
modules. No web-framework imports here — this is pure core.
"""

from __future__ import annotations

import time
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from random import random

from PIL import Image

from google import genai
from google.genai import errors, types

from mockup_generator.config import settings

MAX_SIDE = 1024
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp"}


@lru_cache(maxsize=1)
def get_genai_client() -> genai.Client:
    """Lazily build the Google GenAI client from settings (cached).

    When ``GOOGLE_GENAI_USE_VERTEXAI`` is set, route through Vertex AI so calls
    bill against the GCP project's pay-as-you-go account (uses Application
    Default Credentials) instead of AI Studio prepay credits. Otherwise fall
    back to the Gemini Developer API with an API key.
    """
    if settings.use_vertex:
        return genai.Client(
            vertexai=True,
            project=settings.google_cloud_project,
            location=settings.google_cloud_location,
            credentials=_vertex_credentials(),
        )
    return genai.Client(api_key=settings.google_api_key)


def _vertex_credentials():
    """Service-account credentials for Vertex, or None to use ADC.

    Headless deploys (HF Spaces) have no user ADC, so load a service-account
    key from ``GOOGLE_VERTEX_SA_JSON`` / ``GOOGLE_DRIVE_SA_JSON`` (path or raw
    JSON). Locally the env is unset and we return None so the client uses ADC.
    """
    import json

    raw = settings.vertex_sa_json
    if not raw:
        return None
    from google.oauth2 import service_account

    info = json.loads(raw) if raw.lstrip().startswith("{") else json.load(open(raw))
    return service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )


def part_from_pil(im: Image.Image, fmt: str = "JPEG", quality: int = 90) -> types.Part:
    buf = BytesIO()
    im.save(buf, format=fmt, quality=quality)
    return types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg")


def load_images_from_folder(
    folder: Path, max_side: int = MAX_SIDE, limit: int = 2
) -> list[Image.Image]:
    imgs: list[Image.Image] = []
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.suffix.lower() in ALLOWED_EXT and not p.name.startswith("."):
            try:
                im = Image.open(p).convert("RGB")
                im.thumbnail((max_side, max_side))
                imgs.append(im)
                if len(imgs) >= limit:
                    break
            except Exception:
                print(f"Skip unreadable image: {p}")
    return imgs


def no_image_reason(response) -> str:
    """Why a response carries no image, in words a reviewer can act on.

    A refusal is a normal outcome, not a malformed response: the model answers
    with a candidate whose ``finish_reason`` says NO_IMAGE / a safety verdict and
    whose ``content.parts`` is None — or with no candidates at all. Reach for
    ``candidates[0].content.parts`` directly and that shape raises ``'NoneType'
    object is not iterable``, which tells a reviewer nothing about the cause.
    """
    candidates = getattr(response, "candidates", None)
    if not candidates:
        feedback = getattr(response, "prompt_feedback", None)
        return f"the model returned no candidates (prompt_feedback: {feedback})"
    candidate = candidates[0]
    reason = getattr(candidate, "finish_reason", None)
    detail = getattr(candidate, "finish_message", None)
    ratings = getattr(candidate, "safety_ratings", None)
    parts = [f"the model returned no image (finish_reason: {reason})"]
    if detail:
        parts.append(str(detail))
    if ratings:
        parts.append(f"safety_ratings: {ratings}")
    return "; ".join(parts)


def _image_parts(response):
    """The response's parts, or an empty list when the model returned no image."""
    candidates = getattr(response, "candidates", None)
    if not candidates:
        return []
    content = getattr(candidates[0], "content", None)
    return getattr(content, "parts", None) or []


def first_image_bytes(response) -> bytes | None:
    """Return image bytes of the first image part, preserving the model's
    format (JPEG stays JPEG, everything else normalized to PNG). Returns None
    when the response carries no image at all — see ``no_image_reason``."""
    for part in _image_parts(response):
        blob = getattr(part, "inline_data", None)
        data = getattr(blob, "data", None)
        if not data:
            continue
        mime = getattr(blob, "mime_type", "") or ""
        if mime and not mime.startswith("image/"):
            continue
        img = Image.open(BytesIO(data))
        out_fmt = "JPEG" if (img.format or "PNG").upper() in ("JPEG", "JPG") else "PNG"
        buf = BytesIO()
        img.convert("RGB").save(buf, format=out_fmt)
        return buf.getvalue()
    return None


def save_first_image_part(response, save_path: Path) -> bool:
    for part in response.candidates[0].content.parts:
        if getattr(part, "inline_data", None):
            img = Image.open(BytesIO(part.inline_data.data))
            img.save(save_path)
            return True
    return False


def generate_with_retries(
    model_name: str,
    contents,
    *,
    aspect_ratio: str = "1:1",
    resolution: str = "4K",
    person_generation: str | None = None,
    system_instruction: str | None = None,
    output_mime_type: str | None = None,
    output_compression_quality: int | None = None,
    thinking_level: str | None = None,
    max_attempts: int = 8,
):
    """Call Gemini image generation with exponential backoff on 429/5xx.

    ``person_generation`` is left unset by default: the google-genai 2.x SDK
    accepts it on ``ImageConfig`` client-side, but the **Gemini Developer API
    (api-key) rejects it at call time** ("only supported in Gemini Enterprise
    Agent Platform mode"). Pass it only when running against Vertex.

    ``output_mime_type`` / ``output_compression_quality`` control the format
    returned by the model (default: PNG, no quality override). Pass
    ``output_mime_type="image/jpeg"`` to get JPEG output.

    ``thinking_level`` enables extended thinking (``"low"``, ``"medium"``,
    ``"high"``); omit for default model behaviour.
    """
    image_config = types.ImageConfig(aspect_ratio=aspect_ratio, image_size=resolution)
    if person_generation is not None:
        image_config.person_generation = person_generation
    if output_mime_type is not None or output_compression_quality is not None:
        image_config.image_output_options = types.ImageConfigImageOutputOptions(
            mime_type=output_mime_type,
            compression_quality=output_compression_quality,
        )
    thinking_config = (
        types.ThinkingConfig(thinking_level=thinking_level) if thinking_level else None
    )
    client = get_genai_client()
    if system_instruction is None:
        system_instruction = (
            "You are a professional fashion editor for Bindal's Creation. "
            "Always produce high-end, editorial quality images. Garments must "
            "be wrinkle-free and tailored."
        )
    safety_settings = [
        types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
        types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
    ]
    # Backoff is tuned to the shape of the failure, not one-size-fits-all:
    #
    # 429 is shared-capacity throttling, not "server dying". gemini-*-image is
    # global-only and its pool admits only ~1-2 concurrent requests, so a freed
    # slot opens roughly every generation (~18-20s). A 429 is a cheap fast-reject
    # (~1.5s, no capacity cost — request-count quota is effectively unlimited), so
    # the right move is to retry OFTEN with a LOW cap: backing off to 60s just
    # idles a worker straight past open slots. Full jitter (sleep in [floor, wait])
    # both spreads synchronised workers and lets a worker retry near-immediately to
    # grab a slot the instant it frees.
    #
    # 5xx is genuine server trouble — back off harder (cap 60s).
    _RL_CAP = 20   # 429: near the ~18-20s slot cadence, so retries land on freed slots
    _SRV_CAP = 60  # 5xx: real server error, ease off
    wait = 1
    for attempt in range(1, max_attempts + 1):
        try:
            return client.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_modalities=["IMAGE"],
                    safety_settings=safety_settings,
                    image_config=image_config,
                    thinking_config=thinking_config,
                ),
            )
        except errors.ClientError as e:
            # The SDK's APIError carries the HTTP status as ``code``; ``status_code``
            # is only a local in errors.py and never reaches the exception, so
            # reading it here silently made every 429 permanent.
            if getattr(e, "code", None) == 429 and attempt < max_attempts:
                time.sleep(0.25 + random() * wait)  # full jitter → catch freed slots
                wait = min(wait * 2, _RL_CAP)
                continue
            raise
        except errors.ServerError:
            if attempt < max_attempts:
                time.sleep(wait * (1 + random()))
                wait = min(wait * 2, _SRV_CAP)
                continue
            raise
