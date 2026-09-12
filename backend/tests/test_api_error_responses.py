"""What the browser actually receives when the model refuses the request.

The pieces are tested elsewhere; this is the wire. A reviewer whose Gemini
quota ran out mid-review should get a 429 saying so and naming a model they
can switch to — not a 500 whose body is a provider stack trace, and not a 404
claiming the contract does not exist.
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app

QUOTA_MESSAGE = (
    "429 You exceeded your current quota "
    '[quota_id: "GenerateRequestsPerDayPerProjectPerModel-FreeTier"]'
)

ANALYZE = "/api/intelligence/contracts/contract-1/analyze?model=gemini-flash"


@pytest.fixture
def client():
    # The context manager runs the lifespan, which builds the LLM manager;
    # no provider is contacted until a model is actually called.
    with TestClient(app) as test_client:
        yield test_client


def _analysis_raising(exc: Exception):
    """Patch the intelligence service so analysis fails with `exc`."""
    async def fail(*args, **kwargs):
        raise exc

    patcher = patch(
        "backend.api.contract_intelligence.ContractIntelligenceServiceFactory.create_service"
    )
    factory = patcher.start()
    factory.return_value.analyze_contract_by_id = fail
    return patcher


def test_a_spent_quota_answers_429_with_the_reason(client):
    patcher = _analysis_raising(Exception(QUOTA_MESSAGE))
    try:
        response = client.post(ANALYZE)
    finally:
        patcher.stop()

    assert response.status_code == 429
    body = response.json()
    assert body["error_kind"] == "quota_exceeded"
    assert "gemini-flash" in body["detail"]
    assert "Free ·" in body["detail"]  # the way out, in the message itself


def test_a_rejected_key_is_not_the_caller_s_401(client):
    """401/403 is how this API says "you are not allowed". The reviewer is."""
    patcher = _analysis_raising(Exception("Error code: 401 - invalid api key"))
    try:
        response = client.post(ANALYZE)
    finally:
        patcher.stop()

    assert response.status_code == 503
    assert response.json()["error_kind"] == "auth_invalid"


def test_a_throttled_provider_says_how_long_to_wait(client):
    patcher = _analysis_raising(
        Exception("429 Quota exceeded for quota metric 'requests per minute'. retryDelay: \"25s\"")
    )
    try:
        response = client.post(ANALYZE)
    finally:
        patcher.stop()

    assert response.status_code == 429
    assert response.headers["retry-after"] == "25"
    assert response.json()["retry_after"] == 25


def test_an_unrelated_failure_is_still_a_plain_500(client):
    """Only provider refusals are reclassified; nothing else changes shape."""
    patcher = _analysis_raising(RuntimeError("something else broke"))
    try:
        response = client.post(ANALYZE)
    finally:
        patcher.stop()

    assert response.status_code == 500
    assert "something else broke" in response.json()["detail"]
