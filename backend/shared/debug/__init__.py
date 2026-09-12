"""Developer debug instrumentation — off unless ``DEBUG_EVENTS`` is set.

See ``events.py`` for why this exists alongside the logs.
"""
from backend.shared.debug.events import bus, debug_events_enabled, emit
from backend.shared.debug.instrument import Step, atrace_step, note, trace_step

__all__ = [
    "bus",
    "debug_events_enabled",
    "emit",
    "trace_step",
    "atrace_step",
    "note",
    "Step",
]
