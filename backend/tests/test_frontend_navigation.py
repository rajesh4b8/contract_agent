"""Static checks on the parts of the frontend this increment is responsible for.

There is no JavaScript test runner in this repo and `make test` must stay
offline and Python-only, so these are static — the same approach
`test_smoke_scripts_are_runnable.py` takes to code the suite cannot execute.

They pin two claims that are easy to regress silently and impossible to notice
from a screenshot: that a review has a URL, and that the contract list comes
from the server rather than from this browser's `localStorage`.
"""
import pathlib
import re

import pytest

FRONTEND = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"


def source(relative: str) -> str:
    path = FRONTEND / relative
    assert path.exists(), f"{relative} is missing"
    return path.read_text()


class TestAReviewHasAUrl:
    """`selectedContractId` and the page were `useState` with no URL at all.

    A refresh emptied the screen, a matter could not be sent to a colleague, and
    the back button did nothing.
    """

    def test_the_router_reads_the_address_bar(self):
        router = source("lib/useRouter.ts")

        assert "window.location.pathname" in router
        assert "pushState" in router

    def test_the_back_button_works(self):
        assert "popstate" in source("lib/useRouter.ts")

    def test_a_matter_has_its_own_path(self):
        router = source("lib/useRouter.ts")

        assert "/matters/" in router
        assert re.search(r"\^\\/matters\\/", router), "no /matters/:ref pattern"

    def test_an_unknown_path_lands_on_the_list_rather_than_a_blank_screen(self):
        assert "return { page: 'matters' };" in source("lib/useRouter.ts")


class TestTheListComesFromTheServer:
    """`localStorage` was the contract list. A colleague saw nothing."""

    def test_the_landing_page_asks_the_api(self):
        page = source("pages/MattersPage.tsx")

        assert "listMatters" in page

    def test_what_is_left_of_local_storage_is_named_a_cache(self):
        context = source("contexts/ContractHistoryContext.tsx")

        assert "cacheMatters" in context
        assert "addContract" not in context, (
            "the write-through contract list is back; localStorage is a cache now"
        )

    def test_nothing_else_writes_the_contract_list_to_local_storage(self):
        for path in FRONTEND.rglob("*.tsx"):
            if path.name == "ContractHistoryContext.tsx":
                continue
            assert "contract_history" not in path.read_text(), (
                f"{path.name} still keeps the contract list in localStorage"
            )


class TestTheUploadSendsWhatTheEndpointReads:
    def test_the_model_is_sent_in_the_body(self):
        """The dropdown had no effect because this was never read."""
        api = source("services/mattersApi.ts")

        assert "form.append('model', model)" in api

    def test_a_round_can_name_its_matter(self):
        api = source("services/mattersApi.ts")

        assert "form.append('matter_ref', matterRef)" in api

    def test_the_old_upload_component_is_gone(self):
        """It posted a `model` field the endpoint declared as a query parameter."""
        assert not (FRONTEND / "components/features/contracts/DocumentUpload.tsx").exists()


class TestAnAnalysisThatFinishesElsewhereShowsUp:
    """The analysis outlives the request that started it.

    It runs on a worker thread, so navigating away does not stop it — but the
    page that comes back found the version RUNNING and then sat there, because
    nothing ever asked again. Reported from manual testing: "after the analysis
    is complete, the page didn't load the findings without hitting refresh."
    """

    def test_the_review_panel_polls_while_a_version_is_running(self):
        panel = source("components/features/intelligence/ContractIntelligence.tsx")

        assert "storedStatus !== 'RUNNING'" in panel, "no poll guarded on RUNNING"
        assert "setInterval" in panel

    def test_the_poll_is_quiet(self):
        """Blanking the page back to a spinner every few seconds is not an update."""
        panel = source("components/features/intelligence/ContractIntelligence.tsx")

        assert "quiet: true" in panel
        assert "if (!quiet) setRestoring(true);" in panel

    def test_it_stops_rather_than_polling_a_status_that_will_never_move(self):
        """A server restarted mid-analysis leaves the version RUNNING for ever."""
        panel = source("components/features/intelligence/ContractIntelligence.tsx")

        assert "POLL_ATTEMPTS" in panel
        assert "pollingGaveUp" in panel

    @pytest.mark.parametrize("relative", [
        "pages/MatterPage.tsx",
        "pages/MattersPage.tsx",
    ])
    def test_the_lists_stop_saying_analysing_by_themselves(self, relative):
        page = source(relative)

        assert "'RUNNING'" in page
        assert "setInterval" in page, f"{relative} never refreshes a running analysis"

    @pytest.mark.parametrize("relative", [
        "pages/MatterPage.tsx",
        "pages/MattersPage.tsx",
    ])
    def test_an_idle_page_makes_no_requests(self, relative):
        """The poll exists only while there is something to watch."""
        page = source(relative)

        assert "if (!analysing) return;" in page


class TestTheClientDoesNotAssumeWhatTheServerKnows:
    def test_a_200_is_not_taken_as_proof_the_review_was_saved(self):
        """The server answers with the in-memory results and a warning while
        marking the version FAILED when persistence fails."""
        panel = source("components/features/intelligence/ContractIntelligence.tsx")

        assert "void loadStored({ quiet: true });" in panel, (
            "the panel assumes COMPLETE instead of asking the server"
        )

    def test_a_rejected_action_does_not_replace_the_whole_page(self):
        """A double-clicked status change used to eject the reviewer to the
        "Back to matters" card, losing their place in the review."""
        page = source("pages/MatterPage.tsx")

        assert "actionError" in page
        assert "setActionError" in page

    def test_a_refresh_does_not_truncate_the_pages_already_loaded(self):
        """Loading more, then having a row start analysing, made the extra
        pages vanish on the next poll."""
        page = source("pages/MattersPage.tsx")

        assert "loadedRef" in page
        assert "Math.max(PAGE_SIZE, loadedRef.current)" in page

    def test_the_cache_cleanup_cannot_stop_the_app_mounting(self):
        """If `getItem` threw because storage is disabled, `removeItem` throws
        for the same reason — and that exception escapes the provider."""
        context = source("contexts/ContractHistoryContext.tsx")

        head = context[: context.index("export const ContractHistoryProvider")]
        assert head.count("try {") >= 2, "removeItem is not itself guarded"


class TestThePageIsNeverStuckWaiting:
    """Reported from manual testing: "the app is stuck here" — a matter page
    showing "Loading SER-2026-0001…" indefinitely, with no error, no retry and
    no way back, because the dev backend had stopped answering.

    `fetch` has no timeout of its own, so without a deadline any request that
    never returns is a spinner for ever.
    """

    def test_requests_have_a_deadline(self):
        client = source("lib/apiClient.ts")

        assert "AbortController" in client
        assert "DEFAULT_TIMEOUT_MS" in client

    def test_a_timeout_says_so_rather_than_showing_an_abort_error(self):
        """"The user aborted a request" is not a useful thing to show anyone."""
        client = source("lib/apiClient.ts")

        assert "RequestTimeout" in client
        assert "did not respond within" in client

    def test_the_calls_that_legitimately_take_minutes_are_exempt(self):
        """An analysis is three sequential model calls; an upload extracts and
        embeds. Timing those out would break the feature to fix the symptom."""
        api = source("services/mattersApi.ts")
        panel = source("components/features/intelligence/ContractIntelligence.tsx")

        assert "timeoutMs: 0" in api, "upload has a deadline it cannot meet"
        assert "timeoutMs: 0" in panel, "analyse has a deadline it cannot meet"

    def test_the_loading_state_has_a_way_out(self):
        page = source("pages/MatterPage.tsx")

        loading = page[page.index("if (loading) {"): page.index("if (error || !matter)")]
        assert "onBack" in loading, "a stuck load traps the reviewer on the page"

    @pytest.mark.parametrize("relative", ["pages/MatterPage.tsx", "pages/MattersPage.tsx"])
    def test_a_failed_load_offers_a_retry(self, relative):
        """A restarting backend is transient; making them navigate away is not."""
        page = source(relative)

        assert "Try again" in page


class TestAFailedAnalysisIsNeverShownAsNoFindings:
    @pytest.mark.parametrize("relative", [
        "pages/MatterPage.tsx",
        "components/features/intelligence/ContractIntelligence.tsx",
    ])
    def test_the_failure_state_is_rendered(self, relative):
        assert "FAILED" in source(relative)
