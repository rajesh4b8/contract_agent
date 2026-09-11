"""A model that refuses the request must say so, in words a reviewer can act on.

Every one of these failures used to reach the browser as "Processing error" —
or, worse, as an analysis with no clauses and no explanation, which reads like
a contract with nothing wrong in it.
"""
import pytest

from backend.shared.errors import (
    LLMFailure,
    LLMProviderError,
    classify_llm_error,
    describe_llm_error,
    raise_if_provider_error,
)


# Stand-ins for the provider SDK exceptions, matched the way the classifier
# matches them: module, class name, status attribute and message text. Using
# fakes keeps the suite offline and independent of which SDKs are installed.
class ResourceExhausted(Exception):
    """google.api_core.exceptions.ResourceExhausted"""
    __module__ = "google.api_core.exceptions"
    code = 429


class RateLimitError(Exception):
    """openai.RateLimitError — also what the OpenRouter client raises."""
    __module__ = "openai"
    status_code = 429


class AuthenticationError(Exception):
    __module__ = "openai"
    status_code = 401


class PermissionDeniedError(Exception):
    __module__ = "openai"
    status_code = 403


class NotFoundError(Exception):
    __module__ = "openai"
    status_code = 404


class APITimeoutError(Exception):
    __module__ = "openai"


class InternalServerError(Exception):
    __module__ = "openai"
    status_code = 503


class Neo4jServiceUnavailable(Exception):
    """neo4j.exceptions.ServiceUnavailable — not a model failure at all."""
    __module__ = "neo4j.exceptions"


GEMINI_DAILY_QUOTA = ResourceExhausted(
    '429 You exceeded your current quota, please check your plan and billing details. '
    '[violations { quota_metric: "generativelanguage.googleapis.com/generate_requests" '
    'quota_id: "GenerateRequestsPerDayPerProjectPerModel-FreeTier" }] retryDelay: "27s"'
)


class TestWhatWentWrong:
    def test_a_spent_daily_quota_is_a_quota_failure(self):
        info = classify_llm_error(GEMINI_DAILY_QUOTA, "gemini-flash")

        assert info.failure is LLMFailure.QUOTA_EXCEEDED

    def test_a_per_minute_limit_is_throttling_not_exhaustion(self):
        """Same 429, same word "quota", opposite advice: one is worth retrying."""
        exc = ResourceExhausted(
            "429 Quota exceeded for quota metric 'Generate requests per minute'. "
            'retryDelay: "31s"'
        )

        info = classify_llm_error(exc, "gemini-flash")

        assert info.failure is LLMFailure.RATE_LIMITED
        assert info.retry_after == 31

    def test_a_daily_window_beats_the_retry_delay(self):
        """Google returns a ~30s retryDelay even when the day's allowance is gone."""
        info = classify_llm_error(GEMINI_DAILY_QUOTA, "gemini-flash")

        assert info.failure is LLMFailure.QUOTA_EXCEEDED
        assert info.retry_after == 27  # reported, but not mistaken for throttling

    def test_an_openrouter_free_tier_day_limit(self):
        exc = RateLimitError(
            "Error code: 429 - {'error': {'message': 'Rate limit exceeded: "
            "free-models-per-day', 'code': 429}}"
        )

        assert classify_llm_error(exc, "free-large").failure is LLMFailure.QUOTA_EXCEEDED

    def test_a_rejected_key(self):
        exc = AuthenticationError("Error code: 401 - {'error': {'message': 'No auth credentials found'}}")

        assert classify_llm_error(exc, "free-large").failure is LLMFailure.AUTH_INVALID

    def test_a_forbidden_model(self):
        exc = PermissionDeniedError(
            "Error code: 403 - {'error': {'message': 'This model is only available "
            "on agentic harnesses'}}"
        )

        assert classify_llm_error(exc, "free-small").failure is LLMFailure.PERMISSION_DENIED

    def test_a_retired_model(self):
        exc = NotFoundError("Error code: 404 - {'error': {'message': 'No endpoints found'}}")

        assert classify_llm_error(exc, "free-large").failure is LLMFailure.MODEL_UNAVAILABLE

    def test_a_timeout(self):
        assert classify_llm_error(APITimeoutError("Request timed out."), "free-long").failure \
            is LLMFailure.TIMEOUT

    def test_a_provider_outage(self):
        exc = InternalServerError("Error code: 503 - Service temporarily overloaded")

        assert classify_llm_error(exc, "gemini-flash").failure is LLMFailure.PROVIDER_DOWN

    def test_a_provider_with_no_key_configured(self):
        """`LLMManager` raises this before the provider is ever called."""
        exc = ValueError("The model free-large wasn't initiated")

        assert classify_llm_error(exc, "free-large").failure is LLMFailure.NOT_CONFIGURED

    def test_a_reason_survives_being_rewrapped(self):
        """Intermediate layers wrap the cause in a bare Exception; keep reading."""
        try:
            raise GEMINI_DAILY_QUOTA
        except Exception as cause:
            wrapped = Exception(f"Failed to initialize intelligence system: {cause}")
            wrapped.__cause__ = cause

        assert classify_llm_error(wrapped, "gemini-flash").failure is LLMFailure.QUOTA_EXCEEDED


class TestWhatIsNotAModelFailure:
    """The classifier's silence is what keeps existing handling intact."""

    def test_a_database_outage_is_not_the_model_provider(self):
        exc = Neo4jServiceUnavailable("Unable to retrieve routing information")

        assert classify_llm_error(exc, "gemini-flash") is None

    def test_a_malformed_model_response_is_our_problem_not_the_provider_s(self):
        assert classify_llm_error(ValueError("Invalid json output: {oops"), "gemini-flash") is None

    def test_a_missing_file_is_not_a_model_failure(self):
        assert classify_llm_error(FileNotFoundError("/tmp/contract.pdf"), "gemini-flash") is None

    def test_an_unrecognised_error_keeps_its_own_message(self):
        exc = FileNotFoundError("No such file: /tmp/contract.pdf")

        assert describe_llm_error(exc, "gemini-flash") == str(exc)

    def test_the_fallback_wins_when_one_is_offered(self):
        exc = ValueError("some internal detail")

        assert describe_llm_error(exc, "gemini-flash", fallback="Analysis failed") == "Analysis failed"


class TestWhatTheUserIsTold:
    def test_the_message_names_the_model_and_what_to_do(self):
        info = classify_llm_error(GEMINI_DAILY_QUOTA, "gemini-flash")

        assert "gemini-flash" in info.message
        assert "Gemini" in info.message
        assert "Free ·" in info.message  # the way out, not just the diagnosis

    def test_a_key_problem_names_the_variable_to_fix(self):
        exc = AuthenticationError("Error code: 401 - invalid api key")

        assert "OPENROUTER_API_KEY" in classify_llm_error(exc, "free-large").message

    def test_the_provider_s_own_text_is_kept_for_the_log_not_the_user(self):
        info = classify_llm_error(GEMINI_DAILY_QUOTA, "gemini-flash")

        assert "quota_metric" in info.detail
        assert "quota_metric" not in info.message
        assert len(info.detail) <= 400

    def test_an_unknown_model_id_falls_back_to_the_provider_in_the_error(self):
        """A retired id resolves to nothing in the catalogue, but the failure
        still came from somebody nameable."""
        info = classify_llm_error(GEMINI_DAILY_QUOTA, "some-model-we-retired")

        assert info.provider == "google"
        assert "Gemini" in info.message

    def test_a_failure_with_no_model_id_at_all_is_still_explained(self):
        """Embeddings are not in the model catalogue; search calls them anyway."""
        info = classify_llm_error(GEMINI_DAILY_QUOTA)

        assert info.failure is LLMFailure.QUOTA_EXCEEDED
        assert "GOOGLE_API_KEY" in info.message or "Gemini" in info.message


class TestTheStatusTheApiAnswersWith:
    def test_a_spent_quota_is_a_429(self):
        assert classify_llm_error(GEMINI_DAILY_QUOTA, "gemini-flash").status_code == 429

    @pytest.mark.parametrize("exc", [
        AuthenticationError("Error code: 401 - invalid api key"),
        PermissionDeniedError("Error code: 403 - forbidden"),
    ])
    def test_a_server_side_key_problem_is_never_the_caller_s_401_or_403(self, exc):
        """401/403 is how this API says "you are not allowed"; a bad provider
        key is not the reviewer's fault and must not send them to re-log-in."""
        assert classify_llm_error(exc, "free-large").status_code == 503

    def test_the_payload_carries_the_kind_and_the_wait(self):
        payload = classify_llm_error(
            ResourceExhausted("429 rate limit per minute exceeded, retryDelay: \"20s\""),
            "gemini-flash",
        ).to_dict()

        assert payload["error_kind"] == "rate_limited"
        assert payload["retry_after"] == 20
        assert payload["model"] == "gemini-flash"
        assert payload["provider"] == "google"


class TestRaiseIfProviderError:
    def test_a_provider_failure_becomes_a_typed_error(self):
        with pytest.raises(LLMProviderError) as caught:
            raise_if_provider_error(GEMINI_DAILY_QUOTA, "gemini-flash")

        assert caught.value.status_code == 429
        assert caught.value.info.failure is LLMFailure.QUOTA_EXCEEDED

    def test_an_already_classified_failure_is_passed_through_unchanged(self):
        """Layers re-classify as an error travels up; the first answer wins."""
        first = classify_llm_error(
            RateLimitError("Error code: 429 - Rate limit exceeded, please slow down"),
            "free-large",
        )

        again = classify_llm_error(LLMProviderError(first), "free-large")

        assert again is first

    def test_everything_else_passes_through_untouched(self):
        """So existing degrade-and-continue handling keeps working."""
        raise_if_provider_error(Neo4jServiceUnavailable("no route"), "gemini-flash")
