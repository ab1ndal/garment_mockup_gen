"""Read and replace a product's ``product_tags`` rows.

The table and its write rule belong to retail-inventory: every write goes
through its ``replace_product_tags`` RPC, which swaps the product's AI rows in
one transaction, keeps ``manual`` rows, and rejects values the category may not
use.
"""

from __future__ import annotations

from dataclasses import dataclass

from supabase import Client

# Backfill replaces only ai/ai_reviewed rows; "all" is for retail's admin form.
_SCOPE_AI = "ai"


@dataclass(frozen=True)
class TagRow:
    group: str
    value: str
    source: str
    confidence: float | None


def _row(r: dict) -> TagRow:
    return TagRow(r["tag_group"], r["value"], r["source"], r.get("confidence"))


def get_tags(client: Client, productid: str) -> list[TagRow]:
    rows = (
        client.table("product_tags").select("tag_group, value, source, confidence")
        .eq("productid", productid).execute()
    ).data or []
    return [_row(r) for r in rows]


def replace_ai_tags(client: Client, productid: str, tags: list[TagRow]) -> list[TagRow]:
    """Replace the product's AI tags with ``tags`` (the complete approved set).
    Returns the rows the RPC stored, which can be fewer than sent when a manual
    row already holds a single-value group."""
    rows = client.rpc("replace_product_tags", {
        "p_productid": productid,
        "p_tags": [{"tag_group": t.group, "value": t.value, "source": t.source,
                    "confidence": t.confidence} for t in tags],
        "p_scope": _SCOPE_AI,
    }).execute().data or []
    return [_row(r) for r in rows]
