# Implementation Plan — Phased

**Date:** 2026-06-21 (revised: React + FastAPI stack)
**Companion to:** `2026-06-21-design.md`
**Status:** In progress — Phases 0–2 ✅ shipped; Phase 3 🚧 partial (Drive read + image-select UI; generation still stubbed). Last synced to code: 2026-06-23.

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

## Phase 3 — Generation + Drive + variations  ← 🚧 PARTIAL
**Prereqs:** service-account JSON; folders shared with SA; `mockup_variations` approved.
- [~] `integrations/drive_client.py` — **DONE for read:** `extract_folder_id` (3 formats), `list_folder_images` + thumbnail data-URIs, `DriveNotConfigured`. **Variant subfolders:** `list_folder_image_groups` descends one level — returns `{loose, groups[]}` where each immediate subfolder = a named variant group (decision 2026-06-23; depth 1, ≤30 subfolders, empty ones omitted). UI renders loose images + per-variant sections. **Not yet:** download (bytes) + upload.
- [ ] `integrations/storage_client.py` — Supabase Storage upload + public URL. *(not started)*
- [ ] `db/variations_repo.py`; migration for `mockup_variations`. *(not started)*
- [ ] Backend `/generate`: parse producturl → download inputs → generate → save raw to Drive → insert `mockup_variations` (status pending). Skip if `base_mockup` true unless `redo`. **Still a 501 stub** in `backend/routers/generate.py` — real wiring outstanding.
- [~] React: trigger generation; poll/stream status. **DONE:** Products tab has generate buttons + Drive image preview/multi-select (`GET /api/products/{id}/images`, `image_ids` passed to generate). **Not yet:** real result/poll/stream (buttons hit the 501 stub).

**Verify:** ⬜ Not yet — generation not wired. (Drive folder preview + image selection verified live.)

---

## Phase 4 — Review UI (input vs output, feedback / approve)
- [ ] Backend `/review`: submit feedback (saved + optional re-generate with note → new variation); approve (status approved, copy to Supabase Storage, flip `mockups` flag, write `productimages`).
- [ ] React review screen: side-by-side input images vs generated variations; feedback box; approve button; show approved storage URL.

**Verify:** Feedback saved/re-gens new variation; approve publishes to Storage + flips flag.

---

## Phase 5 — Polish (optional)
- [ ] Video generation surfaced.
- [ ] Backfill `mockups`/variations from existing generated Drive folder.
- [ ] More category prompts. README + `.env.example` + deploy notes. Tests against a Supabase branch; mocked Drive/storage.

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
