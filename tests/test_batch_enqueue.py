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


@pytest.fixture(autouse=True)
def _no_active_cards(monkeypatch):
    """Default: no product already has an un-reviewed card. Tests that exercise
    the skip override this."""
    monkeypatch.setattr(be.repo, "active_productids", lambda db, pids: set())


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
                        lambda db, *, offset=0, **k: [_product("BC1"), _product("BC2"), _product("BC3", url=None)] if offset == 0 else [])
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
                        lambda db, *, offset=0, **k: [_product("BC1"), _product("BC2")] if offset == 0 else [])
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
    monkeypatch.setattr(be.products_repo, "list_products",
                        lambda db, *, offset=0, **k: [_product("BC1")] if offset == 0 else [])
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
                        lambda db, *, offset=0, **k: [_product("BC1", cat="ZZZ")] if offset == 0 else [])
    monkeypatch.setattr(be, "resolve_category_prompt", lambda db, cid: None)
    rows, skipped = be.plan_cards(object(), category="ZZZ", count=10, model="m",
                                  resolution="4K", aspect_ratio="1:1",
                                  batch_id="b1", created_by=None)
    assert rows == []
    assert "no prompt" in skipped[0]["reason"]


def test_plan_cards_skips_product_with_unreviewed_card(monkeypatch):
    """A product that already has an un-reviewed card (queued/generating/ready) is
    skipped, so re-running the batch never duplicates a pending generation."""
    monkeypatch.setattr(be.products_repo, "list_products",
                        lambda db, *, offset=0, **k: [_product("BC1"), _product("BC2")] if offset == 0 else [])
    monkeypatch.setattr(be, "resolve_category_prompt", lambda db, cid: "BODY")
    monkeypatch.setattr(be.drive_client, "extract_folder_id", lambda url: "FID")
    monkeypatch.setattr(be.drive_client, "list_folder_image_ids", lambda fid, limit=14: ["a"])
    monkeypatch.setattr(be.variants_repo, "list_colors", lambda db, pid: [])
    monkeypatch.setattr(be.repo, "active_productids", lambda db, pids: {"BC1"})

    rows, skipped = be.plan_cards(object(), category="SA", count=10, model="m",
                                  resolution="4K", aspect_ratio="1:1",
                                  batch_id="b1", created_by=None)
    assert [r["productid"] for r in rows] == ["BC2"]
    reasons = {s["productid"]: s["reason"] for s in skipped}
    assert "awaiting review" in reasons["BC1"]


def test_plan_cards_backfills_past_skips_to_reach_count(monkeypatch):
    """Skipped products don't consume a slot: the planner pages further down the
    pending list until it has ``count`` products enqueued."""
    # Two pages of three. BC1 has an active card, BC3 has no images -> both skip.
    pages = {
        0: [_product("BC1"), _product("BC2"), _product("BC3")],
        3: [_product("BC4"), _product("BC5"), _product("BC6")],
    }
    seen_offsets = []

    def fake_list(db, *, offset=0, **k):
        seen_offsets.append(offset)
        return pages.get(offset, [])

    monkeypatch.setattr(be.products_repo, "list_products", fake_list)
    monkeypatch.setattr(be, "resolve_category_prompt", lambda db, cid: "BODY")
    # _product embeds the id in the folder url (…/FID-BC3); hand back that id so
    # image lookups can vary per product.
    monkeypatch.setattr(be.drive_client, "extract_folder_id", lambda url: url.rsplit("-", 1)[-1])
    monkeypatch.setattr(be.drive_client, "list_folder_image_ids",
                        lambda fid, limit=14: [] if fid == "BC3" else ["a"])
    monkeypatch.setattr(be.variants_repo, "list_colors", lambda db, pid: [])
    monkeypatch.setattr(be.repo, "active_productids", lambda db, pids: {"BC1"} & set(pids))

    rows, skipped = be.plan_cards(object(), category="SA", count=3, model="m",
                                  resolution="4K", aspect_ratio="1:1",
                                  batch_id="b1", created_by=None)
    # BC1 active, BC3 no images -> skipped; BC2, BC4, BC5 fill the count of 3.
    assert [r["productid"] for r in rows] == ["BC2", "BC4", "BC5"]
    reasons = {s["productid"]: s["reason"] for s in skipped}
    assert "awaiting review" in reasons["BC1"] and "no images" in reasons["BC3"]
    # Paged into the second page to backfill, and stopped before BC6.
    assert seen_offsets[:2] == [0, 3]
    assert "BC6" not in [r["productid"] for r in rows]
