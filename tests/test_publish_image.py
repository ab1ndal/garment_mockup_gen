from io import BytesIO

from PIL import Image

from mockup_generator.generation import publish


def _png() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (4, 4), (1, 2, 3)).save(buf, format="PNG")
    return buf.getvalue()


def _wire(monkeypatch, calls):
    monkeypatch.setattr(publish.storage_client, "short_hex", lambda: "deadbeef")
    monkeypatch.setattr(publish.storage_client, "upload_mockup",
                        lambda pid, data, key, **kw: (f"{pid}/{key}.png", f"https://public/{pid}/{key}.png"))
    monkeypatch.setattr(publish.productimages_repo, "list_for",
                        lambda db, pid, cap, theme="Default": [])
    monkeypatch.setattr(publish.productimages_repo, "delete_for",
                        lambda db, pid, cap, theme="Default": calls.__setitem__("deleted_for", (pid, cap, theme)))
    monkeypatch.setattr(publish.productimages_repo, "insert",
                        lambda db, **kw: (calls.__setitem__("image", kw) or {"imageid": 1}))
    monkeypatch.setattr(publish.mockup_variations_repo, "insert",
                        lambda db, **kw: (calls.__setitem__("variation", kw) or {"variation_id": 7}))
    monkeypatch.setattr(publish.mockups_repo, "set_base_mockup",
                        lambda db, pid, value=True: calls.__setitem__("flag", (pid, value)))


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
    assert out["image_url"] == "https://public/BC25001/red_deadbeef.png"
    assert out["variation_id"] == 7
    assert calls["flag"] == ("BC25001", True)
    assert calls["image"]["caption"] == "Red"
    assert calls["image"]["theme"] == "Default"
    assert calls["variation"]["color"] == "Red"


def test_publish_image_allows_null_prompt_text(monkeypatch):
    calls = {}
    _wire(monkeypatch, calls)
    publish.publish_image(
        object(), productid="BC1", png=_png(), color="Blue",
        theme_name=None, aspect_ratio=None, created_by="u1", prompt_text=None,
    )
    assert calls["variation"]["prompt_text"] is None
