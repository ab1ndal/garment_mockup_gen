"""Rules for ``products.title_detail``, the short phrase that makes a title unique.

retail-inventory's trigger builds ``display_title`` from it, so this module only
decides what text is acceptable and when a save may replace what is stored.
"""

from __future__ import annotations

import re

MAX_TITLE_DETAIL_CHARS = 60  # database constraint products_title_detail_length

_LEADING_WITH = re.compile(r"^with(\s+|$)", re.IGNORECASE)


def normalize(text: str | None) -> str:
    """Single-spaced and without a leading "with", which the title adds itself."""
    return _LEADING_WITH.sub("", " ".join((text or "").split()))


def should_save(detail: str, expected: str | None, edited: bool) -> bool:
    """Whether a review may write ``detail`` over the ``expected`` stored value.

    The table has no record of who wrote a detail, so any non-empty one counts as
    a person's. Only a reviewer's own edit replaces it; an AI draft fills blanks.
    """
    current = (expected or "").strip()
    if detail == current:
        return False
    return edited or not current
