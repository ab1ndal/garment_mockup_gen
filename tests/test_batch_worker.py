import pytest
from google.genai import errors as genai_errors

from backend.services import batch_worker as bw
from mockup_generator.db import batch_items_repo as repo
from mockup_generator.db.batch_items_repo import BatchRow


def _row(id=1, attempts=0):
    return BatchRow(id=id, batch_id="b1", productid="BC1", color="Red",
                    image_ids=["a", "b"], prompt_text="p", status=repo.GENERATING,
                    storage_path=None, error=None,
                    model="m", resolution="4K", aspect_ratio="1:1", attempts=attempts)


def _429():
    return genai_errors.ClientError(
        429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})


def _throw(exc):
    def _raise(*a, **k):
        raise exc
    return _raise


def test_run_one_generates_stages_to_storage_and_marks_ready(monkeypatch):
    claimed = {"n": 0}
    def fake_claim(db, assign_model=None):
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

    assert bw.run_one(object(), "m") is True
    assert updates[0][1] == repo.READY
    assert updates[0][2]["storage_path"] == "BC1/batch-1.png"
    # staged in the private temp bucket, never the public one
    assert uploaded["bucket"] == bw.storage_client.TEMP_BUCKET
    assert uploaded["data"] == b"PNG"


def test_run_one_marks_failed_on_error(monkeypatch):
    monkeypatch.setattr(bw.repo, "claim_next_queued", lambda db, assign_model=None:_row())
    monkeypatch.setattr(bw.drive_client, "download_file", lambda fid: _tiny_png())
    monkeypatch.setattr(bw.service, "generate_mockup_bytes",
                        lambda images, prompt, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    updates = []
    monkeypatch.setattr(bw.repo, "transition",
                        lambda db, *, item_id, expect, to, **f: updates.append((to, f)) or True)
    assert bw.run_one(object(), "m") is True
    assert updates[0][0] == repo.FAILED and "boom" in updates[0][1]["error"]


def test_run_one_marks_failed_when_staging_upload_fails(monkeypatch):
    """A staging failure must land on the card, not kill the worker loop — this is
    the shape of the Drive 403 that made Drive staging unusable."""
    monkeypatch.setattr(bw.repo, "claim_next_queued", lambda db, assign_model=None:_row())
    monkeypatch.setattr(bw.drive_client, "download_file", lambda fid: _tiny_png())
    monkeypatch.setattr(bw.service, "generate_mockup_bytes", lambda images, prompt, **k: b"PNG")
    def _boom(*a, **k): raise RuntimeError("storage down")
    monkeypatch.setattr(bw.storage_client, "upload_mockup", _boom)
    updates = []
    monkeypatch.setattr(bw.repo, "transition",
                        lambda db, *, item_id, expect, to, **f: updates.append((to, f)) or True)
    assert bw.run_one(object(), "m") is True
    assert updates[0][0] == repo.FAILED and "storage down" in updates[0][1]["error"]


def test_run_one_returns_false_when_nothing_queued(monkeypatch):
    monkeypatch.setattr(bw.repo, "claim_next_queued", lambda db, assign_model=None: None)
    assert bw.run_one(object(), "m") is False


def test_run_one_requeues_transient_429_under_cap(monkeypatch):
    """A quota 429 (all internal retries exhausted) goes back to queued for another
    pass, with the attempt counter bumped — it must not die in failed."""
    monkeypatch.setattr(bw.repo, "claim_next_queued", lambda db, assign_model=None:_row(attempts=0))
    monkeypatch.setattr(bw.drive_client, "download_file", lambda fid: _tiny_png())
    monkeypatch.setattr(bw.service, "generate_mockup_bytes", _throw(_429()))
    updates = []
    monkeypatch.setattr(bw.repo, "transition",
                        lambda db, *, item_id, expect, to, **f: updates.append((to, f)) or True)
    assert bw.run_one(object(), "m") is True
    assert updates[0][0] == repo.QUEUED and updates[0][1]["attempts"] == 1


def test_run_one_fails_transient_at_cap(monkeypatch):
    """Once the attempt cap is reached a still-failing card lands in failed for
    good, so a permanently exhausted quota can't loop a card forever."""
    monkeypatch.setattr(bw.repo, "claim_next_queued", lambda db, assign_model=None:_row(attempts=bw._MAX_ATTEMPTS - 1))
    monkeypatch.setattr(bw.drive_client, "download_file", lambda fid: _tiny_png())
    monkeypatch.setattr(bw.service, "generate_mockup_bytes", _throw(_429()))
    updates = []
    monkeypatch.setattr(bw.repo, "transition",
                        lambda db, *, item_id, expect, to, **f: updates.append((to, f)) or True)
    assert bw.run_one(object(), "m") is True
    assert updates[0][0] == repo.FAILED and updates[0][1]["attempts"] == bw._MAX_ATTEMPTS


def test_run_one_does_not_retry_permanent_error(monkeypatch):
    """A non-transient error (bad request, missing images) fails on the first pass
    instead of wasting the retry budget on a generation that can't succeed."""
    monkeypatch.setattr(bw.repo, "claim_next_queued", lambda db, assign_model=None:_row(attempts=0))
    monkeypatch.setattr(bw.drive_client, "download_file", lambda fid: _tiny_png())
    monkeypatch.setattr(bw.service, "generate_mockup_bytes", _throw(ValueError("bad input")))
    updates = []
    monkeypatch.setattr(bw.repo, "transition",
                        lambda db, *, item_id, expect, to, **f: updates.append((to, f)) or True)
    assert bw.run_one(object(), "m") is True
    assert updates[0][0] == repo.FAILED and updates[0][1]["attempts"] == 1


def test_no_image_response_is_transient(monkeypatch):
    """Flash sometimes returns no image part on a fine prompt; treat NO_IMAGE as
    retryable so those cards aren't lost."""
    monkeypatch.setattr(bw.repo, "claim_next_queued", lambda db, assign_model=None:_row(attempts=0))
    monkeypatch.setattr(bw.drive_client, "download_file", lambda fid: _tiny_png())
    monkeypatch.setattr(bw.service, "generate_mockup_bytes",
                        _throw(bw.NoImageReturned("no image (finish_reason: NO_IMAGE)")))
    updates = []
    monkeypatch.setattr(bw.repo, "transition",
                        lambda db, *, item_id, expect, to, **f: updates.append((to, f)) or True)
    assert bw.run_one(object(), "m") is True
    assert updates[0][0] == repo.QUEUED


def _tiny_png() -> bytes:
    from io import BytesIO
    from PIL import Image
    buf = BytesIO(); Image.new("RGB", (2, 2)).save(buf, "PNG"); return buf.getvalue()


@pytest.fixture(autouse=True)
def _no_reclaim(monkeypatch):
    """Pool tests exercise slot math, not DB reclaim — stub it to a no-op so
    ensure_running doesn't touch the fake db. Overridden where reclaim is asserted."""
    monkeypatch.setattr(bw.repo, "reset_stale_generating", lambda db, secs: 0)


def test_ensure_running_reclaims_stale_generating(monkeypatch):
    """Every ensure_running rescues crashed-mid-card rows before topping up, so a
    stuck card recovers on the next enqueue instead of waiting for a restart."""
    monkeypatch.setattr(bw, "_active", {})
    monkeypatch.setattr(bw, "_model_budgets", lambda: {"m": 1})
    monkeypatch.setattr(bw, "_spawn", lambda fn, *a: None)
    calls = []
    monkeypatch.setattr(bw.repo, "reset_stale_generating",
                        lambda db, secs: calls.append(secs) or 2)
    bw.ensure_running(object())
    assert calls == [bw._STALE_SECONDS]


def test_ensure_running_starts_a_drainer_pool_per_model(monkeypatch):
    """Throughput: one dedicated drainer set per model, so the batch fans out
    across each model's independent capacity pool."""
    monkeypatch.setattr(bw, "_active", {})
    monkeypatch.setattr(bw, "_model_budgets", lambda: {"flash": 2, "pro": 1})
    spawned = []
    # record the model each drainer is bound to (last arg of _spawn(run_worker, db, model))
    monkeypatch.setattr(bw, "_spawn", lambda fn, *a: spawned.append(a[-1]))
    bw.ensure_running(object())
    assert sorted(spawned) == ["flash", "flash", "pro"]


def test_ensure_running_tops_up_without_exceeding_budget(monkeypatch):
    """A second caller while drainers are live must not stack a second pool."""
    monkeypatch.setattr(bw, "_active", {})
    monkeypatch.setattr(bw, "_model_budgets", lambda: {"flash": 2, "pro": 1})
    spawned = []
    monkeypatch.setattr(bw, "_spawn", lambda fn, *a: spawned.append(a[-1]))
    bw.ensure_running(object())   # -> 3 drainers (2 flash, 1 pro)
    bw.ensure_running(object())   # pools already full -> no new drainers
    assert len(spawned) == 3
    assert bw._active == {"flash": 2, "pro": 1}


def test_drainer_releases_its_slot_when_queue_empties(monkeypatch):
    monkeypatch.setattr(bw, "_active", {"m": 2})
    monkeypatch.setattr(bw.repo, "claim_next_queued", lambda db, assign_model=None: None)
    bw.run_worker(object(), "m")
    assert bw._active == {"m": 1}


def test_pool_refills_after_draining(monkeypatch):
    """Once drainers exit, a later enqueue can start a fresh pool."""
    monkeypatch.setattr(bw, "_active", {})
    monkeypatch.setattr(bw, "_model_budgets", lambda: {"m": 2})
    monkeypatch.setattr(bw.repo, "claim_next_queued", lambda db, assign_model=None: None)
    monkeypatch.setattr(bw, "_spawn", lambda fn, *a: fn(*a))  # run inline -> drains + exits
    bw.ensure_running(object())
    assert bw._active == {"m": 0}
    bw.ensure_running(object())
    assert bw._active == {"m": 0}


def test_model_budgets_reads_settings_with_sane_fallback(monkeypatch):
    """Exercises the real settings read. The pool tests stub _model_budgets, so
    without this an import/config error here would ship green."""
    monkeypatch.delenv("BATCH_MODEL_CONCURRENCY", raising=False)
    monkeypatch.delenv("BATCH_CONCURRENCY", raising=False)
    single = bw.settings.gemini_image_model
    assert bw._model_budgets() == {single: 3}
    monkeypatch.setenv("BATCH_CONCURRENCY", "5")
    assert bw._model_budgets() == {single: 5}
    monkeypatch.setenv("BATCH_CONCURRENCY", "0")
    assert bw._model_budgets() == {single: 1}  # never zero drainers
    # an explicit per-model split overrides the single-model fallback
    monkeypatch.setenv("BATCH_MODEL_CONCURRENCY", "gemini-3.1-flash-image=3, gemini-3-pro-image=3")
    assert bw._model_budgets() == {"gemini-3.1-flash-image": 3, "gemini-3-pro-image": 3}
    # a malformed entry is skipped, the valid ones survive
    monkeypatch.setenv("BATCH_MODEL_CONCURRENCY", "gemini-3-pro-image=2,garbage")
    assert bw._model_budgets() == {"gemini-3-pro-image": 2}
