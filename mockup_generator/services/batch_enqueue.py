"""Batch Generate enqueue planning.

Pure planning: given a category + count, resolve which products get cards, one
per color, with a composed prompt — and collect a reason for every product that
is skipped (no drive folder, no images, no prompt). Does not touch the DB writer
or the worker; the router persists ``rows`` and kicks the worker.
"""

from __future__ import annotations

from mockup_generator.db import batch_items_repo as repo
from mockup_generator.db import products_repo, prompts_repo, variants_repo
from mockup_generator.integrations import drive_client
from mockup_generator.prompts.defaults import prompt_for_category

_PREFIX_COLOR = "Make the professional mockup of the {color} product."
_PREFIX_PLAIN = "Make the professional mockup of the product."


def compose_prompt(color: str | None, body: str) -> str:
    prefix = _PREFIX_COLOR.format(color=color) if color else _PREFIX_PLAIN
    return f"{prefix}\n\n{body}"


def resolve_category_prompt(db, categoryid: str) -> str | None:
    """DB default -> hardcoded CATEGORY_PROMPTS -> None."""
    for p in prompts_repo.list_by_category(db, categoryid):
        if p.is_default:
            return p.body
    return prompt_for_category(categoryid)


def plan_cards(
    db, *, category: str | None, count: int, model: str, resolution: str,
    aspect_ratio: str, batch_id: str, created_by: str | None,
) -> tuple[list[dict], list[dict]]:
    products = products_repo.list_products(db, category=category, pending=True, limit=count)
    rows: list[dict] = []
    skipped: list[dict] = []

    for p in products:
        body = resolve_category_prompt(db, p.categoryid)
        if not body:
            skipped.append({"productid": p.productid, "reason": f"no prompt for category {p.categoryid}"})
            continue
        folder_id = drive_client.extract_folder_id(p.producturl)
        if not folder_id:
            skipped.append({"productid": p.productid, "reason": "no drive folder"})
            continue
        image_ids = drive_client.list_folder_image_ids(folder_id)
        if not image_ids:
            skipped.append({"productid": p.productid, "reason": "no images"})
            continue
        colors = variants_repo.list_colors(db, p.productid) or [None]
        for color in colors:
            rows.append({
                "batch_id": batch_id,
                "productid": p.productid,
                "color": color,
                "image_ids": image_ids,
                "prompt_text": compose_prompt(color, body),
                "status": repo.QUEUED,
                "model": model,
                "resolution": resolution,
                "aspect_ratio": aspect_ratio,
                "created_by": created_by,
            })

    return rows, skipped
