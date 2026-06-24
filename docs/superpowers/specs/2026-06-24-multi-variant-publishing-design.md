# Multi-Variant Mockup Publishing — Design

**Date:** 2026-06-24
**Status:** Approved (pending implementation plan)

## Problem

Today a published mockup is keyed by `(productid, color)` in the shared
`productimages` table. Re-publishing the same color **overwrites** the prior
image. This blocks two needs:

1. Keeping **non-default** variants of a product (generated from a different
   prompt) alongside the default one.
2. Keeping the same product+color in **different aspect ratios** (e.g. `1:1`
   for storefront, `9:16` for reels).

## Goal

Let multiple mockups of the same product+color coexist, distinguished by the
prompt used and the aspect ratio, without breaking the Inventory-Management
storefront that also reads `productimages`.

## Variant Identity

A published mockup is uniquely identified by:

```
(productid, color, variant_name, aspect_ratio)
```

- **variant_name** is the **label of the prompt** used to generate the image.
  The default prompt yields the literal string `"Default"`.
- **aspect_ratio** is the generation aspect (`1:1`, `9:16`, …).

Re-publishing with the same four values **overwrites** that row (idempotent
re-publish). Any difference in `variant_name` or `aspect_ratio` produces a
**new coexisting row**.

Existing rows backfill to `variant_name = "Default"`, `aspect_ratio = 1:1`,
which matches what the default-prompt 1:1 publish path will produce going
forward.

## Section 1 — Database / shared table

`productimages` is shared with the existing Inventory-Management system, so the
change must be additive and ignorable by that system.

- Add **one column**: `variant text not null default 'Default'`.
  - Existing reads are unaffected (storefront ignores unknown columns; new
    column has a safe default).
  - `caption` continues to hold the **color** (unchanged contract).
- Aspect ratio: store in `variant` as a suffix when not `1:1` (e.g.
  `Studio·9:16`), OR add a second nullable column `aspect text`. **Decision:**
  fold aspect into the `variant` string to keep the shared table to a single
  added column. Format:
  - `1:1` → `variant = "<prompt-label>"` (e.g. `Default`, `Studio`).
  - other → `variant = "<prompt-label>·<aspect>"` (e.g. `Studio·9:16`).
- Dedup key in `productimages_repo` changes from `(productid, caption)` to
  `(productid, caption, variant)`:
  - `list_for(productid, color, variant)` — find prior row for cleanup.
  - `delete_for(productid, color, variant)` — replace just that variant.
  - `insert(...)` — carries `variant`.
  - No NULL-aware filtering needed (`variant` is always concrete).

### Backfill

```sql
alter table public.productimages
  add column if not exists variant text not null default 'Default';
-- existing rows already get 'Default' via the column default; explicit no-op:
-- update public.productimages set variant = 'Default' where variant is null;
```

## Section 2 — API (`/approve`)

Current form fields: `productid, color, prompt_text, source, image`.

Add:

- `variant_name: str | None` — the selected prompt's label (frontend sends it).
- `aspect_ratio: str | None` — the generation aspect (frontend sends it).

Server computes the stored `variant` string:

```
label  = variant_name or "Default"
variant = label if aspect_ratio in (None, "1:1") else f"{label}·{aspect_ratio}"
```

Then dedup against `(productid, color, variant)` — replace prior row +
best-effort delete its orphaned Storage object (same pattern as today), insert
the new `productimages` row, and append a `mockup_variations` audit row
(carrying `variant`/aspect for the gallery).

The Storage object key (`slug(color)_short_hex`) is already unique per upload,
so no overwrite risk in the bucket.

## Section 3 — Frontend (`ProductsTab`)

- On publish, append `variant_name` (selected prompt label) and `aspect`
  (already in component state) to the `/approve` form data.
- Download filename gains variant + aspect:
  `productid_color_promptlabel_9x16.png` (sanitize separators).
- Published thumbnails labelled `color · prompt-label · aspect` instead of just
  color, so coexisting variants are visually distinct.

## Section 4 — Testing

- `tests/test_productimages_repo.py` — composite dedup: same
  `(productid, color, variant)` overwrites; differing `variant` coexists;
  `list_for`/`delete_for` filter on the triple.
- `tests/test_approve_publish.py` — new form fields parsed; `variant` string
  computation (default vs labelled, 1:1 vs other aspect); audit row carries
  variant.

## Out of Scope

- Storefront-side selection of which variant is "primary" (all variants are
  written; the storefront keeps showing whatever it showed before via the
  `Default` row).
- Per-variant `is_primary`/`displayorder` UI controls.
- Video naming changes beyond what already exists.
