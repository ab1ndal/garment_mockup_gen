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
from io import BytesIO

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image
from supabase import Client

from backend.auth import CurrentUser, get_current_user
from backend.deps import get_db
from backend.schemas import ApproveResponse, GeneratePreview, GenerateRequest
from mockup_generator.config import settings
from mockup_generator.db import mockup_variations_repo, mockups_repo, productimages_repo, products_repo
from mockup_generator.generation import service
from mockup_generator.integrations import drive_client, storage_client
from mockup_generator.integrations.drive_client import DriveNotConfigured

router = APIRouter(prefix="/api/generate", tags=["generate"])

_MAX_REFS = 14
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB
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


@router.post("/image", response_model=GeneratePreview)
def generate_image(req: GenerateRequest, user: CurrentUser = Depends(get_current_user),
                   db: Client = Depends(get_db)):
    if req.model is not None and req.model not in ALLOWED_MODELS:
        raise HTTPException(status_code=400, detail=f"Unsupported model: {req.model}")
    if req.resolution is not None and req.resolution not in ALLOWED_RESOLUTIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported resolution: {req.resolution}")
    if req.aspect_ratio is not None and req.aspect_ratio not in ALLOWED_ASPECTS:
        raise HTTPException(status_code=400, detail=f"Unsupported aspect ratio: {req.aspect_ratio}")

    if not req.image_ids:
        raise HTTPException(status_code=400, detail="Select at least one source image.")

    product = products_repo.get_product(db, req.productid)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    folder_id = drive_client.extract_folder_id(product.producturl)
    if not folder_id:
        raise HTTPException(status_code=400, detail="Product has no linked Drive folder")

    ref_ids = req.image_ids[:_MAX_REFS]
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

    return GeneratePreview(
        status="ok", detail="Preview generated.",
        image_b64=base64.b64encode(png).decode("ascii"),
    )


@router.post("/approve", response_model=ApproveResponse)
async def approve_mockup(
    productid: str = Form(...),
    color: str | None = Form(None),
    prompt_text: str | None = Form(None),
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

    slug = storage_client.slugify(color)
    key = f"{slug}_{storage_client.short_hex()}" if slug else storage_client.short_hex()
    try:
        _path, public_url = storage_client.upload_mockup(productid, png, key)
    except storage_client.StorageNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Storage is not configured on the server") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not store the mockup: {exc}") from exc

    # No redundancy: one productimages row per (productid, color). Replace any
    # prior row and clean up its orphaned Storage object (best-effort — a
    # cleanup failure must not fail an otherwise-successful publish).
    for prior in productimages_repo.list_for(db, productid, color):
        old_path = storage_client.path_from_public_url(prior.get("imageurl") or "")
        if old_path:
            try:
                storage_client.delete_object(old_path)
            except Exception:  # noqa: BLE001 - orphan cleanup is non-fatal
                pass
    productimages_repo.delete_for(db, productid, color)

    text = prompt_text or ("(manual upload)" if source == "corrected" else "")
    row = mockup_variations_repo.insert(
        db, productid=productid, prompt_text=text, image_url=public_url,
        color=color, created_by=user.id,
    )
    mockups_repo.set_base_mockup(db, productid, True)
    productimages_repo.insert(db, productid=productid, imageurl=public_url, caption=color)

    return ApproveResponse(
        status="ok", detail="Published.",
        image_url=public_url, variation_id=row.get("variation_id"),
    )


@router.post("/video")
def generate_video(req: GenerateRequest, user: CurrentUser = Depends(get_current_user)):
    return JSONResponse(status_code=501, content={"status": "not_implemented", "detail": _NOT_READY})
