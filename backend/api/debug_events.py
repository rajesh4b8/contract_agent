"""The debug panel's API.

Mounted at ``/api/debug`` rather than the existing ``/debug`` router because
``frontend/vite.config.ts`` proxies only ``/api`` — the older debug routes are not
reachable from a browser at all.

Deliberately no ``requires_permission`` dependency. The existing ``/debug`` routes
require ``VIEW_AUDIT``, which the development default role (``LEGAL_REVIEWER``)
does not hold, so copying that pattern would produce a panel that is always empty.
The gate is ``DEBUG_EVENTS``: with it unset the two event endpoints do not answer
at all.
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from backend.shared.debug.events import BUFFER_SIZE, bus, debug_events_enabled

router = APIRouter(prefix="/api/debug", tags=["debug"])

# How often the stream checks the ring buffer. Events arrive from worker threads
# with no event loop, so this polls rather than being pushed; 200ms is invisible
# against steps measured in seconds and avoids every cross-thread asyncio hazard.
_POLL_SECONDS = 0.2

# Without traffic a proxy or load balancer will drop an idle SSE connection, and
# an analysis can be silent for a minute at a time.
_HEARTBEAT_SECONDS = 15.0


@router.get("/status")
async def debug_status() -> dict:
    """Whether debug is on. The frontend's only gate for rendering the panel."""
    enabled = debug_events_enabled()
    return {
        "enabled": enabled,
        "buffer": bus.size() if enabled else 0,
        "latest_seq": bus.latest_seq() if enabled else 0,
        "capacity": BUFFER_SIZE,
    }


def _require_enabled() -> None:
    if not debug_events_enabled():
        raise HTTPException(
            status_code=404,
            detail="Debug events are off. Set DEBUG_EVENTS=true in .env and restart the backend.",
        )


@router.get("/events")
async def debug_events(since: int = Query(default=0, ge=0)) -> dict:
    """Replay buffered events, so a page refreshed mid-run is not left blank."""
    _require_enabled()
    events = bus.since(since)
    return {"events": events, "latest_seq": bus.latest_seq()}


@router.get("/events/stream")
async def debug_event_stream(since: int = Query(default=0, ge=0)) -> StreamingResponse:
    """Live event feed. Reconnect with ``?since=<last seq>`` to miss nothing."""
    _require_enabled()

    async def generate() -> AsyncIterator[str]:
        cursor = since
        idle = 0.0
        # Send anything already buffered first: a subscriber that connects a beat
        # after the upload started should still see its opening steps.
        try:
            while True:
                events = bus.since(cursor)
                if events:
                    idle = 0.0
                    for event in events:
                        cursor = event["seq"]
                        yield f"data: {json.dumps(event)}\n\n"
                else:
                    idle += _POLL_SECONDS
                    if idle >= _HEARTBEAT_SECONDS:
                        idle = 0.0
                        yield ": heartbeat\n\n"
                await asyncio.sleep(_POLL_SECONDS)
        except asyncio.CancelledError:
            # The client navigated away or reloaded. Nothing to clean up.
            raise

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx and friends buffer proxied responses by default, which turns
            # a live stream into one delivery at the end.
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/events/clear")
async def clear_debug_events() -> dict:
    """Empty the buffer so the next run starts on a clean timeline."""
    _require_enabled()
    bus.clear()
    return {"cleared": True, "latest_seq": bus.latest_seq()}
