"""Upload generated mockups to a Supabase Storage bucket.

Writes use the service-role client (bypasses RLS); the bucket is private and we
hand back a signed URL so the browser can render the result without extra auth.
"""

from __future__ import annotations

from mockup_generator.integrations.supabase_client import service_client

_BUCKET = "mockups"
_SIGNED_TTL = 7 * 24 * 3600  # 7 days


class StorageNotConfigured(RuntimeError):
    """Raised when no service client is available (SUPABASE_SECRET_KEY unset)."""


def upload_mockup(
    productid: str,
    data: bytes,
    key: str,
    *,
    bucket: str = _BUCKET,
    signed_ttl: int = _SIGNED_TTL,
) -> tuple[str, str]:
    """Upload PNG ``data`` under ``{productid}/{key}.png``.

    Returns ``(object_path, signed_url)``: persist the stable object path,
    hand the (expiring) signed URL to the browser.
    """
    client = service_client()
    if client is None:
        raise StorageNotConfigured("SUPABASE_SECRET_KEY is required to upload mockups")

    path = f"{productid}/{key}.png"
    store = client.storage.from_(bucket)
    store.upload(path, data, {"content-type": "image/png", "upsert": "true"})
    res = store.create_signed_url(path, signed_ttl)
    return path, res["signedURL"]
