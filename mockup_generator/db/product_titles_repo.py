"""Read, preview and conditionally save ``products.title_detail``.

retail-inventory owns the title rule: its trigger computes ``display_title`` and
its ``preview_product_title`` RPC answers what a detail would produce, so nothing
here composes a title or writes ``display_title`` or ``name``.
"""

from __future__ import annotations

from dataclasses import dataclass

from supabase import Client

from mockup_generator.db.product_descriptions_repo import _match
from mockup_generator.prompts.describe_prompt import ProductFacts


@dataclass(frozen=True)
class CurrentTitle:
    title_detail: str | None
    display_title: str | None


@dataclass(frozen=True)
class TitlePreview:
    title: str
    duplicate: bool


def get_current(client: Client, productid: str) -> CurrentTitle | None:
    rows = (
        client.table("products").select("title_detail, display_title")
        .eq("productid", productid).limit(1).execute()
    ).data or []
    if not rows:
        return None
    return CurrentTitle(rows[0].get("title_detail"), rows[0].get("display_title"))


def preview(client: Client, facts: ProductFacts, detail: str) -> TitlePreview:
    """The title ``detail`` would give the product, and whether another product has it.

    The RPC checks every product, not only live ones. Products without a detail
    cannot match a title containing "with", and one that has the same detail will
    collide when it goes live, so the wider check only warns earlier.
    """
    if not detail:
        # The RPC returns "" here because it does not get the name; the trigger's
        # no-detail rule is the name with hyphens as spaces.
        return TitlePreview(facts.name.replace("-", " ").strip(), False)
    data = client.rpc("preview_product_title", {
        "p_fabric": facts.fabric,
        "p_categoryid": facts.categoryid,
        "p_detail": detail,
        "p_colours": facts.colours or None,
        "p_exclude_productid": facts.productid,
    }).execute().data
    return TitlePreview(data["title"], bool(data["duplicate"]))


def save_reviewed(client: Client, productid: str, detail: str, *, expected: str | None) -> bool:
    """Save ``detail`` (empty clears it). Returns False, writing nothing, if the
    stored value is no longer the ``expected`` one the reviewer loaded."""
    q = client.table("products").update({"title_detail": detail or None}).eq("productid", productid)
    return bool(_match(q, "title_detail", expected).execute().data)
