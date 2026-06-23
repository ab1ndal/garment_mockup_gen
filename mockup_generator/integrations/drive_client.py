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
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

from mockup_generator.config import settings

# Matches the folder id in the common Drive URL shapes:
#   .../drive/folders/<id>           .../folders/<id>?usp=sharing
#   ...open?id=<id>                  ...?id=<id>
_FOLDER_ID_RE = re.compile(r"(?:/folders/|[?&]id=)([A-Za-z0-9_-]+)")

_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
_MAX_FILES = 100
_MAX_SUBFOLDERS = 30  # variant subfolders scanned per product (bounds Drive list calls)
_FOLDER_MIME = "application/vnd.google-apps.folder"
_THUMB_WORKERS = 8  # parallel thumbnail fetches (each is a serial HTTP GET otherwise)


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


def _list_image_files(svc, folder_id: str) -> list[dict]:
    """Raw Drive file metadata for images directly in a folder (no thumbnail fetch)."""
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
    return resp.get("files", [])


def _attach_thumbnails(session, files: list[dict]) -> dict[str, dict]:
    """Build UI image items for ``files``, fetching thumbnails in parallel.

    Returns a dict keyed by file id so callers can re-assemble any grouping. Each
    item: ``{id, name, mime_type, thumbnail_url}``.
    """
    if not files:
        return {}
    with ThreadPoolExecutor(max_workers=min(_THUMB_WORKERS, len(files))) as ex:
        uris = list(
            ex.map(lambda f: _thumbnail_data_uri(session, f.get("thumbnailLink"), f["id"]), files)
        )
    return {
        f["id"]: {
            "id": f["id"],
            "name": f.get("name", f["id"]),
            "mime_type": f.get("mimeType", "image/*"),
            "thumbnail_url": uri,
        }
        for f, uri in zip(files, uris)
    }


def list_folder_images(folder_id: str) -> list[dict]:
    """Return image files directly in the folder, sorted by name.

    Each item: ``{id, name, mime_type, thumbnail_url}``. One level only — does
    not descend into subfolders (use ``list_folder_image_groups`` for that).
    """
    svc, session = _clients()
    files = _list_image_files(svc, folder_id)
    items = _attach_thumbnails(session, files)
    return [items[f["id"]] for f in files]


def list_folder_image_groups(folder_id: str) -> dict:
    """Return the folder's images grouped by immediate subfolder (variants).

    Shape::

        {
          "loose":  [img, ...],                       # images directly in the folder
          "groups": [{"id", "name", "images": [img]}],# one per subfolder that has images
        }

    Variants are stored as subfolders, so each immediate subfolder becomes a
    named group. Descends exactly one level; empty subfolders are omitted and at
    most ``_MAX_SUBFOLDERS`` are scanned (one Drive list call each). Thumbnails
    for the whole product are fetched in one parallel batch.
    """
    svc, session = _clients()
    resp = (
        svc.files()
        .list(
            q=(
                f"'{folder_id}' in parents and trashed = false and "
                f"(mimeType contains 'image/' or mimeType = '{_FOLDER_MIME}')"
            ),
            fields="files(id,name,mimeType,thumbnailLink)",
            orderBy="folder,name_natural",
            pageSize=_MAX_FILES,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )

    loose_files: list[dict] = []
    subfolders: list[dict] = []
    for f in resp.get("files", []):
        (subfolders if f.get("mimeType") == _FOLDER_MIME else loose_files).append(f)
    subfolders = subfolders[:_MAX_SUBFOLDERS]

    # Metadata list per subfolder (cheap, serial); thumbnails batched below.
    sub_files = {sf["id"]: _list_image_files(svc, sf["id"]) for sf in subfolders}

    all_files = loose_files + [f for files in sub_files.values() for f in files]
    items = _attach_thumbnails(session, all_files)

    groups: list[dict] = []
    for sf in subfolders:
        imgs = [items[f["id"]] for f in sub_files[sf["id"]]]
        if imgs:
            groups.append({"id": sf["id"], "name": sf.get("name", sf["id"]), "images": imgs})

    return {"loose": [items[f["id"]] for f in loose_files], "groups": groups}
