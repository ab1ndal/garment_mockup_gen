"""Upload generated mockups to a Supabase Storage bucket.

Writes use the service-role client (bypasses RLS). The ``mockups`` bucket is
public (view-only for anon), so we hand back the permanent public URL — no
signing needed and no expiry to manage.
"""

from __future__ import annotations

import re
import uuid

from mockup_generator.integrations.supabase_client import service_client

_BUCKET = "mockups"


class StorageNotConfigured(RuntimeError):
    """Raised when no service client is available (SUPABASE_SECRET_KEY unset)."""


def upload_mockup(
    productid: str,
    data: bytes,
    key: str,
    *,
    bucket: str = _BUCKET,
    ext: str = "png",
    content_type: str = "image/png",
) -> tuple[str, str]:
    """Upload ``data`` under ``{productid}/{key}.{ext}`` to the public bucket.

    ``ext``/``content_type`` default to PNG; pass ``webp``/``image/webp`` for the
    web-optimized variant. Returns ``(object_path, public_url)``: persist the
    stable path; hand the permanent public URL to the browser / store in DB.
    """
    client = service_client()
    if client is None:
        raise StorageNotConfigured("SUPABASE_SECRET_KEY is required to upload mockups")

    path = f"{productid}/{key}.{ext}"
    store = client.storage.from_(bucket)
    store.upload(path, data, {"content-type": content_type, "upsert": "true"})
    return path, store.get_public_url(path)


def download_mockup(object_path: str, *, bucket: str = _BUCKET) -> bytes:
    """Download one object's bytes from the bucket (service-role).

    Used as the VEO first-frame source: the video animates an already-published
    mockup that lives in Supabase Storage.
    """
    client = service_client()
    if client is None:
        raise StorageNotConfigured("SUPABASE_SECRET_KEY is required to download mockups")
    return client.storage.from_(bucket).download(object_path)


def slugify(text: str | None) -> str:
    """Filesystem/URL-safe slug: lowercase, non-alphanumeric runs -> single '-'."""
    if not text:
        return ""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def short_hex() -> str:
    """8 hex chars (uuid4) — uniqueness so re-approves don't overwrite."""
    return uuid.uuid4().hex[:8]


def path_from_public_url(url: str, *, bucket: str = _BUCKET) -> str | None:
    """Recover the stored object path from a Supabase public URL, else None."""
    marker = f"/object/public/{bucket}/"
    i = url.find(marker)
    if i == -1:
        return None
    return url[i + len(marker):].split("?")[0]


def delete_object(object_path: str, *, bucket: str = _BUCKET) -> None:
    """Remove one object from the bucket (orphan cleanup). Service-role only."""
    client = service_client()
    if client is None:
        raise StorageNotConfigured("SUPABASE_SECRET_KEY is required to delete objects")
    client.storage.from_(bucket).remove([object_path])
