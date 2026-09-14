"""What the browser actually receives from the matters endpoints.

The pieces are tested in `test_matters.py`; this is the wire. The three reads
here are what the app navigates by, so the status codes are the contract: a
reference the caller may not see must be indistinguishable from one that does
not exist, and a closed matter must refuse a new round by name rather than
accepting it quietly.
"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app

HEADERS = {"X-User-Role": "LEGAL_REVIEWER", "X-Tenant-ID": "acme"}

A_LIST_ROW = {
    "matter_ref": "MSA-2026-0042",
    "title": "Master Services Agreement — Acme",
    "counterparty": "Acme",
    "contract_type": "Master Services Agreement",
    "status": "DRAFT",
    "created_at": "2026-09-01T09:00:00Z",
    "updated_at": "2026-09-02T09:00:00Z",
    "version_count": 2,
    "latest_n": 2,
    "latest_version_id": "UPLOADED_BBB",
    "analysis_status": "COMPLETE",
    "risk_score": 72.0,
    "risk_level": "HIGH",
    "redline_total": 6,
    "redline_pending": 2,
}

A_MATTER = {
    "matter_ref": "MSA-2026-0042",
    "title": "Master Services Agreement — Acme",
    "counterparty": "Acme",
    "contract_type": "Master Services Agreement",
    "status": "DRAFT",
    "created_at": "2026-09-01T09:00:00Z",
    "updated_at": "2026-09-02T09:00:00Z",
    "versions": [
        {"n": 1, "version_id": "UPLOADED_AAA", "filename": "msa-v1.pdf",
         "uploaded_at": "2026-09-01T09:00:00Z", "source_sha256": "aaa",
         "analysis_status": "COMPLETE", "analysis_error": None,
         "risk_score": 80.0, "risk_level": "HIGH",
         "clauses_count": 9, "violations_count": 4, "redlines_count": 4},
        {"n": 2, "version_id": "UPLOADED_BBB", "filename": "msa-v2.pdf",
         "uploaded_at": "2026-09-02T09:00:00Z", "source_sha256": "bbb",
         "analysis_status": "RUNNING", "analysis_error": None,
         "risk_score": None, "risk_level": None,
         "clauses_count": None, "violations_count": None, "redlines_count": None},
    ],
}


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def matters():
    """Stand in for the module-level repository the endpoints hold."""
    with patch("backend.api.matters.repository", new=MagicMock()) as fake:
        fake.review_counts.return_value = {}
        fake.list_matters.return_value = []
        fake.count_matters.return_value = 0
        yield fake


class TestTheList:
    def test_it_is_the_landing_page_and_needs_no_audit_permission(self, client, matters):
        """The only list endpoint before this was gated on VIEW_AUDIT.

        `LEGAL_REVIEWER` — the default role — does not hold it, so the app
        could not have listed contracts even if it had asked.
        """
        matters.list_matters.return_value = [A_LIST_ROW]
        matters.count_matters.return_value = 1

        response = client.get("/api/matters", headers=HEADERS)

        assert response.status_code == 200
        assert response.json()["count"] == 1

    def test_each_row_says_what_state_the_review_is_in(self, client, matters):
        matters.list_matters.return_value = [A_LIST_ROW]

        row = client.get("/api/matters", headers=HEADERS).json()["matters"][0]

        assert row["matter_ref"] == "MSA-2026-0042"
        assert row["status"] == "IN_REVIEW", "two pending redlines is not REVIEWED"
        assert row["redlines_pending"] == 2
        assert row["latest_version"] == 2

    def test_a_matter_whose_redlines_are_all_decided_reads_as_reviewed(self, client, matters):
        matters.list_matters.return_value = [{**A_LIST_ROW, "redline_pending": 0}]

        row = client.get("/api/matters", headers=HEADERS).json()["matters"][0]

        assert row["status"] == "REVIEWED"

    def test_an_empty_tenant_is_an_empty_list_not_an_error(self, client, matters):
        matters.list_matters.return_value = []

        response = client.get("/api/matters", headers=HEADERS)

        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 0 and body["matters"] == []
        assert body["has_more"] is False

    def test_a_truncated_list_says_that_it_is_truncated(self, client, matters):
        """Silently returning the first page makes older matters unreachable.

        For a system whose claim is that the review is durable, that is the
        worst kind of failure: the work is still there and there is no way to
        get to it.
        """
        matters.list_matters.return_value = [A_LIST_ROW]
        matters.count_matters.return_value = 250

        body = client.get("/api/matters?limit=1", headers=HEADERS).json()

        assert body["total"] == 250
        assert body["has_more"] is True

    def test_the_last_page_does_not_claim_there_is_more(self, client, matters):
        matters.list_matters.return_value = [A_LIST_ROW]
        matters.count_matters.return_value = 3

        body = client.get("/api/matters?limit=1&offset=2", headers=HEADERS).json()

        assert body["offset"] == 2
        assert body["has_more"] is False

    def test_a_page_can_be_asked_for(self, client, matters):
        client.get("/api/matters?limit=25&offset=50", headers=HEADERS)

        assert matters.list_matters.call_args.kwargs == {"limit": 25, "offset": 50}

    def test_it_reads_the_tenant_from_the_caller(self, client, matters):
        matters.list_matters.return_value = []

        client.get("/api/matters", headers=HEADERS)

        assert matters.list_matters.call_args[0][0] == "acme"


class TestOneMatter:
    def test_it_carries_every_round(self, client, matters):
        matters.get_matter.return_value = A_MATTER
        matters.review_counts.return_value = {
            1: {"total": 4, "pending": 0, "approved": 4, "modified": 0, "rejected": 0},
            2: {"total": 0, "pending": 0, "approved": 0, "modified": 0, "rejected": 0},
        }

        body = client.get("/api/matters/MSA-2026-0042", headers=HEADERS).json()

        assert [v["n"] for v in body["versions"]] == [1, 2]
        assert body["versions"][0]["review"]["approved"] == 4

    def test_a_version_still_being_analysed_says_so(self, client, matters):
        """Not "no findings" — that reads as a clean contract."""
        matters.get_matter.return_value = A_MATTER

        body = client.get("/api/matters/MSA-2026-0042", headers=HEADERS).json()

        assert body["versions"][1]["analysis_status"] == "RUNNING"

    def test_a_failed_analysis_carries_its_reason(self, client, matters):
        matters.get_matter.return_value = {
            **A_MATTER,
            "versions": [{**A_MATTER["versions"][0],
                          "analysis_status": "FAILED",
                          "analysis_error": "Gemini has no quota left on this key"}],
        }

        version = client.get("/api/matters/MSA-2026-0042", headers=HEADERS).json()["versions"][0]

        assert version["analysis_status"] == "FAILED"
        assert "quota" in version["analysis_error"]

    def test_an_unknown_reference_is_404(self, client, matters):
        matters.get_matter.return_value = None

        response = client.get("/api/matters/MSA-2026-9999", headers=HEADERS)

        assert response.status_code == 404

    def test_another_tenant_s_reference_is_also_404(self, client, matters):
        """403 would confirm that it exists — the same rule redlines follow."""
        matters.get_matter.return_value = None

        response = client.get("/api/matters/MSA-2026-0042",
                              headers={**HEADERS, "X-Tenant-ID": "someone-else"})

        assert response.status_code == 404
        assert "403" not in str(response.status_code)


class TestFilingANewContract:
    def test_confirming_the_card_allocates_the_reference(self, client, matters):
        matters.create_matter.return_value = {"matter_ref": "MSA-2026-0042", "n": 1}

        response = client.post("/api/matters", headers=HEADERS, json={
            "contract_id": "UPLOADED_AAA",
            "title": "Master Services Agreement — Acme",
            "counterparty": "Acme",
            "contract_type": "Master Services Agreement",
        })

        assert response.status_code == 200
        assert response.json()["matter_ref"] == "MSA-2026-0042"
        assert response.json()["version"] == 1

    def test_the_user_s_corrections_are_what_gets_filed(self, client, matters):
        """The card is pre-filled, not authoritative."""
        matters.create_matter.return_value = {"matter_ref": "SOW-2026-0001", "n": 1}

        client.post("/api/matters", headers=HEADERS, json={
            "contract_id": "UPLOADED_AAA",
            "title": "Phase 2 SOW — Globex",
            "counterparty": "Globex",
            "contract_type": "Statement of Work",
        })

        kwargs = matters.create_matter.call_args.kwargs
        assert kwargs["counterparty"] == "Globex"
        assert kwargs["contract_type"] == "Statement of Work"

    def test_an_empty_title_is_filled_in_rather_than_stored_blank(self, client, matters):
        matters.create_matter.return_value = {"matter_ref": "MSA-2026-0042", "n": 1}

        client.post("/api/matters", headers=HEADERS, json={
            "contract_id": "UPLOADED_AAA",
            "counterparty": "Acme",
            "contract_type": "Master Services Agreement",
        })

        assert matters.create_matter.call_args.kwargs["title"] == \
            "Master Services Agreement — Acme"

    def test_a_double_clicked_confirm_reports_the_matter_it_already_made(self, client, matters):
        """Never a second reference number for the same bytes."""
        from backend.infrastructure.matter_repository import AlreadyFiled

        matters.create_matter.side_effect = AlreadyFiled("already filed")
        matters.matter_for_version.return_value = {"matter_ref": "MSA-2026-0042", "n": 1}

        response = client.post("/api/matters", headers=HEADERS,
                               json={"contract_id": "UPLOADED_AAA"})

        assert response.status_code == 409
        assert "MSA-2026-0042" in response.json()["detail"]

    def test_filing_a_document_that_was_never_uploaded_is_404(self, client, matters):
        from backend.infrastructure.matter_repository import AlreadyFiled

        matters.create_matter.side_effect = AlreadyFiled("no such version")
        matters.matter_for_version.return_value = None

        response = client.post("/api/matters", headers=HEADERS,
                               json={"contract_id": "UPLOADED_NOPE"})

        assert response.status_code == 404

    def test_a_viewer_may_not_file_a_matter(self, client, matters):
        response = client.post("/api/matters",
                               headers={**HEADERS, "X-User-Role": "VIEWER"},
                               json={"contract_id": "UPLOADED_AAA"})

        assert response.status_code == 403


class TestTakingUpASuggestion:
    """Filing a document already on the server, rather than re-uploading it.

    The other answer to the question the filing card asks: the advisory match
    said this looks like a new round of MSA-2026-0042, and the reviewer agreed.
    """

    def test_it_files_the_existing_document(self, client, matters):
        matters.attach_version.return_value = {"matter_ref": "MSA-2026-0042", "n": 2}

        response = client.post("/api/matters/MSA-2026-0042/versions", headers=HEADERS,
                               json={"contract_id": "UPLOADED_AAA"})

        assert response.status_code == 200
        assert response.json()["version"] == 2

    def test_a_closed_matter_refuses_by_name(self, client, matters):
        from backend.infrastructure.matter_repository import MatterClosed

        matters.attach_version.side_effect = MatterClosed("MSA-2026-0042")

        response = client.post("/api/matters/MSA-2026-0042/versions", headers=HEADERS,
                               json={"contract_id": "UPLOADED_AAA"})

        assert response.status_code == 409
        assert "MSA-2026-0042" in response.json()["detail"]

    def test_a_document_already_filed_elsewhere_says_where(self, client, matters):
        from backend.infrastructure.matter_repository import MatterNotFound

        matters.attach_version.side_effect = MatterNotFound("x")
        matters.matter_for_version.return_value = {"matter_ref": "SOW-2026-0001", "n": 1}

        response = client.post("/api/matters/MSA-2026-0042/versions", headers=HEADERS,
                               json={"contract_id": "UPLOADED_AAA"})

        assert response.status_code == 409
        assert "SOW-2026-0001" in response.json()["detail"]

    def test_an_unknown_matter_is_404(self, client, matters):
        from backend.infrastructure.matter_repository import MatterNotFound

        matters.attach_version.side_effect = MatterNotFound("x")
        matters.matter_for_version.return_value = None
        matters.get_matter.return_value = None

        response = client.post("/api/matters/MSA-2026-9999/versions", headers=HEADERS,
                               json={"contract_id": "UPLOADED_AAA"})

        assert response.status_code == 404
        assert "No matter" in response.json()["detail"]

    def test_a_missing_document_does_not_claim_the_matter_is_missing(self, client, matters):
        """`attach_version` raises the same error for three situations.

        Telling a reviewer "no matter MSA-2026-0042" while that matter is on
        their screen, when it is the *document* that is not there, sends them
        looking in entirely the wrong place.
        """
        from backend.infrastructure.matter_repository import MatterNotFound

        matters.attach_version.side_effect = MatterNotFound("x")
        matters.matter_for_version.return_value = None
        matters.get_matter.return_value = A_MATTER

        response = client.post("/api/matters/MSA-2026-0042/versions", headers=HEADERS,
                               json={"contract_id": "UPLOADED_NOPE"})

        assert response.status_code == 404
        detail = response.json()["detail"]
        assert "UPLOADED_NOPE" in detail
        assert "No matter" not in detail

    def test_junk_in_the_reference_never_reaches_a_query(self, client, matters):
        response = client.post("/api/matters/..%2F..%2Fetc/versions", headers=HEADERS,
                               json={"contract_id": "UPLOADED_AAA"})

        assert response.status_code == 404
        matters.attach_version.assert_not_called()

    def test_a_viewer_may_not(self, client, matters):
        response = client.post("/api/matters/MSA-2026-0042/versions",
                               headers={**HEADERS, "X-User-Role": "VIEWER"},
                               json={"contract_id": "UPLOADED_AAA"})

        assert response.status_code == 403


class TestMovingAMatterAlong:
    def test_a_reviewed_matter_can_be_sent_to_the_counterparty(self, client, matters):
        matters.get_matter.return_value = A_MATTER
        matters.review_counts.return_value = {
            2: {"total": 4, "pending": 0, "approved": 4, "modified": 0, "rejected": 0}}

        response = client.patch("/api/matters/MSA-2026-0042/status", headers=HEADERS,
                                json={"status": "AWAITING_COUNTERPARTY"})

        assert response.status_code == 200
        assert response.json()["previous_status"] == "REVIEWED"
        assert response.json()["status"] == "AWAITING_COUNTERPARTY"

    def test_reopening_a_closed_matter_is_an_explicit_action(self, client, matters):
        matters.get_matter.return_value = {**A_MATTER, "status": "CLOSED"}

        response = client.patch("/api/matters/MSA-2026-0042/status", headers=HEADERS,
                                json={"status": "DRAFT"})

        assert response.status_code == 200
        assert response.json()["status"] == "DRAFT"

    def test_a_derived_status_is_refused_with_an_explanation(self, client, matters):
        matters.get_matter.return_value = A_MATTER

        response = client.patch("/api/matters/MSA-2026-0042/status", headers=HEADERS,
                                json={"status": "REVIEWED"})

        assert response.status_code == 422
        assert "derived" in response.json()["detail"]

    def test_an_unknown_status_never_reaches_the_graph(self, client, matters):
        response = client.patch("/api/matters/MSA-2026-0042/status", headers=HEADERS,
                                json={"status": "ARCHIVED"})

        assert response.status_code == 422
        matters.set_status.assert_not_called()

    def test_junk_in_the_reference_never_reaches_a_query(self, client, matters):
        response = client.patch("/api/matters/..%2F..%2Fetc/status", headers=HEADERS,
                                json={"status": "CLOSED"})

        assert response.status_code == 404
        matters.set_status.assert_not_called()

    def test_a_viewer_may_not_close_a_matter(self, client, matters):
        """VIEWER holds ANALYZE, so reusing it as the guard would let them."""
        response = client.patch("/api/matters/MSA-2026-0042/status",
                                headers={**HEADERS, "X-User-Role": "VIEWER"},
                                json={"status": "CLOSED"})

        assert response.status_code == 403
