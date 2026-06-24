# Implementation Plan — Phased

**Date:** 2026-06-21 (revised: React + FastAPI stack)
**Companion to:** `2026-06-21-design.md`
**Status:** In progress — Phases 0–4 ✅ shipped (Phase 4 = in-session feedback→regenerate loop). Video generation (a Phase 5 item) also shipped. Remaining Phase 5: backfill, more prompts, docs (README/.env.example/deploy), Supabase-branch tests. Last synced to code: 2026-06-23.

Each phase ships independently and leaves the repo working.

---

## Phase 0 — Refactor core to a clean, framework-agnostic package  ← ✅ DONE
**Goal:** Carve `mockup_generator` into a pure-Python core (no Streamlit) that a FastAPI backend can import. Fix breakage. `app.py` (legacy Streamlit) keeps working on top of the new core.

- [x] `config.py` — `Settings` loads `.env` (and guarded `st.secrets` fallback only if Streamlit present). Single source for `GOOGLE_API_KEY`, `OPENAI_API_KEY`, `SUPABASE_PROJECT_ID`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`.
- [x] `prompts/defaults.py` — move all prompt constants from `prompt.py`; add `CATEGORY_PROMPTS` map keyed by `categoryid`. Keep `prompt.py` as a re-export shim.
- [x] `generation/common.py` — dedup `part_from_pil`, `load_images_from_folder`, `save_first_image_part`, `generate_with_retries`; add lazy `get_genai_client()` (from config, **no module-level client, no `import streamlit`**).
- [x] `generation/images.py` — from `create_base.py` (`generate_image_for_product`, `refine_only_folder`, `output_exists`), using `common`. Leave `create_base.py` as a re-export shim.
- [x] `generation/video.py` — from `create_video.py`; **fix `from prompt import` → package-relative**.
- [x] `generation/legacy_openai.py` — from `create_mockup.py`; **fix `from prompt_config import`**.
- [x] Update `app.py` imports to new paths; verify all 3 modes still work.
- [x] Delete throwaway `trial.py`.
- [x] Smoke test `tests/test_imports.py` — imports resolve, key funcs exist, `CATEGORY_PROMPTS` populated, **no `streamlit` import in core modules**.

**Verify:** `poetry run python -m pytest -q` (smoke) + `poetry run streamlit run app.py` renders and generates one mockup identically.

---

## Phase 1 — Auth (Supabase Google login + profiles gate)  ← ✅ DONE (deployed + verified 2026-06-23)
**Goal:** Backend verifies Supabase JWT and enforces `profiles.is_active`; minimal React login shell.
**Prereqs:** Supabase secret key; Supabase Google provider enabled.

- [x] `poetry add supabase fastapi uvicorn pyjwt[crypto]`.
- [x] `integrations/supabase_client.py` — anon + service clients from config.
- [x] `db/profiles_repo.py` — lookup by email/uid; return role + is_active.
- [x] `backend/main.py` — FastAPI app; auth dependency verifies token via Supabase `auth.get_user` → reject if no active profile; `/api/me` endpoint.
- [x] Scaffold `frontend/` (Vite + TS + `supabase-js`): Google login button, store session, call `/me`, gated shell.

**Verify:** Unauthed → login. Inactive/unknown → 403. `bindal.abhinav@gmail.com` → `/me` returns superadmin. ✅ Deployed: backend on HF Spaces, frontend on Vercel; `profiles.is_active` default flipped to `false` (allowlist gating shared with Inventory app).

---

## Phase 2 — Product data + prompts + status API  ← ✅ DONE (merged to main 2026-06-23, 23 tests green)
**Detailed plan:** `2026-06-22-phase2-implementation-plan.md` (13 TDD tasks).
- [x] `db/products_repo.py` (over `product_browse` view: products + category + mockup flag), `db/prompts_repo.py` (CRUD by categoryid + idempotent seed), `db/mockups_repo.py`, plus `db/product_ids.py` (numeric `BC<YY><seq>` key for non-lexical range filtering).
- [x] Seed `prompts` table from `prompts/defaults.py` (11 defaults); additive migration via MCP `apply_migration` — new `prompts` table + read-only `product_browse` view (no existing table altered).
- [x] Backend routers: list/filter products (default pending = `base_mockup=false`, by category / single id / numeric range), prompts CRUD by category. **No admin gate this phase — any active profile may edit** (deviation from "admin" in original line; intentional per Phase 2 plan).
- [x] React: tabbed shell (Products | Prompts); product list + filters + select; prompt view/edit/add/delete.
- [x] Generation endpoints (`/api/generate/image|video`) scaffolded as 501 stubs — Phase 3 seam.

**Verify:** ✅ Product list shows pending; category prompt loads; prompt edit persists. 23 tests pass, frontend build clean. (Perf follow-up 2026-06-23: 30s per-token auth cache in `backend/auth.py` cuts repeat-request latency — was 3 serial Supabase round-trips/request.)

---

## Phase 3 — Generation + Drive + variations  ← ✅ DONE (generate-preview → approve/publish + variant color)
**Prereqs:** service-account JSON; folders shared with SA; `mockup_variations` approved.
**Companion plan:** `docs/superpowers/plans/2026-06-23-variant-aware-generate-approve.md` (variant-aware generate → approve/publish; the flow below supersedes the original "save raw to Drive" sketch).
- [x] `integrations/drive_client.py` — read (`extract_folder_id`, `list_folder_image_groups` → `{loose, groups[]}`) + `download_file` (bytes).
- [x] `integrations/storage_client.py` — public-URL `upload_mockup` + `slugify`/`short_hex`/`delete_object`/`path_from_public_url` (Task 7).
- [x] `db/` repos — `mockup_variations_repo.insert(..., color)`, `mockups_repo.set_base_mockup`, `productimages_repo` (insert/list_for/delete_for, one row per product+color), `variants_repo.list_colors`. Migration: `mockup_variations.color` + public `mockups` bucket (`docs/migrations/2026-06-23-color-and-public-bucket.md`).
- [x] Backend `/generate/image` is **preview-only** (returns base64, writes nothing, requires ≥1 source image); `/generate/approve` is the sole writer (upload to public bucket → audit row → flip `base_mockup` → replace product+color `productimages` row + orphan-cleanup the prior Storage object). `GET /products/{id}/colors` surfaces variant colors.
- [x] React: color selector, required source selection, preview → Approve / Disapprove / Download / Upload-corrected.

**Verify:** ✅ Backend suite 73 green; frontend build clean. Manual smoke (preview writes nothing → approve publishes public URL renders anon → corrected upload → download) — to confirm live.

---

## Phase 4 — Review UI (input vs output, feedback / approve)  ← ✅ DONE (merged 2026-06-23; 90 tests green, frontend build clean)
**Companion plan:** `docs/superpowers/plans/2026-06-23-phase4-feedback-regenerate.md`. Original `/review` endpoint sketch was **superseded** by an in-session feedback→regenerate loop (no new endpoint, nothing extra persisted) — the Phase 3 `/approve` flow already covers publish-to-Storage + flip flag + write `productimages`.
- [x] Backend: optional `refine_image_b64` on `POST /api/generate/image` — decoded image appended as an extra Gemini reference (14-ref cap, sources keep priority, refine-only valid). Feedback-agnostic: frontend folds the note into `prompt`.
- [x] React review screen: side-by-side picked sources vs active variation; in-session variation history filmstrip; feedback box; Refine-this / Try-again regenerate; Approve & publish / Download / Upload-corrected on the active variation.

**Verify:** ✅ Feedback regenerates new variation in-session; approve publishes to Storage + flips flag (Phase 3 path, unchanged).

---

## Phase 5 — Category prompts + docs  ← ✅ DONE (merged 2026-06-23; 97 tests green, frontend build clean)
**Design:** `docs/superpowers/specs/2026-06-23-phase5-prompts-and-docs-design.md`. **Plan:** `docs/superpowers/plans/2026-06-23-phase5-prompts-and-docs.md`. Scope narrowed from the original grab-bag (video already shipped; backfill → Phase 7; Supabase-branch tests deferred).
- [x] Video generation surfaced. ✅ DONE (PR #6, merged 2026-06-23): async VEO job model on `/api/generate/video` (enqueue → poll `/video/{job_id}` → download mp4; bounded by `VEO_POLL_TIMEOUT_SEC`/`VEO_POLL_INTERVAL_SEC`), `video_service.generate_video_bytes`, frontend video controls.
- [x] Category prompts for the 19 uncovered categories with ≥10 products: 15 shared Gemini-optimized constants (based on existing prompt style + `categories.description`), wired into `CATEGORY_PROMPTS`, seeded idempotently. No schema change. (commits `c420560`, `eaf0cf9`; `tests/test_category_prompts.py`, `tests/test_prompts_repo.py`.)
- [x] Docs: `README.md` + `.env.example` + deploy notes. (commit `84af1e7`.)

## Phase 6 — Auto-refine prompt button  ← ✅ DONE
**Design:** `docs/superpowers/specs/2026-06-23-phase6-auto-refine-prompt-design.md`. **Plan:** `docs/superpowers/plans/2026-06-23-phase6-auto-refine-prompt.md`.
- [x] On-demand button that turns a freeform instruction into a full Gemini-optimized image or video prompt (only when the user asks). Stateless `POST /api/prompts/refine` + shared `RefineButton`; advanced `GEMINI_TEXT_MODEL`; fill-only, no auto-save.

## Phase 7 — Backfill
- [x] Backfill `mockups`/variations from the existing generated Drive folder. Shipped as an interactive **Backfill review tab**: backend scans the generated folder-of-folders into a cached in-memory index, serves paginated review cards; a reviewer assigns color/theme/aspect, then Approve publishes via the shared `publish_image` path (Supabase + archive the Drive original to `published/`) or Flag sets `base_mockup=false` and moves the original to `rejected/`. The service account is an Editor (not owner) of the generated folder, so it moves rather than deletes; both `published/` and `rejected/` are excluded from the scan, so handled images never re-surface. **Plan:** `docs/superpowers/plans/2026-06-24-phase7-backfill-review.md`.
- [ ] (Deferred) Integration tests against a Supabase branch; mocked Drive/storage.

---

## Risk notes
- 2 accounts / folder sharing: unshared folders error per-product — surface, don't crash batch.
- `producturl` format variety: parser handles 3 known + logs/skips unknowns.
- Secret key & service-account JSON: host secrets only, never commit.
- React+FastAPI is more infra than Streamlit (CORS, two deploys) — accepted for the custom review UX.

---

## Open items pending your input
1. Confirm **`mockup_variations`** schema (design §4.8).
2. Where do the **already-generated** images live (Drive folder id)?
3. OK to **create the Supabase Storage bucket** (`mockups`) via MCP when we reach Phase 4?
