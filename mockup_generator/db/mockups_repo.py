"""Read access to the existing per-product ``mockups`` status flags."""

from __future__ import annotations

from supabase import Client

_FLAG_COLS = "productid, redo, base_mockup, file_mockup, mockup, video, ig_reel, ig_post, whatsapp"


def get_flags(client: Client, productid: str) -> dict | None:
    resp = (
        client.table("mockups").select(_FLAG_COLS)
        .eq("productid", productid).limit(1).execute()
    )
    rows = resp.data or []
    return rows[0] if rows else None


def set_base_mockup(client: Client, productid: str, value: bool = True) -> None:
    """Flip the product's ``base_mockup`` flag (row exists per product)."""
    client.table("mockups").update({"base_mockup": value}).eq("productid", productid).execute()
