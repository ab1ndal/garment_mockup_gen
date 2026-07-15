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
