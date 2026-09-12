"""``trace_step`` — the one thing the pipeline code actually calls.

Wrapping a block emits ``start`` when it opens and ``end`` with a duration when it
closes, or ``error`` with the exception's class and message before re-raising. A
step can attach facts it only learns partway through::

    with trace_step("upload", "pdf_extract", filename=name) as step:
        text = extractor.extract_with_fallback(path)
        step.set(chars=len(text))

Fields set with ``step.set`` ride on the ``end`` event, so the timeline shows the
duration and the number that explains it on the same row.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager, contextmanager
from typing import Any, Dict, Iterator

from backend.shared.debug.events import bus, debug_events_enabled


class Step:
    """Handle yielded by ``trace_step``; collects fields and sub-events."""

    __slots__ = ("phase", "name", "_fields", "_started")

    def __init__(self, phase: str, name: str, fields: Dict[str, Any]):
        self.phase = phase
        self.name = name
        self._fields = dict(fields)
        self._started = time.perf_counter()

    def set(self, **fields: Any) -> None:
        """Attach facts to the step's ``end`` event."""
        self._fields.update(fields)

    def progress(self, **fields: Any) -> None:
        """Emit an ``info`` event mid-step.

        For the long silent stretches — chunk embedding runs one network call per
        chunk — a step that only reports at the end looks identical to a hang.
        """
        if not debug_events_enabled():
            return
        bus.emit(
            self.phase,
            self.name,
            "info",
            fields={"elapsed_ms": round((time.perf_counter() - self._started) * 1000, 1), **fields},
        )

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._started) * 1000


def _open(phase: str, name: str, fields: Dict[str, Any]) -> Step:
    bus.emit(phase, name, "start", fields=fields)
    return Step(phase, name, fields)


def _close_ok(step: Step) -> None:
    bus.emit(step.phase, step.name, "end", duration_ms=step.elapsed_ms, fields=step._fields)


def _close_err(step: Step, exc: BaseException) -> None:
    bus.emit(
        step.phase,
        step.name,
        "error",
        duration_ms=step.elapsed_ms,
        fields={**step._fields, "error_type": type(exc).__name__, "error": str(exc)},
    )


class _NullStep(Step):
    """What ``trace_step`` yields when debug is off: every method a no-op."""

    def __init__(self):  # noqa: D107 - deliberately skips Step.__init__
        self.phase = ""
        self.name = ""
        self._fields = {}
        self._started = 0.0

    def set(self, **fields: Any) -> None:
        pass

    def progress(self, **fields: Any) -> None:
        pass


_NULL = _NullStep()


@contextmanager
def trace_step(phase: str, name: str, /, **fields: Any) -> Iterator[Step]:
    """Time a block of synchronous work and report it to the debug panel.

    `phase` and `name` are positional-only so that a step reporting a fact it
    happens to call "phase" or "name" cannot bind to them instead. The same
    reasoning as `DebugEventBus.emit`: telemetry must not be able to break the
    code it measures, and a real upload failed exactly that way.
    """
    if not debug_events_enabled():
        yield _NULL
        return

    step = _open(phase, name, fields)
    try:
        yield step
    except BaseException as exc:
        _close_err(step, exc)
        raise
    else:
        _close_ok(step)


@asynccontextmanager
async def atrace_step(phase: str, name: str, /, **fields: Any):
    """``trace_step`` for ``async with``."""
    if not debug_events_enabled():
        yield _NULL
        return

    step = _open(phase, name, fields)
    try:
        yield step
    except BaseException as exc:
        _close_err(step, exc)
        raise
    else:
        _close_ok(step)


def note(phase: str, name: str, status: str = "info", /, **fields: Any) -> None:
    """A single point-in-time event, for things with no duration to measure.

    Pass a non-default `status` positionally — ``note("upload", "x", "error")``.
    It is positional-only because `status` is a perfectly reasonable *field*
    name (an upload reports the status it finished with), and a keyword would
    silently relabel the event instead of recording the fact.
    """
    bus.emit(phase, name, status, fields=fields)
