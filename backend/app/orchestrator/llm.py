"""
LLM provider factory
=====================
Lets each agent (planner / navigator / verifier) use a different provider and model.

All three providers expose an OpenAI-COMPATIBLE chat-completions API, so we reuse the
`openai` SDK and just point it at a different base_url + key:

  - openai : api.openai.com (default)           -> needs OPENAI_API_KEY
  - gemini : Google Generative Language OpenAI   -> needs GEMINI_API_KEY
  - ollama : local Ollama server                 -> no key (runs on your machine)

This means the existing chat.completions.create(...) calls (including vision via
image_url and response_format json_object) work unchanged across providers.

Defaults are OpenAI, so existing behavior is preserved unless a provider is opted into.
"""
from __future__ import annotations

from typing import Tuple

from openai import OpenAI

from ..config import load_settings

# OpenAI-compatible base URLs per provider
_PROVIDER_BASE = {
    "openai": None,  # SDK default (api.openai.com)
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "ollama": "http://localhost:11434/v1",
}


def make_client_and_model(provider: str, model: str, settings) -> Tuple[OpenAI, str]:
    provider = (provider or "openai").strip().lower()

    if provider == "gemini":
        key = settings.gemini_api_key or "missing-gemini-key"
        return OpenAI(api_key=key, base_url=_PROVIDER_BASE["gemini"]), model

    if provider == "ollama":
        base = settings.ollama_base_url or _PROVIDER_BASE["ollama"]
        # Ollama ignores the key, but the SDK requires a non-empty string
        return OpenAI(api_key="ollama", base_url=base), model

    # default: openai (unchanged behavior)
    return OpenAI(api_key=settings.openai_api_key), model


def agent_llm(agent: str) -> Tuple[OpenAI, str]:
    """Return (client, model) for the given agent: 'planner'|'navigator'|'verifier'.
    Reads per-agent provider + model from settings. Defaults to OpenAI."""
    s = load_settings()
    provider = getattr(s, f"{agent}_provider", "openai")
    model = getattr(s, f"model_{agent}", "gpt-4.1-mini")
    return make_client_and_model(provider, model, s)
