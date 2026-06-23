# Phase 2 Design — Product Selection, Prompts Management, Generation UI

**Date:** 2026-06-22
**Companion to:** `2026-06-21-design.md`, `2026-06-21-implementation-plan.md` (Phase 2)
**Status:** Approved — ready for implementation plan

## Goal

Ship the working UI and data layer for browsing products, managing per-category
prompt variants, and triggering image/video generation. Generation handlers are
**stubs** in this phase; Phase 3 wires the real Drive + Gemini/VEO engine behind
the same endpoints, so this phase is UI + data only and never blocks on the
Google service-account credential.

## Scope

**In scope:**
- Product selection by category, single product id, and product-id **range**.
- Default list filter = pending (`mockups.base_mockup = false`); toggle to show all.
- Display the product's `producturl` (raw Google Drive link, opened in a new tab).
- Prompts tab: multiple **named prompt variants per category** — view, edit, add, delete, set default.
- Generate Image (uses a chosen/edited category prompt) and Generate Video
  (custom per-request prompt) buttons → backend **stub** handlers.

**Out of scope (later phases):**
- Real Drive download, real Gemini/VEO calls, output upload (Phase 3).
- `mockup_variations` table + review/approve UI (Phase 3/4).
- In-app image thumbnails (needs Drive; Phase 3).
- Video prompt persistence (video prompts are typed per request, never stored).

**Permissions:** any active profile (`Depends(get_current_user)`) can do
everything — browse, manage prompts, and generate. No admin gate this phase.

## Data model — new `prompts` table

Additive migration via Supabase MCP `apply_migration`. Shared project, so
additive only — no changes to existing tables.

```sql
create table public.prompts (
  prompt_id   bigint generated always as identity primary key,
  categoryid  text not null references public.categories(categoryid),
  label       text not null,                       -- e.g. "Studio", "Outdoor"
  body        text not null,                        -- the prompt text
  is_default  boolean not null default false,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  updated_by  uuid references public.profiles(id),
  unique (categoryid, label)
);
-- at most one default per category
create unique index prompts_one_default_per_category
  on public.prompts (categoryid) where is_default;
```

RLS: enable, with policies allowing active profiles (consistent with the rest of
the schema; server writes use the service key per existing `supabase_client`).

**Seeding:** upsert the 11 entries from `mockup_generator/prompts/defaults.py`
`CATEGORY_PROMPTS` (SA, KP, C-KP, GWN, LE, SHT, KUR, NHJ, SKT-TOP, CRD, TOP),
each as `label='Default'`, `is_default=true`. Idempotent seed routine in
`prompts_repo` (on-conflict do nothing) so re-running is safe.

## Product id parsing (range filter)

Ids are `BC<YY><seq>` with **variable-width** sequence:
- length 7: `BC25001` … `BC26227` (3-digit seq)
- length 8: `BC251000` … `BC253367` (4-digit seq)

Lexical string range is **incorrect** across the 3→4 digit boundary
(`'BC251000' < 'BC25999'`). Instead parse to a numeric sort key:

```
key = YY * 1_000_000 + seq      # BC25001 -> 25_000_001, BC251000 -> 25_001_000, BC26227 -> 26_000_227
```

- Single id filter: exact string match on `productid`.
- Range filter: parse both endpoints to keys; filter rows whose parsed key is in
  `[start_key, end_key]`.
- Guard with `productid ~ '^BC\d+$'`; skip/ignore malformed ids (none exist today).
- 3594 rows → plain scan, no index needed.

## Backend API

All routes under `/api`, all require `Depends(get_current_user)`.

| Method | Route | Purpose |
|---|---|---|
| GET | `/categories` | `categoryid` + `name` for the filter dropdown |
| GET | `/products` | filters: `category`, `id` (exact), `id_start`+`id_end` (numeric-key range), `pending` (default true → base_mockup=false), `limit`, `offset`. Returns id, name, categoryid, base_mockup, producturl |
| GET | `/products/{id}` | single product detail (incl. producturl, base_mockup) |
| GET | `/prompts?categoryid=` | list variants for a category |
| POST | `/prompts` | create variant `{categoryid, label, body, is_default}` |
| PATCH | `/prompts/{prompt_id}` | edit `label` / `body` / `is_default` |
| DELETE | `/prompts/{prompt_id}` | delete a variant |
| POST | `/generate/image` | `{productid, prompt}` → **stub** (returns 501 / "wired in Phase 3") |
| POST | `/generate/video` | `{productid, prompt}` → **stub** |

Setting `is_default=true` on one variant clears it on the category's others
(handled in repo within one transaction; the partial unique index enforces it).

### Repos (`mockup_generator/db/`)
- `products_repo.py` — `list_products(filters)`, `get_product(id)`; join
  `categories` (name) + `mockups` (base_mockup). Range parsing lives here.
- `prompts_repo.py` — `list_by_category`, `create`, `update`, `delete`,
  `set_default`, `seed_defaults`.
- `mockups_repo.py` — `get_flags(productid)` (read `base_mockup` etc.) for now;
  write helpers added in Phase 3.

## Frontend — tabbed shell

Replace the placeholder gated shell with a two-tab layout.

**Products tab:**
- Filter bar: category dropdown · product-id input (single) · optional range-end
  input · "pending only" toggle (default on).
- Results table: productid, name, category, status (pending / has base mockup),
  producturl link (new tab).
- Select a row → detail panel:
  - producturl link.
  - Prompt-variant picker (the category's variants); selected variant's body
    shown in an editable textarea (edit-before-send; not persisted unless saved
    via the Prompts tab).
  - **Generate Image** button → `POST /generate/image`.
  - Video block: custom-prompt textarea + **Generate Video** button →
    `POST /generate/video`.

**Prompts tab:**
- Category dropdown → list of variants → edit label/body, set-default toggle,
  **Add new** variant, delete. Persists via the prompts endpoints.

`frontend/src/api.ts` gains typed helpers for each endpoint; generation calls
surface the stub response clearly ("generation enabled in Phase 3").

## Generation seam (Phase 3 readiness)

Stub handlers validate input shape and return a clear not-yet-enabled response.
Phase 3 replaces only the handler body to call `generation.images` /
`generation.video` with Drive-sourced inputs — no route or frontend change.

## Testing

- Repo smoke tests (extend `tests/`): imports resolve; SQL builders produce
  expected filters; `seed_defaults` is idempotent.
- Unit test the product-id key parser: 7- and 8-digit ids, range correctness
  across the 3→4 digit boundary, malformed-id rejection.
- Prompt CRUD round-trip against a throwaway category id (or mocked client).
- Stub endpoints return the agreed shape/status.

## Open items (not blocking Phase 2)
- Google service-account JSON (blocks Phase 3 generation wiring, not this phase).
- `mockup_variations` schema confirmation (Phase 3).
