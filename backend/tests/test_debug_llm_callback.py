"""The one callback that covers every model call.

`build_llm` is the only place a chat model is constructed, so the panel's
coverage of LLM timing rests entirely on the handler being attached there and on
it surviving a provider that reports usage differently (or not at all).
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.shared.config.models import _debug_callbacks, get_model
from backend.shared.debug.events import DebugEventBus
from backend.shared.debug.llm_callback import DebugLLMCallback


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
    fresh = DebugEventBus(maxlen=50)
    monkeypatch.setattr("backend.shared.debug.events.bus", fresh)
    monkeypatch.setattr("backend.shared.debug.llm_callback.bus", fresh)
    return fresh


@pytest.fixture
def handler():
    return DebugLLMCallback("gemini-flash-lite", "google")


class TestAttachment:
    def test_no_callbacks_when_debug_is_off(self, off):
        assert _debug_callbacks(get_model("gemini-flash-lite")) is None

    def test_one_handler_when_debug_is_on(self, on):
        callbacks = _debug_callbacks(get_model("gemini-flash-lite"))
        assert len(callbacks) == 1
        assert isinstance(callbacks[0], DebugLLMCallback)
        assert callbacks[0].model_id == "gemini-flash-lite"
        assert callbacks[0].provider == "google"


class TestCallLifecycle:
    def test_start_and_end_bracket_the_call(self, on, bus, handler):
        run_id = uuid4()
        handler.on_chat_model_start(
            {}, [[SimpleNamespace(content="review this clause")]], run_id=run_id
        )
        handler.on_llm_end(_result(text="ok", input_tokens=11, output_tokens=3), run_id=run_id)

        start, end = bus.since(0)
        assert start["status"] == "start"
        assert start["fields"]["prompt_chars"] == len("review this clause")
        assert end["status"] == "end"
        assert end["duration_ms"] >= 0
        assert end["fields"]["input_tokens"] == 11
        assert end["fields"]["output_tokens"] == 3

    def test_plain_completion_models_are_measured_too(self, on, bus, handler):
        run_id = uuid4()
        handler.on_llm_start({}, ["abc", "de"], run_id=run_id)
        assert bus.since(0)[0]["fields"]["prompt_chars"] == 5

    def test_an_error_is_reported_with_its_type(self, on, bus, handler):
        run_id = uuid4()
        handler.on_chat_model_start({}, [[SimpleNamespace(content="x")]], run_id=run_id)
        handler.on_llm_error(RuntimeError("429 quota exhausted"), run_id=run_id)

        error = bus.since(0)[-1]
        assert error["status"] == "error"
        assert error["fields"]["error_type"] == "RuntimeError"
        assert "quota" in error["fields"]["error"]

    def test_provider_retries_are_broken_out(self, on, bus, handler):
        """Without this the SDK's own 1s -> 17s backoff is one unexplained stall."""
        retry_state = SimpleNamespace(
            attempt_number=2,
            idle_for=4.0,
            outcome=SimpleNamespace(failed=True, exception=lambda: TimeoutError()),
        )
        handler.on_retry(retry_state, run_id=uuid4())

        event = bus.since(0)[0]
        assert event["step"] == "retry"
        assert event["fields"]["attempt"] == 2
        assert event["fields"]["sleep_s"] == 4.0

    def test_nothing_is_emitted_when_debug_is_off(self, off, bus, handler):
        run_id = uuid4()
        handler.on_chat_model_start({}, [[SimpleNamespace(content="x")]], run_id=run_id)
        handler.on_llm_end(_result(), run_id=run_id)
        handler.on_llm_error(RuntimeError("x"), run_id=run_id)
        assert bus.size() == 0


class TestUsageExtraction:
    def test_openai_shaped_usage(self, on, bus, handler):
        response = SimpleNamespace(
            llm_output={"token_usage": {"prompt_tokens": 7, "completion_tokens": 2}},
            generations=[],
        )
        handler.on_llm_end(response, run_id=uuid4())
        fields = bus.since(0)[0]["fields"]
        assert (fields["input_tokens"], fields["output_tokens"]) == (7, 2)

    def test_a_provider_that_reports_no_usage_still_gets_a_duration(self, on, bus, handler):
        run_id = uuid4()
        handler.on_chat_model_start({}, [[SimpleNamespace(content="x")]], run_id=run_id)
        handler.on_llm_end(SimpleNamespace(llm_output=None, generations=[]), run_id=run_id)

        end = bus.since(0)[-1]
        assert end["status"] == "end"
        assert end["duration_ms"] >= 0
        assert "input_tokens" not in end["fields"]

    def test_a_malformed_response_does_not_break_the_call(self, on, bus, handler):
        """Telemetry must never be the thing that fails a model call."""
        handler.on_llm_end(object(), run_id=uuid4())
        assert bus.since(0)[0]["status"] == "end"


class TestNoPayloadLeaks:
    def test_neither_prompt_nor_completion_text_is_emitted(self, on, bus, handler):
        secret = "CONFIDENTIAL: the Provider shall indemnify"
        run_id = uuid4()
        handler.on_chat_model_start({}, [[SimpleNamespace(content=secret)]], run_id=run_id)
        handler.on_llm_end(_result(text=secret), run_id=run_id)

        assert secret not in str(bus.since(0))


def _result(text: str = "", input_tokens: int = None, output_tokens: int = None):
    usage = {}
    if input_tokens is not None:
        usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    message = SimpleNamespace(usage_metadata=usage)
    generation = SimpleNamespace(text=text, message=message)
    return SimpleNamespace(llm_output=None, generations=[[generation]])
