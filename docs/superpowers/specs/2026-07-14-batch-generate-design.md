# Batch Generate — Design Spec

**Date:** 2026-07-14
**Status:** Approved for planning
**Source goal:** `docs/batch-gen.md`

## 1. Overview

A new **Batch Generate** tab lets an operator generate mockups for many products at once. The operator picks a category (or *All categories*) and a count N (1–100 products). The backend enqueues one review **card per (product × color)**. A background worker generates each mockup and stages the PNG in Google Drive; cards stream into a paginated review UI as they become ready. For each card the operator can **Accept** (publish to Supabase Storage + write DB rows, delete the Drive copy), **Edit** (add a note / change source images, then regenerate in place), or **Reject** (discard, delete the Drive copy).

Cards are DB rows, so the worklist survives sessions, is recoverable after a crash, and is safe for concurrent reviewers. The design deliberately mirrors the existing **Backfill** worklist (`backfill_items`) — same status-driven, paginated, optimistic-transition pattern.

### Non-negotiable requirements (from the goal)
- Select a single category or All categories.
- Count is an integer 1–100 = number of **product IDs** to process.
- Skip any product ID with no product images.
- One mockup **per color** per product (colors from the product's variant list).
- Prompt is prefixed with `"Make the professional mockup of the {color} product"`.
- During review, the generated mockups live in Google Drive; on Accept a copy is saved to Supabase Storage and the Drive copy is removed.
- Cards paginated, retained, and recoverable across sessions.

## 2. Decisions (resolved in brainstorming)

| Decision | Choice |
|---|---|
| Execution model | **Background job + DB worklist.** Enqueue is instant; a worker sweeps rows and populates cards. Fully recoverable. |
| Product selection | **Pending only, by product-id order.** First N products with `base_mockup=False` in the category. |
| Product with no colors | **One colorless card** (`"Make the professional mockup of the product"`, no color word). |
| Drive staging location | **A new subfolder inside `GENERATED_MOCKUPS_FOLDER_ID`** (the same Base Mockup Folder the Backfill tab scans). |
| Category with no resolvable prompt | **Skip the whole category**, report reason. Same skip mechanic as no-images. |
| Generation concurrency | **Sequential (worker concurrency 1)** — respects Gemini rate limits and the existing 8-attempt backoff. |

## 3. Data model — new table `batch_items`

One row per card. Modeled on `backfill_items` (`mockup_generator/db/backfill_items_repo.py`).

| column | type | notes |
|---|---|---|
| `id` | bigint identity PK | |
| `batch_id` | uuid, not null | groups one enqueue run |
| `productid` | text, not null, FK→products | |
| `color` | text null | null = colorless product |
| `image_ids` | jsonb, not null | Drive source file ids used (all product images, cap 14) |
| `prompt_text` | text, not null | composed prompt (prefix + category body) |
| `status` | text, not null | `queued\|generating\|ready\|failed\|published\|rejected` |
| `drive_file_id` | text null | staged mockup file in Drive (set when `ready`) |
| `error` | text null | failure message when `failed` |
| `model` | text, not null | gen params snapshotted at enqueue |
| `resolution` | text, not null | |
| `aspect_ratio` | text, not null | |
| `created_by` | uuid null, FK→profiles | operator who enqueued |
| `created_at` | timestamptz, default now() | |
| `updated_at` | timestamptz, default now() | |

- Indexes: `status`, `batch_id`.
- RLS: enabled, service-role only (server writes via secret key; no client writes) — same as `backfill_items`.
- Migration file: `docs/migrations/2026-07-14-batch-items.sql`.
- New repo: `mockup_generator/db/batch_items_repo.py` with `@dataclass BatchRow` and functions mirroring `backfill_items_repo`: `insert_many`, `page(status, offset, limit) -> (rows, total)`, `counts() -> dict[str,int]`, `get(id)`, `transition(id, expect, to, **fields) -> bool` (optimistic conditional update), `claim_next_queued() -> BatchRow | None`, `reset_orphaned_generating() -> int`.

## 4. State machine

```
queued ─► generating ─► ready ─► published        (Accept)
                            └──► rejected          (Reject)
             └──► failed                           (generation error after retries; retryable → queued)

Edit:  ready ─► queued   (updates prompt_text/image_ids, deletes old Drive file; worker regenerates)
```

- All operator transitions are **optimistic conditional updates** (`UPDATE ... WHERE id=? AND status=?`). If the row is no longer in the expected state, the update affects 0 rows → API returns **409** and the client re-syncs. Reuses the Backfill `transition` pattern.
- **Crash recovery:** on FastAPI startup, `reset_orphaned_generating()` flips any `generating` rows back to `queued` (a worker died mid-generation). Idempotent and safe because generation itself writes nothing until success.

## 5. Enqueue flow — `POST /api/batch`

Request body:
```json
{ "category": "SA | null", "count": 25, "model": "…?", "resolution": "…?", "aspect_ratio": "…?" }
```
`category=null` means All categories. `model/resolution/aspect_ratio` optional; default to the same system defaults `GET /api/generate/options` returns. `pending` is fixed `true` (not exposed).

Steps:
1. `products_repo.list_products(category=category, pending=True, limit=count)` → first N pending products by id key.
2. For each product, resolve the category prompt: DB default (`prompts_repo.list_by_category` → `is_default`) → else `prompts.defaults.prompt_for_category(categoryid)` → else **skip** (reason `"no prompt for category <id>"`).
3. List the product's Drive source images (`drive_client` via `products.producturl`). **Skip if empty** (reason `"no images"`) or the product has no `producturl` (reason `"no drive folder"`).
4. `image_ids` = all product Drive image ids, capped at `_MAX_REFS` (14).
5. Colors via `variants_repo.list_colors(productid)`. If none → a single colorless card.
6. Compose prompt per color:
   - with color: `f"Make the professional mockup of the {color} product.\n\n{category_body}"`
   - colorless: `f"Make the professional mockup of the product.\n\n{category_body}"`
7. `batch_items_repo.insert_many(...)` — one row per (product, color), `status="queued"`, `batch_id` shared, params snapshotted.
8. Ensure the worker thread is running (start if idle).
9. Return `{ "batch_id": "...", "queued": <n>, "skipped": [{ "productid": "...", "reason": "..." }] }`.

Enqueue does **no** Gemini calls — it is fast and returns immediately.

## 6. Background worker

Reuses the detached-background-thread pattern already used for VEO video jobs in `backend/routers/generate.py`. A single module-level worker (`backend/services/batch_worker.py`) with a lock so only one runs.

Loop until no `queued` rows remain:
1. `claim_next_queued()` — optimistic `queued → generating` (race-safe; resumable across restarts).
2. Download each `image_ids` file from Drive (`drive_client.download_file`).
3. `service.generate_mockup_bytes(images, prompt_text, model=…, resolution=…, aspect_ratio=…)` → PNG bytes.
4. Upload PNG to the **Drive staging subfolder** (`drive_client.upload_image`) → `drive_file_id`.
5. `transition(id, expect="generating", to="ready", drive_file_id=…)`.
6. On any error after the generator's internal retries: `transition(id, "generating", "failed", error=str(exc))`. Continue with the next row.

- Sequential (concurrency 1). The generator already retries 429/5xx up to 8 times with backoff.
- Auto-start triggers: on enqueue, and on FastAPI startup if `queued` rows exist (after `reset_orphaned_generating`).

**Deployment risk (HF Spaces):** if the Space sleeps mid-batch, the worker thread dies; rows remain `queued`/`generating`. Mitigation: the startup sweep resets `generating→queued`, and the worker resumes on the next request that reaches the backend. No external queue/worker infrastructure is added. Documented as a known limitation, acceptable for this workload.

## 7. Drive staging

- Staging folder = a subfolder (name e.g. `_batch`) under `GENERATED_MOCKUPS_FOLDER_ID`, created on first use.
- New `drive_client.py` helpers:
  - `get_or_create_subfolder(parent_id, name) -> str` (idempotent: query by name+parent, create if missing).
  - `upload_image(folder_id, name, data: bytes, mime="image/png") -> str` (returns new file id).
  - `delete_file(file_id)` already exists (used for cleanup on Accept/Reject/Edit).
- Staged file naming: `{productid}_{color-slug-or-nocolor}_{short_hex}.png` (avoids collisions across re-generations).
- **Backfill isolation:** the Backfill tab seeds cards by scanning the top level of `GENERATED_MOCKUPS_FOLDER_ID`. The `_batch` staging subfolder must be added to that scan's excluded-folder set so batch files never surface as backfill cards. (Analogous to the existing per-product reserved subfolders `published/rejected/edit/skipped` in `drive_client.py:60-64`.)

## 8. Accept / Edit / Reject endpoints

All take a `batch_items.id`, all use optimistic transitions (expect `ready`), all return `{ status, warning? }` and **409** if already handled.

- **Accept** — `POST /api/batch/{id}/accept`, optional overrides `{ color?, theme_name?, aspect_ratio? }`:
  1. Download staged file from Drive (`drive_file_id`).
  2. `publish.publish_image(db, productid=, png=, color=, theme_name=, aspect_ratio=, created_by=, prompt_text=)` — uploads PNG+WEBP to the public `mockups` bucket and writes the 3 DB rows (`mockup_variations` insert, `mockups.base_mockup=true`, `productimages` insert). This is the existing canonical writer.
  3. Remove the staged Drive file: `delete_file` (the SA owns files it uploaded); **if deletion isn't permitted, fall back to moving the file into the `published/` archive folder** so it always leaves `_batch`.
  4. `transition(id, "ready", "published")`.
  - Ordering: publish first, then remove the Drive copy, then mark published. If both delete and archive-move fail, the publish already succeeded → mark published and surface a `warning`, rather than failing the accept.
- **Edit** — `POST /api/batch/{id}/edit`, body `{ prompt_note?, image_ids? }`:
  1. `transition(id, "ready", "queued", prompt_text=<updated>, image_ids=<updated>)` where updated prompt appends `\n\nRevision note: {prompt_note}` (same convention as `ProductsTab.composePrompt`).
  2. Delete the old staged Drive file (`drive_file_id`), clear `drive_file_id`.
  3. Ensure worker is running → it regenerates the card in place.
- **Reject** — `POST /api/batch/{id}/reject`:
  1. Remove the staged Drive file (delete → fallback move to `published/`).
  2. `transition(id, "ready", "rejected")`.

## 9. API client + backend router

- New router `backend/routers/batch.py`, prefix `/api/batch`, all endpoints `Depends(get_current_user)` + `Depends(get_db)`, registered in `backend/main.py`.
  - `POST /api/batch` (enqueue)
  - `GET /api/batch/items?status=&offset=&limit=` → `{ total, offset, limit, items }`
  - `GET /api/batch/counts` → per-status badge counts
  - `POST /api/batch/{id}/accept`, `POST /api/batch/{id}/edit`, `POST /api/batch/{id}/reject`
- New Pydantic schemas in `backend/schemas.py` (`BatchEnqueueRequest`, `BatchEnqueueResponse`, `BatchItemOut`, `BatchItems`, action responses).
- New `frontend/src/api.ts` functions mirroring the backfill client fns: `enqueueBatch`, `listBatchItems`, `getBatchCounts`, `acceptBatch`, `editBatch`, `rejectBatch`, plus `BatchItem`/`BatchItems`/`BatchStatus` types.

## 10. UI — `frontend/src/components/BatchTab.tsx`

Clone the `BackfillTab` skeleton (`frontend/src/components/BackfillTab.tsx`).

- **Registration:** add `{ id: "batch", label: "Batch Generate" }` to `TABS` in `App.tsx`, import `BatchTab`, add a branch to the panel ternary.
- **Enqueue bar:** category `<select>` (from cached `getCategories`, with an *All categories* option) + a net-new count `<input type="number" min=1 max=100>` + a **Generate** button. On submit → `enqueueBatch`, then show a toast summarizing `queued` and any `skipped[]` reasons.
- **Status sub-tabs with count badges:** **Ready** (review queue) / **In progress** (`queued`+`generating`) / **Failed** / **History** (`published`+`rejected`). Badges from `getBatchCounts`.
- **Cards:** paginated offset/limit = 20 (Prev/Next + "Page X of Y", the Backfill pagination). Each card shows: product id, color, source images (zoomable via `useImageLightbox.showDrive`), the generated mockup (zoomable), and — on Ready cards — **Accept / Edit / Reject** buttons. In-progress cards show a spinner/state; failed cards show the error + a **Retry** action (transition `failed→queued`).
- **Fetching:** the card list is fetched 20 at a time **only on tab/page change, manual Refresh, or after an action** — no background polling of the list. A **Refresh** button re-fetches the current page + counts.
- **Optimistic mutations:** reuse Backfill's `afterAction` / `run` runners (drop card locally, adjust counts, refetch a page that empties, handle **409** by re-syncing).
- Follow the `ui-ux-pro-max` skill for the new controls (touch targets, focus states, contrast).

## 11. Out of scope

- Parallel generation (worker stays concurrency 1).
- Bulk "accept all" / "reject all" actions.
- A per-batch progress bar beyond status counts.
- Changing model/resolution/aspect of a card after enqueue (fixed at enqueue; Edit only changes prompt/images).
- Video in batch.
- Exposing the `pending` flag (always pending-only).
- A generic fallback prompt for prompt-less categories (those categories are skipped for now).

## 12. Files touched (summary)

**New**
- `docs/migrations/2026-07-14-batch-items.sql`
- `mockup_generator/db/batch_items_repo.py`
- `backend/services/batch_worker.py`
- `backend/routers/batch.py`
- `frontend/src/components/BatchTab.tsx`

**Modified**
- `mockup_generator/integrations/drive_client.py` (`get_or_create_subfolder`, `upload_image`, batch-folder scan exclusion)
- `backend/schemas.py` (batch schemas)
- `backend/main.py` (register router; startup orphan-reset + worker resume)
- `frontend/src/api.ts` (batch client fns + types)
- `frontend/src/App.tsx` (register tab)
