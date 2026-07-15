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
from io import BytesIO

import pillow_heif
from googleapiclient.http import MediaIoBaseDownload

from mockup_generator.config import settings

# Drive lists any `image/*` file, including iPhone HEIC/HEIF, but stock Pillow
# can't decode those and raises UnidentifiedImageError on Image.open. Register
# the HEIF opener so such source images decode like any other format. The call
# patches Pillow's global registry, so once here covers every Image.open.
pillow_heif.register_heif_opener()

# Matches the folder id in the common Drive URL shapes:
#   .../drive/folders/<id>           .../folders/<id>?usp=sharing
#   ...open?id=<id>                  ...?id=<id>
_FOLDER_ID_RE = re.compile(r"(?:/folders/|[?&]id=)([A-Za-z0-9_-]+)")

# Generated mockup filenames: "<productid>", "<productid><alpha>", "<productid>_<alpha>".
# productid is "BC" + digits; the greedy \d+ stops at the first letter.
# An optional trailing duplicate marker is tolerated and discarded: a copy made
# by Drive/the OS appends " 2" or " (2)" (e.g. "BC25012 2", "BC25012A (3)").
_GEN_NAME_RE = re.compile(r"^(BC\d+)_?([A-Za-z]+)?(?: (?:\d+|\(\d+\)))?$")
_IMG_EXT_RE = re.compile(r"\.(png|jpe?g|webp)$", re.IGNORECASE)

_SCOPES = ["https://www.googleapis.com/auth/drive"]  # read + write: backfill deletes/moves files
_MAX_FILES = 100
_MAX_SUBFOLDERS = 30  # variant subfolders scanned per product (bounds Drive list calls)
_FOLDER_MIME = "application/vnd.google-apps.folder"
_THUMB_WORKERS = 8  # parallel thumbnail fetches (each is a serial HTTP GET otherwise)

# Reserved worklist subfolders the backfill flow writes into. Approved originals
# are archived to ``published/``, flagged ones moved to ``rejected/``, ones sent
# back for manual editing moved to ``edit/``, and ones deferred for later review
# moved to ``skipped/``; all are excluded from the scan so handled images never
# re-surface as review cards. The sub-tabs read these folders back directly.
ARCHIVE_FOLDER = "published"
REJECTED_FOLDER = "rejected"
EDIT_FOLDER = "edit"
SKIPPED_FOLDER = "skipped"
_RESERVED_SUBFOLDERS = {ARCHIVE_FOLDER, REJECTED_FOLDER, EDIT_FOLDER, SKIPPED_FOLDER}


class DriveNotConfigured(RuntimeError):
    """Raised when GOOGLE_DRIVE_SA_JSON is not set."""


def extract_folder_id(url: str | None) -> str | None:
    if not url:
        return None
    m = _FOLDER_ID_RE.search(url)
    return m.group(1) if m else None


def parse_generated_name(name: str) -> tuple[str | None, str | None]:
    """Split a generated filename into (productid, alpha).

    Returns (None, None) for any stem that isn't a bare ``BC<digits>`` optionally
    followed by an attached or underscore-separated alpha suffix. The alpha is
    upper-cased. A trailing duplicate marker (" 2" or " (2)") left by Drive/the OS
    is discarded. An image extension is stripped first; any other dot makes the
    name malformed.
    """
    stem = _IMG_EXT_RE.sub("", (name or "").strip())
    m = _GEN_NAME_RE.match(stem)
    if not m:
        return None, None
    alpha = m.group(2)
    return m.group(1), (alpha.upper() if alpha else None)


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


def _thumbnail_link(session, file_id: str) -> str | None:
    """Fetch one file's ``thumbnailLink`` over the authorized session.

    Uses the REST endpoint rather than the discovery client because callers fan
    this out across threads: ``requests`` sessions are thread-safe, the
    googleapiclient service object is not (it holds one httplib2 connection).
    """
    resp = session.get(
        f"https://www.googleapis.com/drive/v3/files/{file_id}",
        params={"fields": "thumbnailLink", "supportsAllDrives": "true"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("thumbnailLink")


def thumbnails_for_ids(file_ids: list[str]) -> list[dict]:
    """``{id, thumbnail_url}`` per file id, fetched in parallel.

    For callers that hold ids but no folder listing (a batch card's sources).
    Each id costs a metadata GET plus a thumbnail GET, so the whole set is
    fanned out; serially this is the slowest part of opening a review card.
    """
    if not file_ids:
        return []
    _, session = _clients()

    def one(file_id: str) -> dict:
        try:
            link = _thumbnail_link(session, file_id)
        except Exception:  # noqa: BLE001 - fall back to the public thumbnail URL
            link = None
        return {"id": file_id, "thumbnail_url": _thumbnail_data_uri(session, link, file_id)}

    with ThreadPoolExecutor(max_workers=min(_THUMB_WORKERS, len(file_ids))) as ex:
        return list(ex.map(one, file_ids))


def large_image_data_uri(file_id: str, size: int = 1600) -> str:
    """Browser-renderable enlarged preview for a Drive file, as a data URI.

    Used by the click-to-enlarge lightbox. Upsizes the file's ``thumbnailLink``
    to ``=s{size}`` — Drive renders HEIC/raw to JPEG, so the result is
    browser-safe and far lighter than streaming the original. Falls back to the
    public thumbnail URL when the link is unavailable or the fetch fails."""
    public = f"https://drive.google.com/thumbnail?id={file_id}&sz=w{size}"
    svc, session = _clients()
    try:
        meta = svc.files().get(
            fileId=file_id, fields="thumbnailLink", supportsAllDrives=True,
        ).execute()
    except Exception:
        return public
    link = meta.get("thumbnailLink")
    if not link:
        return public
    big = re.sub(r"=s\d+", f"=s{size}", link) if "=s" in link else f"{link}=s{size}"
    try:
        resp = session.get(big, timeout=15)
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


def download_file(file_id: str) -> bytes:
    """Download a Drive file's full-resolution bytes (``drive.readonly`` scope).

    Thumbnails are capped at ~w600, too small to use as generation references,
    so the real file bytes must be streamed via ``get_media``.
    """
    svc, _ = _clients()
    request = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
    buf = BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


def list_folder_image_ids(folder_id: str, limit: int = 14) -> list[str]:
    """Return image file ids in ``folder_id`` (loose + one level of subfolders),
    capped at ``limit``. Ids only — no thumbnails fetched, so this is cheap enough
    to call per product during batch enqueue."""
    svc, _ = _clients()
    ids: list[str] = []
    subfolders: list[str] = []
    top = _paged_files(
        svc, f"'{folder_id}' in parents and trashed = false", "files(id,name,mimeType)",
    )
    for f in top:
        if f.get("mimeType") == _FOLDER_MIME:
            subfolders.append(f["id"])
        elif (f.get("mimeType") or "").startswith("image/"):
            ids.append(f["id"])
    for sub in subfolders:
        if len(ids) >= limit:
            break
        for f in _paged_files(
            svc, f"'{sub}' in parents and mimeType contains 'image/' and trashed = false",
            "files(id,name,mimeType)",
        ):
            ids.append(f["id"])
    return ids[:limit]


def delete_file(file_id: str) -> None:
    """Permanently delete a Drive file. Requires ownership/organizer rights — the
    backfill flow archives via ``move_file`` instead because its service account is
    only an Editor. Kept as a generic helper for when ownership permits deletion."""
    svc, _ = _clients()
    svc.files().delete(fileId=file_id, supportsAllDrives=True).execute()


def move_file(file_id: str, new_parent_id: str) -> None:
    """Move a file by swapping its parent to ``new_parent_id`` (flag → rejected/)."""
    svc, _ = _clients()
    meta = svc.files().get(fileId=file_id, fields="parents",
                           supportsAllDrives=True).execute()
    old_parents = ",".join(meta.get("parents", []))
    svc.files().update(
        fileId=file_id, addParents=new_parent_id, removeParents=old_parents,
        fields="id,parents", supportsAllDrives=True,
    ).execute()


def find_subfolder(parent_id: str, name: str) -> str | None:
    """Return the id of the child folder ``name`` under ``parent_id``, or ``None``
    if it doesn't exist. Read-only — does not create (used by the sub-tab listings,
    where an absent folder simply means an empty bucket)."""
    svc, _ = _clients()
    resp = (
        svc.files()
        .list(
            q=(f"'{parent_id}' in parents and name = '{name}' and "
               f"mimeType = '{_FOLDER_MIME}' and trashed = false"),
            fields="files(id,name)", pageSize=1,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        )
        .execute()
    )
    existing = resp.get("files", [])
    return existing[0]["id"] if existing else None


def ensure_subfolder(parent_id: str, name: str) -> str:
    """Return the id of the child folder ``name`` under ``parent_id``, creating it
    if absent. Used to resolve the root-level ``rejected/``/``edit/``/``skipped/`` folders."""
    svc, _ = _clients()
    existing = find_subfolder(parent_id, name)
    if existing:
        return existing
    created = svc.files().create(
        body={"name": name, "mimeType": _FOLDER_MIME, "parents": [parent_id]},
        fields="id", supportsAllDrives=True,
    ).execute()
    return created["id"]


def list_bucket(root_id: str, name: str) -> list[dict]:
    """Flat scan-style list of images inside a reserved bucket subfolder
    (``skipped``/``edit``/``rejected``) directly under ``root_id``.

    Mirrors ``scan_folder_of_folders`` item shape (``productid``/``alpha`` parsed
    from the filename, ``thumbnail_link`` for a later batched fetch) so callers
    build review cards the same way. Empty list if the bucket folder doesn't exist
    yet (nothing has been moved there)."""
    folder_id = find_subfolder(root_id, name)
    if not folder_id:
        return []
    svc, _ = _clients()
    files = _list_image_files(svc, folder_id)
    return [_scan_item(f, folder_id, name) for f in files]


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


def _paged_files(svc, q: str, fields: str) -> list[dict]:
    """List all files matching ``q``, following nextPageToken (bounds large folders)."""
    out: list[dict] = []
    token = None
    while True:
        resp = (
            svc.files()
            .list(q=q, fields=f"nextPageToken,{fields}", pageSize=_MAX_FILES,
                  pageToken=token, orderBy="folder,name_natural",
                  supportsAllDrives=True, includeItemsFromAllDrives=True)
            .execute()
        )
        out.extend(resp.get("files", []))
        token = resp.get("nextPageToken")
        if not token:
            return out


def _scan_item(f: dict, subfolder_id: str | None, subfolder_name: str | None) -> dict:
    productid, alpha = parse_generated_name(f.get("name", ""))
    return {
        "productid": productid, "alpha": alpha, "file_id": f["id"],
        "name": f.get("name", f["id"]),
        "subfolder_id": subfolder_id, "subfolder_name": subfolder_name,
        "thumbnail_link": f.get("thumbnailLink"),
    }


def scan_folder_of_folders(root_id: str) -> list[dict]:
    """Flat list of every generated image under ``root_id`` (loose + one level of
    subfolders). Malformed filenames are included with ``productid=None`` so the
    UI can surface them. No thumbnails fetched here (cheap metadata only)."""
    svc, _ = _clients()
    top = _paged_files(
        svc, f"'{root_id}' in parents and trashed = false",
        "files(id,name,mimeType,thumbnailLink)",
    )
    items: list[dict] = []
    subfolders: list[dict] = []
    for f in top:
        if f.get("mimeType") == _FOLDER_MIME:
            if (f.get("name") or "").strip().lower() in _RESERVED_SUBFOLDERS:
                continue                       # published/ + rejected/ are not worklist
            subfolders.append(f)
        elif (f.get("mimeType") or "").startswith("image/"):
            items.append(_scan_item(f, None, None))
    for sf in subfolders:
        sub = _paged_files(
            svc, f"'{sf['id']}' in parents and mimeType contains 'image/' and trashed = false",
            "files(id,name,mimeType,thumbnailLink)",
        )
        for f in sub:
            items.append(_scan_item(f, sf["id"], sf.get("name", sf["id"])))
    return items


def thumbnails_for(items: list[dict]) -> dict[str, str]:
    """Return ``{file_id: data_uri}`` for a page of scan items, fetched in parallel."""
    if not items:
        return {}
    _, session = _clients()
    files = [{"id": i["file_id"], "thumbnailLink": i.get("thumbnail_link")} for i in items]
    got = _attach_thumbnails(session, files)
    return {fid: v["thumbnail_url"] for fid, v in got.items()}
