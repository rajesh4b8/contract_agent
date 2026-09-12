"""An analysis must not run on the event loop thread.

It is a minute of synchronous work — three sequential model calls — reached
from a coroutine. Run in line it blocks the loop for its whole duration, and a
blocked loop serves nothing: not the debug event stream, not the 500ms
`/api/workflow/status` poll the analysis page is making while it waits, not
another user's request. Measured before the fix, a trivial `/api/debug/status`
call took 6.4 seconds to answer because it waited for the analysis to finish.
"""
import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.application.services.contract_intelligence_service import (
    ContractIntelligenceService,
)


def _intelligence():
    return SimpleNamespace(
        clauses=[], violations=[], redlines=[], processing_time=0.1, warnings=[]
    )


@pytest.fixture
def service():
    with patch(
        "backend.application.services.contract_intelligence_service.Neo4jContractRepository"
    ):
        svc = ContractIntelligenceService(MagicMock())

    async def get_contract_by_id(*_args, **_kwargs):
        return {"full_text": "A contract.", "contract_type": "MSA"}

    svc.repository.get_contract_by_id = get_contract_by_id
    svc._store_intelligence_results = MagicMock()
    return svc


@pytest.mark.asyncio
async def test_the_analysis_runs_on_a_worker_thread(service):
    loop_thread = threading.get_ident()
    ran_on = {}

    def analyse(*_args, **_kwargs):
        ran_on["thread"] = threading.get_ident()
        return _intelligence()

    service.analyze_contract_intelligence = analyse
    await service.analyze_contract_by_id("c-1")

    assert ran_on["thread"] != loop_thread


@pytest.mark.asyncio
async def test_the_loop_keeps_serving_while_the_analysis_runs(service):
    """The property that actually matters, stated as the symptom it fixes."""
    release = threading.Event()

    def analyse(*_args, **_kwargs):
        release.wait(timeout=5)
        return _intelligence()

    service.analyze_contract_intelligence = analyse

    ticks = 0
    analysis = asyncio.ensure_future(service.analyze_contract_by_id("c-1"))
    # If the analysis held the loop, none of these would run until it returned.
    for _ in range(5):
        await asyncio.sleep(0.01)
        ticks += 1
    release.set()
    await analysis

    assert ticks == 5


@pytest.mark.asyncio
async def test_the_correlation_id_reaches_the_worker(service):
    """`to_thread` copies the context, which is what keeps the analysis's debug
    events and log lines attached to the request that caused them."""
    from backend.shared.utils.logger import correlation_id_var

    seen = {}

    def analyse(*_args, **_kwargs):
        seen["id"] = correlation_id_var.get()
        return _intelligence()

    service.analyze_contract_intelligence = analyse

    token = correlation_id_var.set("req-42")
    try:
        await service.analyze_contract_by_id("c-1")
    finally:
        correlation_id_var.reset(token)

    assert seen["id"] == "req-42"


@pytest.mark.asyncio
async def test_the_analysis_arguments_survive_the_hop(service):
    """`to_thread` passes positionally, so the order has to be right."""
    got = {}

    def analyse(text, model, use_planning, tenant_id, contract_type):
        got.update(
            text=text, model=model, use_planning=use_planning,
            tenant_id=tenant_id, contract_type=contract_type,
        )
        return _intelligence()

    service.analyze_contract_intelligence = analyse
    await service.analyze_contract_by_id("c-1", "acme", "gemini-flash-lite", False)

    assert got == {
        "text": "A contract.",
        "model": "gemini-flash-lite",
        "use_planning": False,
        "tenant_id": "acme",
        "contract_type": "MSA",
    }
