"""Read product variant colors from the existing ``productsizecolors`` table.

Color is the visual variation for a mockup (size does not change appearance).
Inventory data has stray whitespace and case dupes, so we normalize read-side.
"""

from __future__ import annotations

from supabase import Client


def list_colors(client: Client, productid: str) -> list[str]:
    """Distinct colors for a product: trimmed, case-insensitive deduped, sorted."""
    resp = (
        client.table("productsizecolors").select("color")
        .eq("productid", productid).execute()
    )
    seen: dict[str, str] = {}
    for r in (resp.data or []):
        raw = (r.get("color") or "").strip()
        if not raw:
            continue
        key = raw.lower()
        if key not in seen:
            seen[key] = raw
    return sorted(seen.values(), key=str.lower)
