"""Publish mockup images into the existing ``productimages`` table.

At most one row per ``(productid, caption, phototheme)`` where ``caption`` holds
the variant color and ``phototheme`` holds the photo-theme label (the prompt
label, plus an aspect suffix for non-1:1). This is a *photo* theme — how the
shot is styled/framed — not a product variant. Re-publishing the same triple
replaces it (see ``delete_for``), so no duplicates pile up; differing
theme/aspect coexist. ``list_for`` lets the caller find prior rows' URLs for
Storage cleanup.

``caption`` is NULL-aware (color may be absent). ``phototheme`` is always
concrete (defaults to ``"Default"``) so it filters with a plain equality.
"""

from __future__ import annotations

from supabase import Client

DEFAULT_THEME = "Default"


def _filter(query, caption: str | None, theme: str):
    """Add the NULL-aware color filter and the concrete photo-theme filter."""
    q = query.eq("caption", caption) if caption is not None else query.is_("caption", "null")
    return q.eq("phototheme", theme)


def list_for(client: Client, productid: str, caption: str | None,
             theme: str = DEFAULT_THEME) -> list[dict]:
    """Existing rows for one product + color + photo-theme (NULL-aware on caption)."""
    q = client.table("productimages").select("imageid, imageurl").eq("productid", productid)
    resp = _filter(q, caption, theme).execute()
    return list(resp.data or [])


def delete_for(client: Client, productid: str, caption: str | None,
               theme: str = DEFAULT_THEME) -> None:
    """Delete rows for one product + color + photo-theme — keeps one row per triple."""
    q = client.table("productimages").delete().eq("productid", productid)
    _filter(q, caption, theme).execute()


def insert(
    client: Client,
    *,
    productid: str,
    imageurl: str,
    caption: str | None = None,
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
    if caption is not None:
        payload["caption"] = caption

    resp = client.table("productimages").insert(payload).execute()
    return (resp.data or [{}])[0]
