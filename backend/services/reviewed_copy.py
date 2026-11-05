"""Saving the text a reviewer approved, after an approve route has published the image.

Nothing here raises: the image is already out, so problems come back as warnings.
AI text never replaces what a person wrote unless the reviewer chose to replace it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from supabase import Client

from backend.services.title_detail import save_title_detail
from mockup_generator.db import copy_drafts_repo as drafts_repo
from mockup_generator.db import product_descriptions_repo as descriptions_repo
from mockup_generator.db import product_tags_repo as tags_repo
from mockup_generator.db import product_titles_repo as titles_repo
from mockup_generator.db import products_repo
from mockup_generator.db.product_tags_repo import TagRow
from mockup_generator.describe.draft import MAX_DESCRIPTION_CHARS
from mockup_generator.prompts.tag_vocab import SINGLE, allowed, family_of, groups_for

log = logging.getLogger(__name__)

DESCRIPTION_CHANGED = "Image approved, but the description changed since you opened it. Not saved."
DESCRIPTION_SAVE_FAILED = "Image approved, but the description could not be saved."
DESCRIPTION_TOO_LONG = (
    f"Image approved, but the description is over {MAX_DESCRIPTION_CHARS} characters. Not saved.")
DESCRIPTION_KEPT = "Kept the description someone wrote; the AI text was not saved."

TAGS_NOT_SAVED = "Tags not saved because the description was not saved."
_TAGS_INVALID = "Tags not saved: {detail}."
_TAGS_SAVE_FAILED = "Image approved, but the tags could not be saved: {detail}"
_TAGS_SKIPPED = "Kept hand-entered tags; not saved: {values}."
_TAG_SOURCES = ("ai_reviewed", "manual")


@dataclass(frozen=True)
class CopyWarnings:
    description: str | None = None
    tags: str | None = None
    title: str | None = None

    def all(self) -> list[str]:
        return [w for w in (self.description, self.tags, self.title) if w]


def save_description(db: Client, productid: str, text: str, *, expected_description: str | None,
                     expected_reviewed_at: str | None, edited: bool) -> str | None:
    text = text.strip()
    if len(text) > MAX_DESCRIPTION_CHARS:
        return DESCRIPTION_TOO_LONG
    try:
        if not edited:
            current = descriptions_repo.get_current(db, productid)
            if current is not None and current.is_human:
                return DESCRIPTION_KEPT
        saved = descriptions_repo.save_reviewed(
            db, productid, text,
            expected_description=expected_description,
            expected_reviewed_at=expected_reviewed_at)
    except Exception as exc:  # noqa: BLE001 - image is published; report, don't fail
        log.warning("description save failed for %s: %s", productid, exc)
        return DESCRIPTION_SAVE_FAILED
    if not saved:
        log.info("description for %s changed since load; draft not saved", productid)
        return DESCRIPTION_CHANGED
    return None


def _tag_problem(tags: list[TagRow], family: str | None) -> str | None:
    for t in tags:
        if t.source not in _TAG_SOURCES:
            return f"{t.group}: {t.value} has source {t.source!r}"
        if t.confidence is not None and not 0 <= t.confidence <= 1:
            return f"{t.group}: {t.value} has confidence {t.confidence}"
        if t.value not in allowed(t.group, family):
            return f"{t.group}: {t.value} is not allowed for this category"
    for group in SINGLE:
        if sum(t.group == group for t in tags) > 1:
            return f"more than one {group}"
    return None


def save_tags(db: Client, productid: str, tags: list[TagRow]) -> str | None:
    """Replace the product's AI tags with the approved set, once the description saved."""
    try:
        product = products_repo.get_product(db, productid)
        family = family_of(product.categoryid if product else None)
        if not groups_for(family):
            # No vocab for this category here (retail's may be newer): an empty
            # set would delete AI tags the reviewer never saw.
            return None
        problem = _tag_problem(tags, family)
        if problem:
            return _TAGS_INVALID.format(detail=problem)
        stored = tags_repo.replace_ai_tags(db, productid, tags)
    except Exception as exc:  # noqa: BLE001 - image is published; report, don't fail
        log.warning("tag save failed for %s: %s", productid, exc, exc_info=True)
        # PostgREST errors stringify as a dict; the reviewer needs only the message.
        return _TAGS_SAVE_FAILED.format(detail=getattr(exc, "message", None) or exc)
    kept = {(t.group, t.value) for t in stored}
    skipped = [f"{t.group}: {t.value}" for t in tags if (t.group, t.value) not in kept]
    return _TAGS_SKIPPED.format(values=", ".join(skipped)) if skipped else None


def save_reviewed_copy(db: Client, productid: str, req) -> CopyWarnings:
    """Apply the text fields of a Review screen's request (``ReviewedCopyFields``)."""
    description = None
    if req.description is not None:
        description = save_description(
            db, productid, req.description, expected_description=req.expected_description,
            expected_reviewed_at=req.expected_reviewed_at, edited=req.description_edited)
    tags = None
    if req.tags is not None:
        # New tags over a description that didn't save would describe a stale product.
        if req.description is not None and description is None:
            tags = save_tags(db, productid, [TagRow(t.group, t.value, t.source, t.confidence) for t in req.tags])
        else:
            tags = TAGS_NOT_SAVED
    # Independent of the description: it can save when a stale description does not.
    title = save_title_detail(db, productid, req.title_detail, req.expected_title_detail, req.title_detail_edited)
    return CopyWarnings(description, tags, title)


def apply_approved_draft(db: Client, productid: str, row: drafts_repo.CopyDraft) -> CopyWarnings:
    """Save a draft approved with one click on a card. Only blanks and earlier AI text
    are filled, so what is saved is what the card showed (see ``card_previews``)."""
    draft = row.draft or {}
    try:
        current_title = titles_repo.get_current(db, productid)
        current = descriptions_repo.get_current(db, productid)
    except Exception as exc:  # noqa: BLE001 - image is published; report, don't fail
        log.warning("approved draft %s for %s not applied: %s", row.id, productid, exc)
        return CopyWarnings(description=DESCRIPTION_SAVE_FAILED)
    title = save_title_detail(db, productid, draft.get("title_detail") or "",
                              current_title.title_detail if current_title else None, False)
    if current is None or current.is_human:
        return CopyWarnings(title=title)
    description = save_description(
        db, productid, draft.get("description") or "", expected_description=current.description,
        expected_reviewed_at=current.reviewed_at, edited=False)
    if description:
        return CopyWarnings(description=description, title=title)
    tags = save_tags(db, productid, [TagRow(t["group"], t["value"], "ai_reviewed", t.get("confidence"))
                                     for t in draft.get("tags") or []])
    return CopyWarnings(tags=tags, title=title)


@dataclass(frozen=True)
class CardCopy:
    can_accept: bool
    draft_id: int | None = None
    title: str | None = None
    description: str | None = None
    colour_mismatch: str = ""
    duplicate: bool = False


def _card_copy(current: titles_repo.ProductCopy, row: drafts_repo.CopyDraft | None) -> CardCopy:
    human_title = bool((current.title.title_detail or "").strip())
    human_description = current.description.is_human
    d = row.draft or {} if row and row.status == drafts_repo.READY else None
    if d is None:
        # With no finished draft, one click is safe only if people wrote all of it.
        if human_title and human_description:
            return CardCopy(True, None, current.title.display_title, current.description.description)
        return CardCopy(False)
    title = current.title.display_title if human_title else d.get("preview_title")
    if not human_title and not d.get("title_detail"):
        title = None
    return CardCopy(
        can_accept=bool(title), draft_id=row.id, title=title,
        description=current.description.description if human_description else d.get("description"),
        colour_mismatch=d.get("colour_mismatch") or "",
        duplicate=bool(d.get("duplicate")) and not human_title,
    )


def card_previews(db: Client, productids: list[str]) -> dict[str, CardCopy]:
    """What one click on each card would approve. Empty on any failure, including a
    missing migration: with no preview, Accept opens Review, which is always safe."""
    try:
        current = titles_repo.get_copy_many(db, productids)
        drafts = drafts_repo.get_active_many(db, productids)
    except Exception as exc:  # noqa: BLE001 - see docstring
        log.warning("card previews unavailable: %s", exc)
        return {}
    return {pid: _card_copy(c, drafts.get(pid)) for pid, c in current.items()}
