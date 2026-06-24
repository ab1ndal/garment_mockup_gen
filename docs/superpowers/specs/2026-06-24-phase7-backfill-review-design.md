# Phase 7 — Backfill Review tab — Design

**Date:** 2026-06-24
**Companion to:** `docs/plans/2026-06-21-implementation-plan.md` (Phase 7)
**Status:** Approved — ready for implementation plan.

Phase 7 was originally a silent, idempotent backfill script. It is redefined here
as an **interactive review tab**: a reviewer walks the previously-generated Drive
mockups one by one, assigns each a color (and theme/aspect), and either publishes
it into Supabase or flags it for regeneration. Approve and flag both mutate Drive,
so the generated folder is itself the worklist and shrinks toward empty.

---

## 1. Goal & context

We have ~1,000–2,000 already-generated base mockups sitting in a Google Drive
folder (`1FBDw_F40zDt4zvp6el3Ei50Nw8cOahm4`), organized **folder-of-folders**
(one subfolder per grouping). Filenames are `<productid>.png` or
`<productid>_<alpha>.png`, where the alpha suffix marks additional variants under
the same product (a variant is, for the most part, a **color**).

None of these are in the app's database or Supabase Storage yet. This tab lets a
reviewer:

1. See each generated image next to its original source ("clicked") images.
2. Assign the correct **color** (always present in `productsizecolors`) plus an
   editable **photo theme** and an auto-detected **aspect ratio**.
3. **Approve & publish** → upload to Supabase, write DB rows, flip `base_mockup`,
   and **delete** the Drive original (move semantics).
4. **Flag for regeneration** → force `base_mockup = false` for that product and
   **move** the Drive original to a root-level `rejected/` folder.

The generated Drive folder is the source of truth for "what still needs review."
Every approve/flag removes the file from that folder, so re-scanning never
re-surfaces a handled image — idempotency comes from Drive state, not a DB queue.

---

## 2. Architecture & data flow

New React tab **"Backfill"** in the existing tabbed shell. The backend scans the
generated Drive folder once into an **in-memory flat index** (TTL 5 min + manual
refresh), and serves paginated review cards. Per-card originals load lazily on
open.

```
Drive generated folder (folder-of-folders, ~1-2k imgs)
   │  scan once (TTL 300s + manual refresh)
   ▼
in-memory flat index: [{productid, alpha, file_id, filename, subfolder_name}]
   │  paginate (offset/limit)
   ▼
GET /api/backfill/items → cards (thumbnail + product colors)
   │
   ├── click a card ──▶ GET /api/backfill/{file_id}/sources?productid=
   │                     (lazy: product.producturl → list_folder_image_groups
   │                      + a larger preview of the generated image)
   │
   ├── Approve ──▶ download bytes → publish_image(Supabase + DB) → delete Drive file → evict
   │
   └── Flag ─────▶ set base_mockup=false → move Drive file → rejected/ → evict
```

**Why Approach 1 (in-memory cached flat index):** backfill is a finite cleanup
task. One expensive scan is amortized across all paging; cards are fast; mutations
are cheap (evict one entry). The cache dies on backend restart (HF Spaces) — it
simply re-scans, which is acceptable. A manual **Refresh** button covers Drive
edits made mid-session. No schema churn, no drift between a DB queue and Drive
reality. (Rejected alternatives: a DB-materialized `backfill_queue` table — more
infra and can drift from Drive; scan-per-page — dozens of Drive calls per
page-flip, rate-limit risk.)

---

## 3. Backend

### 3.1 Config (`config.py`)
- Add `GENERATED_MOCKUPS_FOLDER_ID` (default `1FBDw_F40zDt4zvp6el3Ei50Nw8cOahm4`),
  surfaced on `settings`.

### 3.2 Drive write access (`integrations/drive_client.py`)
- **Scope change:** `_SCOPES` → `["https://www.googleapis.com/auth/drive"]`
  (read **and** write). The service account already has Editor rights on the
  generated folder (confirmed). This is required for delete and move.
- `scan_folder_of_folders(root_id) -> list[dict]` — walk immediate subfolders and
  list images in each, returning a **flat** list of
  `{productid, alpha, file_id, name, subfolder_id, subfolder_name}`. Raise the
  `_MAX_SUBFOLDERS` cap / paginate `files().list` so ~2k images across many
  subfolders are fully covered. No thumbnails fetched here (cheap metadata only).
- `parse_generated_name(name) -> (productid, alpha|None)` — strip extension, split
  the stem on the **first** `_`: part before is `productid`, part after is `alpha`
  (productids contain no `_`). `<productid>.png` → `(productid, None)`.
- `delete_file(file_id)` — `files().delete(supportsAllDrives=True)`. Used by approve.
- `move_file(file_id, new_parent_id)` — `files().update(addParents=new, removeParents=old, supportsAllDrives=True)`; fetch current parents first. Used by flag.
- `ensure_subfolder(parent_id, name) -> str` — find a child folder named `name`
  under `parent_id`, create it if absent, return its id (cached). Used to resolve
  the root-level `rejected/` folder once.
- Larger preview helper: reuse `_thumbnail_data_uri` with a `w1000`-class link, or
  add a `preview_data_uri(file_id)` that fetches a bigger thumbnail for the
  right-hand review pane (full-res download is reserved for publish).

### 3.3 Shared publish path (`generation/publish.py`, new — refactor)
Extract the publish body currently inline in `backend/routers/generate.py`
`approve_mockup` into a reusable function:

```python
def publish_image(
    db, *, productid, png, color, theme_name, aspect_ratio,
    created_by, prompt_text=None, prompt_id=None,
) -> dict:  # returns {public_url, imageid, ...}
```

It performs, unchanged from Phase 3 semantics: compute `_photo_theme(theme_name,
aspect_ratio)`; upload to the public `mockups` bucket under
`{productid}/{colorslug}_{hex}.png`; orphan-clean prior `productimages` rows for
the same `(productid, color, theme)` (delete their Storage objects); write the
`mockup_variations` audit row; flip `mockups.base_mockup = true`; replace the
`productimages` row. Both Phase 3 `approve_mockup` and Phase 7 backfill call this
— single publish path, no duplicated logic. `prompt_text`/`prompt_id` default to
`None` for backfill (no prompt was used to make these images).

### 3.4 Index service (`backfill_service.py`, new)
- Module-level cache: `{folder_id: (timestamp, list[item])}`, TTL 300s.
- `get_index(db, *, refresh=False) -> list[item]` — scan via
  `scan_folder_of_folders` when stale/forced; for each item validate `productid`
  against `products` (set `unknown_product=True` when not found); cache and return.
- `paginate(items, offset, limit)` and `evict(folder_id, file_id)`.
- `monotonic`-based timestamps (no wall-clock dependency).

### 3.5 Router (`backend/routers/backfill.py`, new)
- `GET /api/backfill/items?offset=&limit=&refresh=` →
  `{total, remaining, items: [{productid, product_name, alpha, file_id, filename,
  thumbnail_url, colors[], unknown_product}]}`. Thumbnails and `list_colors` are
  fetched **per page only** (page size ~20), not for the whole index.
- `GET /api/backfill/{file_id}/sources?productid=` →
  `{originals: list_folder_image_groups(product.producturl), generated_preview}`.
  Lazy — called when a card opens. Larger generated preview for the right pane.
- `POST /api/backfill/approve` `{file_id, productid, color, theme_name,
  aspect_ratio}` → `download_file(file_id)` → `publish_image(...)` →
  `delete_file(file_id)` → `evict`. Returns the published row/url.
- `POST /api/backfill/flag` `{file_id, productid}` →
  `mockups_repo.set_base_mockup(db, productid, False)` →
  `move_file(file_id, ensure_subfolder(root, "rejected"))` → `evict`.
  For `unknown_product` items: move only, skip the DB flip.

All endpoints behind the existing active-profile auth dependency.

### 3.6 Migration (`docs/migrations/2026-06-24-backfill.sql`)
```sql
alter table public.mockup_variations alter column prompt_text drop not null;
```
Applied via Supabase MCP `apply_migration`. The backfill audit row stores the
published Storage path in `image_url`, `kind='image'`, `created_by` = approver,
`prompt_text = null`, `prompt_id = null`.

---

## 4. Frontend (`frontend/src/components/BackfillTab.tsx`)

Designed with the `ui-ux-pro-max` skill at build time (touch targets, focus
states, hover-vs-tap, contrast).

- **Tab:** add "Backfill" to the `App.tsx` tabbed shell (alongside Products /
  Prompts).
- **Card grid (paginated):** each card shows the generated thumbnail, a
  `productid` + product-name badge, a **color dropdown** (prepopulated when the
  product has exactly one color), and a **Review** action. Header shows
  "N remaining" and a **Refresh** button (forces a Drive rescan).
- **Review panel** (modal or in-place split), opened from a card:
  - **Left:** source / "clicked" originals for the product (lazy
    `GET /sources`), grouped as Drive returns them.
  - **Right:** the generated image, larger.
  - **Controls:** color dropdown; **theme** text input (default `Default`,
    editable); **aspect** dropdown auto-detected from the image dimensions
    (`1:1, 4:5, 3:4, 9:16, 16:9`), override allowed.
  - **Buttons:** **Approve & publish** · **Flag for regeneration** · Close.
- On approve/flag success → remove the card, decrement the remaining counter.
- `unknown_product` card → show an "unknown product" badge, **disable Approve**,
  keep Flag (move-to-rejected only).

`frontend/src/api.ts` gains typed wrappers for the four endpoints.

---

## 5. Edge cases & error handling

- **Drive not configured / insufficient scope** → surface a 4xx, do not crash the
  tab.
- **Unknown productid** (filename parses but no matching product) → item flagged
  `unknown_product`; Approve blocked, Flag (move-to-rejected) still allowed.
- **Product `producturl` missing / folder unshared** → originals panel renders
  empty with a notice; Approve still allowed (originals are reference only).
- **Approve partial failure** — operation order is upload → DB writes → **Drive
  delete last**. If the Drive delete fails, the image stays in Drive and
  reappears on the next scan; because `publish_image` upserts and orphan-cleans
  (Phase 3 behavior), re-approving the same image is idempotent. Surface a
  warning toast rather than failing hard.
- **Aspect auto-detect** — PIL reads the downloaded bytes' width×height and snaps
  to the nearest of `{1:1, 4:5, 3:4, 9:16, 16:9}`; the reviewer can override.
- **Concurrency** — single-reviewer assumption; the in-memory index is per
  backend instance. A second reviewer (or a restart) just re-scans Drive.

---

## 6. Testing

- **Unit**
  - `parse_generated_name` — `BC25123.png`, `BC25123_a.png`, no-extension, names
    with extra dots, productid-only.
  - `drive_client` — `delete_file`, `move_file`, `ensure_subfolder` (get vs
    create), `scan_folder_of_folders` flattening — all against a mocked Drive
    service.
  - `backfill_service` — index build + productid validation, TTL staleness,
    pagination, eviction.
  - `publish_image` — shared publish path (theme, upload key, orphan cleanup,
    base_mockup flip, productimages replace), `prompt_text=None` allowed.
- **Endpoints** — `items` pagination shape; `sources` lazy fetch; `approve`
  (mock download + publish + delete + evict); `flag` (mock flip + move + evict);
  `unknown_product` approve-blocked / flag-move-only.
- **Regression** — existing `generate.py` `approve_mockup` tests stay green after
  extracting `publish_image`.
- **Frontend** — `npm run build` clean; typed api wrappers compile.

---

## 7. Out of scope

- Bulk / auto-approve (every image is reviewed individually).
- Editing the generated image (that is the existing generate/refine flow; flag →
  regenerate routes a product back to the Products tab).
- A persisted DB review queue (Drive state is the worklist).
- Restoring images out of `rejected/` (manual Drive operation if ever needed).
