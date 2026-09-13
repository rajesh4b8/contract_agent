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


class TestAFailedAnalysisIsNeverShownAsNoFindings:
    @pytest.mark.parametrize("relative", [
        "pages/MatterPage.tsx",
        "components/features/intelligence/ContractIntelligence.tsx",
    ])
    def test_the_failure_state_is_rendered(self, relative):
        assert "FAILED" in source(relative)
