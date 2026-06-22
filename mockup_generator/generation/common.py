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
    """Lazily build the Google GenAI client from settings (cached)."""
    return genai.Client(api_key=settings.google_api_key)


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
    system_instruction: str | None = None,
    max_attempts: int = 5,
):
    """Call Gemini image generation with exponential backoff on 429/5xx."""
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
    wait = 8
    for attempt in range(1, max_attempts + 1):
        try:
            return client.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_modalities=["IMAGE"],
                    safety_settings=safety_settings,
                    image_config=types.ImageConfig(
                        aspect_ratio=aspect_ratio,
                        image_size=resolution,
                    ),
                ),
            )
        except errors.ClientError as e:
            if getattr(e, "status_code", None) == 429 and attempt < max_attempts:
                time.sleep(wait)
                wait = min(wait * 2, 60)
                continue
            raise
        except errors.ServerError:
            if attempt < max_attempts:
                time.sleep(int(wait * (1 + random())))
                wait = min(wait * 2, 60)
                continue
            raise
