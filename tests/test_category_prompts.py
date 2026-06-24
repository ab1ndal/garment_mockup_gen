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
