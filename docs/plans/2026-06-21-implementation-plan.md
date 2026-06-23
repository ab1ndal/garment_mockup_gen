# Implementation Plan — Phased

**Date:** 2026-06-21 (revised: React + FastAPI stack)
**Companion to:** `2026-06-21-design.md`
**Status:** Draft for review — **building piece by piece, refactor first, then auth**

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

## Phase 2 — Product data + prompts + status API
- [ ] `db/products_repo.py` (join categories + mockups), `db/prompts_repo.py` (by categoryid), `db/mockups_repo.py`.
- [ ] Seed prompt table from `prompts/defaults.py`; migration via MCP `apply_migration` (additive only).
- [ ] Backend routers: list/filter products (default pending = `base_mockup=false`), get prompt by category, edit prompt (admin).
- [ ] React: product list + filters; prompt view/edit.

**Verify:** Product list shows pending; category prompt loads; admin edit persists.

---

## Phase 3 — Generation + Drive + variations
**Prereqs:** service-account JSON; folders shared with SA; `mockup_variations` approved.
- [ ] `integrations/drive_client.py` — `parse_folder_id` (3 formats), list/download/upload.
- [ ] `integrations/storage_client.py` — Supabase Storage upload + public URL.
- [ ] `db/variations_repo.py`; migration for `mockup_variations`.
- [ ] Backend `/generate`: parse producturl → download inputs → generate → save raw to Drive → insert `mockup_variations` (status pending). Skip if `base_mockup` true unless `redo`.
- [ ] React: trigger generation; poll/stream status.

**Verify:** Generate a pending product → inputs from Drive → variation row created → raw in Drive.

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
