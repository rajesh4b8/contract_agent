"""Single source of truth for user-selectable LLM models.

Add, retire, or re-tier an option here and it propagates everywhere:
  * ``GET /api/models`` (what the frontend dropdowns render)
  * request validation / model resolution in the API + services
  * ``LLMManager`` agent registration

Design notes
------------
* ``ModelOption.id`` is the stable public identifier used by API query params
  and as the frontend ``<option value>``. Keep it human-meaningful and cheap to
  type ("gemini-flash"), decoupled from the provider's real model string.
* ``ModelOption.backend_model`` is the actual string handed to the provider SDK.
  It is env-overridable so a provider retiring a version (e.g. the
  ``gemini-2.5-*`` shutdown for new accounts) is a one-line env change, not a
  code change.
* ``LEGACY_ALIASES`` keeps older ids (persisted in past responses, bookmarked
  URLs, the DB) resolving after the list is trimmed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelOption:
    id: str            # stable public id: API param value + frontend option value
    label: str         # human label for the dropdown
    provider: str      # "google" | "openai" | "anthropic" | "mistral"
    tier: str          # "lite" | "standard" | "premium" (cost/quality band)
    backend_model: str  # actual model string passed to the provider SDK
    description: str = ""
    recommended: bool = False


# Actual provider model strings — override per-deployment via env.
_GEMINI_FLASH_LITE = os.getenv("GEMINI_FLASH_LITE_MODEL", "gemini-flash-lite-latest")
_GEMINI_FLASH = os.getenv("GEMINI_FLASH_MODEL", "gemini-flash-latest")
_GEMINI_PRO = os.getenv("GEMINI_PRO_MODEL", "gemini-pro-latest")
_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
_ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")
_MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-large-latest")


# The curated list. Order = display order. Keep it small and cheap-by-default:
# only the "flash" / "flash-lite" tiers are on by default; "premium" is opt-in.
MODEL_OPTIONS: list[ModelOption] = [
    ModelOption(
        id="gemini-flash-lite",
        label="Gemini Flash Lite — fastest, lowest cost",
        provider="google",
        tier="lite",
        backend_model=_GEMINI_FLASH_LITE,
        description="Cheapest option; good for bulk extraction and high-volume runs.",
    ),
    ModelOption(
        id="gemini-flash",
        label="Gemini Flash — balanced (recommended)",
        provider="google",
        tier="standard",
        backend_model=_GEMINI_FLASH,
        description="Best quality/cost balance for contract analysis.",
        recommended=True,
    ),
    ModelOption(
        id="gemini-pro",
        label="Gemini Pro — highest quality, slower & costlier",
        provider="google",
        tier="premium",
        backend_model=_GEMINI_PRO,
        description="For the hardest documents. Notably pricier; rate-limited on free keys.",
    ),
    # Non-Google options are defined but only surface when their API key is set.
    ModelOption(
        id="gpt-4o",
        label="OpenAI GPT-4o",
        provider="openai",
        tier="premium",
        backend_model=_OPENAI_MODEL,
        description="Requires OPENAI_API_KEY.",
    ),
    ModelOption(
        id="claude-sonnet",
        label="Claude Sonnet",
        provider="anthropic",
        tier="premium",
        backend_model=_ANTHROPIC_MODEL,
        description="Requires ANTHROPIC_API_KEY.",
    ),
]

# Fallback chain / app default. Overridable so ops can pin a cheaper default.
DEFAULT_MODEL_ID = os.getenv("DEFAULT_MODEL_ID", "gemini-flash")

# Old ids that may still arrive from persisted data, cached clients, or bookmarks.
LEGACY_ALIASES: dict[str, str] = {
    "gemini-2.5-flash": "gemini-flash",
    "gemini-2.5-flash-exp": "gemini-flash",
    "gemini-1.5-flash": "gemini-flash",
    "gemini-2.5-pro": "gemini-pro",
    "gemini-1.5-pro": "gemini-pro",
    "sonnet-3.5": "claude-sonnet",
}

_PROVIDER_KEY_ENV: dict[str, tuple[str, ...]] = {
    "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "mistral": ("MISTRAL_API_KEY",),
}

_BY_ID: dict[str, ModelOption] = {m.id: m for m in MODEL_OPTIONS}


def provider_configured(provider: str) -> bool:
    """True when at least one API key for ``provider`` is present in the env."""
    return any(os.getenv(name) for name in _PROVIDER_KEY_ENV.get(provider, ()))


def normalize_model_id(model_id: str | None) -> str:
    """Resolve legacy/blank ids to a current, known id.

    Falls back to ``DEFAULT_MODEL_ID`` for unknown or empty input rather than
    raising, so a stale client value degrades gracefully.
    """
    if not model_id:
        return DEFAULT_MODEL_ID
    if model_id in _BY_ID:
        return model_id
    if model_id in LEGACY_ALIASES:
        return LEGACY_ALIASES[model_id]
    return DEFAULT_MODEL_ID


def get_model(model_id: str | None) -> ModelOption:
    return _BY_ID[normalize_model_id(model_id)]


def resolve_backend_model(model_id: str | None) -> str:
    """Public id -> the actual provider model string."""
    return get_model(model_id).backend_model


def available_models(only_configured: bool = False) -> list[dict[str, Any]]:
    """Serialisable option list for ``GET /api/models``.

    Each entry carries ``available`` (provider key present). When
    ``only_configured`` is True, unavailable options are dropped entirely.
    """
    out: list[dict[str, Any]] = []
    for m in MODEL_OPTIONS:
        is_available = provider_configured(m.provider)
        if only_configured and not is_available:
            continue
        out.append(
            {
                "id": m.id,
                "label": m.label,
                "provider": m.provider,
                "tier": m.tier,
                "description": m.description,
                "recommended": m.recommended,
                "available": is_available,
            }
        )
    return out


def configured_model_ids() -> list[str]:
    """Ids whose provider is configured — what ``LLMManager`` should load."""
    return [m.id for m in MODEL_OPTIONS if provider_configured(m.provider)]


def build_llm(model_id: str | None, *, temperature: float = 0):
    """Construct the raw LangChain chat model for a public model id.

    Centralises provider-SDK construction that was previously duplicated across
    ``llm_manager`` and the document-processing services. Imports are lazy so
    importing this config module stays cheap.
    """
    option = get_model(model_id)
    name = option.backend_model

    if option.provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=name, temperature=temperature)
    if option.provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=name, temperature=temperature)
    if option.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=name, temperature=temperature)
    if option.provider == "mistral":
        from langchain_mistralai import ChatMistralAI

        return ChatMistralAI(model=name)

    raise ValueError(f"Unsupported provider {option.provider!r} for model {option.id!r}")
