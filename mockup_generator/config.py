"""Single source for configuration and secrets.

Loads values from the environment (via .env) with an optional, guarded
fallback to Streamlit secrets when running inside a Streamlit app. The core
package must not hard-depend on Streamlit, so that import is best-effort.
"""

from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def _from_streamlit_secrets(key: str) -> str | None:
    """Best-effort read from st.secrets without making Streamlit a hard dep."""
    try:
        import streamlit as st  # noqa: PLC0415 - optional, only when running under Streamlit
    except Exception:
        return None
    try:
        return st.secrets.get(key)  # type: ignore[no-any-return]
    except Exception:
        return None


def _get(key: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.getenv(key) or _from_streamlit_secrets(key) or default
    if required and not value:
        raise RuntimeError(f"{key} is not set")
    return value


class Settings:
    """Lazily-evaluated settings. Access attributes; missing required keys
    raise only when actually accessed, so importing the package never fails."""

    @property
    def google_api_key(self) -> str:
        return _get("GOOGLE_API_KEY", required=True)  # type: ignore[return-value]

    @property
    def openai_api_key(self) -> str:
        return _get("OPENAI_API_KEY", required=True)  # type: ignore[return-value]

    @property
    def supabase_project_id(self) -> str | None:
        return _get("SUPABASE_PROJECT_ID")

    @property
    def supabase_publishable_key(self) -> str | None:
        return _get("SUPABASE_PUBLISHABLE_KEY")

    @property
    def supabase_secret_key(self) -> str | None:
        return _get("SUPABASE_SECRET_KEY")

    @property
    def supabase_url(self) -> str | None:
        pid = self.supabase_project_id
        return f"https://{pid}.supabase.co" if pid else None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
