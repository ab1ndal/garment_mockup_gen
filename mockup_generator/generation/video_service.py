"""Web-oriented, in-memory VEO video generation service.

The legacy ``video.py`` engine is filesystem-oriented (reads image paths, writes
mp4 files). The web flow animates an already-published mockup whose bytes come
from Supabase Storage and wants the mp4 bytes back to stream to the browser for
download — nothing is persisted. This thin service reuses ``common`` (client
factory, config) without touching the CLI path.

VEO jobs take minutes: the poll loop is bounded by a configurable wall-clock
timeout (``VEO_POLL_TIMEOUT_SEC`` / ``VEO_POLL_INTERVAL_SEC``).
"""

from __future__ import annotations

import time

from google.genai import types

from mockup_generator.config import settings
from mockup_generator.generation.common import get_genai_client

ASPECT_RATIO = "9:16"
RESOLUTION = "720p"
DURATION_SEC = 4
DEFAULT_NEGATIVE = (
    "morphing faces, melting bodies, changing expressions, cartoon, illustration, "
    "drawing, painting, fast movement, jerky camera, blurry, distorted text, bad "
    "spelling, extra limbs, warm summer lighting, high contrast."
)


class NoVideoReturned(RuntimeError):
    """Raised when VEO completes but returns no usable video."""


class VideoTimeout(RuntimeError):
    """Raised when the VEO job does not finish within the poll timeout."""


def generate_video_bytes(
    image_bytes: bytes,
    prompt: str,
    *,
    model: str | None = None,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    duration: int | None = None,
    negative_prompt: str | None = None,
    poll_timeout: int | None = None,
    poll_interval: int | None = None,
) -> bytes:
    """Animate ``image_bytes`` with VEO under ``prompt`` → mp4 bytes.

    The first frame is the supplied mockup image. Blocks (polling) until the job
    finishes, the timeout elapses, or the model returns nothing.
    """
    model_name = model or settings.veo_model
    timeout = poll_timeout if poll_timeout is not None else settings.veo_poll_timeout_sec
    interval = poll_interval if poll_interval is not None else settings.veo_poll_interval_sec

    client = get_genai_client()
    operation = client.models.generate_videos(
        model=model_name,
        prompt=prompt,
        image=types.Image(image_bytes=image_bytes, mime_type="image/png"),
        config=types.GenerateVideosConfig(
            aspect_ratio=aspect_ratio or ASPECT_RATIO,
            resolution=resolution or RESOLUTION,
            duration_seconds=duration if duration is not None else DURATION_SEC,
            number_of_videos=1,
            negative_prompt=negative_prompt if negative_prompt is not None else DEFAULT_NEGATIVE,
        ),
    )

    start = time.monotonic()
    while not operation.done:
        if time.monotonic() - start > timeout:
            raise VideoTimeout(f"VEO job exceeded {timeout}s (op={operation.name})")
        time.sleep(interval)
        operation = client.operations.get(operation)

    result = getattr(operation, "response", None)
    videos = getattr(result, "generated_videos", None) if result else None
    if not videos:
        raise NoVideoReturned(f"VEO returned no video (op={operation.name}, error={operation.error})")

    generated = videos[0]
    client.files.download(file=generated.video)
    data = getattr(generated.video, "video_bytes", None)
    if not data:
        raise NoVideoReturned("VEO video had no bytes after download")
    return data
