# Phase 5 Design — Category Prompts + Docs

**Date:** 2026-06-23
**Companion to:** `docs/plans/2026-06-21-implementation-plan.md` (Phase 5)
**Status:** Design — approved for planning.

## Scope

Phase 5 covers two independent polish items:

- **Part A — Category prompts:** add Gemini-optimized default prompts for every category with **≥10 products** that currently lacks one (19 category IDs).
- **Part B — Docs:** `README.md`, `.env.example`, and deploy notes.

**Explicitly out of scope** (roadmap shift confirmed with owner 2026-06-23):
- **Phase 6** — on-demand "auto-refine prompt" button: generate a detailed Gemini-optimized prompt from a thin one when the user asks.
- **Phase 7** — backfill `mockups`/variations from the existing generated Drive folder.
- Supabase-branch integration tests — deferred (not selected for this phase).
- Categories with <10 products (long tail: Plazo, Dhoti, fabrics, child Indo-Western, etc.) — YAGNI; revisit if demand appears.

Video generation, originally a Phase 5 item, already shipped (PR #6).

---

## Part A — Category prompts

### Coverage gap (source of truth)

Queried live 2026-06-23. 11 categories already have a `Default` prompt. The 19 uncovered categories with ≥10 products, with their `categories.description` (used as the spec seed):

| categoryid | name | products | description |
|---|---|---|---|
| ST | Suits | 223 | Traditional/formal set: kurta + bottoms |
| RMS | Readymade Suits | 148 | Pre-stitched women's suit: kurta, bottom, dupatta |
| S3P | Suit (3 Pc) | 60 | Three-piece formal suit incl. vest |
| BLZ | Blazer | 58 | Single/double-breasted jacket |
| SW | Sherwani | 36 | Long coat-like traditional menswear |
| DPT | Dupatta | 31 | Long scarf worn with Indian suits |
| JNS | Jeans | 26 | Casual denim pants |
| SHWL | Shawl | 25 | Woolen/silk wrap worn over shoulders |
| S2P | Suit (2 Pc) | 25 | Two-piece formal suit |
| DRS | Dress | 25 | One-piece women's garment, casual→formal |
| C-S5P | 5 Pc Suit (Child) | 23 | Five-piece formal suit for boys |
| TRS | Trousers | 21 | Formal/semi-formal pants |
| SK | Short Kurta | 18 | Short tunic worn over pants/jeans |
| SJ-2P | Suit (Jodhpuri) - 2Pc | 16 | Indian formal suit, often embroidered (2 Pc) |
| FRMP | Formal Pant | 16 | Tailored trousers for formal occasions |
| IW | Indo-Western | 13 | Fusion of Indian + Western styles |
| SJ-3P | Suit (Jodhpuri) - 3Pc | 12 | Indian formal suit, often embroidered (3 Pc) |
| T-SHT | T-Shirt | 10 | Collarless pull-over shirt (crew/V-neck) |
| SHR | Sharara | 10 | Wide-legged pants worn with a kurta |

### Prompt reuse — 15 constants for 19 IDs

Group category IDs whose photoshoot is genuinely the same garment archetype into one shared constant. Shared prompts handle the variant inline (e.g. "include the dupatta if present", "include the waistcoat/vest if present"). This reduces authoring and prevents drift between near-identical garments.

| constant | category IDs | notes |
|---|---|---|
| `WOMENS_SUIT_PROMPT` | ST, RMS | kurta + bottom; dupatta if present |
| `MENS_FORMAL_SUIT_PROMPT` | S2P, S3P | western 2pc/3pc; waistcoat if present |
| `JODHPURI_SUIT_PROMPT` | SJ-2P, SJ-3P | bandhgala/Jodhpuri, embroidery |
| `FORMAL_TROUSER_PROMPT` | TRS, FRMP | tailored formal trouser, bottoms framing |
| `BLAZER_PROMPT` | BLZ | single/double-breasted jacket |
| `SHERWANI_PROMPT` | SW | long traditional coat |
| `DUPATTA_PROMPT` | DPT | draped scarf styling |
| `JEANS_PROMPT` | JNS | casual denim |
| `SHAWL_PROMPT` | SHWL | draped wrap over shoulders |
| `DRESS_PROMPT` | DRS | women's one-piece |
| `CHILD_SUIT_PROMPT` | C-S5P | boys' 5-piece formal |
| `SHORT_KURTA_PROMPT` | SK | short tunic over bottoms |
| `INDO_WESTERN_PROMPT` | IW | Indo-Western fusion |
| `T_SHIRT_PROMPT` | T-SHT | casual crew/V-neck tee |
| `SHARARA_PROMPT` | SHR | kurta + wide-legged sharara set |

### Style contract for each prompt

Every new constant mirrors the structure and idiom of the shipped prompts (`SAREE_PROMPT`, `CORD_SET_PROMPT`, `GOWN_PROMPT`) and is Gemini-optimized for `gemini-3-pro-image-preview`:

1. **Opening directive** — "Generate an ultra-realistic, hyper-detailed, high-end editorial mockup of a `<garment>`, based on the `[UPLOADED <GARMENT> IMAGE HERE]`. The final output MUST be indistinguishable from a professional 4K photograph…". Seed the garment description from the `categories.description` above.
2. **Camera/quality spec** — full-frame DSLR, 85mm f/1.4 (or lens appropriate to framing), suitable for luxury Indian ethnicwear brand magazine + Instagram/WhatsApp.
3. **Garment specs** — what to faithfully reproduce; archetype-specific construction (e.g. waistcoat layering for 3pc, drape for dupatta/shawl, denim wash for jeans).
4. **Model / pose / framing** — model type, pose, full-body vs 3/4, gender appropriate to category (men: SW, S2P/S3P, SJ-*, BLZ, FRMP/TRS, JNS, T-SHT, SK; women: ST/RMS, DRS, DPT, SHWL, SHR; child: C-S5P; IW per garment).
5. **Fabric replication fidelity** — pixel-perfect replication of the uploaded textile, color, embroidery, print; no invented motifs.
6. **Background / accessories** — luxury editorial backdrop, tasteful accessories that don't occlude the garment.
7. **Safety / anti-hallucination** (same as existing prompts) — remove price tags/labels, no mannequins, no extra garments, do not alter the garment design, no text artifacts.

Gemini-optimization means: explicit positive instructions (not negations-only), concrete nouns for camera/lighting/material, and strong reference-fidelity language — the same idiom the shipped prompts already use successfully.

The author (Claude) drafts all 15 full bodies during implementation; owner reviews them in the PR.

### Wiring & seed mechanism

- Add the 15 constants to `mockup_generator/prompts/defaults.py`.
- Extend `CATEGORY_PROMPTS` with 19 new keys mapping to the 15 constants (shared constants appear under multiple keys, e.g. `"ST": WOMENS_SUIT_PROMPT, "RMS": WOMENS_SUIT_PROMPT`).
- Re-run `prompts_repo.seed_defaults(client)`. It is **idempotent**: for each `categoryid` it skips when a `(categoryid, label="Default")` row already exists, so the 11 existing prompts (including any the owner has since edited) are never touched; only the 19 new rows are inserted.
- **No schema change, no migration, no new dependency, no new endpoint.** `prompt.py` re-export shim and `get_category_prompt` unchanged.

---

## Part B — Docs

- **`README.md`** (repo root) — project overview; architecture (FastAPI core + React frontend + Supabase + Drive + Gemini/VEO); local run (`poetry install`, `poetry run uvicorn backend.main:app`, `frontend/ npm run dev`); deploy (backend → HF Space, frontend → Vercel); link to `docs/plans/`.
- **`.env.example`** (repo root, committed; real `.env` stays gitignored) — every key with a placeholder value: `GOOGLE_API_KEY`, `OPENAI_API_KEY`, `SUPABASE_PROJECT_ID`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`, `VEO_MODEL`, `VEO_POLL_TIMEOUT_SEC`, `VEO_POLL_INTERVAL_SEC`, and the service-account JSON path. Derived from the actual `.env` key list and `config.py`.
- **Deploy notes** (in README or `docs/`) — folds in the operational facts: HF Space must be public for anon image URLs, `profiles.is_active` allowlist gating shared with the Inventory app, Gemini routes via Vertex AI billing (not the AI Studio key), the dedicated `mockup-drive-reader` service account reads product folders.

---

## Verification

- `poetry run python -m pytest -q` — full suite green; extend the seed test so it asserts the 19 new category IDs are present after `seed_defaults` and that re-running inserts 0.
- `cd frontend && npm run build` — clean (no frontend code change, but confirm no regression).
- Live smoke: pick a product in each new category (start with ST, RMS) → its `Default` prompt loads in the Prompts tab → generate a preview renders.
- `.env.example` lists every key `config.Settings` reads; README run commands execute as written.

---

## Risks / notes

- **Prompt quality is subjective.** Owner reviews all 15 bodies in the PR; iterate before seeding to production.
- **Shared-constant variants.** A shared prompt must read sensibly for every garment it covers (e.g. `MENS_FORMAL_SUIT_PROMPT` must work for both 2pc and 3pc). The "include X if present" phrasing handles this without per-ID forks.
- **Seed is insert-only.** If a category's default later needs changing, edit via the Prompts tab UI (Phase 2) — re-seeding will not overwrite it. Document this in the README.
