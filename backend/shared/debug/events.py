"""An in-memory event bus for the developer debug panel.

The pipeline's slow parts are invisible from the UI: an upload blocks for tens of
seconds and an analysis for minutes, and the only signal is a spinner. The logs
carry the information but are a single stream mixed with uvicorn access lines and
LangChain chatter, so finding the relevant twenty lines costs more than it gives.

This module is the other half of that: a small, curated stream of *named steps*
with durations and a handful of structured facts, which the frontend renders as a
live timeline.

Two design points worth keeping:

* **Off by default, and genuinely free when off.** ``emit`` returns on a single
  boolean check, so instrumenting a hot path costs nothing in a normal run.
* **Thread-safe, not loop-safe.** LangGraph nodes run inside a
  ``ThreadPoolExecutor`` (see ``run_coroutine`` in ``contract_intelligence_agents``),
  so events arrive from threads with no running event loop. A plain lock plus a
  ring buffer that the SSE endpoint polls avoids every cross-thread asyncio hazard;
  ``loop.call_soon_threadsafe`` would be the wrong tool here.
"""
from __future__ import annotations

import os
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# How many events are kept for replay. A full upload + analysis is well under a
# hundred, so this holds several runs of history for a page that reconnects.
BUFFER_SIZE = 1000

# Values we are willing to put on the wire. Anything else — a contract's text, a
# model response, a numpy array — is dropped rather than truncated, so the panel
# cannot become a side channel for document content.
_SCALARS = (str, int, float, bool, type(None))

# Field values are still capped: a "scalar" string can be a whole clause.
_MAX_FIELD_CHARS = 300


def debug_events_enabled() -> bool:
    """Whether the debug event stream is on: ``DEBUG_EVENTS``, and not production.

    The environment check is not belt-and-braces. These events name filenames,
    tenant ids, contract ids and provider errors, and the endpoint that serves
    them is unauthenticated — so "development only" has to be something the code
    enforces, not something the documentation asks for. Setting the flag on a
    production deployment must be inert rather than an exfiltration endpoint.

    Checking here rather than at the router means a misconfigured production
    process does not even *buffer* the events, so there is nothing to leak
    however the endpoints are reached.

    Read per call rather than cached at import so tests (and a reloaded worker)
    can flip it with ``monkeypatch.setenv``. It is two dict lookups.
    """
    if os.getenv("DEBUG_EVENTS", "").strip().lower() not in ("1", "true", "yes", "on"):
        return False
    # Imported lazily to keep this module's imports to the standard library.
    from backend.shared.utils.route_utils import is_production

    return not is_production()


def _clean_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only small scalars. See ``_SCALARS`` for why."""
    cleaned: Dict[str, Any] = {}
    for key, value in fields.items():
        if not isinstance(value, _SCALARS):
            continue
        if isinstance(value, str) and len(value) > _MAX_FIELD_CHARS:
            value = value[:_MAX_FIELD_CHARS] + "…"
        cleaned[str(key)] = value
    return cleaned


class DebugEventBus:
    """A bounded, append-only, thread-safe ring buffer of pipeline events."""

    def __init__(self, maxlen: int = BUFFER_SIZE):
        self._lock = threading.Lock()
        self._events: deque = deque(maxlen=maxlen)
        self._seq = 0

    def emit(
        self,
        phase: str,
        step: str,
        status: str = "info",
        *,
        duration_ms: Optional[float] = None,
        correlation_id: str = "",
        fields: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Record one event. Returns it, or ``None`` when debug is off.

        Fields arrive as a dict rather than ``**kwargs`` on purpose. A traced
        step reports facts chosen by the code being measured, and one of the
        first real ones was called ``status`` — which collided with this
        method's own parameter and failed the upload it was measuring. A dict
        makes that class of bug impossible: no field name can shadow anything.
        """
        if not debug_events_enabled():
            return None

        if not correlation_id:
            # Imported here so this module stays importable in isolation.
            from backend.shared.utils.logger import correlation_id_var

            correlation_id = correlation_id_var.get() or ""

        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "correlation_id": correlation_id,
            "phase": phase,
            "step": step,
            "status": status,
            "fields": _clean_fields(fields or {}),
        }
        if duration_ms is not None:
            event["duration_ms"] = round(float(duration_ms), 1)

        with self._lock:
            self._seq += 1
            event["seq"] = self._seq
            self._events.append(event)
        return event

    def since(self, seq: int = 0) -> List[Dict[str, Any]]:
        """Every buffered event newer than ``seq``, oldest first."""
        with self._lock:
            return [e for e in self._events if e["seq"] > seq]

    def latest_seq(self) -> int:
        with self._lock:
            return self._seq

    def size(self) -> int:
        with self._lock:
            return len(self._events)

    def clear(self) -> None:
        """Drop the buffer. The sequence keeps counting, so a subscriber holding
        an old ``since`` cursor is not silently re-sent events it already saw."""
        with self._lock:
            self._events.clear()


# One bus per process. The buffer is deliberately not shared across workers —
# this is a local development tool, and the alternative is infrastructure.
bus = DebugEventBus()


def emit(phase: str, step: str, status: str = "info", /, **fields: Any) -> Optional[Dict[str, Any]]:
    """Module-level shorthand: every field name is free-form here."""
    return bus.emit(phase, step, status, fields=fields)
