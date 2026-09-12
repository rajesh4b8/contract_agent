"""The `/api/debug` endpoints the panel talks to.

The gate matters as much as the payload: with `DEBUG_EVENTS` unset the status
endpoint must still answer (so the frontend can decide not to render) while the
event endpoints must not.
"""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from backend.api.debug_events import debug_event_stream
from backend.main import app
from backend.shared.debug.events import DebugEventBus


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def bus(monkeypatch):
    fresh = DebugEventBus(maxlen=50)
    monkeypatch.setattr("backend.shared.debug.events.bus", fresh)
    monkeypatch.setattr("backend.shared.debug.instrument.bus", fresh)
    monkeypatch.setattr("backend.api.debug_events.bus", fresh)
    return fresh


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("DEBUG_EVENTS", "true")


@pytest.fixture
def off(monkeypatch):
    monkeypatch.delenv("DEBUG_EVENTS", raising=False)


class TestStatus:
    def test_reports_off_without_the_flag(self, client, off, bus):
        body = client.get("/api/debug/status").json()
        assert body["enabled"] is False
        assert body["buffer"] == 0

    def test_reports_on_with_the_flag(self, client, on, bus):
        bus.emit("upload", "read_file", "start")
        body = client.get("/api/debug/status").json()
        assert body["enabled"] is True
        assert body["buffer"] == 1
        assert body["latest_seq"] == 1

    def test_needs_no_role_header(self, client, on, bus):
        """The dev default role lacks VIEW_AUDIT; requiring it would mean an
        always-empty panel, which is how the older /debug routes behave."""
        assert client.get("/api/debug/status").status_code == 200


class TestEvents:
    def test_404_when_debug_is_off(self, client, off, bus):
        response = client.get("/api/debug/events")
        assert response.status_code == 404
        assert "DEBUG_EVENTS" in response.json()["detail"]

    def test_stream_404s_when_debug_is_off(self, client, off, bus):
        assert client.get("/api/debug/events/stream").status_code == 404

    def test_replays_the_buffer(self, client, on, bus):
        bus.emit("upload", "read_file", "start")
        bus.emit("upload", "read_file", "end", duration_ms=12.3, fields={"bytes": 2048})

        body = client.get("/api/debug/events").json()
        assert [e["step"] for e in body["events"]] == ["read_file", "read_file"]
        assert body["events"][1]["duration_ms"] == 12.3
        assert body["events"][1]["fields"]["bytes"] == 2048
        assert body["latest_seq"] == 2

    def test_since_returns_only_newer_events(self, client, on, bus):
        for _ in range(3):
            bus.emit("analysis", "extract_clauses", "info")
        body = client.get("/api/debug/events?since=2").json()
        assert [e["seq"] for e in body["events"]] == [3]

    def test_negative_cursor_is_rejected(self, client, on, bus):
        assert client.get("/api/debug/events?since=-1").status_code == 422

    def test_clear_empties_the_buffer(self, client, on, bus):
        bus.emit("upload", "read_file", "start")
        assert client.post("/api/debug/events/clear").json()["cleared"] is True
        assert bus.size() == 0


class TestStream:
    """The SSE generator, exercised directly.

    Going through TestClient would mean reading from a response that never ends,
    which deadlocks its portal on teardown. The generator is the thing worth
    testing anyway: the route around it is three lines of headers.
    """

    @pytest.mark.asyncio
    async def test_sends_buffered_events_first(self, on, bus):
        bus.emit("upload", "read_file", "start", fields={"bytes": 10})
        bus.emit("upload", "read_file", "end", duration_ms=1.0)

        seen = await _drain(await debug_event_stream(since=0), count=2)

        assert [e["status"] for e in seen] == ["start", "end"]
        assert seen[0]["fields"]["bytes"] == 10

    @pytest.mark.asyncio
    async def test_since_skips_what_the_client_already_has(self, on, bus):
        bus.emit("upload", "read_file", "start")
        bus.emit("upload", "read_file", "end")

        seen = await _drain(await debug_event_stream(since=1), count=1)

        assert [e["seq"] for e in seen] == [2]

    @pytest.mark.asyncio
    async def test_picks_up_events_emitted_after_the_client_connected(self, on, bus):
        """The whole point: a step that starts mid-stream must reach the panel."""
        response = await debug_event_stream(since=0)
        iterator = response.body_iterator

        bus.emit("analysis", "extract_clauses", "start")
        chunk = await asyncio.wait_for(iterator.__anext__(), timeout=5)

        assert json.loads(chunk[len("data: "):])["step"] == "extract_clauses"
        await iterator.aclose()

    @pytest.mark.asyncio
    async def test_sets_the_streaming_headers(self, on, bus):
        response = await debug_event_stream(since=0)
        assert response.media_type == "text/event-stream"
        assert response.headers["cache-control"] == "no-cache"
        # Proxies buffer by default, which turns a live stream into one delivery
        # at the end.
        assert response.headers["x-accel-buffering"] == "no"
        await response.body_iterator.aclose()


async def _drain(response, count: int) -> list:
    """Pull `count` data frames off a StreamingResponse, then close it."""
    iterator = response.body_iterator
    seen = []
    try:
        while len(seen) < count:
            chunk = await asyncio.wait_for(iterator.__anext__(), timeout=5)
            if chunk.startswith("data: "):
                seen.append(json.loads(chunk[len("data: "):]))
    finally:
        await iterator.aclose()
    return seen
