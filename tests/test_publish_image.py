from io import BytesIO

from PIL import Image

from mockup_generator.generation import publish


def _png() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (4, 4), (1, 2, 3)).save(buf, format="PNG")
    return buf.getvalue()


def _wire(monkeypatch, calls, order=0):
    monkeypatch.setattr(publish.storage_client, "short_hex", lambda: "deadbeef")
    monkeypatch.setattr(publish.storage_client, "upload_mockup",
                        lambda pid, data, key, **kw: (f"{pid}/{key}.png", f"https://public/{pid}/{key}.png"))
    monkeypatch.setattr(publish.productimages_repo, "next_display_order",
                        lambda db, pid: order)
    monkeypatch.setattr(publish.productimages_repo, "insert",
                        lambda db, **kw: (calls.__setitem__("image", kw) or {"imageid": 1}))
    monkeypatch.setattr(publish.mockup_variations_repo, "insert",
                        lambda db, **kw: (calls.__setitem__("variation", kw) or {"variation_id": 7}))
    monkeypatch.setattr(publish.mockups_repo, "set_base_mockup",
                        lambda db, pid, value=True: calls.__setitem__("flag", (pid, value)))
    # Append model: the replace path must NOT run — old rows/PNGs are kept.
    monkeypatch.setattr(publish.productimages_repo, "delete_for",
                        lambda *a, **k: calls.__setitem__("deleted_for", True))
    monkeypatch.setattr(publish.storage_client, "delete_object",
                        lambda *a, **k: calls.__setitem__("storage_deleted", True))


def test_build_photo_theme():
    assert publish.build_photo_theme(None, None) == "Default"
    assert publish.build_photo_theme("Studio", "1:1") == "Studio"
    assert publish.build_photo_theme("Studio", "9:16") == "Studio·9:16"


def test_publish_image_writes_all_rows(monkeypatch):
    calls = {}
    _wire(monkeypatch, calls)
    out = publish.publish_image(
        object(), productid="BC25001", png=_png(), color="Red",
        theme_name=None, aspect_ratio=None, created_by="u1",
    )
    assert out["image_url"] == "https://public/BC25001/red_0_deadbeef.png"
    assert out["variation_id"] == 7
    assert calls["flag"] == ("BC25001", True)
    assert calls["image"]["productcolor"] == "Red"
    assert calls["image"]["theme"] == "Default"
    assert calls["variation"]["color"] == "Red"


def test_publish_image_appends_without_replacing(monkeypatch):
    """A second design for the same color+theme is added, not overwritten:
    the storage key carries the display order and nothing is deleted."""
    calls = {}
    _wire(monkeypatch, calls, order=2)
    out = publish.publish_image(
        object(), productid="BC25001", png=_png(), color="Red",
        theme_name=None, aspect_ratio=None, created_by="u1",
    )
    assert out["image_url"] == "https://public/BC25001/red_2_deadbeef.png"
    assert calls["image"]["displayorder"] == 2
    assert "deleted_for" not in calls       # prior productimages row kept
    assert "storage_deleted" not in calls   # prior PNG kept


def test_publish_image_key_without_color(monkeypatch):
    calls = {}
    _wire(monkeypatch, calls, order=1)
    out = publish.publish_image(
        object(), productid="BC1", png=_png(), color=None,
        theme_name=None, aspect_ratio=None, created_by="u1",
    )
    assert out["image_url"] == "https://public/BC1/1_deadbeef.png"


def test_publish_image_allows_null_prompt_text(monkeypatch):
    calls = {}
    _wire(monkeypatch, calls)
    publish.publish_image(
        object(), productid="BC1", png=_png(), color="Blue",
        theme_name=None, aspect_ratio=None, created_by="u1", prompt_text=None,
    )
    assert calls["variation"]["prompt_text"] is None
