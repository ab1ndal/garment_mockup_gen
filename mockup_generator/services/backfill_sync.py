"""Seed / rescan the ``backfill_items`` table from Google Drive.

This is the *only* code path that scans Drive. Everything else reads review state
from Postgres. A scan walks the worklist root (``pending`` cards) plus each
reserved subfolder (already-handled files), maps each file's folder to a status,
and upserts the rows by ``file_id`` so the Drive folder wins on reconcile.
"""

from __future__ import annotations

from mockup_generator.db import backfill_items_repo as repo
from mockup_generator.integrations import drive_client

# Reserved Drive subfolder -> backfill_items status for already-handled files.
_FOLDER_STATUS = {
    drive_client.SKIPPED_FOLDER: repo.SKIPPED,
    drive_client.EDIT_FOLDER: repo.EDIT,
    drive_client.REJECTED_FOLDER: repo.REGENERATE,
    drive_client.ARCHIVE_FOLDER: repo.PUBLISHED,
}


def _to_row(item: dict, status: str) -> dict:
    return {
        "file_id": item["file_id"],
        "productid": item["productid"],
        "alpha": item["alpha"],
        "filename": item["name"],
        "thumbnail_link": item.get("thumbnail_link"),
        "status": status,
    }


def scan(root_id: str) -> list[dict]:
    """Build upsert rows for every generation under ``root_id``: worklist root files
    as ``pending`` plus each reserved folder's files at its mapped status."""
    rows = [_to_row(it, repo.PENDING) for it in drive_client.scan_folder_of_folders(root_id)]
    for folder, status in _FOLDER_STATUS.items():
        rows.extend(_to_row(it, status) for it in drive_client.list_bucket(root_id, folder))
    return rows


def rescan(client, root_id: str) -> int:
    """Scan Drive and upsert into ``backfill_items``. Returns rows written."""
    return repo.upsert_many(client, scan(root_id))
