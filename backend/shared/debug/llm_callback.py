"""One callback handler, attached once, that sees every model call.

``build_llm`` in ``backend/shared/config/models.py`` is the only place a chat model
is constructed, so a handler attached there covers clause extraction, policy
checking, redline drafting, the upload's parties/dates call, chat and search —
without touching any of those call sites.

It also catches time nothing else reports: the provider SDKs do their own
transient-retry backoff (google-genai walks 1s → 17s), which from the outside
looks like one slow call. ``on_retry`` breaks that out.

What it reports: model, provider, prompt size, duration, token counts when the
provider returns them. What it never reports: prompts or completions. The panel
is a timing tool, not a transcript — Phoenix at :6006 already has the traces.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional
from uuid import UUID

from langchain_core.callbacks.base import BaseCallbackHandler

from backend.shared.debug.events import bus, debug_events_enabled


class DebugLLMCallback(BaseCallbackHandler):
    """Emits ``llm`` phase events for every model invocation."""

    def __init__(self, model_id: str, provider: str):
        self.model_id = model_id
        self.provider = provider
        self._lock = threading.Lock()
        self._started: Dict[UUID, float] = {}

    # -- helpers ---------------------------------------------------------

    def _begin(self, run_id: UUID, chars: int) -> None:
        if not debug_events_enabled():
            return
        with self._lock:
            self._started[run_id] = time.perf_counter()
        bus.emit(
            "llm",
            "call",
            "start",
            fields={"model": self.model_id, "provider": self.provider, "prompt_chars": chars},
        )

    def _elapsed_ms(self, run_id: UUID) -> Optional[float]:
        with self._lock:
            start = self._started.pop(run_id, None)
        return None if start is None else (time.perf_counter() - start) * 1000

    # -- langchain hooks -------------------------------------------------

    def on_llm_start(self, serialized: Any, prompts: List[str], *, run_id: UUID, **kwargs: Any) -> None:
        self._begin(run_id, sum(len(p) for p in prompts or []))

    def on_chat_model_start(self, serialized: Any, messages: Any, *, run_id: UUID, **kwargs: Any) -> None:
        chars = 0
        for batch in messages or []:
            for message in batch or []:
                content = getattr(message, "content", "")
                chars += len(content) if isinstance(content, str) else len(str(content))
        self._begin(run_id, chars)

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        if not debug_events_enabled():
            return
        fields: Dict[str, Any] = {"model": self.model_id, "provider": self.provider}
        fields.update(_token_counts(response))
        fields["response_chars"] = _response_chars(response)
        bus.emit("llm", "call", "end", duration_ms=self._elapsed_ms(run_id), fields=fields)

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        if not debug_events_enabled():
            return
        bus.emit(
            "llm",
            "call",
            "error",
            duration_ms=self._elapsed_ms(run_id),
            fields={
                "model": self.model_id,
                "provider": self.provider,
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )

    def on_retry(self, retry_state: Any, *, run_id: UUID, **kwargs: Any) -> None:
        """The SDK's own backoff. Without this it reads as one unexplained stall."""
        if not debug_events_enabled():
            return
        outcome = getattr(retry_state, "outcome", None)
        exc = outcome.exception() if outcome is not None and outcome.failed else None
        bus.emit(
            "llm",
            "retry",
            "info",
            fields={
                "model": self.model_id,
                "provider": self.provider,
                "attempt": getattr(retry_state, "attempt_number", None),
                "sleep_s": getattr(retry_state, "idle_for", None),
                "error_type": type(exc).__name__ if exc else None,
            },
        )


def _token_counts(response: Any) -> Dict[str, Any]:
    """Token usage, wherever this provider happens to have put it.

    OpenAI-shaped clients report it on ``llm_output``; Gemini puts it on the
    message's ``usage_metadata``. Neither is guaranteed, so every lookup is
    defensive — a missing count must not break the call it is measuring.
    """
    try:
        llm_output = getattr(response, "llm_output", None) or {}
        usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
        if usage:
            return {
                "input_tokens": usage.get("prompt_tokens") or usage.get("input_tokens"),
                "output_tokens": usage.get("completion_tokens") or usage.get("output_tokens"),
            }

        generations = getattr(response, "generations", None) or []
        for batch in generations:
            for generation in batch or []:
                message = getattr(generation, "message", None)
                usage = getattr(message, "usage_metadata", None) or {}
                if usage:
                    return {
                        "input_tokens": usage.get("input_tokens"),
                        "output_tokens": usage.get("output_tokens"),
                    }
    except Exception:  # noqa: BLE001 - telemetry must never break the pipeline
        pass
    return {}


def _response_chars(response: Any) -> Optional[int]:
    try:
        total = 0
        for batch in getattr(response, "generations", None) or []:
            for generation in batch or []:
                total += len(getattr(generation, "text", "") or "")
        return total
    except Exception:  # noqa: BLE001
        return None
