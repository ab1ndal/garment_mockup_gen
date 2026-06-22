"""Phase-0 refactor smoke tests: structure resolves, no Streamlit in core."""

import sys


def test_core_modules_import():
    import mockup_generator.config  # noqa: F401
    from mockup_generator.generation import common, images, video, legacy_openai  # noqa: F401

    assert hasattr(images, "generate_image_for_product")
    assert hasattr(images, "refine_only_folder")
    assert hasattr(images, "output_exists")
    assert hasattr(common, "part_from_pil")
    assert hasattr(common, "generate_with_retries")
    assert hasattr(common, "get_genai_client")


def test_category_prompts_populated():
    from mockup_generator.prompts.defaults import CATEGORY_PROMPTS, prompt_for_category

    assert {"SA", "KP", "GWN", "LE", "SHT", "KUR", "NHJ", "SKT-TOP", "CRD"} <= set(CATEGORY_PROMPTS)
    assert prompt_for_category("SA")
    assert prompt_for_category("NOPE") is None


def test_backcompat_shims():
    # Old import paths must still resolve via shims.
    from mockup_generator.create_base import generate_image_for_product  # noqa: F401
    from mockup_generator.prompt import SAREE_PROMPT, CATEGORY_PROMPTS  # noqa: F401

    assert SAREE_PROMPT


def test_core_does_not_import_streamlit():
    # Importing the core engine must not pull in Streamlit.
    for mod in list(sys.modules):
        if mod == "streamlit" or mod.startswith("streamlit."):
            del sys.modules[mod]
    import importlib

    import mockup_generator.generation.images  # noqa: F401
    import mockup_generator.generation.common  # noqa: F401
    importlib.reload(mockup_generator.generation.common)

    assert "streamlit" not in sys.modules
