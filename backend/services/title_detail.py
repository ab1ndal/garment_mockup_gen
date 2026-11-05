"""Saving a reviewer's title detail after an approve route has published the image.

Every route that publishes shares this because a published photo puts the product
live. Nothing here raises: the image is already out, so problems become warnings.
"""

from __future__ import annotations

import logging

from supabase import Client

from mockup_generator.db import product_titles_repo as titles_repo
from mockup_generator.describe.title_detail import MAX_TITLE_DETAIL_CHARS, normalize, should_save

log = logging.getLogger(__name__)


def save_title_detail(db: Client, productid: str, detail: str | None,
                      expected: str | None, edited: bool) -> str | None:
    """Apply the reviewer's title detail. ``detail`` None means the screen sent none."""
    if detail is None:
        return None
    text = normalize(detail)
    if not should_save(text, expected, edited):
        return None
    if len(text) > MAX_TITLE_DETAIL_CHARS:
        return f'Image published, but the title detail is over {MAX_TITLE_DETAIL_CHARS} characters. Not saved: "{text}"'
    try:
        saved = titles_repo.save_reviewed(db, productid, text, expected=expected)
    except Exception as exc:  # noqa: BLE001 - image is published; report, don't fail
        log.warning("title detail save failed for %s: %s", productid, exc, exc_info=True)
        return f'Image published, but the title detail could not be saved: "{text}"'
    if not saved:
        log.info("title detail for %s changed since load; not saved", productid)
        return f'Image published, but the title detail changed since you opened it. Not saved: "{text}"'
    return None


def missing_title_warning(db: Client, productid: str) -> str | None:
    """Warning for a publish that bypassed the title field, if the product has no detail."""
    try:
        cur = titles_repo.get_current(db, productid)
    except Exception as exc:  # noqa: BLE001 - only a reminder; the publish stands
        log.warning("title detail check failed for %s: %s", productid, exc)
        return None
    if cur is None or (cur.title_detail or "").strip():
        return None
    return f'Published with no title detail, so its title is "{cur.display_title}". Set one in the Inventory admin; Review offers it before publishing.'
