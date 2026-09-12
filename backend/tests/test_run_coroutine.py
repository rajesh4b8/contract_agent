"""Sync LangGraph nodes must be able to await things inside FastAPI's loop.

`_pattern_analysis` used a bare `asyncio.run`, which raises "cannot be called
from a running event loop" under the server. That exception propagated out of
the node and failed the whole analysis — extracted clauses included — but only
for contracts complex enough for a pattern to be selected, so it looked like
"this document has no clauses" rather than a crash.
"""
import asyncio

from backend.agents.contract_intelligence_agents import run_coroutine


async def _answer():
    await asyncio.sleep(0)
    return 42


def test_runs_when_no_loop_is_active():
    assert run_coroutine(_answer()) == 42


async def test_runs_when_called_from_inside_a_running_loop():
    """The server case: a sync callee reached from async code."""
    assert await asyncio.to_thread(lambda: run_coroutine(_answer())) == 42


def test_exceptions_propagate():
    async def boom():
        raise ValueError("inner failure")

    try:
        run_coroutine(boom())
    except ValueError as e:
        assert "inner failure" in str(e)
    else:
        raise AssertionError("exception should propagate to the caller")


async def test_the_correlation_id_survives_the_thread_hop():
    """`ThreadPoolExecutor.submit` drops contextvars unless the context is copied.

    Everything the analysis does runs behind this hop, so without the copy the
    request's correlation id is absent from its logs and its debug events — the
    two places you go looking when an analysis takes two minutes.
    """
    from backend.shared.utils.logger import correlation_id_var

    async def read_it():
        return correlation_id_var.get()

    token = correlation_id_var.set("run-abc")
    try:
        seen = await asyncio.to_thread(lambda: run_coroutine(read_it()))
    finally:
        correlation_id_var.reset(token)

    assert seen == "run-abc"
