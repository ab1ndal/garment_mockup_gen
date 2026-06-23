"""Generation endpoints. Phase 2 = stubs; Phase 3 wires Drive + Gemini/VEO."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from backend.auth import CurrentUser, get_current_user
from backend.schemas import GenerateRequest

router = APIRouter(prefix="/api/generate", tags=["generate"])

_NOT_READY = "Generation is enabled in Phase 3 (needs Drive service-account setup)."


@router.post("/image")
def generate_image(req: GenerateRequest, user: CurrentUser = Depends(get_current_user)):
    return JSONResponse(status_code=501, content={"status": "not_implemented", "detail": _NOT_READY})


@router.post("/video")
def generate_video(req: GenerateRequest, user: CurrentUser = Depends(get_current_user)):
    return JSONResponse(status_code=501, content={"status": "not_implemented", "detail": _NOT_READY})
