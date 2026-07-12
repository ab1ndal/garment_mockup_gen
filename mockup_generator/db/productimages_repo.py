"""Publish mockup images into the existing ``productimages`` table.

Each approved design is *appended* as its own row: ``productcolor`` holds the
variant color and ``phototheme`` holds the photo-theme label (the prompt label,
plus an aspect suffix for non-1:1). This is a *photo* theme — how the shot is
styled/framed — not a product variant. Multiple designs for the same
``(productid, productcolor, phototheme)`` coexist; ``next_display_order`` gives
the next append position. ``list_for`` / ``delete_for`` (NULL-aware on
productcolor) let a caller query or prune rows for one product + color +
photo-theme.

``productcolor`` is NULL-aware (color may be absent). ``phototheme`` is always
concrete (defaults to ``"Default"``) so it filters with a plain equality.
"""

from __future__ import annotations

from supabase import Client

DEFAULT_THEME = "Default"


def _filter(query, productcolor: str | None, theme: str):
    """Add the NULL-aware color filter and the concrete photo-theme filter."""
    q = (query.eq("productcolor", productcolor) if productcolor is not None
         else query.is_("productcolor", "null"))
    return q.eq("phototheme", theme)


def list_for(client: Client, productid: str, productcolor: str | None,
             theme: str = DEFAULT_THEME) -> list[dict]:
    """Existing rows for one product + color + photo-theme (NULL-aware on productcolor)."""
    q = client.table("productimages").select("imageid, imageurl").eq("productid", productid)
    resp = _filter(q, productcolor, theme).execute()
    return list(resp.data or [])


def next_display_order(client: Client, productid: str) -> int:
    """Count of existing images for a product = the next append position."""
    resp = (
        client.table("productimages").select("imageid", count="exact")
        .eq("productid", productid).execute()
    )
    return resp.count or 0


def next_product_shot_order(client: Client, productid: str) -> int:
    """Next display order in the reserved 20+ band for imported product shots.

    Returns max(displayorder >= 20) + 1, or 20 when the band is empty. Orders
    below 20 are reserved for model mockups and never touched.
    """
    resp = (
        client.table("productimages")
        .select("displayorder")
        .eq("productid", productid)
        .gte("displayorder", 20)
        .order("displayorder", desc=True)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    if not rows:
        return 20
    return (rows[0].get("displayorder") or 19) + 1


def delete_for(client: Client, productid: str, productcolor: str | None,
               theme: str = DEFAULT_THEME) -> None:
    """Delete rows for one product + color + photo-theme — keeps one row per triple."""
    q = client.table("productimages").delete().eq("productid", productid)
    _filter(q, productcolor, theme).execute()


def insert(
    client: Client,
    *,
    productid: str,
    imageurl: str,
    productcolor: str | None = None,
    theme: str = DEFAULT_THEME,
    displayorder: int | None = None,
) -> dict:
    """Insert one product image row. Appends after existing images by default."""
    # Always query count to maintain consistent table() call order for mocking
    cnt = client.table("productimages").select("imageid", count="exact").eq("productid", productid).execute()

    if displayorder is None:
        displayorder = cnt.count or 0

    payload: dict = {"productid": productid, "imageurl": imageurl,
                     "displayorder": displayorder, "phototheme": theme}
    if productcolor is not None:
        payload["productcolor"] = productcolor

    resp = client.table("productimages").insert(payload).execute()
    return (resp.data or [{}])[0]
