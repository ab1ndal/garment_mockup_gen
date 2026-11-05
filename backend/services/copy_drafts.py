"""When a review card may start an AI draft.

Spend rules, in order: a product has one current draft. Another colour's card reuses
it, because a draft never names a colour on a multi-colour product, which takes the
worklist from 3,673 cards to 2,120 products. It is redone by itself only when the
same card's own images changed, or when a reviewer asks. A failed draft is not retried
by itself. A product whose text people have already written gets no draft.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable

from supabase import Client

from backend.services import draft_worker as worker
from mockup_generator.db import copy_drafts_repo as repo
from mockup_generator.db import product_descriptions_repo as descriptions_repo
from mockup_generator.db import product_titles_repo as titles_repo


class NoImages(ValueError):
    """Nothing to draft from."""


@dataclass(frozen=True)
class DraftImages:
    originals: tuple[str, ...] = ()
    mockup_drive: str | None = None
    mockup_storage: str | None = None

    @property
    def key(self) -> str:
        ids = sorted(self.originals) + [self.mockup_drive or "", self.mockup_storage or ""]
        return hashlib.sha256("\n".join(ids).encode()).hexdigest()

    @property
    def refs(self) -> dict:
        return {"originals": list(self.originals), "mockup_drive": self.mockup_drive,
                "mockup_storage": self.mockup_storage}

    @property
    def empty(self) -> bool:
        return not (self.originals or self.mockup_drive or self.mockup_storage)


def _people_wrote_it_all(db: Client, productid: str) -> bool:
    title = titles_repo.get_current(db, productid)
    description = descriptions_repo.get_current(db, productid)
    return bool(title and (title.title_detail or "").strip() and description and description.is_human)


def request_draft(db: Client, productid: str, card: str, load_images: Callable[[], DraftImages],
                  *, force: bool = False, note: str | None = None) -> repo.CopyDraft | None:
    """The product's current draft, queuing one if the rules above allow. None when
    no draft is needed. ``load_images`` runs only when the answer depends on it,
    because for Backfill it is a call to Drive."""
    active = repo.get_active(db, productid)
    if active and active.status in repo.IN_FLIGHT:
        return active
    if active and not force and active.source_card != card:
        return active
    if not active and not force and _people_wrote_it_all(db, productid):
        return None

    images = load_images()
    if active and active.images_key == images.key:
        if not force:
            return active
        if active.status == repo.FAILED and repo.requeue_failed(db, active.id, note):
            worker.ensure_running(db)
            return repo.get_active(db, productid)
    if images.empty:
        raise NoImages("No images to draft from.")
    if active:
        repo.supersede(db, active.id)
    row = repo.create(db, productid=productid, source_card=card, images_key=images.key,
                      image_refs=images.refs, note=note)
    worker.ensure_running(db)
    return row
