"""Generation endpoints.

``/image`` is preview-only: download the selected Drive reference images,
generate a mockup with Gemini, and return it as base64 — it writes nothing.
``/approve`` is the sole writer: it uploads the (generated or corrected) image
to the public ``mockups`` bucket, appends a ``mockup_variations`` audit row,
flips ``mockups.base_mockup``, and replaces the product+color ``productimages``
row (cleaning up the prior Storage object). ``/video`` is still a stub.
"""

from __future__ import annotations

import base64
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from io import BytesIO

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from PIL import Image
from supabase import Client

from backend.auth import CurrentUser, get_current_user
from backend.deps import get_db
from backend.schemas import (
    ApproveResponse, GeneratePreview, GenerateRequest, VideoGenerateRequest, VideoJobResponse,
)
from mockup_generator.config import settings
from mockup_generator.db import productimages_repo, products_repo
from mockup_generator.generation import publish, service, video_service
from mockup_generator.integrations import drive_client, storage_client
from mockup_generator.integrations.drive_client import DriveNotConfigured

router = APIRouter(prefix="/api/generate", tags=["generate"])

log = logging.getLogger(__name__)


def _decode_b64_image(b64: str) -> Image.Image:
    """Decode a base64 PNG/JPEG into a PIL image, or raise 400 on bad input."""
    try:
        raw = base64.b64decode(b64, validate=True)
        return Image.open(BytesIO(raw)).convert("RGB")
    except Exception as exc:  # noqa: BLE001 - any decode/parse failure is a bad request
        raise HTTPException(status_code=400, detail="Invalid refine image.") from exc


_MAX_REFS = 14
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB

# Selectable image-generation options surfaced to the portal.
ALLOWED_MODELS = ["gemini-3-pro-image", "gemini-3.1-flash-image", "gemini-2.5-flash-image"]
ALLOWED_RESOLUTIONS = ["1K", "2K", "4K"]          # 1K and 2K cost the same → default 2K
ALLOWED_ASPECTS = ["1:1", "3:4", "4:3", "9:16", "16:9", "2:3", "3:2", "21:9"]  # model-supported set
_DEFAULTS = {"model": "gemini-3-pro-image", "resolution": "2K", "aspect_ratio": "1:1"}

# Selectable video-generation options. 1080p requires duration 8s (VEO constraint).
ALLOWED_VEO_MODELS = [
    "veo-3.1-generate-preview", "veo-3.1-fast-generate-preview", "veo-3.1-lite-generate-preview",
]
ALLOWED_VIDEO_RESOLUTIONS = ["720p", "1080p"]
ALLOWED_VIDEO_ASPECTS = ["9:16", "16:9"]
ALLOWED_VIDEO_DURATIONS = [4, 6, 8]
_VIDEO_DEFAULTS = {"model": "veo-3.1-generate-preview", "resolution": "720p",
                   "aspect_ratio": "9:16", "duration": 4}


# ── In-memory video jobs ──────────────────────────────────────────────────────
# VEO renders take minutes — longer than a reverse proxy (HF Space) will hold a
# request open. So /video enqueues a background thread and returns a job_id; the
# client polls /video/{job_id} until the mp4 is ready, then downloads it. The
# bytes live only in this process's memory and are evicted on download (or by
# TTL) — nothing is persisted to Storage. NOTE: single-process only; a
# multi-worker deploy would not share this dict.
@dataclass
class _VideoJob:
    status: str                      # pending | running | done | error
    filename: str
    data: bytes | None = None
    detail: str | None = None
    created: float = field(default_factory=time.monotonic)


_video_jobs: dict[str, _VideoJob] = {}
_video_jobs_lock = threading.Lock()
_VIDEO_JOB_TTL = 1800.0  # 30 min — reap abandoned jobs so memory can't grow unbounded


def _reap_video_jobs() -> None:
    now = time.monotonic()
    with _video_jobs_lock:
        stale = [k for k, v in _video_jobs.items() if now - v.created > _VIDEO_JOB_TTL]
        for k in stale:
            _video_jobs.pop(k, None)


def _set_job(job_id: str, **changes) -> None:
    with _video_jobs_lock:
        job = _video_jobs.get(job_id)
        if job is not None:
            for k, v in changes.items():
                setattr(job, k, v)


def _spawn(fn, *args) -> None:
    """Run ``fn`` off the request thread. Overridden in tests to run inline."""
    threading.Thread(target=fn, args=args, daemon=True).start()


def _run_video_job(job_id, image_bytes, prompt, model, aspect_ratio, resolution, duration) -> None:
    _set_job(job_id, status="running")
    try:
        mp4 = video_service.generate_video_bytes(
            image_bytes, prompt, model=model, aspect_ratio=aspect_ratio,
            resolution=resolution, duration=duration,
        )
        _set_job(job_id, status="done", data=mp4)
    except video_service.VideoTimeout:
        _set_job(job_id, status="error", detail="Video generation timed out. Try again.")
    except video_service.NoVideoReturned:
        _set_job(job_id, status="error", detail="The model returned no video. Try again.")
    except Exception as exc:  # noqa: BLE001 - surface any failure to the poller
        _set_job(job_id, status="error", detail=f"Video generation failed: {exc}")


@router.get("/options")
def generation_options(user: CurrentUser = Depends(get_current_user)):
    """Selectable model / quality / aspect-ratio choices + recommended defaults."""
    models = ALLOWED_MODELS.copy()
    # ensure the env-configured default is offered even if not in the static list
    if settings.gemini_image_model not in models:
        models.insert(0, settings.gemini_image_model)
    video_models = ALLOWED_VEO_MODELS.copy()
    if settings.veo_model not in video_models:
        video_models.insert(0, settings.veo_model)
    return {
        "models": models,
        "resolutions": ALLOWED_RESOLUTIONS,
        "aspect_ratios": ALLOWED_ASPECTS,
        "defaults": {**_DEFAULTS, "model": settings.gemini_image_model},
        "video_models": video_models,
        "video_resolutions": ALLOWED_VIDEO_RESOLUTIONS,
        "video_aspect_ratios": ALLOWED_VIDEO_ASPECTS,
        "video_durations": ALLOWED_VIDEO_DURATIONS,
        "video_defaults": {**_VIDEO_DEFAULTS, "model": settings.veo_model},
    }


@router.post("/image", response_model=GeneratePreview)
def generate_image(req: GenerateRequest, user: CurrentUser = Depends(get_current_user),
                   db: Client = Depends(get_db)):
    if req.model is not None and req.model not in ALLOWED_MODELS:
        raise HTTPException(status_code=400, detail=f"Unsupported model: {req.model}")
    if req.resolution is not None and req.resolution not in ALLOWED_RESOLUTIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported resolution: {req.resolution}")
    if req.aspect_ratio is not None and req.aspect_ratio not in ALLOWED_ASPECTS:
        raise HTTPException(status_code=400, detail=f"Unsupported aspect ratio: {req.aspect_ratio}")

    if not req.image_ids and not req.refine_image_b64:
        raise HTTPException(status_code=400, detail="Select at least one source image.")

    # Decode the refine reference first so bad input fails fast as a 400.
    refine_img = _decode_b64_image(req.refine_image_b64) if req.refine_image_b64 else None

    product = products_repo.get_product(db, req.productid)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    images: list[Image.Image] = []
    ref_ids = req.image_ids[:_MAX_REFS]
    if ref_ids:
        folder_id = drive_client.extract_folder_id(product.producturl)
        if not folder_id:
            raise HTTPException(status_code=400, detail="Product has no linked Drive folder")
        try:
            images = [Image.open(BytesIO(drive_client.download_file(fid))) for fid in ref_ids]
        except DriveNotConfigured as exc:
            raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Could not download Drive images: {exc}") from exc

    # Sources own the reference budget; the refine image is appended only if room.
    if refine_img is not None:
        if len(images) < _MAX_REFS:
            images.append(refine_img)
        else:
            log.warning("refine image dropped: %d source refs already at cap %d",
                        len(images), _MAX_REFS)

    try:
        png = service.generate_mockup_bytes(
            images, req.prompt,
            model=req.model, resolution=req.resolution, aspect_ratio=req.aspect_ratio,
        )
    except service.NoImageReturned as exc:
        raise HTTPException(status_code=502, detail="The model returned no image. Try again.") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Image generation failed: {exc}") from exc

    return GeneratePreview(
        status="ok", detail="Preview generated.",
        image_b64=base64.b64encode(png).decode("ascii"),
    )


@router.post("/approve", response_model=ApproveResponse)
async def approve_mockup(
    productid: str = Form(...),
    color: str | None = Form(None),
    prompt_text: str | None = Form(None),
    theme_name: str | None = Form(None),
    aspect_ratio: str | None = Form(None),
    source: str = Form("generated"),
    image: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
    db: Client = Depends(get_db),
):
    raw = await image.read()
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image is too large.")
    try:
        Image.open(BytesIO(raw)).verify()              # cheap validity check
        png_img = Image.open(BytesIO(raw)).convert("RGB")  # reopen (verify exhausts it)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid image.") from exc

    buf = BytesIO()
    png_img.save(buf, format="PNG")
    png = buf.getvalue()

    text = prompt_text or ("(manual upload)" if source == "corrected" else "")
    try:
        result = publish.publish_image(
            db, productid=productid, png=png, color=color,
            theme_name=theme_name, aspect_ratio=aspect_ratio,
            created_by=user.id, prompt_text=text,
        )
    except storage_client.StorageNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Storage is not configured on the server") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not store the mockup: {exc}") from exc

    return ApproveResponse(
        status="ok", detail="Published.",
        image_url=result["image_url"], variation_id=result["variation_id"],
    )


@router.post("/video", response_model=VideoJobResponse)
def generate_video(req: VideoGenerateRequest, user: CurrentUser = Depends(get_current_user),
                   db: Client = Depends(get_db)):
    """Enqueue a VEO render of an already-published Supabase mockup. Returns a
    job_id immediately; poll ``/video/{job_id}`` for the mp4. Persists nothing —
    the video lives in memory until downloaded, then is evicted."""
    if req.model is not None and req.model not in ALLOWED_VEO_MODELS:
        raise HTTPException(status_code=400, detail=f"Unsupported video model: {req.model}")
    if req.resolution is not None and req.resolution not in ALLOWED_VIDEO_RESOLUTIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported video resolution: {req.resolution}")
    if req.aspect_ratio is not None and req.aspect_ratio not in ALLOWED_VIDEO_ASPECTS:
        raise HTTPException(status_code=400, detail=f"Unsupported video aspect ratio: {req.aspect_ratio}")
    if req.duration is not None and req.duration not in ALLOWED_VIDEO_DURATIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported video duration: {req.duration}")
    # VEO: 1080p only renders at 8s.
    if req.resolution == "1080p" and (req.duration or _VIDEO_DEFAULTS["duration"]) != 8:
        raise HTTPException(status_code=400, detail="1080p video requires an 8-second duration.")

    # Resolve the source mockup that lives in Supabase Storage.
    object_path = storage_client.path_from_public_url(req.image_url or "") if req.image_url else None
    if req.image_url and object_path is None:
        raise HTTPException(status_code=400, detail="image_url is not a mockups-bucket URL.")
    if object_path is None:
        rows = productimages_repo.list_for(db, req.productid, req.color)
        url = next((r.get("imageurl") for r in rows if r.get("imageurl")), None)
        object_path = storage_client.path_from_public_url(url or "") if url else None
    if object_path is None:
        raise HTTPException(status_code=400,
                            detail="No published mockup to animate. Approve & publish an image first.")

    try:
        image_bytes = storage_client.download_mockup(object_path)
    except storage_client.StorageNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Storage is not configured on the server") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not load the source mockup: {exc}") from exc

    # Source resolved synchronously (so config errors fail fast); the slow VEO
    # call runs in the background.
    _reap_video_jobs()
    job_id = uuid.uuid4().hex
    slug = storage_client.slugify(req.color)
    filename = f"{req.productid}_{slug}.mp4" if slug else f"{req.productid}.mp4"
    with _video_jobs_lock:
        _video_jobs[job_id] = _VideoJob(status="pending", filename=filename)
    _spawn(_run_video_job, job_id, image_bytes, req.prompt,
           req.model, req.aspect_ratio, req.resolution, req.duration)
    return VideoJobResponse(job_id=job_id, status="pending")


@router.get("/video/{job_id}")
def video_job(job_id: str, user: CurrentUser = Depends(get_current_user)):
    """Poll a video job. While running → JSON status; on error → JSON status +
    detail; when done → stream the mp4 (and evict the job)."""
    with _video_jobs_lock:
        job = _video_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown or expired video job.")
    if job.status in ("pending", "running"):
        return VideoJobResponse(job_id=job_id, status=job.status)
    if job.status == "error":
        with _video_jobs_lock:
            _video_jobs.pop(job_id, None)
        return VideoJobResponse(job_id=job_id, status="error", detail=job.detail)
    # done — hand over the bytes once, then drop them from memory
    with _video_jobs_lock:
        _video_jobs.pop(job_id, None)
    return StreamingResponse(
        BytesIO(job.data or b""), media_type="video/mp4",
        headers={"Content-Disposition": f'attachment; filename="{job.filename}"'},
    )
