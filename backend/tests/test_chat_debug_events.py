"""Time to first token, as reported to the debug panel.

This is the number that decides whether a chat feels broken: everything after
the first token streams, everything before it is a blank box. The stream also
carries `updates` frames and tool-call chunks, so marking the first item of any
kind would report a time-to-first-token that had not happened yet.

`_run_chat_turn` has no other coverage, so the collaborators are stubbed to the
thinnest thing that lets the generator run.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessageChunk

from backend.main import _run_chat_turn
from backend.shared.debug.events import DebugEventBus


@pytest.fixture
def bus(monkeypatch):
    fresh = DebugEventBus(maxlen=100)
    monkeypatch.setenv("DEBUG_EVENTS", "true")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setattr("backend.shared.debug.events.bus", fresh)
    monkeypatch.setattr("backend.shared.debug.instrument.bus", fresh)
    return fresh


def _safe_guard():
    guard = MagicMock()
    guard.return_value.validate.return_value = SimpleNamespace(
        is_safe=True, violation_type=None, message="", metadata={}
    )
    return guard


def _llm_manager(frames):
    """An LLM manager whose model replays `frames` as its stream."""
    async def astream(**_kwargs):
        for frame in frames:
            yield frame

    model = SimpleNamespace(astream=lambda **kwargs: astream(**kwargs))
    return SimpleNamespace(get_model_by_name=lambda _name: model)


async def _drain(frames):
    with patch("backend.main.AuditLogger"), \
         patch("backend.main.PromptGuard", _safe_guard()), \
         patch("backend.main.OutputGuard", _safe_guard()), \
         patch("backend.infrastructure.agent_audit_service.AgentAuditService"):
        async for _ in _run_chat_turn(
            "gemini-flash-lite", "what does clause 4 say?", "[]", _llm_manager(frames)
        ):
            pass


def _steps(bus):
    return [e["step"] for e in bus.since(0) if e["phase"] == "chat"]


@pytest.mark.asyncio
async def test_first_token_waits_for_a_real_token(bus):
    await _drain([
        ("updates", {"assistant": {"messages": []}}),
        ("messages", [AIMessageChunk(content="")]),
        ("messages", [AIMessageChunk(content="Clause 4")]),
        ("messages", [AIMessageChunk(content=" says…")]),
    ])

    steps = _steps(bus)
    assert steps.count("first_token") == 1, "reported once, not per chunk"

    # The two frames before the first real token must not have triggered it.
    seqs = {e["step"]: e["seq"] for e in bus.since(0) if e["step"] in ("stream_started", "first_token")}
    tokens = [e for e in bus.since(0) if e["fields"].get("response_chars") is not None]
    assert seqs["first_token"] > seqs["stream_started"]
    assert tokens and tokens[0]["step"] == "stream_ended"


@pytest.mark.asyncio
async def test_a_stream_with_no_tokens_reports_none(bus):
    """A turn that produced only tool traffic never had a first token."""
    await _drain([
        ("updates", {"tools": {"messages": []}}),
        ("messages", [AIMessageChunk(content="")]),
    ])

    assert "first_token" not in _steps(bus)
    assert "stream_ended" in _steps(bus)


@pytest.mark.asyncio
async def test_the_turn_is_bracketed(bus):
    await _drain([("messages", [AIMessageChunk(content="hi")])])

    steps = _steps(bus)
    assert steps.index("turn_started") < steps.index("stream_started")
    assert steps.index("first_token") < steps.index("stream_ended")
