"""Google Drive read access via a service account.

Lists image files inside a product's Drive folder so the UI can preview and
select source images for generation.

Credentials come from ``GOOGLE_DRIVE_SA_JSON`` (config), which may be either a
path to a service-account key file or the JSON content itself. The account
needs read access to the product folders (share the parent folder with the
service-account email).

Thumbnails are streamed server-side through the authorized session and returned
as ``data:`` URIs, so the browser renders them without any extra auth and the
folders don't need to be publicly shared. If a thumbnail can't be fetched we
fall back to Drive's public thumbnail URL.
"""

from __future__ import annotations

import base64
import json
import re
from functools import lru_cache

from mockup_generator.config import settings

# Matches the folder id in the common Drive URL shapes:
#   .../drive/folders/<id>           .../folders/<id>?usp=sharing
#   ...open?id=<id>                  ...?id=<id>
_FOLDER_ID_RE = re.compile(r"(?:/folders/|[?&]id=)([A-Za-z0-9_-]+)")

_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
_MAX_FILES = 100


class DriveNotConfigured(RuntimeError):
    """Raised when GOOGLE_DRIVE_SA_JSON is not set."""


def extract_folder_id(url: str | None) -> str | None:
    if not url:
        return None
    m = _FOLDER_ID_RE.search(url)
    return m.group(1) if m else None


@lru_cache(maxsize=1)
def _clients():
    """Build (drive service, authorized session) once and cache them."""
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    from googleapiclient.discovery import build

    raw = settings.google_drive_sa_json
    if not raw:
        raise DriveNotConfigured("GOOGLE_DRIVE_SA_JSON is not set")

    info = json.loads(raw) if raw.lstrip().startswith("{") else json.load(open(raw))
    creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    svc = build("drive", "v3", credentials=creds, cache_discovery=False)
    return svc, AuthorizedSession(creds)


def _thumbnail_data_uri(session, link: str | None, file_id: str) -> str:
    """Fetch the thumbnail bytes via the authorized session and inline them.

    Falls back to Drive's public thumbnail URL (works only for link-viewable
    files) when no link is available or the fetch fails."""
    public = f"https://drive.google.com/thumbnail?id={file_id}&sz=w600"
    if not link:
        return public
    try:
        resp = session.get(link, timeout=10)
        resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "image/jpeg").split(";")[0]
        b64 = base64.b64encode(resp.content).decode("ascii")
        return f"data:{ctype};base64,{b64}"
    except Exception:
        return public


def list_folder_images(folder_id: str) -> list[dict]:
    """Return image files in the folder, sorted by name.

    Each item: ``{id, name, mime_type, thumbnail_url}``.
    """
    svc, session = _clients()
    resp = (
        svc.files()
        .list(
            q=(
                f"'{folder_id}' in parents and mimeType contains 'image/' "
                "and trashed = false"
            ),
            fields="files(id,name,mimeType,thumbnailLink)",
            orderBy="name_natural",
            pageSize=_MAX_FILES,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    images: list[dict] = []
    for f in resp.get("files", []):
        images.append(
            {
                "id": f["id"],
                "name": f.get("name", f["id"]),
                "mime_type": f.get("mimeType", "image/*"),
                "thumbnail_url": _thumbnail_data_uri(session, f.get("thumbnailLink"), f["id"]),
            }
        )
    return images
