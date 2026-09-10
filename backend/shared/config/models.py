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
    provider: str      # "google" | "openai" | "anthropic" | "mistral" | "openrouter"
    tier: str          # "free" | "lite" | "standard" | "premium" (cost/quality band)
    backend_model: str  # actual model string passed to the provider SDK
    description: str = ""
    recommended: bool = False


def _env(name: str, default: str) -> str:
    """Read an env var, treating an empty value as unset.

    docker-compose renders an unset passthrough (``${FOO-}``) as an empty
    string, which `os.getenv(name, default)` would happily return — silently
    replacing the model id with "". Blank means "not configured".
    """
    return os.getenv(name) or default


# Actual provider model strings — override per-deployment via env.
_GEMINI_FLASH_LITE = _env("GEMINI_FLASH_LITE_MODEL", "gemini-flash-lite-latest")
_GEMINI_FLASH = _env("GEMINI_FLASH_MODEL", "gemini-flash-latest")
_GEMINI_PRO = _env("GEMINI_PRO_MODEL", "gemini-pro-latest")
_OPENAI_MODEL = _env("OPENAI_MODEL", "gpt-4o")
_ANTHROPIC_MODEL = _env("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")
_MISTRAL_MODEL = _env("MISTRAL_MODEL", "mistral-large-latest")

# OpenRouter ":free" tiers, for development without burning a paid quota. The
# free line-up changes over time, so these are env-overridable; check
# https://openrouter.ai/api/v1/models for what is currently free.
# Verified callable over the plain API on 2026-09-10. Not every ":free" model
# is: thinkingmachines/inkling returns 403 "only available on agentic
# harnesses", and dots-3-note-preview returns null content.
_OR_SMALL = _env("OPENROUTER_SMALL_MODEL", "google/gemma-4-26b-a4b-it:free")
_OR_LARGE = _env("OPENROUTER_LARGE_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
_OR_LONG = _env("OPENROUTER_LONG_MODEL", "nvidia/nemotron-3.5-lightning:free")


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
    # Free-tier options for development. Gemini's free tier is 20 requests/day
    # per model, which a few analysis runs exhaust; these cost nothing and keep
    # iteration unblocked. Quality is lower than the paid tiers — do not judge
    # extraction accuracy from them.
    ModelOption(
        id="free-large",
        label="Free · Nemotron 3 Super 120B (OpenRouter)",
        provider="openrouter",
        tier="free",
        backend_model=_OR_LARGE,
        description="The free option to use. Reliable for extraction; ~50s per analysis.",
    ),
    ModelOption(
        id="free-small",
        label="Free · Gemma 4 26B (OpenRouter)",
        provider="openrouter",
        tier="free",
        backend_model=_OR_SMALL,
        description="Faster and cleaner JSON, but its free endpoint is often rate-limited upstream.",
    ),
    ModelOption(
        id="free-long",
        label="Free · Nemotron 3.5 Lightning, 1M context (OpenRouter)",
        provider="openrouter",
        tier="free",
        backend_model=_OR_LONG,
        description="1M-token context for very long contracts. Slow — can exceed 200s.",
    ),
]

# Fallback chain / app default. Overridable so ops can pin a cheaper default.
DEFAULT_MODEL_ID = _env("DEFAULT_MODEL_ID", "gemini-flash")

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
    "openrouter": ("OPENROUTER_API_KEY",),
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
    if option.provider == "openrouter":
        # OpenRouter speaks the OpenAI wire protocol, so the OpenAI client works
        # against it with a different base URL and key.
        from langchain_openai import ChatOpenAI

        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError(
                f"Model {option.id!r} needs OPENROUTER_API_KEY. "
                "Get a key at https://openrouter.ai/keys and set it in .env"
            )
        return ChatOpenAI(
            model=name,
            temperature=temperature,
            base_url=_env("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            api_key=api_key,
            # Shared free tiers return transient upstream 429s far more often
            # than paid endpoints. Raise the client's own retry budget (default
            # 2) rather than wrapping a second retry layer around it.
            max_retries=5,
        )

    raise ValueError(f"Unsupported provider {option.provider!r} for model {option.id!r}")
