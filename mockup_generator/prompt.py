"""Backwards-compatible shim.

Prompt constants moved to ``mockup_generator.prompts.defaults``. This module
re-exports them so existing imports (``from mockup_generator.prompt import ...``)
keep working.
"""

from mockup_generator.prompts.defaults import *  # noqa: F401,F403
from mockup_generator.prompts.defaults import (  # noqa: F401
    CATEGORY_PROMPTS,
    prompt_for_category,
)
