"""Backwards-compatible shim.

The Gemini image engine moved to ``mockup_generator.generation.images`` and the
shared helpers to ``mockup_generator.generation.common``. Re-exported here so
existing imports keep working.
"""

from mockup_generator.generation.common import (  # noqa: F401
    ALLOWED_EXT,
    MAX_SIDE,
    generate_with_retries,
    load_images_from_folder,
    part_from_pil,
    save_first_image_part,
)
from mockup_generator.generation.images import (  # noqa: F401
    ASPECT_RATIO,
    MODEL_NAME,
    RESOLUTION,
    generate_image_for_product,
    output_exists,
    refine_only_folder,
)

__all__ = [
    "ALLOWED_EXT",
    "MAX_SIDE",
    "generate_with_retries",
    "load_images_from_folder",
    "part_from_pil",
    "save_first_image_part",
    "ASPECT_RATIO",
    "MODEL_NAME",
    "RESOLUTION",
    "generate_image_for_product",
    "output_exists",
    "refine_only_folder",
]
