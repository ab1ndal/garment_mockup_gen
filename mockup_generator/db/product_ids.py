"""Parse Bindal product ids (``BC<YY><seq>``) into sortable numeric keys.

Ids have a ``BC`` prefix, a 2-digit year, and a variable-width sequence (3 or 4
digits today). Lexical comparison breaks across that width boundary
(``'BC251000' < 'BC25999'``), so range filtering compares on a parsed key.
"""

from __future__ import annotations

import re

_ID_RE = re.compile(r"^BC(\d{2})(\d+)$")


def product_key(productid: str | None) -> int | None:
    """Return a monotonic sort key for a product id, or None if malformed.

    key = YY * 1_000_000 + seq   (seq < 1_000_000 for all real ids).
    """
    m = _ID_RE.match(productid or "")
    if not m:
        return None
    yy, seq = int(m.group(1)), int(m.group(2))
    return yy * 1_000_000 + seq


def parse_range(start: str, end: str) -> tuple[int, int]:
    """Return (low_key, high_key) for an inclusive id range.

    Raises ValueError if either endpoint is not a valid product id.
    """
    lo, hi = product_key(start), product_key(end)
    if lo is None or hi is None:
        raise ValueError(f"invalid product id range: {start!r}..{end!r}")
    return (lo, hi) if lo <= hi else (hi, lo)
