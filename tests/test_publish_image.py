import threading
import time
from io import BytesIO

import pytest
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


# ---------------- web size variants ----------------

def _png_sized(w: int, h: int, mode: str = "RGB") -> bytes:
    buf = BytesIO()
    Image.new(mode, (w, h)).save(buf, format="PNG")
    return buf.getvalue()


def _record_uploads(monkeypatch, events):
    """Capture every upload (key, decoded size, kwargs) and the DB insert, in order."""
    def _upload(pid, data, key, **kw):
        size = Image.open(BytesIO(data)).size
        events.append(("upload", key, size, kw))
        ext = kw.get("ext", "png")
        return f"{pid}/{key}.{ext}", f"https://public/{pid}/{key}.{ext}"
    _wire(monkeypatch, {})
    monkeypatch.setattr(publish.storage_client, "upload_mockup", _upload)
    monkeypatch.setattr(publish.productimages_repo, "insert",
                        lambda db, **kw: events.append(("insert", kw["imageurl"])) or {"imageid": 1})


def _publish(png):
    return publish.publish_image(
        object(), productid="BC1", png=png, color="Red",
        theme_name=None, aspect_ratio=None, created_by="u1",
    )


def _variants(events):
    return {e[1]: e for e in events if e[0] == "upload" and e[1].startswith("red_0_deadbeef_")}


def test_publish_uploads_three_webp_variants_before_insert(monkeypatch):
    events = []
    _record_uploads(monkeypatch, events)
    out = _publish(_png_sized(2048, 2048))

    variants = _variants(events)
    assert set(variants) == {"red_0_deadbeef_150", "red_0_deadbeef_600", "red_0_deadbeef_1200"}
    for w in (150, 600, 1200):
        _, _, size, kw = variants[f"red_0_deadbeef_{w}"]
        assert size == (w, w)
        assert kw["ext"] == "webp" and kw["content_type"] == "image/webp"
        assert kw["cache_control"] == "31536000"
    # The DB still references the full-size display WEBP, inserted only after
    # every variant is in Storage.
    assert out["image_url"] == "https://public/BC1/red_0_deadbeef.webp"
    assert events[-1] == ("insert", "https://public/BC1/red_0_deadbeef.webp")
    assert all(e[0] == "upload" for e in events[:-1])


def test_publish_variants_never_enlarge(monkeypatch):
    events = []
    _record_uploads(monkeypatch, events)
    _publish(_png_sized(800, 800))
    variants = _variants(events)
    assert variants["red_0_deadbeef_150"][2] == (150, 150)
    assert variants["red_0_deadbeef_600"][2] == (600, 600)
    assert variants["red_0_deadbeef_1200"][2] == (800, 800)


def test_publish_variants_keep_aspect_ratio(monkeypatch):
    events = []
    _record_uploads(monkeypatch, events)
    _publish(_png_sized(1500, 2000))  # 3:4 portrait
    variants = _variants(events)
    assert variants["red_0_deadbeef_150"][2] == (150, 200)
    assert variants["red_0_deadbeef_600"][2] == (600, 800)
    assert variants["red_0_deadbeef_1200"][2] == (1200, 1600)


def test_publish_variants_handle_palette_png(monkeypatch):
    events = []
    _record_uploads(monkeypatch, events)
    _publish(_png_sized(1000, 500, mode="P"))
    assert _variants(events)["red_0_deadbeef_600"][2] == (600, 300)


def test_publish_display_webp_capped_at_max_side(monkeypatch):
    """A 4K output is shrunk once to _WEBP_MAX_SIDE for the display WEBP; the
    archival PNG keeps full size and variants still derive at their widths."""
    events = []
    _record_uploads(monkeypatch, events)
    _publish(_png_sized(3000, 4000))
    displays = [e for e in events if e[0] == "upload" and e[1] == "red_0_deadbeef"]
    assert {(e[2], e[3].get("ext", "png")) for e in displays} == {
        ((3000, 4000), "png"), ((1536, 2048), "webp")}
    assert _variants(events)["red_0_deadbeef_1200"][2] == (1200, 1600)


def test_encode_webp_unchanged():
    out = publish._encode_webp(_png_sized(300, 200))
    with Image.open(BytesIO(out)) as img:
        assert img.format == "WEBP" and img.size == (300, 200)


# ---------------- parallel uploads ----------------

_UPLOADS_PER_PUBLISH = 5  # archival PNG + display WEBP + 150/600/1200 variants


def test_publish_uploads_run_concurrently(monkeypatch):
    """Every upload of one publish is in flight at once: the barrier only opens
    when all of them are waiting on it, so serial uploads time out."""
    barrier = threading.Barrier(_UPLOADS_PER_PUBLISH, timeout=3)

    def _upload(pid, data, key, **kw):
        barrier.wait()
        ext = kw.get("ext", "png")
        return f"{pid}/{key}.{ext}", f"https://public/{pid}/{key}.{ext}"

    _wire(monkeypatch, {})
    monkeypatch.setattr(publish.storage_client, "upload_mockup", _upload)
    out = _publish(_png_sized(2048, 2048))
    assert out["image_url"] == "https://public/BC1/red_0_deadbeef.webp"


def test_web_uploads_do_not_hold_an_encode_slot(monkeypatch):
    """Network uploads run after the encode slot is released, so a slow upload
    can't keep another approval from encoding."""
    free_slots = []

    def _upload(pid, data, key, **kw):
        if kw.get("ext") == "webp":
            got = [publish._ENCODE_SLOTS.acquire(blocking=False) for _ in range(2)]
            free_slots.append(sum(got))
            for ok in got:
                if ok:
                    publish._ENCODE_SLOTS.release()
        return f"{pid}/{key}", f"https://public/{pid}/{key}"

    monkeypatch.setattr(publish.storage_client, "upload_mockup", _upload)
    publish.upload_web_images("BC1", _png_sized(800, 800), "k")
    assert free_slots == [2, 2, 2, 2]


def test_failed_upload_fails_publish_after_others_settle(monkeypatch):
    """One failed upload fails the publish with no DB rows written, and only
    once every other upload has finished — nothing keeps running behind it."""
    calls, finished = {}, []

    def _upload(pid, data, key, **kw):
        if key.endswith("_600"):
            raise RuntimeError("storage down")
        time.sleep(0.05)
        finished.append(key)
        return f"{pid}/{key}", f"https://public/{pid}/{key}"

    _wire(monkeypatch, calls)
    monkeypatch.setattr(publish.storage_client, "upload_mockup", _upload)
    with pytest.raises(RuntimeError, match="storage down"):
        _publish(_png_sized(800, 800))
    assert len(finished) == _UPLOADS_PER_PUBLISH - 1
    assert "image" not in calls and "variation" not in calls


def test_failed_publish_still_logs_timings(monkeypatch, caplog):
    calls = {}
    _wire(monkeypatch, calls)

    def fail(db, **kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(publish.mockup_variations_repo, "insert", fail)
    caplog.set_level("INFO")

    with pytest.raises(RuntimeError):
        publish.publish_image(object(), productid="BC1", png=_png(), color=None,
                              theme_name=None, aspect_ratio=None, created_by="u1")

    line = next(r.getMessage() for r in caplog.records
                if r.getMessage().startswith("publish timing"))
    assert "outcome=failed" in line
    assert " insert_variation=" in line  # the failing stage is still timed
    assert " total=" in line
