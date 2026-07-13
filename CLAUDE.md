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
# Run the Streamlit web app
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

### Data Flow
1. User uploads images or points to a folder
2. `app.py` selects the appropriate prompt from `prompt.py` based on garment type
3. `create_base.py` loads images, builds Gemini API request with the prompt, retries on failure
4. Output images are saved and displayed in the UI for download

### API Keys
Stored in `.env` (gitignored):
- `OPENAI_API_KEY`
- `GOOGLE_API_KEY`

### Key Constants in `create_base.py`
- Max input images per generation: 2 (loaded from folder sorted alphabetically)
- Max image size: 1024×1024 (resized on load)
- Retry attempts: 8, starting backoff: 8s, max backoff: 60s
- Safety settings: all set to `BLOCK_NONE`

### Garment Types Supported
`SAREE`, `KURTA_PAJAMA`, `KURTI`, `MEN_SHIRT`, `CORD_SET`, `GOWN`, `NEHRU_JACKET`, `WOMEN_TOP`, `LEHENGA`, `SKIRT_CROP_TOP`
