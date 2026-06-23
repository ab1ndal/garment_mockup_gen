# backend/schemas.py
from __future__ import annotations

from pydantic import BaseModel


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


class GenerateRequest(BaseModel):
    productid: str
    prompt: str


class GenerateResponse(BaseModel):
    status: str
    detail: str
