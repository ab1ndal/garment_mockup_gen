"""Read access to the product browse view (products + category + mockup flag)."""

from __future__ import annotations

from dataclasses import dataclass

from supabase import Client

from mockup_generator.db.product_ids import parse_range

_COLS = "productid, name, categoryid, category_name, producturl, base_mockup"


@dataclass
class Product:
    productid: str
    name: str
    categoryid: str | None
    category_name: str | None
    base_mockup: bool
    producturl: str | None


def _row(r: dict) -> Product:
    return Product(
        productid=r["productid"],
        name=r["name"],
        categoryid=r.get("categoryid"),
        category_name=r.get("category_name"),
        base_mockup=bool(r.get("base_mockup")),
        producturl=r.get("producturl"),
    )


def list_products(
    client: Client,
    *,
    category: str | None = None,
    product_id: str | None = None,
    id_start: str | None = None,
    id_end: str | None = None,
    pending: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> list[Product]:
    q = client.table("product_browse").select(_COLS)
    if category:
        q = q.eq("categoryid", category)
    if pending:
        q = q.eq("base_mockup", False)
    if product_id:
        q = q.eq("productid", product_id)
    elif id_start and id_end:
        lo, hi = parse_range(id_start, id_end)
        q = q.gte("id_key", lo).lte("id_key", hi)
    q = q.order("id_key").range(offset, offset + limit - 1)
    resp = q.execute()
    return [_row(r) for r in (resp.data or [])]


def get_product(client: Client, productid: str) -> Product | None:
    resp = (
        client.table("product_browse").select(_COLS)
        .eq("productid", productid).limit(1).execute()
    )
    rows = resp.data or []
    return _row(rows[0]) if rows else None


def list_categories(client: Client) -> list[tuple[str, str]]:
    resp = client.table("categories").select("categoryid, name").order("name").execute()
    return [(r["categoryid"], r["name"]) for r in (resp.data or [])]
