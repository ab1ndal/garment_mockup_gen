"""Read and conditionally save ``products.description``.

The description is shared with retail-inventory (admin edits, its describe
batch), so a save never overwrites blindly: it applies only if the text and the
review stamp are still what the reviewer loaded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from supabase import Client

from mockup_generator.prompts.describe_prompt import ProductFacts

AI_REVIEWED = "ai_reviewed"


@dataclass
class CurrentDescription:
    description: str | None
    source: str | None
    reviewed_at: str | None


def get_current(client: Client, productid: str) -> CurrentDescription | None:
    resp = (
        client.table("products")
        .select("description, description_source, description_reviewed_at")
        .eq("productid", productid).limit(1).execute()
    )
    rows = resp.data or []
    if not rows:
        return None
    r = rows[0]
    return CurrentDescription(r.get("description"), r.get("description_source"),
                              r.get("description_reviewed_at"))


def _distinct(values) -> list[str]:
    """Trimmed, case-insensitive deduped, sorted (inventory data has stray case/space dupes)."""
    seen: dict[str, str] = {}
    for v in values:
        raw = (v or "").strip()
        if raw and raw.lower() not in seen:
            seen[raw.lower()] = raw
    return sorted(seen.values(), key=str.lower)


def get_facts(client: Client, productid: str) -> ProductFacts | None:
    resp = (
        client.table("products")
        .select("productid, name, categoryid, fabric, description, categories(name)")
        .eq("productid", productid).limit(1).execute()
    )
    rows = resp.data or []
    if not rows:
        return None
    p = rows[0]
    variants = (
        client.table("productsizecolors").select("color, size")
        .eq("productid", productid).execute()
    ).data or []
    return ProductFacts(
        productid=p["productid"], name=p["name"], categoryid=p.get("categoryid"),
        category_name=(p.get("categories") or {}).get("name"), fabric=p.get("fabric"),
        colours=_distinct(v.get("color") for v in variants),
        sizes=_distinct(v.get("size") for v in variants),
        description=p.get("description"),
    )


def _match(q, column: str, expected: str | None):
    return q.is_(column, "null") if expected is None else q.eq(column, expected)


def save_reviewed(client: Client, productid: str, text: str, *,
                  expected_description: str | None, expected_reviewed_at: str | None) -> bool:
    """Save ``text`` as an owner-reviewed AI description. Returns False (and writes
    nothing) if someone changed the description or its review stamp since load."""
    q = client.table("products").update({
        "description": text,
        "description_source": AI_REVIEWED,
        "description_reviewed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("productid", productid)
    q = _match(q, "description", expected_description)
    q = _match(q, "description_reviewed_at", expected_reviewed_at)
    return bool(q.execute().data)
