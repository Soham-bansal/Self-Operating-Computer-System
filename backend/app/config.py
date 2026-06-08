from __future__ import annotations

import os
from enum import Enum
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, StrictStr


class Mode(str, Enum):
    Observe = "Observe"
    Suggest = "Suggest"
    Confirm = "Confirm"
    Autonomous = "Autonomous"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    openai_api_key: Optional[StrictStr] = Field(default=None)
    gemini_api_key: Optional[StrictStr] = Field(default=None)
    ollama_base_url: StrictStr = Field(default="http://localhost:11434/v1")

    # Per-agent provider: "openai" | "gemini" | "ollama"  (default openai = unchanged)
    planner_provider: StrictStr = Field(default="openai")
    navigator_provider: StrictStr = Field(default="openai")
    verifier_provider: StrictStr = Field(default="openai")

    model_planner: StrictStr = Field(default="gpt-4.1-mini")
    model_navigator: StrictStr = Field(default="gpt-4.1-mini")
    model_verifier: StrictStr = Field(default="gpt-4.1-mini")

    omni_url: StrictStr = Field(default="http://127.0.0.1:8010")

    # Web (DOM) mode browser settings
    use_real_chrome_profile: bool = Field(default=False)
    chrome_user_data_dir: Optional[StrictStr] = Field(default=None)
    chrome_profile_dir: StrictStr = Field(default="Default")

    app_host: StrictStr = Field(default="127.0.0.1")
    app_port: int = Field(default=8000, ge=1, le=65535)
    log_level: StrictStr = Field(default="INFO")

    mode: Mode = Field(default=Mode.Observe)


def _get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v


def load_settings() -> Settings:
    # Load .env if present. override=True so edits made in the UI Settings tab are
    # picked up on the next read (the .env file is the source of truth).
    load_dotenv(override=True)

    return Settings(
        openai_api_key=_get_env("OPENAI_API_KEY", None),
        gemini_api_key=_get_env("GEMINI_API_KEY", None),
        ollama_base_url=_get_env("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        planner_provider=_get_env("PLANNER_PROVIDER", "openai"),
        navigator_provider=_get_env("NAVIGATOR_PROVIDER", "openai"),
        verifier_provider=_get_env("VERIFIER_PROVIDER", "openai"),
        model_planner=_get_env("MODEL_PLANNER", "gpt-4.1-mini"),
        model_navigator=_get_env("MODEL_NAVIGATOR", "gpt-4.1-mini"),
        model_verifier=_get_env("MODEL_VERIFIER", "gpt-4.1-mini"),
        omni_url=_get_env("OMNI_URL", "http://127.0.0.1:8010"),
        use_real_chrome_profile=(_get_env("USE_REAL_CHROME_PROFILE", "false") or "false").lower() in ("1", "true", "yes"),
        chrome_user_data_dir=_get_env("CHROME_USER_DATA_DIR", None),
        chrome_profile_dir=_get_env("CHROME_PROFILE_DIR", "Default"),
        app_host=_get_env("APP_HOST", "127.0.0.1"),
        app_port=int(_get_env("APP_PORT", "8000") or "8000"),
        log_level=_get_env("LOG_LEVEL", "INFO"),
        mode=Mode(_get_env("MODE", "Observe") or "Observe"),
    )
