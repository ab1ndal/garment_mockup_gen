# Phase 5 — Category Prompts + Docs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 15 shared, Gemini-optimized default prompts covering the 19 product categories (≥10 products) that currently lack one, and ship `README.md` + `.env.example` + deploy notes.

**Architecture:** Pure data + docs. New prompt constants land in `mockup_generator/prompts/defaults.py`; `CATEGORY_PROMPTS` gains 19 keys mapping to the 15 constants (shared archetypes appear under multiple keys). The existing idempotent `prompts_repo.seed_defaults` picks the new entries up with no code change. No schema, migration, dependency, or endpoint change.

**Tech Stack:** Python 3.10, Poetry, pytest. Prompts target Google Gemini `gemini-3-pro-image-preview`.

**Design:** `docs/superpowers/specs/2026-06-23-phase5-prompts-and-docs-design.md`

## Global Constraints

- Python 3.10 (`>=3.10,<3.11`). Run backend tests with `poetry run pytest`.
- No DB schema change, no migration, no new dependency, no new endpoint.
- New prompts mirror the structure and idiom of the shipped prompts (`SAREE_PROMPT`, `CORD_SET_PROMPT`, `GOWN_PROMPT`) and are Gemini-optimized: explicit positive directives, concrete camera/lighting/material nouns, strong reference-fidelity language, anti-hallucination/cleanup tail.
- `seed_defaults` is insert-only and idempotent — it must never overwrite an existing `(categoryid, label="Default")` row.
- The 11 existing category prompts must stay byte-for-byte unchanged.
- Caveman mode is for chat only; code, comments, commit messages, and docs are normal prose.

---

### Task 1: 15 Gemini-optimized prompt constants + `CATEGORY_PROMPTS` wiring

**Files:**
- Modify: `mockup_generator/prompts/defaults.py` (add 15 constants before the `CATEGORY_PROMPTS` dict at ~line 291; add 19 keys inside the dict)
- Test: `tests/test_category_prompts.py` (new)

**Interfaces:**
- Consumes: `mockup_generator.prompts.defaults.CATEGORY_PROMPTS` (dict), `prompt_for_category(categoryid) -> str | None` (existing).
- Produces: `CATEGORY_PROMPTS` resolves the 19 new category IDs (`ST, RMS, S3P, BLZ, SW, DPT, JNS, SHWL, S2P, DRS, C-S5P, TRS, SK, SJ-2P, FRMP, IW, SJ-3P, T-SHT, SHR`) to a non-empty Gemini-style prompt. `prompts_repo.seed_defaults` (Task 2) relies on these keys.

- [ ] **Step 1: Write the failing test**

Create `tests/test_category_prompts.py`:

```python
"""Phase 5: the 19 newly covered categories resolve to Gemini-style prompts."""
from mockup_generator.prompts.defaults import CATEGORY_PROMPTS, prompt_for_category

NEW_CATEGORY_IDS = [
    "ST", "RMS", "S3P", "BLZ", "SW", "DPT", "JNS", "SHWL", "S2P", "DRS",
    "C-S5P", "TRS", "SK", "SJ-2P", "FRMP", "IW", "SJ-3P", "T-SHT", "SHR",
]

# Pre-existing 11 must remain present and untouched by this phase.
EXISTING_CATEGORY_IDS = [
    "SA", "KP", "C-KP", "GWN", "LE", "SHT", "KUR", "NHJ", "SKT-TOP", "CRD", "TOP",
]


def test_all_new_categories_resolve():
    for cid in NEW_CATEGORY_IDS:
        body = prompt_for_category(cid)
        assert body, f"{cid} has no prompt"
        assert len(body) > 400, f"{cid} prompt too short to be detailed: {len(body)}"


def test_new_prompts_have_gemini_structure_markers():
    # Every prompt must carry the house style + anti-hallucination contract.
    for cid in NEW_CATEGORY_IDS:
        body = prompt_for_category(cid).lower()
        assert "ultra-realistic" in body
        assert "pixel" in body                      # pixel-for-pixel fidelity language
        assert "do not" in body or "never" in body  # anti-hallucination directive
        assert "tag" in body                         # cleanup tail (remove tags)


def test_shared_constants_are_truly_shared():
    # Archetype groups must point at the same object, not near-duplicates.
    assert CATEGORY_PROMPTS["ST"] is CATEGORY_PROMPTS["RMS"]
    assert CATEGORY_PROMPTS["S2P"] is CATEGORY_PROMPTS["S3P"]
    assert CATEGORY_PROMPTS["SJ-2P"] is CATEGORY_PROMPTS["SJ-3P"]
    assert CATEGORY_PROMPTS["TRS"] is CATEGORY_PROMPTS["FRMP"]


def test_existing_prompts_still_present():
    for cid in EXISTING_CATEGORY_IDS:
        assert prompt_for_category(cid), f"regressed: {cid} lost its prompt"


def test_total_category_count():
    assert len(CATEGORY_PROMPTS) == 30   # 11 existing + 19 new
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/test_category_prompts.py -v`
Expected: FAIL — `prompt_for_category` returns `None` for the new IDs, so `test_all_new_categories_resolve` and the others fail.

- [ ] **Step 3: Add the 15 prompt constants**

In `mockup_generator/prompts/defaults.py`, immediately above the `CATEGORY_PROMPTS: dict[str, str] = {` line (~line 291), insert all 15 constants:

```python
WOMENS_SUIT_PROMPT = """Generate an ultra-realistic, hyper-detailed, high-end editorial mockup of a women's Indian suit set (kurta with matching bottom), based on the [UPLOADED SUIT IMAGE HERE]. The final output MUST be indistinguishable from a professional 4K fashion photograph, as if captured by a high-end full-frame DSLR camera with a fast prime lens (e.g., 85mm f/1.4), suitable for a luxury Indian ethnicwear brand's magazine and high-resolution social media (Instagram, WhatsApp).
Absolute Priority: The kurta and bottom must be replicated exactly, pixel-for-pixel, from the uploaded reference — fabric weave, print, embroidery, neckline, sleeve length, hemline, and bottom silhouette (straight pant, palazzo, churidar, or sharara as shown). DO NOT invent or hallucinate new motifs, borders, or colors. No simplified or filler sections.
Dupatta: Only include a dupatta if it is clearly visible in the reference. If present, drape it exactly as shown with motifs and borders fully visible; if absent, do not invent one.
Full-Length Model: A graceful young Indian female model, alive and natural — realistic skin texture, organic posture, natural hair flow, subtle confident expression. No mannequins, no doll-like faces, no stiff limbs. Full head-to-toe, front-facing or three-quarter stance that shows the complete set.
Accessories: Minimal and tasteful — subtle earrings, bangles, a maang tikka or ring consistent with the reference styling. They must enhance, never distract from the garment.
Technical & Aesthetic: 4K ultra-sharp, capturing every thread and embroidery detail. Professional editorial studio lighting — soft yet sculpted with subtle rim light; no flat or harsh shadows. Pristine seamless white or neutral backdrop. Shallow depth of field with the model and garment razor-sharp.
Final Cleanup: Completely remove all product tags, yellow hanging tags, pins, stands, and labels from the final output."""

MENS_FORMAL_SUIT_PROMPT = """Generate an ultra-realistic, hyper-detailed, high-end editorial mockup of a men's western formal suit, based on the [UPLOADED SUIT IMAGE HERE]. The final output MUST be indistinguishable from a professional 4K fashion photograph, as if captured by a high-end full-frame DSLR camera with a fast prime lens (e.g., 85mm f/1.4), suitable for a luxury menswear brand's magazine and high-resolution social media (Instagram, WhatsApp).
Absolute Priority: The suit must be replicated exactly, pixel-for-pixel, from the uploaded reference — fabric texture and weave, color, lapel style, button stance, pocket detail, and stitching. DO NOT invent or hallucinate patterns, change the cut, or alter the color.
Waistcoat: Include the waistcoat/vest only if it is clearly visible in the reference (e.g. a three-piece set). If absent, render a two-piece jacket-and-trouser look; do not invent a vest.
Full-Length Model: A confident young Indian male model, alive and natural — realistic skin texture, organic posture, well-groomed, poised expression. No mannequins, no doll-like features, no stiff limbs. Full head-to-toe, front-facing stance that shows the complete suit, jacket buttoned as in the reference.
Accessories: Minimal and tasteful — a sleek tie or pocket square and formal leather shoes consistent with the reference. They must enhance, never distract from the suit.
Technical & Aesthetic: 4K ultra-sharp, capturing every weave and seam. Professional editorial studio lighting — soft yet sculpted with subtle rim light; no flat or harsh shadows. Pristine seamless white or neutral backdrop. Shallow depth of field with the model and garment razor-sharp.
Final Cleanup: Completely remove all product tags, yellow hanging tags, pins, stands, and labels from the final output."""

JODHPURI_SUIT_PROMPT = """Generate an ultra-realistic, hyper-detailed, high-end editorial mockup of a men's Jodhpuri (bandhgala) suit, based on the [UPLOADED JODHPURI SUIT IMAGE HERE]. The final output MUST be indistinguishable from a professional 4K fashion photograph, as if captured by a high-end full-frame DSLR camera with a fast prime lens (e.g., 85mm f/1.4), suitable for a luxury Indian ethnicwear brand's magazine and high-resolution social media (Instagram, WhatsApp).
Absolute Priority: The bandhgala jacket and trouser must be replicated exactly, pixel-for-pixel, from the uploaded reference — fabric, color, the closed mandarin (bandhgala) collar, button placement, and any embroidery, brooch, or zari work. DO NOT invent or hallucinate motifs or alter the cut.
Waistcoat / layers: Include any waistcoat or additional layer only if clearly visible in the reference; do not invent one.
Full-Length Model: A confident young Indian male model, alive and natural — realistic skin texture, organic posture, well-groomed, regal yet approachable expression. No mannequins, no doll-like features, no stiff limbs. Full head-to-toe, front-facing stance showing the complete suit with the collar fastened as in the reference.
Accessories: Minimal and tasteful — a subtle brooch or pocket square and formal mojari/leather shoes consistent with the reference. They must enhance, never distract from the suit.
Technical & Aesthetic: 4K ultra-sharp, capturing every weave, embroidery, and metallic sparkle. Professional editorial studio lighting — soft yet sculpted with subtle rim light; no flat or harsh shadows. Pristine seamless white or neutral backdrop. Shallow depth of field with the model and garment razor-sharp.
Final Cleanup: Completely remove all product tags, yellow hanging tags, pins, stands, and labels from the final output."""

FORMAL_TROUSER_PROMPT = """Generate an ultra-realistic, hyper-detailed, high-end editorial mockup of men's tailored formal trousers, based on the [UPLOADED TROUSER IMAGE HERE]. The final output MUST be indistinguishable from a professional 4K fashion photograph, as if captured by a high-end full-frame DSLR camera with a fast prime lens (e.g., 85mm f/1.4), suitable for a premium menswear brand's catalog and high-resolution social media (Instagram, WhatsApp).
Absolute Priority: The trousers must be replicated exactly, pixel-for-pixel, from the uploaded reference — fabric texture and weave, color, pleat/flat-front style, crease, pocket detail, and hem. DO NOT invent patterns, change the cut, or alter the color.
Full Model: A confident young Indian male model, alive and natural — realistic skin texture, organic posture, well-groomed. No mannequins, no doll-like features, no stiff limbs. Frame the lower body prominently to showcase the trouser fit, drape, and break at the shoe, with a clean neutral upper styling (a simple tucked formal shirt) that does not compete with the trousers.
Accessories: Minimal — a slim leather belt and formal leather shoes consistent with the reference. They must enhance, never distract from the trousers.
Technical & Aesthetic: 4K ultra-sharp, capturing every weave and crease. Professional editorial studio lighting — soft yet sculpted with subtle rim light; no flat or harsh shadows. Pristine seamless white or neutral backdrop. Shallow depth of field with the garment razor-sharp.
Final Cleanup: Completely remove all product tags, yellow hanging tags, pins, stands, and labels from the final output."""

BLAZER_PROMPT = """Generate an ultra-realistic, hyper-detailed, high-end editorial mockup of a men's blazer, based on the [UPLOADED BLAZER IMAGE HERE]. The final output MUST be indistinguishable from a professional 4K fashion photograph, as if captured by a high-end full-frame DSLR camera with a fast prime lens (e.g., 85mm f/1.4), suitable for a luxury menswear brand's magazine and high-resolution social media (Instagram, WhatsApp).
Absolute Priority: The blazer must be replicated exactly, pixel-for-pixel, from the uploaded reference — fabric texture and weave, color, single- or double-breasted button configuration, lapel style and width, pocket and vent detail, and stitching. DO NOT invent patterns, change the cut, or alter the color.
Full-Length Model: A confident young Indian male model, alive and natural — realistic skin texture, organic posture, well-groomed, poised expression. No mannequins, no doll-like features, no stiff limbs. Full head-to-toe, front-facing stance with the blazer worn open or buttoned exactly as shown in the reference, styled over a clean neutral shirt and trousers that do not compete with the blazer.
Accessories: Minimal and tasteful — an optional pocket square and formal shoes consistent with the reference. They must enhance, never distract from the blazer.
Technical & Aesthetic: 4K ultra-sharp, capturing every weave and seam. Professional editorial studio lighting — soft yet sculpted with subtle rim light; no flat or harsh shadows. Pristine seamless white or neutral backdrop. Shallow depth of field with the model and garment razor-sharp.
Final Cleanup: Completely remove all product tags, yellow hanging tags, pins, stands, and labels from the final output."""

SHERWANI_PROMPT = """Generate an ultra-realistic, hyper-detailed, high-end editorial mockup of a men's sherwani, based on the [UPLOADED SHERWANI IMAGE HERE]. The final output MUST be indistinguishable from a professional 4K fashion photograph, as if captured by a high-end full-frame DSLR camera with a fast prime lens (e.g., 85mm f/1.4), suitable for a luxury Indian ethnicwear brand's magazine and high-resolution social media (Instagram, WhatsApp).
Absolute Priority: The long sherwani coat and its bottom (churidar or trouser) must be replicated exactly, pixel-for-pixel, from the uploaded reference — fabric, color, collar, button placket, length, and every embroidery, zari, or stonework motif. DO NOT invent or hallucinate motifs, alter the length, or simplify any section.
Dupatta / stole: Include a dupatta or stole only if clearly visible in the reference, draped exactly as shown; if absent, do not invent one.
Full-Length Model: A confident young Indian male model, alive and natural — realistic skin texture, organic posture, well-groomed, regal yet approachable expression. No mannequins, no doll-like features, no stiff limbs. Full head-to-toe, front-facing stance showing the complete sherwani, fastened as in the reference.
Accessories: Minimal and tasteful — an optional brooch, safa/turban, or mojari consistent with the reference styling. They must enhance, never distract from the sherwani.
Technical & Aesthetic: 4K ultra-sharp, capturing every weave, embroidery, and metallic sparkle. Professional editorial studio lighting — soft yet sculpted with subtle rim light; no flat or harsh shadows. Pristine seamless white or neutral backdrop. Shallow depth of field with the model and garment razor-sharp.
Final Cleanup: Completely remove all product tags, yellow hanging tags, pins, stands, and labels from the final output."""

DUPATTA_PROMPT = """Generate an ultra-realistic, hyper-detailed, high-end editorial mockup of an Indian dupatta (long scarf), based on the [UPLOADED DUPATTA IMAGE HERE]. The final output MUST be indistinguishable from a professional 4K fashion photograph, as if captured by a high-end full-frame DSLR camera with a fast prime lens (e.g., 85mm f/1.4), suitable for a luxury Indian ethnicwear brand's magazine and high-resolution social media (Instagram, WhatsApp).
Absolute Priority: The dupatta must be replicated exactly, pixel-for-pixel, from the uploaded reference — fabric (chiffon, silk, georgette, etc.), color, print, every border, motif, embroidery, and tassel detail. DO NOT invent or hallucinate patterns or alter the color.
Styling: Drape the dupatta elegantly over a graceful young Indian female model styled in a clean, neutral solid-toned suit or kurta so the dupatta is the unmistakable focus. Show the full length and both borders flowing naturally; the drape must reveal the print and border clearly. Do not add a competing patterned garment.
Model: Alive and natural — realistic skin texture, organic posture, natural hair flow, subtle confident expression. No mannequins, no doll-like faces, no stiff limbs. Full head-to-toe, front-facing or three-quarter stance.
Accessories: Minimal — subtle earrings or bangles. They must enhance, never distract from the dupatta.
Technical & Aesthetic: 4K ultra-sharp, capturing every thread, border, and embroidery detail. Professional editorial studio lighting — soft yet sculpted with subtle rim light that reveals the fabric's sheen and translucency; no flat or harsh shadows. Pristine seamless white or neutral backdrop. Shallow depth of field with the model and dupatta razor-sharp.
Final Cleanup: Completely remove all product tags, yellow hanging tags, pins, stands, and labels from the final output."""

JEANS_PROMPT = """Generate an ultra-realistic, hyper-detailed, high-end editorial mockup of casual denim jeans, based on the [UPLOADED JEANS IMAGE HERE]. The final output MUST be indistinguishable from a professional 4K fashion photograph, as if captured by a high-end full-frame DSLR camera with a fast prime lens (e.g., 85mm f/1.4), suitable for a premium apparel brand's catalog and high-resolution social media (Instagram, WhatsApp).
Absolute Priority: The jeans must be replicated exactly, pixel-for-pixel, from the uploaded reference — denim wash and fade, color, fit (skinny, straight, bootcut, etc.), rips/distressing, stitching, pocket design, and rivets. DO NOT invent washes, change the fit, or alter the distressing pattern.
Model & gender: A young Indian fashion model whose gender presentation matches the jeans shown in the reference. The model must look alive and natural — realistic skin texture, organic posture, natural hair flow. No mannequins, no doll-like features, no stiff limbs. Frame the lower body prominently to showcase the fit and drape, with a clean neutral solid top that does not compete with the jeans.
Accessories: Minimal — a simple belt and casual footwear (sneakers or heels) consistent with the reference. They must enhance, never distract from the jeans.
Technical & Aesthetic: 4K ultra-sharp, capturing every weave, fade, and stitch. Professional editorial studio lighting — soft yet sculpted with subtle rim light; no flat or harsh shadows. Pristine seamless white or neutral backdrop. Shallow depth of field with the garment razor-sharp.
Final Cleanup: Completely remove all product tags, yellow hanging tags, pins, stands, and labels from the final output."""

SHAWL_PROMPT = """Generate an ultra-realistic, hyper-detailed, high-end editorial mockup of a shawl (woolen or silk wrap), based on the [UPLOADED SHAWL IMAGE HERE]. The final output MUST be indistinguishable from a professional 4K fashion photograph, as if captured by a high-end full-frame DSLR camera with a fast prime lens (e.g., 85mm f/1.4), suitable for a luxury Indian ethnicwear brand's magazine and high-resolution social media (Instagram, WhatsApp).
Absolute Priority: The shawl must be replicated exactly, pixel-for-pixel, from the uploaded reference — fabric (pashmina, wool, silk), color, weave, every border, motif, embroidery (e.g. kani/sozni), and fringe detail. DO NOT invent or hallucinate patterns or alter the color.
Styling: Drape the shawl elegantly over the shoulders of a graceful young Indian model whose gender presentation matches the reference, styled in clean neutral solid-toned attire so the shawl is the unmistakable focus. Show the full spread and both borders flowing naturally; the drape must reveal the weave and border clearly. Do not add a competing patterned garment.
Model: Alive and natural — realistic skin texture, organic posture, natural hair flow, subtle confident expression. No mannequins, no doll-like faces, no stiff limbs. Full head-to-toe or three-quarter, front-facing stance.
Accessories: Minimal — subtle, refined pieces that never distract from the shawl.
Technical & Aesthetic: 4K ultra-sharp, capturing every thread, border, and embroidery detail. Professional editorial studio lighting — soft yet sculpted with subtle rim light that reveals the fabric's texture and sheen; no flat or harsh shadows. Pristine seamless white or neutral backdrop. Shallow depth of field with the model and shawl razor-sharp.
Final Cleanup: Completely remove all product tags, yellow hanging tags, pins, stands, and labels from the final output."""

DRESS_PROMPT = """Generate an ultra-realistic, hyper-detailed, high-end editorial mockup of a women's dress (one-piece), based on the [UPLOADED DRESS IMAGE HERE]. The final output MUST be indistinguishable from a professional 4K fashion photograph, as if captured by a high-end full-frame DSLR camera with a fast prime lens (e.g., 85mm f/1.4), suitable for a luxury fashion brand's magazine and high-resolution social media (Instagram, WhatsApp).
Absolute Priority: The dress must be replicated exactly, pixel-for-pixel, from the uploaded reference — fabric texture, color, print, neckline, sleeve style, waistline, and hem length. DO NOT invent patterns, add a slit unless clearly visible, change the length, or alter the silhouette. Replicate the length precisely as shown (mini, knee, midi, or maxi).
Full-Length Model: A graceful young Indian female model, alive and natural — realistic skin texture, organic posture, natural hair flow, confident subtle expression. No mannequins, no doll-like faces, no stiff limbs. Full head-to-toe, front-facing or three-quarter stance that highlights the dress's silhouette and defining features.
Accessories: Minimal and tasteful — subtle earrings, a delicate bracelet, and footwear consistent with the reference styling. They must enhance, never distract from the dress.
Technical & Aesthetic: 4K ultra-sharp, capturing every thread and print detail. Professional editorial studio lighting — soft yet sculpted with subtle rim light; no flat or harsh shadows. Pristine seamless white or neutral backdrop. Shallow depth of field with the model and garment razor-sharp.
Final Cleanup: Completely remove all product tags, yellow hanging tags, pins, stands, and labels from the final output."""

CHILD_SUIT_PROMPT = """Generate an ultra-realistic, hyper-detailed, high-end editorial mockup of a boy's five-piece formal suit, based on the [UPLOADED CHILD SUIT IMAGE HERE]. The final output MUST be indistinguishable from a professional 4K fashion photograph, as if captured by a high-end full-frame DSLR camera with a fast prime lens (e.g., 85mm f/1.4), suitable for a luxury kidswear brand's magazine and high-resolution social media (Instagram, WhatsApp).
Absolute Priority: The full set (jacket, waistcoat, shirt, trouser, and tie/bow as present) must be replicated exactly, pixel-for-pixel, from the uploaded reference — fabric, color, lapel, button stance, and every detail. DO NOT invent patterns, omit a visible piece, or alter the cut. Include only the pieces visible in the reference.
Full-Length Model: A cheerful young Indian boy child model, alive and natural — realistic child skin texture, age-appropriate proportions, organic posture, natural hair, a warm genuine expression. No mannequins, no doll-like faces, no stiff or adult-like poses. Full head-to-toe, front-facing stance showing the complete set, jacket buttoned as in the reference.
Accessories: Minimal — a tie or bow tie and formal shoes consistent with the reference. They must enhance, never distract from the suit.
Technical & Aesthetic: 4K ultra-sharp, capturing every weave and seam. Professional editorial studio lighting — soft yet sculpted with subtle rim light; no flat or harsh shadows. Pristine seamless white or neutral backdrop. Shallow depth of field with the model and garment razor-sharp.
Final Cleanup: Completely remove all product tags, yellow hanging tags, pins, stands, and labels from the final output."""

SHORT_KURTA_PROMPT = """Generate an ultra-realistic, hyper-detailed, high-end editorial mockup of a men's short kurta, based on the [UPLOADED SHORT KURTA IMAGE HERE]. The final output MUST be indistinguishable from a professional 4K fashion photograph, as if captured by a high-end full-frame DSLR camera with a fast prime lens (e.g., 85mm f/1.4), suitable for a premium Indian fusion-wear brand's magazine and high-resolution social media (Instagram, WhatsApp).
Absolute Priority: The short kurta must be replicated exactly, pixel-for-pixel, from the uploaded reference — fabric texture, color, print, neckline/placket, sleeve length, and the (above-knee) hemline. DO NOT invent motifs, lengthen the kurta, or alter the color.
Bottom: Pair it with the bottom shown in the reference (jeans or trousers); if no bottom is shown, style it over plain neutral denim or trousers that do not compete with the kurta.
Full-Length Model: A confident young Indian male model, alive and natural — realistic skin texture, organic posture, well-groomed, relaxed approachable expression. No mannequins, no doll-like features, no stiff limbs. Full head-to-toe, front-facing or three-quarter stance showing the complete look.
Accessories: Minimal and tasteful — casual footwear (loafers or sneakers) consistent with the reference. They must enhance, never distract from the kurta.
Technical & Aesthetic: 4K ultra-sharp, capturing every thread and print detail. Professional editorial studio lighting — soft yet sculpted with subtle rim light; no flat or harsh shadows. Pristine seamless white or neutral backdrop. Shallow depth of field with the model and garment razor-sharp.
Final Cleanup: Completely remove all product tags, yellow hanging tags, pins, stands, and labels from the final output."""

INDO_WESTERN_PROMPT = """Generate an ultra-realistic, hyper-detailed, high-end editorial mockup of an Indo-Western fusion outfit, based on the [UPLOADED INDO-WESTERN IMAGE HERE]. The final output MUST be indistinguishable from a professional 4K fashion photograph, as if captured by a high-end full-frame DSLR camera with a fast prime lens (e.g., 85mm f/1.4), suitable for a luxury fusion-wear brand's magazine and high-resolution social media (Instagram, WhatsApp).
Absolute Priority: The complete Indo-Western outfit must be replicated exactly, pixel-for-pixel, from the uploaded reference — every layer and piece, fabric, color, drape, asymmetric cut, and all embroidery or embellishment. DO NOT invent patterns, omit a visible layer, or alter the silhouette. Render only the pieces visible in the reference.
Model & gender: A graceful young Indian model whose gender presentation matches the outfit shown. Alive and natural — realistic skin texture, organic posture, natural hair flow, confident subtle expression. No mannequins, no doll-like faces, no stiff limbs. Full head-to-toe, front-facing or three-quarter stance that highlights the fusion silhouette and layering.
Accessories: Minimal and contemporary — refined pieces consistent with the reference styling. They must enhance, never distract from the outfit.
Technical & Aesthetic: 4K ultra-sharp, capturing every thread, embroidery, and metallic detail. Professional editorial studio lighting — soft yet sculpted with subtle rim light; no flat or harsh shadows. Pristine seamless white or neutral backdrop. Shallow depth of field with the model and garment razor-sharp.
Final Cleanup: Completely remove all product tags, yellow hanging tags, pins, stands, and labels from the final output."""

T_SHIRT_PROMPT = """Generate an ultra-realistic, hyper-detailed, high-end editorial mockup of a casual t-shirt, based on the [UPLOADED T-SHIRT IMAGE HERE]. The final output MUST be indistinguishable from a professional 4K fashion photograph, as if captured by a high-end full-frame DSLR camera with a fast prime lens (e.g., 85mm f/1.4), suitable for a premium apparel brand's catalog and high-resolution social media (Instagram, WhatsApp).
Absolute Priority: The t-shirt must be replicated exactly, pixel-for-pixel, from the uploaded reference — fabric, color, neckline (crew or V-neck), sleeve length, fit, and any print, graphic, or text on it. DO NOT invent graphics, change the color, alter any printed text, or modify the fit.
Model & gender: A young Indian fashion model whose gender presentation matches the t-shirt shown. Alive and natural — realistic skin texture, organic posture, natural hair, relaxed approachable expression. No mannequins, no doll-like features, no stiff limbs. Frame the upper body prominently to showcase the fit and any print, paired with clean neutral bottoms that do not compete with the t-shirt.
Accessories: Minimal — casual pieces consistent with the reference. They must enhance, never distract from the t-shirt.
Technical & Aesthetic: 4K ultra-sharp, capturing every weave and print detail. Professional editorial studio lighting — soft yet sculpted with subtle rim light; no flat or harsh shadows. Pristine seamless white or neutral backdrop. Shallow depth of field with the garment razor-sharp.
Final Cleanup: Completely remove all product tags, yellow hanging tags, pins, stands, and labels from the final output."""

SHARARA_PROMPT = """Generate an ultra-realistic, hyper-detailed, high-end editorial mockup of a women's sharara set (kurta with wide-legged sharara), based on the [UPLOADED SHARARA IMAGE HERE]. The final output MUST be indistinguishable from a professional 4K fashion photograph, as if captured by a high-end full-frame DSLR camera with a fast prime lens (e.g., 85mm f/1.4), suitable for a luxury Indian ethnicwear brand's magazine and high-resolution social media (Instagram, WhatsApp).
Absolute Priority: The kurta and the flared wide-legged sharara must be replicated exactly, pixel-for-pixel, from the uploaded reference — fabric, color, print, neckline, sleeve length, the sharara's flare and pleating, and every border, motif, and embroidery. DO NOT invent or hallucinate motifs, alter the flare, or simplify any section.
Dupatta: Include a dupatta only if clearly visible in the reference, draped exactly as shown with borders fully visible; if absent, do not invent one.
Full-Length Model: A graceful young Indian female model, alive and natural — realistic skin texture, organic posture, natural hair flow, subtle confident expression. No mannequins, no doll-like faces, no stiff limbs. Full head-to-toe, front-facing stance that showcases the full volume and flow of the sharara.
Accessories: Minimal and tasteful — statement earrings, bangles, or a maang tikka consistent with the reference styling. They must enhance, never distract from the set.
Technical & Aesthetic: 4K ultra-sharp, capturing every thread, embroidery, and metallic sparkle. Professional editorial studio lighting — soft yet sculpted with subtle rim light; no flat or harsh shadows. Pristine seamless white or neutral backdrop. Shallow depth of field with the model and garment razor-sharp.
Final Cleanup: Completely remove all product tags, yellow hanging tags, pins, stands, and labels from the final output."""
```

- [ ] **Step 4: Wire the 19 keys into `CATEGORY_PROMPTS`**

In the same file, add these entries inside the existing `CATEGORY_PROMPTS` dict (after the last existing entry `"TOP": WOMEN_TOP,` — keep the existing 11 lines unchanged):

```python
    # Phase 5 — categories with >=10 products (15 shared constants, 19 ids)
    "ST": WOMENS_SUIT_PROMPT,
    "RMS": WOMENS_SUIT_PROMPT,
    "S2P": MENS_FORMAL_SUIT_PROMPT,
    "S3P": MENS_FORMAL_SUIT_PROMPT,
    "SJ-2P": JODHPURI_SUIT_PROMPT,
    "SJ-3P": JODHPURI_SUIT_PROMPT,
    "TRS": FORMAL_TROUSER_PROMPT,
    "FRMP": FORMAL_TROUSER_PROMPT,
    "BLZ": BLAZER_PROMPT,
    "SW": SHERWANI_PROMPT,
    "DPT": DUPATTA_PROMPT,
    "JNS": JEANS_PROMPT,
    "SHWL": SHAWL_PROMPT,
    "DRS": DRESS_PROMPT,
    "C-S5P": CHILD_SUIT_PROMPT,
    "SK": SHORT_KURTA_PROMPT,
    "IW": INDO_WESTERN_PROMPT,
    "T-SHT": T_SHIRT_PROMPT,
    "SHR": SHARARA_PROMPT,
```

- [ ] **Step 5: Run the new test to verify it passes**

Run: `poetry run pytest tests/test_category_prompts.py -v`
Expected: PASS — all 6 tests green (19 resolve, markers present, shared identity holds, existing intact, count == 30).

- [ ] **Step 6: Run the full suite for regressions**

Run: `poetry run pytest -q`
Expected: PASS — all prior tests still green (including `tests/test_imports.py::test_category_prompts_populated`).

- [ ] **Step 7: Commit**

```bash
git add mockup_generator/prompts/defaults.py tests/test_category_prompts.py
git commit -m "feat(prompts): add 15 Gemini-optimized default prompts for 19 categories

Covers every category with >=10 products that lacked a default prompt
(Suits, Readymade Suits, Sherwani, Blazer, Dupatta, Jeans, Dress, etc.).
Shared archetype constants map multiple category ids to one prompt to
avoid drift. Built on the existing prompt house style; no schema change."
```

---

### Task 2: Seed regression test — new categories flow through `seed_defaults`

**Files:**
- Test: `tests/test_prompts_repo.py` (append; `FakeClient`/`FakeTable` already defined at the top)

**Interfaces:**
- Consumes: `mockup_generator.db.prompts_repo.seed_defaults(client) -> int`, `mockup_generator.prompts.defaults.CATEGORY_PROMPTS`, and the existing `FakeClient` test double (its `select` returns the rows it was constructed with, for every call).
- Produces: nothing for later tasks — pure regression coverage.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_prompts_repo.py`:

```python
def test_seed_inserts_all_categories_when_table_empty():
    from mockup_generator.prompts.defaults import CATEGORY_PROMPTS
    c = FakeClient([])                       # every existence-check select returns no rows
    inserted = prompts_repo.seed_defaults(c)
    assert inserted == len(CATEGORY_PROMPTS)  # 30
    seeded_ids = {p["categoryid"] for kind, p in c.sink if kind == "insert"}
    assert {"ST", "RMS", "SW", "DPT", "SHR"} <= seeded_ids   # new Phase 5 ids inserted


def test_seed_is_idempotent_when_rows_exist():
    c = FakeClient([{"prompt_id": 1}])       # existence check always finds a Default row
    inserted = prompts_repo.seed_defaults(c)
    assert inserted == 0
    assert not any(kind == "insert" for kind, _ in c.sink)   # nothing overwritten
```

- [ ] **Step 2: Run to verify the new tests pass immediately**

Run: `poetry run pytest tests/test_prompts_repo.py -v`
Expected: PASS — `seed_defaults` already iterates `CATEGORY_PROMPTS` generically, so the new keys flow through with no code change. This task is regression coverage that locks the behavior in. (If `test_seed_inserts_all_categories_when_table_empty` fails on the count, confirm Task 1 added all 19 keys.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_prompts_repo.py
git commit -m "test(prompts): lock seed idempotency + new-category coverage

Asserts seed_defaults inserts all 30 category defaults into an empty
table and inserts nothing when defaults already exist (no overwrite)."
```

---

### Task 3: README + `.env.example` + deploy notes

**Files:**
- Create: `README.md` (repo root)
- Create: `.env.example` (repo root)

**Interfaces:**
- Consumes: the env keys read by `mockup_generator/config.py` (`Settings`) and the VEO keys in `backend/routers/generate.py` / `video_service.py`.
- Produces: documentation only.

- [ ] **Step 1: Confirm the full env-key list from config**

Run: `grep -niE "os.environ|getenv|settings\.|VEO_|SUPABASE_|GOOGLE_|OPENAI_|GOOGLE_APPLICATION_CREDENTIALS|service.account" mockup_generator/config.py`
Expected: surfaces every key `Settings` reads. Use the exact names that appear (e.g. `GOOGLE_API_KEY`, `OPENAI_API_KEY`, `SUPABASE_PROJECT_ID`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`, `VEO_MODEL`, `VEO_POLL_TIMEOUT_SEC`, `VEO_POLL_INTERVAL_SEC`, and any Google service-account path). Add any not listed below to `.env.example` verbatim.

- [ ] **Step 2: Write `.env.example`**

Create `.env.example` with every key as a placeholder (no real secrets). Reconcile against Step 1 output before finalizing:

```bash
# --- AI providers ---
GOOGLE_API_KEY=your-google-genai-or-vertex-key
OPENAI_API_KEY=your-openai-key            # legacy gpt-image-1 path only

# --- Supabase (shared with the Inventory-Management project) ---
SUPABASE_PROJECT_ID=your-project-ref
SUPABASE_PUBLISHABLE_KEY=your-anon-publishable-key
SUPABASE_SECRET_KEY=your-service-secret-key   # server-only, never ship to the client

# --- Google Drive service account (reads product image folders) ---
GOOGLE_APPLICATION_CREDENTIALS=./service-account.json

# --- VEO video generation ---
VEO_MODEL=veo-3.1-generate-preview
VEO_POLL_TIMEOUT_SEC=600
VEO_POLL_INTERVAL_SEC=10
```

- [ ] **Step 3: Write `README.md`**

Create `README.md`:

````markdown
# Bindal's Creation — Mockup Generator

AI-powered luxury garment mockup generator for Bindal's Creation (Indian ethnic
wear). Generates photorealistic fashion mockups with Google Gemini and short
product videos with Google VEO, over a FastAPI backend and a React frontend,
backed by Supabase (auth + data + storage) and Google Drive (source images).

## Architecture

- **`mockup_generator/`** — framework-agnostic core: config, prompts, Gemini/VEO
  generation, Supabase + Drive integrations, DB repos.
- **`backend/`** — FastAPI app: Supabase-JWT auth (`profiles.is_active` gate),
  product/prompt APIs, preview-only `/generate/image`, approve/publish, async
  `/generate/video` job model.
- **`frontend/`** — Vite + React + TS: Google login, Products/Prompts tabs,
  generate → review → approve flow, video controls.
- **`app.py`** — legacy Streamlit UI on top of the same core.

## Local development

```bash
poetry install                       # Python 3.10
cp .env.example .env                  # fill in secrets (see below)

# Backend (FastAPI)
poetry run uvicorn backend.main:app --reload

# Frontend (Vite) — in another shell
cd frontend && npm install && npm run dev

# Tests
poetry run pytest -q
cd frontend && npm run build          # frontend typecheck/build gate
```

## Configuration

Copy `.env.example` to `.env` and fill each key. The Supabase project, Drive
service account, and Gemini billing are shared infrastructure — see the deploy
notes below. The frontend reads its own Supabase publishable key via Vite env
vars (`frontend/.env`).

## Category prompts

Default prompts live in `mockup_generator/prompts/defaults.py`, keyed by
Supabase `categories.categoryid` in `CATEGORY_PROMPTS`. They are seeded into the
`prompts` table by `prompts_repo.seed_defaults`, which is **insert-only**: it
never overwrites an existing default. To change a category's default after
seeding, edit it in the Prompts tab (re-seeding will not clobber your edit).

## Deployment

- **Backend → Hugging Face Space.** The Space **must be public** so approved
  mockups served from the public Supabase Storage bucket render for anonymous
  viewers. Host all secrets as Space secrets; never commit them.
- **Frontend → Vercel.** Set the Supabase URL + publishable key and the backend
  API base URL as Vercel env vars.
- **Auth gating.** Access is allowlist-based: `profiles.is_active` defaults to
  `false` (shared with the Inventory-Management app). Flip a profile to `true`
  to grant access.
- **Gemini billing.** Image/video generation routes via **Vertex AI**
  (Cloud pay-as-you-go), not an AI Studio key.
- **Drive access.** A dedicated service account
  (`mockup-drive-reader@...`) reads product image folders; each product folder
  must be shared with that account.

## Plans & specs

Design docs and phased implementation plans live in `docs/plans/` and
`docs/superpowers/`.
````

- [ ] **Step 4: Verify the docs are accurate**

Run: `poetry run uvicorn backend.main:app --reload` (confirm it boots with a filled `.env`), then in another shell `cd frontend && npm run build` (confirm clean). Confirm every key in `.env.example` matches Step 1's grep output — add any missing, remove any that `config.py` does not read.
Expected: backend boots, frontend build passes, `.env.example` is a superset of the keys `Settings` requires.

- [ ] **Step 5: Commit**

```bash
git add README.md .env.example
git commit -m "docs: add README, .env.example, and deploy notes

Documents architecture, local dev, configuration, the insert-only prompt
seed behavior, and deploy specifics (public HF Space, Vercel frontend,
allowlist gating, Vertex billing, Drive service account)."
```

---

## Self-Review

**Spec coverage:**
- Part A — 15 shared Gemini-optimized prompts for the 19 ≥10-product categories → Task 1 (full bodies in Step 3, wiring in Step 4). ✓
- Reuse grouping (ST+RMS, S2P+S3P, SJ-2P+SJ-3P, TRS+FRMP share constants) → Task 1 Step 4 + `test_shared_constants_are_truly_shared`. ✓
- Built on existing prompt style + anti-hallucination/cleanup tail → every body mirrors `CORD_SET_PROMPT`; `test_new_prompts_have_gemini_structure_markers` enforces markers. ✓
- Idempotent insert-only seed, existing 11 untouched → Task 1 (`test_existing_prompts_still_present`, count==30) + Task 2 (idempotency + empty-table tests). ✓
- No schema/migration/dependency/endpoint → no such steps; seed reused as-is. ✓
- Part B — README + `.env.example` + deploy notes (public Space, allowlist gating, Vertex billing, Drive SA) → Task 3. ✓
- Verification: full pytest green, frontend build clean, live prompt-loads → Task 1 Step 6, Task 3 Step 4 + design's live smoke. ✓

**Placeholder scan:** No TBD/TODO. All 15 prompt bodies written in full; all test code complete; README and `.env.example` content complete. ✓

**Type consistency:** `CATEGORY_PROMPTS` (dict[str,str]), `prompt_for_category(str) -> str | None`, `seed_defaults(client) -> int`, and the `FakeClient([...]).sink` `("insert", payload)` tuple shape all match the existing code read during planning. The 19 keys in Task 1 Step 4 exactly match `NEW_CATEGORY_IDS` in Task 1 Step 1 and the ids asserted in Task 2. ✓
