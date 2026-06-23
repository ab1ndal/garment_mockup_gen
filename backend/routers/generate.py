"""Generation endpoints.

``/image`` is the Phase-3 engine: resolve a product's Drive folder, download
reference images, generate a mockup with Gemini, upload it to Supabase Storage,
record a ``mockup_variations`` row, and return a signed URL. ``/video`` is still
a stub (next session).
"""

from __future__ import annotations

import uuid
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from PIL import Image
from supabase import Client

from backend.auth import CurrentUser, get_current_user
from backend.deps import get_db
from backend.schemas import GenerateRequest, GenerateResponse
from mockup_generator.config import settings
from mockup_generator.db import mockup_variations_repo, products_repo
from mockup_generator.generation import service
from mockup_generator.integrations import drive_client, storage_client
from mockup_generator.integrations.drive_client import DriveNotConfigured

router = APIRouter(prefix="/api/generate", tags=["generate"])

_MAX_REFS = 14
_NOT_READY = "Video generation is enabled in a later phase."

# Selectable image-generation options surfaced to the portal.
ALLOWED_MODELS = ["gemini-3-pro-image", "gemini-3.1-flash-image", "gemini-2.5-flash-image"]
ALLOWED_RESOLUTIONS = ["1K", "2K", "4K"]          # 1K and 2K cost the same → default 2K
ALLOWED_ASPECTS = ["1:1", "3:4", "4:3", "9:16", "16:9", "2:3", "3:2", "21:9"]  # model-supported set
_DEFAULTS = {"model": "gemini-3-pro-image", "resolution": "2K", "aspect_ratio": "1:1"}


@router.get("/options")
def generation_options(user: CurrentUser = Depends(get_current_user)):
    """Selectable model / quality / aspect-ratio choices + recommended defaults."""
    models = ALLOWED_MODELS.copy()
    # ensure the env-configured default is offered even if not in the static list
    if settings.gemini_image_model not in models:
        models.insert(0, settings.gemini_image_model)
    return {
        "models": models,
        "resolutions": ALLOWED_RESOLUTIONS,
        "aspect_ratios": ALLOWED_ASPECTS,
        "defaults": {**_DEFAULTS, "model": settings.gemini_image_model},
    }


def _resolve_ref_ids(folder_id: str, image_ids: list[str]) -> list[str]:
    """The requested ids, or every image in the folder (loose + variant groups)."""
    if image_ids:
        return image_ids[:_MAX_REFS]
    grouped = drive_client.list_folder_image_groups(folder_id)
    ids = [img["id"] for img in grouped.get("loose", [])]
    for g in grouped.get("groups", []):
        ids.extend(img["id"] for img in g.get("images", []))
    return ids[:_MAX_REFS]


@router.post("/image", response_model=GenerateResponse)
def generate_image(req: GenerateRequest, user: CurrentUser = Depends(get_current_user),
                   db: Client = Depends(get_db)):
    if req.model is not None and req.model not in ALLOWED_MODELS:
        raise HTTPException(status_code=400, detail=f"Unsupported model: {req.model}")
    if req.resolution is not None and req.resolution not in ALLOWED_RESOLUTIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported resolution: {req.resolution}")
    if req.aspect_ratio is not None and req.aspect_ratio not in ALLOWED_ASPECTS:
        raise HTTPException(status_code=400, detail=f"Unsupported aspect ratio: {req.aspect_ratio}")

    product = products_repo.get_product(db, req.productid)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    folder_id = drive_client.extract_folder_id(product.producturl)
    if not folder_id:
        raise HTTPException(status_code=400, detail="Product has no linked Drive folder")

    try:
        ref_ids = _resolve_ref_ids(folder_id, req.image_ids)
    except DriveNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not read Drive folder: {exc}") from exc

    if not ref_ids:
        raise HTTPException(status_code=400, detail="No source images found for this product")

    try:
        images = [Image.open(BytesIO(drive_client.download_file(fid))) for fid in ref_ids]
    except DriveNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Drive access is not configured on the server") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not download Drive images: {exc}") from exc

    try:
        png = service.generate_mockup_bytes(
            images, req.prompt,
            model=req.model, resolution=req.resolution, aspect_ratio=req.aspect_ratio,
        )
    except service.NoImageReturned as exc:
        raise HTTPException(status_code=502, detail="The model returned no image. Try again.") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Image generation failed: {exc}") from exc

    try:
        object_path, signed_url = storage_client.upload_mockup(
            req.productid, png, uuid.uuid4().hex
        )
    except storage_client.StorageNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Storage is not configured on the server") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not store the mockup: {exc}") from exc

    row = mockup_variations_repo.insert(
        db, productid=req.productid, prompt_text=req.prompt,
        image_url=object_path, created_by=user.id,
    )

    return GenerateResponse(
        status="ok", detail="Mockup generated.",
        image_url=signed_url, variation_id=row.get("variation_id"),
    )


@router.post("/video")
def generate_video(req: GenerateRequest, user: CurrentUser = Depends(get_current_user)):
    return JSONResponse(status_code=501, content={"status": "not_implemented", "detail": _NOT_READY})
