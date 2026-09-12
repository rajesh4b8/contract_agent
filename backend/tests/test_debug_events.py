"""The debug event bus and `trace_step`.

These run offline: the bus is plain Python, which is the point — the panel's
correctness should not need a stack to check.
"""
import threading

import pytest

from backend.shared.debug.events import DebugEventBus, debug_events_enabled
from backend.shared.debug.instrument import atrace_step, note, trace_step


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("DEBUG_EVENTS", "true")
    # Explicit, because the flag alone is not enough: production ignores it.
    monkeypatch.setenv("ENVIRONMENT", "development")


@pytest.fixture
def off(monkeypatch):
    monkeypatch.delenv("DEBUG_EVENTS", raising=False)


@pytest.fixture
def bus(monkeypatch):
    """A bus of our own, swapped in everywhere the instrumentation reaches it."""
    fresh = DebugEventBus(maxlen=5)
    monkeypatch.setattr("backend.shared.debug.events.bus", fresh)
    monkeypatch.setattr("backend.shared.debug.instrument.bus", fresh)
    return fresh


class TestEnableFlag:
    @pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on"])
    def test_recognised_truthy_values(self, monkeypatch, value):
        monkeypatch.setenv("DEBUG_EVENTS", value)
        assert debug_events_enabled() is True

    @pytest.mark.parametrize("value", ["false", "0", "", "no", "off", "maybe"])
    def test_everything_else_is_off(self, monkeypatch, value):
        monkeypatch.setenv("DEBUG_EVENTS", value)
        assert debug_events_enabled() is False

    def test_unset_is_off(self, off):
        assert debug_events_enabled() is False

    def test_production_ignores_the_flag(self, monkeypatch):
        """The events name filenames, tenants and contract ids, and the endpoint
        serving them is unauthenticated. "Development only" has to be enforced,
        not documented — a flag set by mistake in production must be inert."""
        monkeypatch.setenv("DEBUG_EVENTS", "true")
        monkeypatch.setenv("ENVIRONMENT", "production")
        assert debug_events_enabled() is False

    def test_production_buffers_nothing(self, monkeypatch, bus):
        """Checked at the gate rather than at the router, so there is nothing to
        leak however the endpoints are reached."""
        monkeypatch.setenv("DEBUG_EVENTS", "true")
        monkeypatch.setenv("ENVIRONMENT", "production")
        with trace_step("upload", "read_file") as step:
            step.set(filename="client-contract.pdf")
        note("upload", "received", tenant="acme")
        assert bus.size() == 0

    def test_development_with_the_flag_is_on(self, monkeypatch):
        monkeypatch.setenv("DEBUG_EVENTS", "true")
        monkeypatch.setenv("ENVIRONMENT", "development")
        assert debug_events_enabled() is True

    def test_emit_is_a_no_op_when_off(self, off, bus):
        assert bus.emit("upload", "read_file", "start") is None
        assert bus.size() == 0


class TestBus:
    def test_seq_is_monotonic(self, on, bus):
        for _ in range(3):
            bus.emit("upload", "step", "info")
        assert [e["seq"] for e in bus.since(0)] == [1, 2, 3]

    def test_since_returns_only_newer_events(self, on, bus):
        for _ in range(3):
            bus.emit("upload", "step", "info")
        assert [e["seq"] for e in bus.since(2)] == [3]
        assert bus.since(3) == []

    def test_buffer_is_bounded_but_seq_keeps_counting(self, on, bus):
        for _ in range(8):
            bus.emit("upload", "step", "info")
        assert bus.size() == 5
        # The oldest three were dropped; the cursor still means what it meant.
        assert [e["seq"] for e in bus.since(0)] == [4, 5, 6, 7, 8]
        assert bus.latest_seq() == 8

    def test_clear_keeps_the_sequence(self, on, bus):
        bus.emit("upload", "step", "info")
        bus.clear()
        assert bus.size() == 0
        # A subscriber holding seq=1 must not be re-sent events it already saw.
        assert bus.latest_seq() == 1
        bus.emit("upload", "step", "info")
        assert bus.since(1)[0]["seq"] == 2

    def test_emit_is_safe_from_worker_threads(self, on, bus):
        """LangGraph nodes run in a ThreadPoolExecutor, so this is the real case."""
        big = DebugEventBus(maxlen=1000)
        errors = []

        def hammer():
            try:
                for _ in range(50):
                    big.emit("analysis", "step", "info")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        seqs = [e["seq"] for e in big.since(0)]
        assert len(seqs) == 400
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == 400


class TestFieldSafety:
    def test_non_scalar_fields_are_dropped(self, on, bus):
        """A contract's clause list must not be able to reach the panel."""
        bus.emit("upload", "step", "info", fields={"chunks": 4, "clauses": [{"text": "..."}], "ok": True})
        fields = bus.since(0)[0]["fields"]
        assert fields == {"chunks": 4, "ok": True}

    def test_long_strings_are_truncated(self, on, bus):
        bus.emit("upload", "step", "info", fields={"note": "x" * 500})
        assert len(bus.since(0)[0]["fields"]["note"]) == 301


class TestTraceStep:
    def test_emits_start_then_end_with_a_duration(self, on, bus):
        with trace_step("upload", "pdf_extract", filename="a.pdf"):
            pass
        start, end = bus.since(0)
        assert (start["status"], start["step"]) == ("start", "pdf_extract")
        assert end["status"] == "end"
        assert end["duration_ms"] >= 0
        assert end["fields"]["filename"] == "a.pdf"

    def test_fields_set_inside_the_block_ride_on_the_end_event(self, on, bus):
        with trace_step("upload", "pdf_extract") as step:
            step.set(chars=1058)
        assert bus.since(0)[-1]["fields"]["chars"] == 1058

    def test_progress_emits_mid_step(self, on, bus):
        with trace_step("upload", "embed") as step:
            step.progress(done=12, total=48)
        statuses = [e["status"] for e in bus.since(0)]
        assert statuses == ["start", "info", "end"]
        assert bus.since(0)[1]["fields"]["done"] == 12

    def test_an_exception_is_reported_and_re_raised(self, on, bus):
        with pytest.raises(ValueError, match="boom"):
            with trace_step("upload", "pdf_extract"):
                raise ValueError("boom")
        error = bus.since(0)[-1]
        assert error["status"] == "error"
        assert error["fields"]["error_type"] == "ValueError"
        assert error["fields"]["error"] == "boom"

    def test_no_events_when_debug_is_off(self, off, bus):
        with trace_step("upload", "pdf_extract") as step:
            step.set(chars=1)
            step.progress(done=1)
        note("upload", "anything")
        assert bus.size() == 0

    @pytest.mark.asyncio
    async def test_async_variant(self, on, bus):
        async with atrace_step("upload", "chunking") as step:
            step.set(chunks=3)
        assert [e["status"] for e in bus.since(0)] == ["start", "end"]
        assert bus.since(0)[-1]["fields"]["chunks"] == 3

    @pytest.mark.asyncio
    async def test_async_variant_reports_errors(self, on, bus):
        with pytest.raises(RuntimeError):
            async with atrace_step("upload", "chunking"):
                raise RuntimeError("nope")
        assert bus.since(0)[-1]["status"] == "error"


class TestCorrelation:
    def test_events_carry_the_request_correlation_id(self, on, bus):
        from backend.shared.utils.logger import correlation_id_var

        token = correlation_id_var.set("abc-123")
        try:
            bus.emit("upload", "step", "info")
        finally:
            correlation_id_var.reset(token)
        assert bus.since(0)[0]["correlation_id"] == "abc-123"

    def test_a_missing_correlation_id_is_empty_not_an_error(self, on, bus):
        bus.emit("upload", "step", "info")
        assert bus.since(0)[0]["correlation_id"] == ""


class TestFieldNamesCannotShadowTheEnvelope:
    """A live upload failed with `emit() got multiple values for 'status'`.

    A traced step reports facts chosen by the code being measured, and one of
    the first real ones was called `status`. Telemetry breaking the thing it
    measures is the worst failure this module can have, so the shape is pinned.
    """

    @pytest.mark.parametrize(
        "name", ["status", "phase", "step", "duration_ms", "correlation_id", "seq", "ts", "fields"]
    )
    def test_a_field_named_after_an_envelope_key_is_harmless(self, on, bus, name):
        with trace_step("upload", "process_pdf") as step:
            step.set(**{name: "success"})
        end = bus.since(0)[-1]
        assert end["status"] == "end"
        assert end["fields"][name] == "success"

    def test_note_can_report_an_error_without_a_duration(self, on, bus):
        note("analysis", "extract_clauses", "error", error="timed out")
        event = bus.since(0)[0]
        assert event["status"] == "error"
        assert event["fields"]["error"] == "timed out"

    def test_a_note_field_called_status_stays_a_field(self, on, bus):
        """The upload's own completion event reports `status=success`.

        `status` is positional-only on `note` precisely so this records the
        fact instead of relabelling the event as a "success"-status event.
        """
        note("upload", "completed", status="success", contract_id="c-1")
        event = bus.since(0)[0]
        assert event["status"] == "info"
        assert event["fields"]["status"] == "success"

    @pytest.mark.parametrize("name", ["phase", "name", "status"])
    def test_trace_step_field_names_cannot_bind_to_its_parameters(self, on, bus, name):
        with trace_step("upload", "process_pdf", **{name: "x"}):
            pass
        start = bus.since(0)[0]
        assert (start["phase"], start["step"]) == ("upload", "process_pdf")
        assert start["fields"][name] == "x"
