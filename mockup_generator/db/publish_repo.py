"""The DB side of a publish, as one ``publish_mockup_rows()`` call.

Writes the ``mockup_variations`` audit row, flips ``mockups.base_mockup`` and
appends the ``productimages`` row in a single transaction. One round-trip instead
of three matters here: the database is in Mumbai and the backend is not, so each
PostgREST call costs ~650ms (see ``docs/migrations/2026-10-22-fewer-round-trips.sql``).
"""

from __future__ import annotations

from supabase import Client


def write_rows(
    client: Client,
    *,
    productid: str,
    imageurl: str,
    color: str | None,
    theme: str,
    displayorder: int,
    prompt_text: str | None,
    prompt_id: int | None,
    created_by: str | None,
) -> int:
    """Write all three publish rows; return the new ``variation_id``."""
    resp = client.rpc("publish_mockup_rows", {
        "p_productid": productid,
        "p_imageurl": imageurl,
        "p_color": color,
        "p_theme": theme,
        "p_displayorder": displayorder,
        "p_prompt_text": prompt_text,
        "p_prompt_id": prompt_id,
        "p_created_by": created_by,
    }).execute()
    return int(resp.data)
