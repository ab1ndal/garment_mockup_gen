"""Web-oriented, in-memory image generation service.

The legacy ``images.py`` engine is filesystem-oriented (reads a dir of paths,
writes png files). The web flow has reference images already in memory (bytes
downloaded from Drive) and wants PNG bytes back to upload to Storage. This thin
service reuses ``common`` (retry/backoff, config) without touching the CLI path.
"""

from __future__ import annotations

from PIL import Image

from mockup_generator.config import settings
from mockup_generator.generation.common import (
    MAX_SIDE,
    first_image_bytes,
    generate_with_retries,
    part_from_pil,
)

ASPECT_RATIO = "1:1"
RESOLUTION = "4K"


class NoImageReturned(RuntimeError):
    """Raised when Gemini returns a response with no image part."""


def generate_mockup_bytes(
    images: list[Image.Image],
    prompt: str,
    *,
    model: str | None = None,
    resolution: str | None = None,
    aspect_ratio: str | None = None,
    output_mime_type: str | None = None,
    output_compression_quality: int | None = None,
    person_generation: str | None = None,
    thinking_level: str | None = None,
) -> bytes:
    """Generate one mockup from reference ``images`` + ``prompt`` → image bytes
    (PNG by default, JPEG when ``output_mime_type='image/jpeg'``).
    JPEG output converts each image to RGB first, flattening any alpha channel."""
    model_name = model or settings.gemini_image_model
    parts = []
    for im in images:
        im = im.convert("RGB")
        im.thumbnail((MAX_SIDE, MAX_SIDE))
        parts.append(part_from_pil(im))

    contents = [prompt, *parts]
    response = generate_with_retries(
        model_name, contents,
        aspect_ratio=aspect_ratio or ASPECT_RATIO,
        resolution=resolution or RESOLUTION,
        output_mime_type=output_mime_type,
        output_compression_quality=output_compression_quality,
        person_generation=person_generation,
        thinking_level=thinking_level,
    )
    data = first_image_bytes(response)
    if data is None:
        raise NoImageReturned("Gemini returned no image part")
    return data
