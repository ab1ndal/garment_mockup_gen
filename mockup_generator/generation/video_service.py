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
    image_bytes: bytes | None = None,
    prompt: str = "",
    *,
    model: str | None = None,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    duration: int | None = None,
    negative_prompt: str | None = None,
    person_generation: str | None = None,
    generate_audio: bool | None = None,
    last_frame_bytes: bytes | None = None,
    reference_image_bytes: list[bytes] | None = None,
    extend_video_bytes: bytes | None = None,
    poll_timeout: int | None = None,
    poll_interval: int | None = None,
) -> bytes:
    """Generate an mp4 with VEO under ``prompt`` → mp4 bytes.

    Mode is inferred from the inputs: ``extend_video_bytes`` extends a prior
    clip; otherwise ``image_bytes`` is the first frame (and ``last_frame_bytes``
    the last, for interpolation); ``reference_image_bytes`` supply consistency
    assets; with none of these it is text-to-video. Blocks (polling) until the
    job finishes, the timeout elapses, or the model returns nothing.
    """
    model_name = model or settings.veo_model
    timeout = poll_timeout if poll_timeout is not None else settings.veo_poll_timeout_sec
    interval = poll_interval if poll_interval is not None else settings.veo_poll_interval_sec

    cfg_kwargs: dict = dict(
        aspect_ratio=aspect_ratio or ASPECT_RATIO,
        resolution=resolution or RESOLUTION,
        duration_seconds=duration if duration is not None else DURATION_SEC,
        number_of_videos=1,
        negative_prompt=negative_prompt if negative_prompt is not None else DEFAULT_NEGATIVE,
    )
    if person_generation:
        cfg_kwargs["person_generation"] = person_generation
    if generate_audio is not None:
        cfg_kwargs["generate_audio"] = generate_audio
    if last_frame_bytes:
        cfg_kwargs["last_frame"] = types.Image(image_bytes=last_frame_bytes, mime_type="image/png")
    if reference_image_bytes:
        cfg_kwargs["reference_images"] = [
            types.VideoGenerationReferenceImage(
                image=types.Image(image_bytes=b, mime_type="image/png"),
                reference_type=types.VideoGenerationReferenceType.ASSET,
            )
            for b in reference_image_bytes
        ]

    call_kwargs: dict = dict(
        model=model_name, prompt=prompt, config=types.GenerateVideosConfig(**cfg_kwargs),
    )
    if extend_video_bytes:
        call_kwargs["video"] = types.Video(video_bytes=extend_video_bytes, mime_type="video/mp4")
    elif image_bytes:
        call_kwargs["image"] = types.Image(image_bytes=image_bytes, mime_type="image/png")

    client = get_genai_client(settings.veo_location)
    operation = client.models.generate_videos(**call_kwargs)

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
