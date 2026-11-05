# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Communication style

Use **caveman mode (full)** for all responses in this repo (`caveman:caveman` skill). Drop articles, filler, pleasantries, and hedging; fragments are fine; prefer short synonyms. Keep code, commit messages, PR bodies, and security/destructive-action warnings in normal prose. Stays active across the whole session unless the user says "stop caveman" / "normal mode". Requires the `caveman` plugin installed (user-level: `/plugin`); this directive scopes it to the project.

## Frontend design

Always use the `ui-ux-pro-max:ui-ux-pro-max` skill for any frontend design work — new UI, component changes, styling, layout, color/typography decisions, interaction patterns, or UI bug fixes. Invoke it before writing or changing frontend code, and apply its accessibility/interaction rules (touch targets, focus states, hover-vs-tap, contrast).

## Project Overview

AI-powered luxury garment mockup generator for Bindal's Creation (Indian ethnic wear brand). Generates photorealistic fashion mockups using Google Gemini and OpenAI APIs, plus short product videos using Google VEO.

## Commands

```bash
# Run the legacy Streamlit web app (streamlit is an optional `legacy` extra,
# excluded from the Docker/prod image — install it first)
poetry install --extras legacy
poetry run streamlit run app.py

# Run a specific module directly
poetry run python -m mockup_generator.create_base

# Install dependencies
poetry install

# Add a dependency
poetry add <package>
```

**Python version**: 3.10 (strict: >=3.10, <3.11)

## Architecture

### Entry Points
- **`app.py`** — Streamlit UI. Three input modes: Upload Files, Use Folder, Folder of Folders (batch). Supports 9 garment types with pre-built prompts. User can edit prompts inline before generating.
- **`mockup_generator/create_base.py`** — Primary generation engine using Google Gemini (`gemini-3-pro-image-preview`). All new garment generation goes through `generate_image_for_product()`. Handles retry logic with exponential backoff for 429/5xx errors.
- **`mockup_generator/create_video.py`** — Video generation using Google VEO (`veo-3.1-generate-preview`), 9:16 portrait, 4 seconds, 720p.
- **`mockup_generator/create_mockup.py`** — Legacy OpenAI path (`gpt-image-1`), outputs 1080x1920 PNG with embedded metadata.

### Prompt System
All prompts live in **`mockup_generator/prompt.py`**. Each garment type has a dedicated prompt constant (e.g., `SAREE_PROMPT`, `KURTI_PROMPT`, `LEHENGA_PROMPT`). Prompts are highly detailed — they specify model pose, framing, fabric replication fidelity, accessories, background, and explicit instructions to prevent hallucinations.

When updating prompts, preserve the structure: garment specs → model requirements → technical quality specs → safety instructions (remove tags, no mannequins, pixel-perfect replication).

### Product Descriptions
Backfill and Batch review show a product's `products.description`, and an AI draft fills in by itself when the card opens (`mockup_generator/describe/draft.py`, model `DESCRIPTION_MODEL`, default `claude-opus-5-5`). Approving the image saves the draft as `description_source='ai_reviewed'`, only if the text and `description_reviewed_at` are unchanged since load. Unedited AI text never replaces a description a person wrote (`CurrentDescription.is_human`); it waits beside it as a choice.

### AI Drafts
Owner decision, 2026-11-03: title detail, description and tags are drafted with no click, from the images the reviewer is looking at, and a person approves all of it. This reverses the October spec's "no draft is ever made without a click", because the owner wants the text built from the final images, and a step that can be skipped lets a product go live with a shared title (Batch's one-click Accept never offered it at all). Nothing reaches `products` before Approve. Drafts wait in `product_copy_drafts` (`docs/migrations/2026-11-03-product-copy-drafts.sql`, which must be applied before deploying); without that table the screens say AI drafts are off and work by hand.

- **Trigger:** opening a review card, or choosing an image on the Products tab or Product Shots. Listing a page never drafts, and there is no bulk run. Batch cards are not drafted when generation finishes.
- **Spend** (`backend/services/copy_drafts.py`, `draft_worker.py`): one current draft per product, enforced by a unique index. Another colour's card reuses it. It is redone by itself only when that same card's images change, otherwise only when a reviewer asks. A failure waits for Retry. A product whose text people already wrote gets no draft. `DRAFT_CONCURRENCY` (2) limits calls at once. `DRAFT_DAILY_USD_CAP` (10) stops new calls once the rolling 24 hours reach it. Measured 2026-11-03: about $0.07 and 9s per draft on `claude-opus-5-5`.
- **One-click Accept on Batch** approves what the card shows (`card_previews`). With no finished draft and no human text it opens Review, and a draft that changed since the card loaded publishes nothing (412).

The prompt, schema, tag vocabulary and approved examples in `mockup_generator/prompts/describe/` are **verbatim copies** of retail-inventory's `scripts/describe/` (see `SOURCE.md`). Never edit them here: change them in retail-inventory, re-copy, and keep `tests/test_describe_prompt.py::test_vendored_matches_retail` green. The prompt's `{TAGS}` block is filled from the product's category family (`mockup_generator/prompts/tag_vocab.py`, a port of retail's `tag_vocab.js`); a category in no family (one added after the vocab) gets every tag group empty. The same draft proposes tags (filtered to the family). The reviewer edits them in the panel, and Approve saves them through retail's `replace_product_tags` RPC (scope `ai`: replaces AI rows, keeps `manual` ones), only after the description saved (`mockup_generator/db/product_tags_repo.py`). Retail-inventory owns descriptions of already-live products; this app writes descriptions only for new products going live through Backfill or Batch.

### Product Title Detail
`products.title_detail` is the short phrase that makes a customer title unique. Retail-inventory's trigger builds `display_title` from it, so this app never writes `display_title` or `name`, and the database decides the preview and duplicate warning (`preview_product_title`, through `mockup_generator/db/product_titles_repo.py`). A published photo puts a product live, so every screen that publishes shows `TitleDetailField`: Backfill and Batch review (inside `DescriptionSection`), and the Products tab and Product Shots (`AutoTitleDetailField`, which draft from Drive originals and save only the title). A save compares against the value the screen loaded, and an AI draft fills only an empty detail: replacing existing text needs the reviewer's edit (`backend/services/title_detail.py`, rule in `mockup_generator/describe/title_detail.py`). A duplicate warns and does not block.

The prompt section and schema fields (`mockup_generator/prompts/title_detail.md`, `_TITLE_FIELDS` in `describe_prompt.py`) are added at load time and are not vendored, so retail-inventory's describe files stay unchanged and `test_vendored_matches_retail` stays green. Change them here.

### Data Flow
1. User uploads images or points to a folder
2. `app.py` selects the appropriate prompt from `prompt.py` based on garment type
3. `create_base.py` loads images, builds Gemini API request with the prompt, retries on failure
4. Output images are saved and displayed in the UI for download

### API Keys
Stored in `.env` (gitignored):
- `OPENAI_API_KEY`
- `GOOGLE_API_KEY`
- `ANTHROPIC_API_KEY` — Claude, for product description drafts (also an HF Space secret)

### Key Constants in `create_base.py`
- Max input images per generation: 2 (loaded from folder sorted alphabetically)
- Max image size: 1024×1024 (resized on load)
- Retry attempts: 8, starting backoff: 8s, max backoff: 60s
- Safety settings: all set to `BLOCK_NONE`

### Garment Types Supported
`SAREE`, `KURTA_PAJAMA`, `KURTI`, `MEN_SHIRT`, `CORD_SET`, `GOWN`, `NEHRU_JACKET`, `WOMEN_TOP`, `LEHENGA`, `SKIRT_CROP_TOP`
