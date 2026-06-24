"""Shared publish path for approved mockups.

Uploads the PNG to the public ``mockups`` bucket, replaces the single
``productimages`` row for ``(productid, color, phototheme)`` (cleaning up the
prior Storage object), writes a ``mockup_variations`` audit row, and flips
``mockups.base_mockup``. Used by both ``/generate/approve`` (Phase 3) and the
Phase 7 backfill flow — one publish path, no duplication.
"""

from __future__ import annotations

from mockup_generator.db import mockup_variations_repo, mockups_repo, productimages_repo
from mockup_generator.integrations import storage_client


def build_photo_theme(theme_name: str | None, aspect_ratio: str | None) -> str:
    """Dedup photo-theme string: label, plus ``·<aspect>`` for non-1:1."""
    label = (theme_name or productimages_repo.DEFAULT_THEME).strip() \
        or productimages_repo.DEFAULT_THEME
    if aspect_ratio and aspect_ratio != "1:1":
        return f"{label}·{aspect_ratio}"
    return label


def publish_image(
    db, *, productid: str, png: bytes, color: str | None,
    theme_name: str | None, aspect_ratio: str | None, created_by: str | None,
    prompt_text: str | None = None, prompt_id: int | None = None,
) -> dict:
    """Publish ``png`` for ``productid`` + ``color``. Returns
    ``{"image_url", "variation_id"}``."""
    slug = storage_client.slugify(color)
    key = f"{slug}_{storage_client.short_hex()}" if slug else storage_client.short_hex()
    _path, public_url = storage_client.upload_mockup(productid, png, key)

    theme = build_photo_theme(theme_name, aspect_ratio)

    # One row per (productid, color, theme): replace the prior row and clean up
    # its orphaned Storage object (best-effort — cleanup must not fail a publish).
    for prior in productimages_repo.list_for(db, productid, color, theme):
        old_path = storage_client.path_from_public_url(prior.get("imageurl") or "")
        if old_path:
            try:
                storage_client.delete_object(old_path)
            except Exception:  # noqa: BLE001 - orphan cleanup is non-fatal
                pass
    productimages_repo.delete_for(db, productid, color, theme)

    row = mockup_variations_repo.insert(
        db, productid=productid, prompt_text=prompt_text, image_url=public_url,
        color=color, created_by=created_by, prompt_id=prompt_id,
    )
    mockups_repo.set_base_mockup(db, productid, True)
    productimages_repo.insert(db, productid=productid, imageurl=public_url,
                              caption=color, theme=theme)
    return {"image_url": public_url, "variation_id": row.get("variation_id")}
