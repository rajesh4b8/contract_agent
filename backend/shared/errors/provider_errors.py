"""Turn a raw LLM-provider exception into something a reviewer can act on.

When the Gemini free-tier quota runs out mid-analysis the SDK raises
``ResourceExhausted: 429 You exceeded your current quota, please check your
plan and billing details ... [violations { quota_metric: ... }]``. Every layer
between that and the browser either flattened it to ``Processing error`` or
swallowed it entirely — an analysis that "succeeded" with zero clauses and no
explanation. The same is true of a 403 from OpenRouter, an expired key, or a
model the provider has retired.

This module is the single place that reads such an exception and answers two
questions: **what happened** and **what do I do about it**.

Design notes
------------
* Classification is duck-typed on purpose — exception *class names*, HTTP
  status codes and message text, never ``import openai``. A provider whose SDK
  is not installed costs nothing, and a new one is usually already covered by
  the generic status-code rules.
* ``classify_llm_error`` returns ``None`` for anything that is not plainly a
  provider failure. A Neo4j ``ServiceUnavailable`` must not be reported as "the
  AI provider is down", so relevance is gated (see ``_looks_llm_related``)
  before any rule runs.
* The HTTP status the API answers with is *not* the provider's status. A bad
  server-side key is not the caller's 401 — see ``_HTTP_STATUS``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterator, Optional


class LLMFailure(str, Enum):
    """Why a model call failed, at the granularity a user can act on."""

    QUOTA_EXCEEDED = "quota_exceeded"
    RATE_LIMITED = "rate_limited"
    AUTH_INVALID = "auth_invalid"
    PERMISSION_DENIED = "permission_denied"
    MODEL_UNAVAILABLE = "model_unavailable"
    CONTEXT_LENGTH = "context_length"
    CONTENT_FILTERED = "content_filtered"
    TIMEOUT = "timeout"
    UNREACHABLE = "unreachable"
    PROVIDER_DOWN = "provider_down"
    NOT_CONFIGURED = "not_configured"


# What the API answers for each failure.
#
# The caller's own credentials are fine in every one of these cases — it is the
# server's provider key, or the provider itself, that is at fault. So an
# invalid or forbidden *provider* key answers 503, never 401/403: those two are
# already how this API says "you are not allowed", and the frontend would send
# the reviewer off to re-authenticate for a problem only an operator can fix.
_HTTP_STATUS: dict[LLMFailure, int] = {
    LLMFailure.QUOTA_EXCEEDED: 429,
    LLMFailure.RATE_LIMITED: 429,
    LLMFailure.AUTH_INVALID: 503,
    LLMFailure.PERMISSION_DENIED: 503,
    LLMFailure.MODEL_UNAVAILABLE: 503,
    LLMFailure.CONTEXT_LENGTH: 422,
    LLMFailure.CONTENT_FILTERED: 422,
    LLMFailure.TIMEOUT: 504,
    LLMFailure.UNREACHABLE: 503,
    LLMFailure.PROVIDER_DOWN: 502,
    LLMFailure.NOT_CONFIGURED: 503,
}

_PROVIDER_LABELS = {
    "google": "Google Gemini",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "mistral": "Mistral",
    "openrouter": "OpenRouter",
}

# How long the raw provider text kept alongside the friendly message may be.
# Enough to identify the failure in a log or a bug report; not the 4KB of
# protobuf violations Google returns.
_DETAIL_LIMIT = 400


@dataclass(frozen=True)
class LLMErrorInfo:
    """A provider failure, described for a person rather than a stack trace."""

    failure: LLMFailure
    message: str                     # user-facing: what happened + what to do
    status_code: int                 # what the API should answer with
    provider: Optional[str] = None   # catalogue provider id, e.g. "google"
    model_id: Optional[str] = None   # public model id, e.g. "gemini-flash"
    retry_after: Optional[int] = None  # seconds, when the provider said so
    detail: str = ""                 # truncated raw provider text, for logs

    def to_dict(self) -> dict[str, Any]:
        """The shape every API error body uses for a model failure."""
        return {
            "detail": self.message,
            "error_kind": self.failure.value,
            "provider": self.provider,
            "model": self.model_id,
            "retry_after": self.retry_after,
            "provider_detail": self.detail,
        }


class LLMProviderError(Exception):
    """A model call failed for a reason we can explain.

    Raised in place of the provider's own exception once it has been
    classified, so the layers above can stop guessing: ``main.py`` has a
    handler that turns it into the right status code and message.
    """

    def __init__(self, info: LLMErrorInfo):
        super().__init__(info.message)
        self.info = info

    @property
    def status_code(self) -> int:
        return self.info.status_code


# --------------------------------------------------------------------------
# Reading the exception
# --------------------------------------------------------------------------

_MAX_CAUSE_DEPTH = 8

# Module roots whose exceptions are, by definition, about talking to a model.
_PROVIDER_MODULE_ROOTS = {
    "openai",
    "anthropic",
    "google",
    "googleapiclient",
    "mistralai",
    "langchain_openai",
    "langchain_google_genai",
    "langchain_anthropic",
    "langchain_mistralai",
    "httpx",
    "httpcore",
    "grpc",
}

# Text that only appears when the message is about a model provider. This is
# what rescues an error someone re-wrapped in a bare ``Exception``.
_TEXT_HINTS = (
    "api key",
    "api_key",
    "quota",
    "rate limit",
    "rate-limit",
    "ratelimit",
    "too many requests",
    "insufficient_quota",
    "credit balance",
    "openrouter",
    "googleapis",
    "generativelanguage",
    "openai",
    "anthropic",
    "gemini",
    "mistral",
    "model not found",
    "context length",
    "context window",
    "safety filter",
    "content filter",
    "block_reason",
    # Our own "no key configured" errors, which arrive as a plain ValueError.
    "wasn't initiated",
    "was not initiated",
)

_STATUS_PATTERNS = (
    re.compile(r"\berror code:\s*([45]\d\d)\b", re.I),
    re.compile(r"^\s*([45]\d\d)\b"),
    re.compile(r"\b(?:status|status_code|http)\W{0,3}([45]\d\d)\b", re.I),
)

_RETRY_PATTERNS = (
    re.compile(r"retry[-_ ]?after[\"']?\s*[:=]?\s*[\"']?(\d+(?:\.\d+)?)", re.I),
    re.compile(r"retry[-_ ]?delay[\"']?\s*[:=]\s*[\"']?(\d+(?:\.\d+)?)s", re.I),
    re.compile(r"(?:retry|try again) in\s+(\d+(?:\.\d+)?)\s*s", re.I),
)


def _causes(exc: BaseException) -> Iterator[BaseException]:
    """The exception and what it was raised from, outermost first."""
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen and len(seen) < _MAX_CAUSE_DEPTH:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _status_of(exc: BaseException) -> Optional[int]:
    """The HTTP status the provider returned, from wherever the SDK put it."""
    for attr in ("status_code", "http_status", "code"):
        value = getattr(exc, attr, None)
        # google.api_core puts an int HTTP code on ``.code``; grpc puts an enum
        # there, hence the strict int check (bool is an int, so exclude it).
        if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
            return value

    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    if isinstance(value, int) and 100 <= value <= 599:
        return value

    text = str(exc)
    for pattern in _STATUS_PATTERNS:
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    return None


def _retry_after_of(exc: BaseException) -> Optional[int]:
    """Seconds the provider asked us to wait, when it said."""
    value = getattr(exc, "retry_after", None)
    if isinstance(value, (int, float)) and value >= 0:
        return int(value)

    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is not None:
        try:
            raw = headers.get("retry-after") or headers.get("Retry-After")
        except Exception:  # noqa: BLE001 - odd header objects are not worth a crash
            raw = None
        if raw is not None:
            try:
                return int(float(raw))
            except (TypeError, ValueError):
                pass

    text = str(exc)
    for pattern in _RETRY_PATTERNS:
        match = pattern.search(text)
        if match:
            return int(float(match.group(1)))
    return None


def _looks_llm_related(exc: BaseException) -> bool:
    """Gate every rule below, so unrelated infrastructure errors pass through.

    Neo4j raises its own ``ServiceUnavailable``; without this, a database
    outage would be reported to the reviewer as a model-provider outage.
    """
    module_root = (type(exc).__module__ or "").split(".")[0].lower()
    if module_root in _PROVIDER_MODULE_ROOTS:
        return True
    text = str(exc).lower()
    return any(hint in text for hint in _TEXT_HINTS)


def _failure_of(exc: BaseException, status: Optional[int]) -> Optional[LLMFailure]:
    """Which failure this one exception represents, if any."""
    name = type(exc).__name__.lower()
    text = str(exc).lower()

    def says(*needles: str) -> bool:
        return any(needle in text for needle in needles)

    # Our own configuration errors, raised before the provider is ever called.
    if (
        says("wasn't initiated", "was not initiated")
        or re.search(r"needs\s+[a-z_]*api_key", text)
        or says("api_key client option must be set", "no api key", "missing api key")
        or says("default credentials were not found")
    ):
        return LLMFailure.NOT_CONFIGURED

    if says("api key not valid", "invalid api key", "incorrect api key", "invalid_api_key",
            "api key expired", "unauthenticated"):
        return LLMFailure.AUTH_INVALID

    # "Your allowance is gone until tomorrow" and "you are going too fast" both
    # arrive as a 429 and both tend to say "quota", but they need opposite
    # advice — so split on the window the provider names rather than on the
    # wording. Gemini puts it in the quota metric (``..._per_model_per_day``),
    # OpenRouter in the message ("free-models-per-day"). The retryDelay is no
    # help: Google returns a ~30s one even when the daily allowance ran out.
    out_of_allowance = (
        says("quota", "insufficient_quota", "billing", "credit balance", "out of credits",
             "resource_exhausted", "resource has been exhausted")
        or "resourceexhausted" in name
    )
    throttled = (
        status == 429 or says("too many requests", "rate limit", "rate-limit") or "ratelimit" in name
    )
    if out_of_allowance or throttled:
        if says("per day", "per-day", "per_day", "daily"):
            return LLMFailure.QUOTA_EXCEEDED
        if says("per minute", "per_minute", "per-minute", "per second", "per_second"):
            return LLMFailure.RATE_LIMITED
        return LLMFailure.QUOTA_EXCEEDED if out_of_allowance else LLMFailure.RATE_LIMITED

    if status == 401 or "authentication" in name:
        return LLMFailure.AUTH_INVALID

    if status == 403 or says("permission denied", "forbidden", "not allowed to", "access denied") \
            or "permissiondenied" in name:
        return LLMFailure.PERMISSION_DENIED

    if says("context length", "context window", "maximum context", "too many tokens",
            "prompt is too long", "request too large", "reduce the length"):
        return LLMFailure.CONTEXT_LENGTH

    if says("safety", "content filter", "content_filter", "content_policy",
            "prohibited_content", "blocked by", "block_reason"):
        return LLMFailure.CONTENT_FILTERED

    if status == 404 or "notfound" in name or says("model not found", "does not exist",
                                                   "is not a valid model", "unknown model",
                                                   "no such model"):
        return LLMFailure.MODEL_UNAVAILABLE

    if status == 504 or "timeout" in name or "deadlineexceeded" in name \
            or says("timed out", "deadline exceeded"):
        return LLMFailure.TIMEOUT

    if "connection" in name or says("connection error", "connection refused",
                                    "failed to establish", "getaddrinfo",
                                    "name or service not known", "temporary failure in name resolution"):
        return LLMFailure.UNREACHABLE

    if (status is not None and 500 <= status <= 599) or says("overloaded", "service unavailable",
                                                             "internal server error", "bad gateway"):
        return LLMFailure.PROVIDER_DOWN

    return None


# --------------------------------------------------------------------------
# Saying what to do about it
# --------------------------------------------------------------------------

def _model_context(model_id: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """``(provider, backend_model)`` for a known public id, else ``(None, None)``."""
    if not model_id:
        return None, None
    try:
        from backend.shared.config.models import find_model

        option = find_model(model_id)
    except Exception:  # noqa: BLE001 - the catalogue must never break error reporting
        return None, None
    if option is None:
        return None, None
    return option.provider, option.backend_model


# Falling back from the model id to the exception itself. Embeddings are not
# in the model catalogue, and a legacy id may no longer resolve, but the
# failure still came from a nameable provider.
_MODULE_PROVIDERS = {
    "openai": "openai",
    "langchain_openai": "openai",
    "anthropic": "anthropic",
    "langchain_anthropic": "anthropic",
    "google": "google",
    "langchain_google_genai": "google",
    "mistralai": "mistral",
    "langchain_mistralai": "mistral",
}

_TEXT_PROVIDERS = (
    # OpenRouter first: it speaks the OpenAI protocol through the OpenAI
    # client, so its exceptions claim the openai module.
    ("openrouter", "openrouter"),
    ("generativelanguage", "google"),
    ("googleapis", "google"),
    ("gemini", "google"),
    ("anthropic", "anthropic"),
    ("openai", "openai"),
    ("mistral", "mistral"),
)


def _provider_from_exception(exc: BaseException) -> Optional[str]:
    text = str(exc).lower()
    for needle, provider in _TEXT_PROVIDERS:
        if needle in text:
            return provider
    return _MODULE_PROVIDERS.get((type(exc).__module__ or "").split(".")[0].lower())


def _key_env(provider: Optional[str]) -> str:
    """The env var an operator should check for this provider."""
    if not provider:
        return "the provider API key"
    try:
        from backend.shared.config.models import provider_key_env

        names = provider_key_env(provider)
    except Exception:  # noqa: BLE001
        names = ()
    return names[0] if names else "the provider API key"


def _where(provider: Optional[str], model_id: Optional[str]) -> str:
    """"Google Gemini (model 'gemini-flash')" — whichever parts we know."""
    label = _PROVIDER_LABELS.get(provider or "", "The AI provider")
    if model_id:
        return f"{label} (model '{model_id}')"
    return label


def _quota_advice(provider: Optional[str]) -> str:
    if provider == "google":
        return (
            "The Gemini free tier allows only a handful of requests per day and resets at "
            "midnight Pacific. Pick one of the 'Free ·' models in the dropdown to keep working, "
            "or enable billing on the key in GOOGLE_API_KEY."
        )
    if provider == "openrouter":
        return (
            "Free OpenRouter models share a daily allowance across everything using this key. "
            "Try another 'Free ·' model, add credit at https://openrouter.ai/credits, or retry later."
        )
    if provider in ("openai", "anthropic", "mistral"):
        return (
            "The account behind this key has no credit left. Top it up, or pick one of the "
            "'Free ·' models in the dropdown to keep working."
        )
    return "Wait for the quota to reset, or choose a different model in the dropdown."


def _message_for(
    failure: LLMFailure,
    provider: Optional[str],
    model_id: Optional[str],
    backend_model: Optional[str],
    retry_after: Optional[int],
) -> str:
    """One sentence on what happened, one on what to do about it."""
    where = _where(provider, model_id)
    key_env = _key_env(provider)

    if failure is LLMFailure.QUOTA_EXCEEDED:
        return f"{where} has no quota left on this API key. {_quota_advice(provider)}"

    if failure is LLMFailure.RATE_LIMITED:
        wait = f" Retry in about {retry_after}s" if retry_after else " Retry in a minute"
        return (
            f"{where} is rate-limiting this key — too many requests in a short window."
            f"{wait}, or switch to a different model in the dropdown."
        )

    if failure is LLMFailure.AUTH_INVALID:
        return (
            f"{where} rejected the API key as invalid or expired. "
            f"Check {key_env} in the server's .env and restart the backend."
        )

    if failure is LLMFailure.PERMISSION_DENIED:
        return (
            f"{where} refused this request: the API key is not allowed to use this model. "
            f"Enable the model for the key's account, or pick a different model in the dropdown."
        )

    if failure is LLMFailure.MODEL_UNAVAILABLE:
        named = f" ('{backend_model}')" if backend_model else ""
        return (
            f"{where} does not offer the requested model{named} — it may have been retired. "
            f"Pick a different model in the dropdown, or update the model id in the server's .env."
        )

    if failure is LLMFailure.CONTEXT_LENGTH:
        return (
            f"The document is longer than {where} can read in one request. "
            "Use the 'Free · Nemotron 3.5 Lightning' option (1M-token context), or split the document."
        )

    if failure is LLMFailure.CONTENT_FILTERED:
        return (
            f"{where} blocked this request with its safety filter, so no analysis was produced. "
            "This is the provider's own filter; a different model may accept the same document."
        )

    if failure is LLMFailure.TIMEOUT:
        return (
            f"{where} did not respond in time. Long contracts on the free tiers regularly "
            "exceed the timeout — retry, or pick a faster model in the dropdown."
        )

    if failure is LLMFailure.UNREACHABLE:
        return (
            f"The server could not reach {where}. Check the backend's network access "
            "and any proxy settings, then retry."
        )

    if failure is LLMFailure.PROVIDER_DOWN:
        return (
            f"{where} returned a server error — the problem is on the provider's side. "
            "This is usually brief: retry, or pick a different model in the dropdown."
        )

    if failure is LLMFailure.NOT_CONFIGURED:
        return (
            f"{where} is not configured on this server: {key_env} is missing, so the model "
            f"could not be loaded. Set it in .env and restart the backend, or pick a model "
            f"whose provider is configured."
        )

    return f"{where} could not complete the request."


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def classify_llm_error(exc: BaseException, model_id: Optional[str] = None) -> Optional[LLMErrorInfo]:
    """Describe ``exc`` as a provider failure, or ``None`` if it is not one.

    ``None`` is the important half of the contract: callers keep their existing
    handling for everything that is genuinely their own bug.
    """
    for cause in _causes(exc):
        # Already classified once, somewhere below. Re-reading our own wording
        # would only risk landing on a different answer than the first pass.
        if isinstance(cause, LLMProviderError):
            return cause.info
        if not _looks_llm_related(cause):
            continue
        status = _status_of(cause)
        failure = _failure_of(cause, status)
        if failure is None:
            continue

        provider, backend_model = _model_context(model_id)
        provider = provider or _provider_from_exception(cause)
        retry_after = _retry_after_of(cause)
        return LLMErrorInfo(
            failure=failure,
            message=_message_for(failure, provider, model_id, backend_model, retry_after),
            status_code=_HTTP_STATUS[failure],
            provider=provider,
            model_id=model_id,
            retry_after=retry_after,
            detail=_truncate(f"{type(cause).__name__}: {cause}"),
        )
    return None


def describe_llm_error(exc: BaseException, model_id: Optional[str] = None,
                       fallback: Optional[str] = None) -> str:
    """The message to show a user for ``exc``, provider failure or not.

    Anything unrecognised falls back to ``str(exc)`` — unchanged from before,
    so this is always safe to swap in where an error was being rendered.
    """
    info = classify_llm_error(exc, model_id)
    if info is not None:
        return info.message
    return fallback or str(exc) or type(exc).__name__


def llm_error_payload(exc: BaseException, model_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    """``LLMErrorInfo.to_dict()`` for a provider failure, else ``None``."""
    info = classify_llm_error(exc, model_id)
    return info.to_dict() if info is not None else None


def raise_if_provider_error(exc: BaseException, model_id: Optional[str] = None) -> None:
    """Re-raise ``exc`` as an ``LLMProviderError`` when it is one.

    Call this at the top of a broad ``except Exception`` that degrades to an
    empty result. Degrading is right for a partial failure; for "the model is
    out of quota" it produces an empty analysis that reads like a clean bill of
    health, which is worse than an error.
    """
    info = classify_llm_error(exc, model_id)
    if info is not None:
        raise LLMProviderError(info) from exc


def _truncate(text: str, limit: int = _DETAIL_LIMIT) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
