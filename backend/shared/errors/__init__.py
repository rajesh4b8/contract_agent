"""Error types shared across layers."""

from backend.shared.errors.provider_errors import (
    LLMFailure,
    LLMErrorInfo,
    LLMProviderError,
    classify_llm_error,
    describe_llm_error,
    llm_error_payload,
    raise_if_provider_error,
)

__all__ = [
    "LLMFailure",
    "LLMErrorInfo",
    "LLMProviderError",
    "classify_llm_error",
    "describe_llm_error",
    "llm_error_payload",
    "raise_if_provider_error",
]
