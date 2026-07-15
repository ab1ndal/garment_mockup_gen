import pytest

from backend.services import batch_worker as bw
from mockup_generator.db import batch_items_repo as repo
from mockup_generator.db.batch_items_repo import BatchRow


def _row(id=1):
    return BatchRow(id=id, batch_id="b1", productid="BC1", color="Red",
                    image_ids=["a", "b"], prompt_text="p", status=repo.GENERATING,
                    storage_path=None, error=None,
                    model="m", resolution="4K", aspect_ratio="1:1")


def test_run_one_generates_stages_to_storage_and_marks_ready(monkeypatch):
    claimed = {"n": 0}
    def fake_claim(db):
        claimed["n"] += 1
        return _row() if claimed["n"] == 1 else None
    monkeypatch.setattr(bw.repo, "claim_next_queued", fake_claim)
    monkeypatch.setattr(bw.drive_client, "download_file", lambda fid: _tiny_png())
    uploaded = {}
    def fake_upload(productid, data, key, *, bucket, **k):
        uploaded.update({"productid": productid, "key": key, "bucket": bucket, "data": data})
        return f"{productid}/{key}.png", "https://public/ignored"
    monkeypatch.setattr(bw.storage_client, "upload_mockup", fake_upload)
    monkeypatch.setattr(bw.service, "generate_mockup_bytes", lambda images, prompt, **k: b"PNG")
    updates = []
    monkeypatch.setattr(bw.repo, "transition",
                        lambda db, *, item_id, expect, to, **f: updates.append((item_id, to, f)) or True)

    assert bw.run_one(object()) is True
    assert updates[0][1] == repo.READY
    assert updates[0][2]["storage_path"] == "BC1/batch-1.png"
    # staged in the private temp bucket, never the public one
    assert uploaded["bucket"] == bw.storage_client.TEMP_BUCKET
    assert uploaded["data"] == b"PNG"


def test_run_one_marks_failed_on_error(monkeypatch):
    monkeypatch.setattr(bw.repo, "claim_next_queued", lambda db: _row())
    monkeypatch.setattr(bw.drive_client, "download_file", lambda fid: _tiny_png())
    monkeypatch.setattr(bw.service, "generate_mockup_bytes",
                        lambda images, prompt, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    updates = []
    monkeypatch.setattr(bw.repo, "transition",
                        lambda db, *, item_id, expect, to, **f: updates.append((to, f)) or True)
    assert bw.run_one(object()) is True
    assert updates[0][0] == repo.FAILED and "boom" in updates[0][1]["error"]


def test_run_one_marks_failed_when_staging_upload_fails(monkeypatch):
    """A staging failure must land on the card, not kill the worker loop — this is
    the shape of the Drive 403 that made Drive staging unusable."""
    monkeypatch.setattr(bw.repo, "claim_next_queued", lambda db: _row())
    monkeypatch.setattr(bw.drive_client, "download_file", lambda fid: _tiny_png())
    monkeypatch.setattr(bw.service, "generate_mockup_bytes", lambda images, prompt, **k: b"PNG")
    def _boom(*a, **k): raise RuntimeError("storage down")
    monkeypatch.setattr(bw.storage_client, "upload_mockup", _boom)
    updates = []
    monkeypatch.setattr(bw.repo, "transition",
                        lambda db, *, item_id, expect, to, **f: updates.append((to, f)) or True)
    assert bw.run_one(object()) is True
    assert updates[0][0] == repo.FAILED and "storage down" in updates[0][1]["error"]


def test_run_one_returns_false_when_nothing_queued(monkeypatch):
    monkeypatch.setattr(bw.repo, "claim_next_queued", lambda db: None)
    assert bw.run_one(object()) is False


def _tiny_png() -> bytes:
    from io import BytesIO
    from PIL import Image
    buf = BytesIO(); Image.new("RGB", (2, 2)).save(buf, "PNG"); return buf.getvalue()


def test_ensure_running_starts_a_pool_of_drainers(monkeypatch):
    """Throughput: the pool runs `batch_concurrency` drainers, not one."""
    monkeypatch.setattr(bw, "_active", 0)
    monkeypatch.setattr(bw, "_concurrency", lambda: 3)
    spawned = []
    # record instead of running, so the pool stays "full" for the assertions
    monkeypatch.setattr(bw, "_spawn", lambda fn, *a: spawned.append(fn))
    bw.ensure_running(object())
    assert len(spawned) == 3


def test_ensure_running_tops_up_without_exceeding_concurrency(monkeypatch):
    """A second caller while drainers are live must not stack a second pool."""
    monkeypatch.setattr(bw, "_active", 0)
    monkeypatch.setattr(bw, "_concurrency", lambda: 3)
    spawned = []
    monkeypatch.setattr(bw, "_spawn", lambda fn, *a: spawned.append(fn))
    bw.ensure_running(object())   # -> 3 drainers
    bw.ensure_running(object())   # pool already full -> no new drainers
    assert len(spawned) == 3
    assert bw._active == 3


def test_drainer_releases_its_slot_when_queue_empties(monkeypatch):
    monkeypatch.setattr(bw, "_active", 2)
    monkeypatch.setattr(bw.repo, "claim_next_queued", lambda db: None)
    bw.run_worker(object())
    assert bw._active == 1


def test_pool_refills_after_draining(monkeypatch):
    """Once drainers exit, a later enqueue can start a fresh pool."""
    monkeypatch.setattr(bw, "_active", 0)
    monkeypatch.setattr(bw, "_concurrency", lambda: 2)
    monkeypatch.setattr(bw.repo, "claim_next_queued", lambda db: None)
    monkeypatch.setattr(bw, "_spawn", lambda fn, *a: fn(*a))  # run inline -> drains + exits
    bw.ensure_running(object())
    assert bw._active == 0
    bw.ensure_running(object())
    assert bw._active == 0


def test_concurrency_reads_settings_with_sane_fallback(monkeypatch):
    """Exercises the real settings read. The pool tests stub _concurrency, so
    without this an import/config error here would ship green."""
    monkeypatch.delenv("BATCH_CONCURRENCY", raising=False)
    assert bw._concurrency() == 3
    monkeypatch.setenv("BATCH_CONCURRENCY", "5")
    assert bw._concurrency() == 5
    monkeypatch.setenv("BATCH_CONCURRENCY", "not-a-number")
    assert bw._concurrency() == 3
    monkeypatch.setenv("BATCH_CONCURRENCY", "0")
    assert bw._concurrency() == 1  # never zero drainers
