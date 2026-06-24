"""In-memory index of the generated Drive folder for the backfill review tab.

One expensive ``scan_folder_of_folders`` is amortized across all paging
(TTL 300s + manual refresh). Approve/flag evict the handled file. The cache is
per-process and simply re-scans after a restart — the Drive folder is the source
of truth for what still needs review.
"""

from __future__ import annotations

import time

from mockup_generator.integrations import drive_client

_TTL = 300.0
_cache: dict[str, tuple[float, list[dict]]] = {}


def clear_cache() -> None:
    _cache.clear()


def get_index(root_id: str, *, refresh: bool = False) -> list[dict]:
    """Return the cached flat index for ``root_id``, scanning Drive if stale/forced."""
    now = time.monotonic()
    cached = _cache.get(root_id)
    if not refresh and cached and (now - cached[0]) < _TTL:
        return cached[1]
    items = drive_client.scan_folder_of_folders(root_id)
    _cache[root_id] = (now, items)
    return items


def paginate(items: list[dict], offset: int, limit: int) -> list[dict]:
    return items[offset:offset + limit]


def evict(file_id: str, *, root_id: str | None = None) -> None:
    """Drop a handled file from the cached index (keeps the page counts honest)."""
    keys = [root_id] if root_id else list(_cache.keys())
    for k in keys:
        cached = _cache.get(k)
        if cached:
            ts, items = cached
            _cache[k] = (ts, [i for i in items if i["file_id"] != file_id])
