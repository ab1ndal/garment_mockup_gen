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
    def use_vertex(self) -> bool:
        """Route google-genai through Vertex AI (Cloud billing) instead of the
        Gemini Developer API (AI Studio prepay credits). Enable by setting
        ``GOOGLE_GENAI_USE_VERTEXAI=true`` in the environment."""
        return str(_get("GOOGLE_GENAI_USE_VERTEXAI", default="") or "").strip().lower() in {
            "1",
            "true",
            "yes",
        }

    @property
    def google_cloud_project(self) -> str:
        """GCP project id for Vertex AI. Required when ``use_vertex`` is on."""
        return _get("GOOGLE_CLOUD_PROJECT", required=True)  # type: ignore[return-value]

    @property
    def google_cloud_location(self) -> str:
        return _get("GOOGLE_CLOUD_LOCATION", default="global")  # type: ignore[return-value]

    @property
    def vertex_sa_json(self) -> str | None:
        """Service-account credentials for Vertex AI (path or JSON content).

        Used on headless deploys (HF Spaces) where user ADC is unavailable.
        Falls back to the Drive SA so a single key can serve both — that SA
        must hold ``roles/aiplatform.user`` on the project. When unset, the
        client uses ADC (local ``gcloud auth application-default login``)."""
        return _get("GOOGLE_VERTEX_SA_JSON") or self.google_drive_sa_json

    @property
    def google_api_key(self) -> str:
        return _get("GOOGLE_API_KEY", required=True)  # type: ignore[return-value]

    @property
    def openai_api_key(self) -> str:
        return _get("OPENAI_API_KEY", required=True)  # type: ignore[return-value]

    @property
    def gemini_image_model(self) -> str:
        """Gemini image-generation model. GA name; override for preview/flash."""
        return _get("GEMINI_IMAGE_MODEL", default="gemini-3-pro-image")  # type: ignore[return-value]

    @property
    def rembg_model(self) -> str:
        """BiRefNet session name for rembg background removal (product-shot import).
        Lite model (~214 MB) by default; set to 'birefnet-general' for the ~928 MB
        full model where quality warrants the RAM/latency cost."""
        return _get("REMBG_MODEL", default="birefnet-general-lite")  # type: ignore[return-value]

    @property
    def rembg_warm(self) -> bool:
        """Warm the rembg model at startup. Off by default: warming downloads the
        model (~214 MB) and only helps where storage is persistent/pre-cached —
        otherwise it slows every cold boot. Enable where the model is baked in."""
        return str(_get("REMBG_WARM", default="0")).lower() in ("1", "true", "yes")

    @property
    def gemini_text_model(self) -> str:
        """Advanced Gemini text model used to refine/expand prompts.
        Text sibling of the image model (not Flash); override per deploy.
        gemini-3-pro is not yet served on Vertex for this project (404), so the
        default is the latest GA Pro text model."""
        return _get("GEMINI_TEXT_MODEL", default="gemini-2.5-pro")  # type: ignore[return-value]

    @property
    def veo_model(self) -> str:
        """VEO video-generation model. Override for fast/lite variants."""
        return _get("VEO_MODEL", default="veo-3.1-generate-preview")  # type: ignore[return-value]

    @property
    def veo_poll_timeout_sec(self) -> int:
        """Max seconds to wait for a VEO job before giving up. VEO is slow
        (minutes); raise this on deploys with long-lived request timeouts."""
        return int(_get("VEO_POLL_TIMEOUT_SEC", default="900"))  # type: ignore[arg-type]

    @property
    def veo_poll_interval_sec(self) -> int:
        """Seconds between VEO operation polls."""
        return int(_get("VEO_POLL_INTERVAL_SEC", default="10"))  # type: ignore[arg-type]

    @property
    def google_drive_sa_json(self) -> str | None:
        """Service-account credentials for Drive read access. Either a path to
        a JSON key file or the JSON content itself. Optional — only needed for
        the Drive image-preview endpoint."""
        return _get("GOOGLE_DRIVE_SA_JSON")

    @property
    def generated_mockups_folder_id(self) -> str:
        """Drive folder-of-folders holding previously-generated mockups to backfill."""
        return _get(
            "GENERATED_MOCKUPS_FOLDER_ID",
            default="1FBDw_F40zDt4zvp6el3Ei50Nw8cOahm4",
        )  # type: ignore[return-value]

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
