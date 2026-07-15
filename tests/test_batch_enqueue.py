import pytest

from mockup_generator.services import batch_enqueue as be
from mockup_generator.db import batch_items_repo as repo
from mockup_generator.db.products_repo import Product
from mockup_generator.db.prompts_repo import Prompt

_SENTINEL = object()


def _product(pid, cat="SA", url=_SENTINEL):
    # Each product gets its own folder id embedded in the URL so a patched
    # extract_folder_id / list_folder_image_ids can behave per-product.
    if url is _SENTINEL:
        url = f"https://drive.google.com/drive/folders/FID-{pid}"
    return Product(productid=pid, name=pid, categoryid=cat, category_name="Saree",
                   base_mockup=False, producturl=url)


def test_compose_prompt_prefixes_color():
    assert be.compose_prompt("Red", "BODY").startswith("Make the professional mockup of the Red product.")
    assert "BODY" in be.compose_prompt("Red", "BODY")


def test_compose_prompt_colorless():
    assert be.compose_prompt(None, "BODY").startswith("Make the professional mockup of the product.")


def test_resolve_prompt_prefers_db_default(monkeypatch):
    monkeypatch.setattr(be.prompts_repo, "list_by_category",
                        lambda db, cid: [Prompt(1, cid, "Default", "DBBODY", True)])
    assert be.resolve_category_prompt(object(), "SA") == "DBBODY"


def test_resolve_prompt_falls_back_to_constant(monkeypatch):
    monkeypatch.setattr(be.prompts_repo, "list_by_category", lambda db, cid: [])
    monkeypatch.setattr(be, "prompt_for_category", lambda cid: "CONSTBODY")
    assert be.resolve_category_prompt(object(), "SA") == "CONSTBODY"


def test_resolve_prompt_none_when_absent(monkeypatch):
    monkeypatch.setattr(be.prompts_repo, "list_by_category", lambda db, cid: [])
    monkeypatch.setattr(be, "prompt_for_category", lambda cid: None)
    assert be.resolve_category_prompt(object(), "SA") is None


def test_plan_cards_one_row_per_color_and_skips(monkeypatch):
    monkeypatch.setattr(be.products_repo, "list_products",
                        lambda db, **k: [_product("BC1"), _product("BC2"), _product("BC3", url=None)])
    monkeypatch.setattr(be, "resolve_category_prompt", lambda db, cid: "BODY")
    monkeypatch.setattr(be.drive_client, "extract_folder_id",
                        lambda url: url.rsplit("/", 1)[-1] if url else None)
    imgs = {"FID-BC1": ["a", "b"], "FID-BC2": []}  # BC2 folder has no images -> skip
    monkeypatch.setattr(be.drive_client, "list_folder_image_ids",
                        lambda fid, limit=14: imgs.get(fid, []))
    monkeypatch.setattr(be.variants_repo, "list_colors",
                        lambda db, pid: ["Red", "Blue"] if pid == "BC1" else [])

    rows, skipped = be.plan_cards(object(), category="SA", count=10, model="m",
                                  resolution="4K", aspect_ratio="1:1",
                                  batch_id="b1", created_by="u1")
    # BC1: 2 colors -> 2 rows; BC2: no images -> skip; BC3: no drive folder -> skip
    assert len(rows) == 2
    assert {r["color"] for r in rows} == {"Red", "Blue"}
    assert all(r["status"] == repo.QUEUED and r["image_ids"] == ["a", "b"] for r in rows)
    reasons = {s["productid"]: s["reason"] for s in skipped}
    assert "no images" in reasons["BC2"] and "drive folder" in reasons["BC3"]


def test_plan_cards_skips_product_with_too_many_images(monkeypatch):
    """Over the limit is left for manual generation: choosing among many shots is
    a judgement call, and a card that guesses wastes a generation."""
    monkeypatch.setattr(be.products_repo, "list_products",
                        lambda db, **k: [_product("BC1"), _product("BC2")])
    monkeypatch.setattr(be, "resolve_category_prompt", lambda db, cid: "BODY")
    monkeypatch.setattr(be.drive_client, "extract_folder_id",
                        lambda url: url.rsplit("/", 1)[-1])
    limits = {}
    # BC1 is at the limit and stays; BC2 comes back with a full slate -> over it.
    imgs = {"FID-BC1": ["a", "b", "c", "d", "e"],
            "FID-BC2": ["a", "b", "c", "d", "e", "f"]}
    def fake_list(fid, limit=14):
        limits[fid] = limit
        return imgs[fid][:limit]
    monkeypatch.setattr(be.drive_client, "list_folder_image_ids", fake_list)
    monkeypatch.setattr(be.variants_repo, "list_colors", lambda db, pid: [])

    rows, skipped = be.plan_cards(object(), category="SA", count=10, model="m",
                                  resolution="2K", aspect_ratio="1:1",
                                  batch_id="b1", created_by=None)
    assert [r["productid"] for r in rows] == ["BC1"]
    assert len(rows[0]["image_ids"]) == 5
    assert [s["productid"] for s in skipped] == ["BC2"]
    assert "manually" in skipped[0]["reason"]
    # one over the limit is enough to prove overflow — never page the whole folder
    assert set(limits.values()) == {be._MAX_SOURCE_IMAGES + 1}


def test_plan_cards_colorless_product_gets_one_row(monkeypatch):
    monkeypatch.setattr(be.products_repo, "list_products", lambda db, **k: [_product("BC1")])
    monkeypatch.setattr(be, "resolve_category_prompt", lambda db, cid: "BODY")
    monkeypatch.setattr(be.drive_client, "extract_folder_id", lambda url: "FID")
    monkeypatch.setattr(be.drive_client, "list_folder_image_ids", lambda fid, limit=14: ["a"])
    monkeypatch.setattr(be.variants_repo, "list_colors", lambda db, pid: [])
    rows, skipped = be.plan_cards(object(), category="SA", count=10, model="m",
                                  resolution="4K", aspect_ratio="1:1",
                                  batch_id="b1", created_by=None)
    assert len(rows) == 1 and rows[0]["color"] is None
    assert rows[0]["prompt_text"].startswith("Make the professional mockup of the product.")


def test_plan_cards_skips_product_with_no_prompt(monkeypatch):
    monkeypatch.setattr(be.products_repo, "list_products",
                        lambda db, **k: [_product("BC1", cat="ZZZ")])
    monkeypatch.setattr(be, "resolve_category_prompt", lambda db, cid: None)
    rows, skipped = be.plan_cards(object(), category="ZZZ", count=10, model="m",
                                  resolution="4K", aspect_ratio="1:1",
                                  batch_id="b1", created_by=None)
    assert rows == []
    assert "no prompt" in skipped[0]["reason"]
