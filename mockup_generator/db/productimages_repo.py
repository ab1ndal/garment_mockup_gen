"""Publish mockup images into the existing ``productimages`` table.

At most one row per ``(productid, caption)`` where caption holds the variant
color — re-publishing replaces it (see ``delete_for``), so no duplicates pile
up. ``list_for`` lets the caller find the prior row's URL for Storage cleanup.
"""

from __future__ import annotations

from supabase import Client


def _filter_color(query, caption: str | None):
    """Apply a productid is implied by the caller; add the NULL-aware color filter."""
    return query.eq("caption", caption) if caption is not None else query.is_("caption", "null")


def list_for(client: Client, productid: str, caption: str | None) -> list[dict]:
    """Existing rows for one product + color (NULL-aware on caption)."""
    q = client.table("productimages").select("imageid, imageurl").eq("productid", productid)
    resp = _filter_color(q, caption).execute()
    return list(resp.data or [])


def delete_for(client: Client, productid: str, caption: str | None) -> None:
    """Delete rows for one product + color (NULL-aware) — keeps one row per pair."""
    q = client.table("productimages").delete().eq("productid", productid)
    _filter_color(q, caption).execute()


def insert(
    client: Client,
    *,
    productid: str,
    imageurl: str,
    caption: str | None = None,
    displayorder: int | None = None,
) -> dict:
    """Insert one product image row. Appends after existing images by default."""
    # Always query count to maintain consistent table() call order for mocking
    cnt = client.table("productimages").select("imageid", count="exact").eq("productid", productid).execute()

    if displayorder is None:
        displayorder = cnt.count or 0

    payload: dict = {"productid": productid, "imageurl": imageurl, "displayorder": displayorder}
    if caption is not None:
        payload["caption"] = caption

    resp = client.table("productimages").insert(payload).execute()
    return (resp.data or [{}])[0]
