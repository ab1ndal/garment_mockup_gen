# Backfill Product Descriptions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In the Backfill review panel, show a product's current description, draft a new one with Claude on click, and save the draft as `ai_reviewed` when the image is approved.

**Architecture:** Expand-only migration on shared `products` (retail-inventory repo). Vendored copy of retail's final describe prompt + schema + approved examples, filled by a Python port of `build_prompt.js`. A drafting service calls Claude (Anthropic SDK, structured output) with Drive originals and the card's mockup. Backfill routes: a read of the current description, a draft route, and an extended approve that does a conditional save after publishing.

**Tech Stack:** FastAPI (plain `def` routes), supabase-py, `anthropic` Python SDK, Pillow, React + TypeScript (Vite).

**Spec:** `docs/superpowers/specs/2026-10-24-backfill-product-descriptions-design.md`

## Global Constraints

- Python `>=3.10,<3.11`; new dependency only `anthropic` (check it resolves on py3.10 with `poetry add --dry-run`).
- Model `claude-opus-5-5`, `output_config.effort="medium"`, adaptive thinking (omit `thinking`), configurable via `DESCRIPTION_MODEL`. Key in `ANTHROPIC_API_KEY` (already in `.env` and HF Space secrets).
- Only ever write `description_source='ai_reviewed'`. Never write name, fabric, colour, size, tags.
- Description: plain text, trimmed, ≤ 600 chars hard cap, empty allowed.
- Never overwrite: conditional on both `description` and `description_reviewed_at` matching what the panel loaded.
- Image approval never fails because of the description.
- All new routes plain `def` (lessons 2026-10-21).
- Migration applied to prod **before** any code using the columns is merged (merge = deploy).
- Draft is made only on click; panel open does a DB read only.
- Frontend work: invoke `ui-ux-pro-max:ui-ux-pro-max` first.

## Review Focus

1. Product with no Drive folder / no originals, mockup toggle off → clear 400 "no images to draft from", not a 500. (Task 5 test `test_draft_no_images_400`.)
2. Reviewer clears the draft textarea then approves → saves empty string (spec allows empty), not "no draft". Draft state must distinguish "no draft" (null) from "" . (Task 5 test `test_approve_saves_empty_draft`; Task 6 state type `string | null`.)
3. Another writer changed the text between open and approve → image approved, `description_warning` set, DB text untouched. (Task 5 `test_approve_description_conflict_warns`.)
4. Huge Drive original (e.g. 6000×4000 JPEG) → resized to ≤1568px before sending; memory stays bounded (one image decoded at a time). (Task 3 `test_prepare_image_resizes_large`.)
5. Model refusal / `max_tokens` / schema-invalid output → 502 "Could not draft a description", never a half-parsed draft. (Task 3 `test_draft_refusal_raises`, `test_draft_max_tokens_raises`.)

---

### Task 0: Migration in retail-inventory (prod)

**Files:**
- Create: `../retail-inventory/schema/migration_2026_10_24_description_source.sql` (match the naming of existing `schema/migration_*.sql` files)

- [ ] **Step 1:** In `../retail-inventory`, `git checkout -b feat/description-source`. Read its `CLAUDE.md`; fetch live schema with the `get_schema_info` RPC (Supabase MCP; re-auth OAuth first per memory) and confirm `products` has no `description_source` / `description_reviewed_at` yet.
- [ ] **Step 2:** Write the migration:

```sql
alter table public.products
  add column if not exists description_source text
    check (description_source in ('manual', 'ai', 'ai_reviewed')),
  add column if not exists description_reviewed_at timestamptz;
comment on column public.products.description_source is
  'Who wrote products.description: manual (admin edit), ai (unreviewed draft), ai_reviewed (owner-approved AI draft). Null = legacy/unknown.';
comment on column public.products.description_reviewed_at is
  'When an ai_reviewed description was approved. Null = never reviewed.';
```

No grant changes (storefront does not read these).
- [ ] **Step 3:** Apply to prod (Supabase MCP `apply_migration`). Verify: `select column_name, data_type from information_schema.columns where table_name='products' and column_name like 'description_%'` returns 3 rows; storefront anon read of products still works (`select productid, description from products limit 1` as anon). Check column privileges: `select grantee, column_name, privilege_type from information_schema.column_privileges where table_name='products' and column_name like 'description_%'` plus table-level grants for `service_role` (this backend uses the service key) and `authenticated` (retail admin PR #63 writes the column as a signed-in user). If `authenticated` only has column-level grants on `products`, add `grant select (description_source, description_reviewed_at), update (description_source, description_reviewed_at) on public.products to authenticated` in the same migration.
- [ ] **Step 4:** Commit in retail-inventory (`feat(schema): add description_source and description_reviewed_at`), message retail-inventory-d6 that it's applied. Do not push without asking.

### Task 1: Vendored prompt + prompt builder

**Files:**
- Create: `mockup_generator/prompts/describe/prompt.md`, `schema.json`, `approved.json`, `SOURCE.md` (copied from `../retail-inventory/scripts/describe/` and `tasks/description-pilot/approved.json`; `SOURCE.md` records source paths + retail commit sha)
- Create: `mockup_generator/prompts/describe_prompt.py`
- Test: `tests/test_describe_prompt.py`
- Modify: `pyproject.toml` (package-include the `.md`/`.json` files if poetry would drop them — verify with `poetry build` + `unzip -l`)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class ProductFacts: productid: str; name: str; category_name: str | None; fabric: str | None; colours: list[str]; sizes: list[str]; description: str | None`
  - `build_prompt(facts: ProductFacts, owner_note: str | None = None) -> str`
  - `load_schema() -> dict`, `load_examples() -> dict[str, str]`

- [ ] **Step 1: Failing tests**
  - `test_data_block`: facts(name="Georgette Saree", category "Saree", fabric "Georgette", colours ["Red","Blue"], sizes ["Free Size"]) → prompt contains `"name: Georgette Saree\ncategory: Saree\nfabric: Georgette\ncolours: Red, Blue\nsizes: Free Size"`.
  - `test_text_source_shop` (description "Mirror work on yoke") → contains "written by the shop; facts here are true unless a photo clearly contradicts them" and the text.
  - `test_text_source_ai_markdown` (`"**Bold** caption"`) and `test_text_source_ai_long` (201 chars) → contain "written earlier by an AI from photos; it may be wrong, so trust the photos over it".
  - `test_empty_description` → contains "(none)".
  - `test_owner_note` → contains "OWNER REVIEW NOTE on the previous draft (follow it):\nhas mirror work on sleeves"; with `None` the `{OWNER_NOTE}` placeholder becomes "" and no `{` placeholder remains.
  - `test_vendored_matches_retail`: if `../retail-inventory/scripts/describe/prompt.md` exists, vendored `prompt.md` and `schema.json` are byte-identical; else `pytest.skip`.
- [ ] **Step 2:** Run `poetry run pytest tests/test_describe_prompt.py -v` → FAIL (module missing).
- [ ] **Step 3:** Implement as a direct port of `build_prompt.js` (same strings, same `**`/>200 rule, `colours`/`sizes` already deduped by caller). Files read via `importlib.resources`.
- [ ] **Step 4:** Tests PASS.
- [ ] **Step 5:** Commit `feat(describe): vendor retail describe prompt and port prompt builder`.

### Task 2: Description repo

**Files:**
- Create: `mockup_generator/db/product_descriptions_repo.py`
- Test: `tests/test_product_descriptions_repo.py` (fake client recording the PostgREST chain, same style as `tests/test_products_repo.py`)

**Interfaces:**
- Consumes: `ProductFacts` (Task 1)
- Produces:
  - `@dataclass class CurrentDescription: description: str | None; source: str | None; reviewed_at: str | None`
  - `get_current(client, productid) -> CurrentDescription | None` — one `products` select of `description, description_source, description_reviewed_at`.
  - `get_facts(client, productid) -> ProductFacts | None` — `products` (name, fabric, description) + category name (via `product_browse.category_name`) + `productsizecolors` distinct `color`, `size` (trimmed, case-insensitive dedupe like `variants_repo.list_colors`).
  - `save_reviewed(client, productid, text, *, expected_description: str | None, expected_reviewed_at: str | None) -> bool` — `update({"description": text, "description_source": "ai_reviewed", "description_reviewed_at": <ISO datetime.now(timezone.utc)>})` filtered `.eq("productid", ...)`, then `.eq("description", v)` or `.is_("description", "null")`, same for `description_reviewed_at`; returns `bool(resp.data)`.

- [ ] **Step 1: Failing tests:** `test_save_reviewed_filters_on_expected` (asserts the eq/is_ filters and payload `description_source == "ai_reviewed"`), `test_save_reviewed_null_expected_uses_is_null`, `test_save_reviewed_returns_false_when_no_row`, `test_get_facts_dedupes_colours_and_sizes`, `test_get_current_missing_returns_none`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run → PASS. Then one real read against prod: `poetry run python -c "from backend.deps import ...; print(get_facts(db,'BC25008'))"` (read-only) to confirm column names and the `now` timestamp format round-trips through `.eq` on `description_reviewed_at` (lessons 2026-07-15: hit the real service once).
- [ ] **Step 5:** Commit `feat(describe): products description read and conditional save`.

### Task 3: Drafting service (Claude)

**Files:**
- Modify: `pyproject.toml`, `poetry.lock` (`poetry add anthropic`)
- Modify: `mockup_generator/config.py` (add `anthropic_api_key` required, `description_model` default `"claude-opus-5-5"`)
- Create: `mockup_generator/describe/__init__.py`, `mockup_generator/describe/draft.py`
- Test: `tests/test_describe_draft.py`

**Interfaces:**
- Consumes: `ProductFacts`, `build_prompt`, `load_schema`, `load_examples` (Task 1)
- Produces:
  - `@dataclass class Draft: description: str; includes: str; uncertain: list[str]; conflicts: list[str]`
  - `class DraftFailed(RuntimeError)`
  - `prepare_image(raw: bytes) -> str` — base64 JPEG, long edge ≤ 1568, RGB, quality 85.
  - `draft_description(facts: ProductFacts, originals: list[bytes], mockup: bytes | None, owner_note: str | None = None, *, client: anthropic.Anthropic | None = None) -> Draft`

- [ ] **Step 1:** Read the claude-api skill's `python/claude-api/README.md` structured-outputs section (`shared/tool-use-concepts.md`) for the exact `output_config.format` JSON-schema shape. Do not guess it.
- [ ] **Step 2: Failing tests** (inject a fake client whose `messages.create` records kwargs and returns an `anthropic.types.Message` built with `Message.model_construct(...)`):
  - `test_request_shape`: `model == settings.description_model`, `output_config["effort"] == "medium"`, schema passed, images labelled in order — a text block `"orig_1"` before each original image, `"mockup_1"` before the mockup — then the prompt text; approved examples included in `system` as style references with the line "Match the shape and level of detail of these approved descriptions; never copy their facts."
  - `test_parses_draft`: JSON text `{"description": "...", "includes": "saree", "uncertain": ["length"], "conflicts": [], ...}` → `Draft`.
  - `test_draft_refusal_raises` (`stop_reason="refusal"`), `test_draft_max_tokens_raises`, `test_draft_invalid_json_raises`, `test_draft_over_cap_raises` (description > 600 chars) → `DraftFailed`.
  - `test_prepare_image_resizes_large`: 6000×4000 PNG → decoded result long edge 1568, format JPEG.
  - `test_no_images_raises`: `originals=[]`, `mockup=None` → `DraftFailed("no images")`.
- [ ] **Step 3:** Run → FAIL.
- [ ] **Step 4:** Implement. Client created lazily as `anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=4, timeout=90.0)` (SDK retries 429/5xx; no hand-rolled retry). Max 3 originals (caller already trims; assert here too). `max_tokens=16000`.
- [ ] **Step 5:** Run → PASS. `python -c "import mockup_generator.describe.draft"` resolves.
- [ ] **Step 6:** Commit `feat(describe): Claude drafting service`.

### Task 4: Quality check against the approved pilot (gate)

**Files:**
- Create: `scripts/describe_check.py` (not shipped in the app)

- [ ] **Step 1:** Script takes product ids (default: 5 ids from `approved.json`, mixing saree, set, ready-made), loads facts (Task 2), downloads up to 3 originals via `drive_client.list_folder_image_groups` + `download_file` and the product's first mockup from `productimages` storage, calls `draft_description`, writes an HTML side-by-side (approved vs new draft, uncertain, conflicts) to the scratchpad.
- [ ] **Step 2:** Run once with the real API. Record cost from `usage`.
- [ ] **Step 3:** Show the owner the page (publish as a private artifact). **Stop here until the owner says the quality is OK.** If not OK: try `effort="high"`, then OpenAI as the spec's fallback — re-plan before continuing.
- [ ] **Step 4:** Commit the script `chore(describe): pilot comparison script`.

### Task 5: Backend routes

**Files:**
- Modify: `backend/schemas.py` (new models; extend `BackfillApproveRequest`)
- Modify: `backend/routers/backfill.py`
- Test: `tests/test_backfill_description_api.py`

**Interfaces:**
- Consumes: Tasks 2 and 3.
- Produces (HTTP, used by Task 6):
  - `GET /api/backfill/description/{productid}` → `{description: str|null, description_source: str|null, description_reviewed_at: str|null}`; 404 if no product.
  - `POST /api/backfill/{file_id}/description/draft` body `{productid: str, use_originals: bool = true, use_mockup: bool = true, note: str|null}` → `{description, includes, uncertain: [str], conflicts: [str]}`. 400 if both toggles off or no images found; 404 unknown product; 502 on `DraftFailed`; 503 Drive not configured.
  - `BackfillApproveRequest` gains `description: str | None = None`, `expected_description: str | None = None`, `expected_reviewed_at: str | None = None`. Approve response gains `description_warning: str | None`.

- [ ] **Step 1: Failing tests** (monkeypatch repo/service/drive like `tests/test_backfill_api.py`):
  - `test_get_description`, `test_get_description_404`.
  - `test_draft_uses_first_three_originals_and_mockup` (loose images first, then groups; mockup = `download_file(file_id)`), `test_draft_mockup_only`, `test_draft_no_images_400`, `test_draft_failed_502`, `test_draft_note_passed_through`, `test_draft_note_over_500_chars_422`.
  - `test_approve_without_description_does_not_touch_it` (`save_reviewed` not called).
  - `test_approve_saves_description` (called with trimmed text + expected values; `description_warning is None`).
  - `test_approve_saves_empty_draft` (`description=""` → `save_reviewed(..., "")`).
  - `test_approve_description_conflict_warns` (`save_reviewed` → False: 200, image published, warning "Image approved, but the description changed since you opened it. Not saved.").
  - `test_approve_description_error_warns` (`save_reviewed` raises → 200 + warning, logged).
  - `test_approve_description_over_cap_422` (601 chars, validated before claiming the row).
  - `test_description_routes_are_sync` (`inspect.iscoroutinefunction` false).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement. Draft route downloads originals one at a time and passes bytes on; the save runs after `publish_image` succeeds and before the background archive is queued.
- [ ] **Step 4:** `poetry run pytest -q` — full suite green.
- [ ] **Step 5:** Commit `feat(backfill): description read, draft and save-on-approve`.

### Task 6: Frontend description section

**Files:**
- Modify: `frontend/src/api.ts` (`getBackfillDescription`, `draftBackfillDescription`, extend `approveBackfill` body/response types)
- Create: `frontend/src/components/DescriptionSection.tsx`
- Modify: `frontend/src/components/BackfillTab.tsx` (`ReviewPanel` renders the section; `doApprove` sends `description`/`expected_*` when a draft exists and shows `description_warning`)

**Interfaces:**
- `DescriptionSection` props: `{ productid: string; fileId: string; draft: string | null; onDraftChange: (d: string | null) => void; onLoaded: (cur: { description: string | null; reviewed_at: string | null }) => void }`. `ReviewPanel` owns `draft` and the loaded `expected_*` values.

- [ ] **Step 1:** Invoke `ui-ux-pro-max:ui-ux-pro-max`; follow the existing panel styles (`modal-*`, `pill-*` classes).
- [ ] **Step 2:** Build: current text read-only + source label (Manual / AI-reviewed {date} / AI / No description); "Draft description" button with toggles (Drive originals, Mockup; button disabled if both off) and note field (max 500); loading state; error with Retry; draft textarea below current text; warnings list for conflicts and uncertain; word count "N words (target 15–60)"; Discard draft (sets `null`). Hidden when `item.unknown_product`.
- [ ] **Step 3:** In `doApprove`, if `draft !== null` send `description: draft.trim()` + expected values. If the response has `description_warning`, show it via the tab-level `notice` with the draft text included so it isn't lost (panel closes on approve).
- [ ] **Step 4:** `cd frontend && npm run build` → no TypeScript errors.
- [ ] **Step 5:** Run the app locally (`run` skill: backend `uvicorn` + `npm run dev`), open a pending Backfill card for a real test product, check: current text shows without an AI call (network tab), Draft works, approve saves (`select description, description_source, description_reviewed_at from products where productid=...`). Use a product the owner names for this check, since approve publishes a real image.
- [ ] **Step 6:** Commit `feat(backfill): description section in review panel`.

### Task 7: Docs, memory, ship

- [ ] **Step 1:** Update `CLAUDE.md` "API Keys" (`ANTHROPIC_API_KEY`) and "Architecture" (describe service, vendored prompt and the re-copy rule).
- [ ] **Step 2:** Memory: note the vendored-prompt sync rule and HF secret added 2026-10-24.
- [ ] **Step 3:** Confirm Task 0 migration live, then ask the owner before merging to `main` (merge = deploy). After deploy: `curl https://abindal95-mockupgen.hf.space/openapi.json | grep description/draft` to confirm the new routes are live before testing the Vercel UI.
