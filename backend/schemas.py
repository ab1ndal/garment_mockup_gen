# backend/schemas.py
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CategoryOut(BaseModel):
    categoryid: str
    name: str


class ProductOut(BaseModel):
    productid: str
    name: str
    categoryid: str | None = None
    category_name: str | None = None
    base_mockup: bool = False
    producturl: str | None = None


class ProductImage(BaseModel):
    id: str
    name: str
    mime_type: str
    thumbnail_url: str


class ProductImageGroup(BaseModel):
    """A variant subfolder and its images."""
    id: str
    name: str
    images: list[ProductImage]


class ProductImages(BaseModel):
    """Source images for a product: loose (top-level) + per-subfolder variant groups."""
    loose: list[ProductImage]
    groups: list[ProductImageGroup]


class PromptOut(BaseModel):
    prompt_id: int
    categoryid: str
    label: str
    body: str
    is_default: bool


class PromptCreate(BaseModel):
    categoryid: str
    label: str
    body: str
    is_default: bool = False


class PromptUpdate(BaseModel):
    label: str | None = None
    body: str | None = None
    is_default: bool | None = None


class RefineRequest(BaseModel):
    instruction: str
    categoryid: str | None = None
    kind: Literal["image", "video"] = "image"


class RefineResponse(BaseModel):
    refined: str


class GenerateRequest(BaseModel):
    productid: str
    prompt: str
    image_ids: list[str] = []
    model: str | None = None
    resolution: str | None = None
    aspect_ratio: str | None = None
    color: str | None = None
    refine_image_b64: str | None = None   # prior output, included as an extra reference on refine


class VideoGenerateRequest(BaseModel):
    productid: str
    prompt: str
    image_url: str | None = None   # supabase mockup URL to animate; else resolved by color
    color: str | None = None
    model: str | None = None
    resolution: str | None = None
    aspect_ratio: str | None = None
    duration: int | None = None


class VideoJobResponse(BaseModel):
    job_id: str
    status: str               # pending | running | done | error
    detail: str | None = None


class GeneratePreview(BaseModel):
    status: str
    detail: str
    image_b64: str


class GenerateUploadPreview(BaseModel):
    status: str
    detail: str
    image_b64: str
    mime_type: str


class GenerateResponse(BaseModel):
    status: str
    detail: str
    image_url: str | None = None
    variation_id: int | None = None


class ApproveResponse(BaseModel):
    status: str
    detail: str
    image_url: str
    variation_id: int | None = None


class BackfillItem(BaseModel):
    productid: str | None
    product_name: str | None
    alpha: str | None
    file_id: str
    filename: str
    thumbnail_url: str | None
    unknown_product: bool


class BackfillItemsResponse(BaseModel):
    total: int            # full count for the requested status (drives the pager)
    offset: int
    limit: int
    items: list[BackfillItem]


class BackfillCountsResponse(BaseModel):
    counts: dict[str, int]   # {status: count} for each sub-tab


class BackfillApproveRequest(BaseModel):
    file_id: str
    productid: str
    color: str | None = None
    theme_name: str | None = None
    aspect_ratio: str | None = None
    remove_watermark: bool = False


class BackfillFlagRequest(BaseModel):
    file_id: str
    productid: str | None = None


class BackfillEditRequest(BaseModel):
    file_id: str
    productid: str | None = None
    comment: str | None = None


# --- product-shot import + edit presets ---

class EditParamsModel(BaseModel):
    rotate_quarter: int = Field(default=0, ge=0, le=3)
    straighten_deg: float = Field(default=0.0, ge=-15.0, le=15.0)
    autocontrast: bool = True
    white_balance: bool = False
    brightness: float = Field(default=1.0, ge=0.5, le=1.5)
    saturation: float = Field(default=1.0, ge=0.5, le=1.5)
    bg: Literal["white", "cream"] = "white"
    shadow: bool = False


class ImportImage(BaseModel):
    id: str
    name: str
    mime_type: str | None = None
    thumbnail_url: str | None = None


class ImportGroup(BaseModel):
    id: str
    name: str
    images: list[ImportImage]


class ImportDriveImagesResponse(BaseModel):
    loose: list[ImportImage]
    groups: list[ImportGroup]


class PreviewRequest(BaseModel):
    file_id: str
    params: EditParamsModel = EditParamsModel()


class WarmRequest(BaseModel):
    file_id: str


class ReleaseRequest(BaseModel):
    file_id: str


class PreviewResponse(BaseModel):
    preview: str            # data:image/png;base64,...


class ImportPublishRequest(BaseModel):
    productid: str
    file_id: str
    color: str | None = None
    params: EditParamsModel = EditParamsModel()


class ImportPublishResponse(BaseModel):
    image_url: str
    displayorder: int


class PresetModel(BaseModel):
    preset_id: int
    name: str
    params: EditParamsModel
    is_default: bool


class PresetsResponse(BaseModel):
    presets: list[PresetModel]


class CreatePresetRequest(BaseModel):
    name: str
    params: EditParamsModel
    is_default: bool = False
