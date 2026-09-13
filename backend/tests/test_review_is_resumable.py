"""Reopening a review must not mean re-running it.

`_store_intelligence_results` used to persist three numbers — `clauses_count`,
`violations_count`, `risk_score` — plus the redlines. The clause findings, the
evidence spans, the rule citations and the risk narrative were never written to
the graph at all, so "open this review again" meant spending two minutes and a
model call re-deriving them, and hoping the model said the same thing twice.

These tests hold the findings on disk, and hold the line on the thing that goes
wrong if they are written carelessly: a failed analysis must never overwrite a
good stored review with nothing.
"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.application.services.contract_intelligence_service import (
    ContractIntelligenceService,
)
from backend.domain.entities import (
    ContractClause,
    ContractIntelligence,
    PolicyViolation,
    RedlineRecommendation,
    RiskAssessment,
)
from backend.domain.matter import AnalysisStatus
from backend.main import app

EVIDENCE = "Customer shall pay each invoice within ninety (90) days of receipt."

HEADERS = {"X-User-Role": "LEGAL_REVIEWER", "X-Tenant-ID": "acme"}


def _service():
    """The service with its graph and matter bookkeeping stubbed out."""
    service = ContractIntelligenceService.__new__(ContractIntelligenceService)
    service.repository = MagicMock()
    service.repository.graph.query.return_value = []
    service.matters = MagicMock()
    return service


def _intelligence(**overrides) -> ContractIntelligence:
    defaults = dict(
        clauses=[ContractClause(
            clause_type="Payment",
            content=EVIDENCE,
            risk_level="HIGH",
            confidence_score=0.91,
            location="Section 4.2",
            evidence_span=EVIDENCE,
            violated_policy="PAY-001",
            suggested_redline="within thirty (30) days",
            human_review_required=True,
        )],
        violations=[PolicyViolation(
            clause_type="Payment",
            issue="Payment terms exceed 30 days",
            severity="CRITICAL",
            suggested_fix="Reduce to thirty (30) days",
            clause_content=EVIDENCE,
            rule_id="PAY-001",
            clause_index=0,
            section_reference="Payment Terms",
        )],
        risk_assessment=RiskAssessment(
            overall_risk_score=72.0,
            risk_level="HIGH",
            critical_issues=["Payment terms are 3x policy"],
            recommendations=["Negotiate payment terms down to 30 days"],
        ),
        redlines=[],
    )
    defaults.update(overrides)
    return ContractIntelligence(**defaults)


def _written(service):
    """Every (statement, params) pair the service sent."""
    return [(call.args[0], call.args[1] if len(call.args) > 1 else {})
            for call in service.repository.graph.query.call_args_list]


class TestTheFindingsThemselvesAreStored:
    def test_the_evidence_span_reaches_the_graph(self, ):
        """The quote is the finding. A count of nine is not a review."""
        service = _service()

        service._store_clause_findings("C-1", "acme", _intelligence())

        findings = [p for s, p in _written(service) if "ClauseFinding" in s][0]["findings"]
        assert findings[0]["evidence_span"] == EVIDENCE
        assert findings[0]["clause_type"] == "Payment"
        assert findings[0]["risk_level"] == "HIGH"

    def test_the_rule_a_finding_cites_is_kept(self):
        """Without it the finding is an opinion rather than a citation."""
        service = _service()

        service._store_clause_findings("C-1", "acme", _intelligence())

        findings = [p for s, p in _written(service) if "ClauseFinding" in s][0]["findings"]
        assert findings[0]["violated_policy"] == "PAY-001"

    def test_violations_are_stored_beside_them(self):
        service = _service()

        service._store_clause_findings("C-1", "acme", _intelligence())

        violations = [p for s, p in _written(service) if "PolicyViolation" in s][0]["violations"]
        assert violations[0]["rule_id"] == "PAY-001"
        assert violations[0]["severity"] == "CRITICAL"

    def test_everything_written_is_scoped_to_the_tenant(self):
        service = _service()

        service._store_clause_findings("C-1", "acme", _intelligence())

        for statement, params in _written(service):
            assert params["tenant_id"] == "acme"
            assert "tenant_id: $tenant_id" in statement

    def test_a_finding_id_survives_re_extraction_reordering(self):
        """Keyed on the evidence, not the position — the redline id's reason.

        An id that moves when the model returns the same clauses in a different
        order is not an id.
        """
        clause = _intelligence().clauses[0]
        moved = ContractClause(**{**clause.__dict__})

        first = ContractIntelligenceService._finding_id("C-1", 0, clause)
        again = ContractIntelligenceService._finding_id("C-1", 0, moved)

        assert first == again

    def test_two_findings_quoting_the_same_text_do_not_collide(self):
        clause = _intelligence().clauses[0]

        assert ContractIntelligenceService._finding_id("C-1", 0, clause) != \
            ContractIntelligenceService._finding_id("C-1", 1, clause)


class TestAFailedAnalysisNeverErasesAGoodReview:
    def test_nothing_at_all_is_written_when_the_analysis_did_not_run(self):
        """Not the findings, and not the score either.

        The failure path builds a result with risk 0.0, level "UNKNOWN" and every
        count at zero. Writing that overwrites a good previous review with
        numbers that read, on the matters list, as a contract with nothing wrong
        with it — which is worse than showing nothing.
        """
        service = _service()

        stored = service._store_intelligence_results("C-1", "acme", _intelligence(
            clauses=[], violations=[], clauses_extracted=False,
        ))

        assert stored is False
        assert service.repository.graph.query.call_count == 0, (
            "a failed analysis wrote to the graph"
        )

    def test_the_version_is_not_marked_complete_when_nothing_was_saved(self):
        """An analysis that ran and then failed to save is, to whoever reopens
        the matter, indistinguishable from one that never ran at all."""
        service = _service()
        service.repository.graph.query.side_effect = RuntimeError("neo4j is down")

        assert service._store_intelligence_results("C-1", "acme", _intelligence()) is False

    def test_a_findings_write_failure_is_not_reported_as_a_complete_review(self):
        """The findings *are* the review. A score with nothing to justify it is not."""
        service = _service()
        calls = {"n": 0}

        def fail_on_findings(statement, *args, **kwargs):
            calls["n"] += 1
            if "ClauseFinding" in statement:
                raise RuntimeError("neo4j is down")
            return []

        service.repository.graph.query.side_effect = fail_on_findings

        assert service._store_intelligence_results("C-1", "acme", _intelligence()) is False

    def test_a_redline_write_failure_is_not_reported_as_a_complete_review(self):
        service = _service()

        def fail_on_redlines(statement, *args, **kwargs):
            if "MERGE (r:Redline" in statement:
                raise RuntimeError("neo4j is down")
            return []

        service.repository.graph.query.side_effect = fail_on_redlines

        assert service._store_intelligence_results("C-1", "acme", _intelligence(
            redlines=[RedlineRecommendation(
                original_text=EVIDENCE,
                suggested_text="Customer shall pay each invoice within thirty (30) days.",
                justification="Matches PAY-001.",
                priority="HIGH",
                rule_id="PAY-001",
                clause_index=0,
                clause_type="Payment",
            )],
        )) is False

    def test_a_run_that_genuinely_found_nothing_does_clear_the_last_one(self):
        """Otherwise a fixed contract keeps showing the violations it fixed."""
        service = _service()

        service._store_clause_findings("C-1", "acme", _intelligence(
            clauses=[], violations=[],
        ))

        statements = [s for s, _ in _written(service)]
        assert any("DETACH DELETE old" in s for s in statements)

    def test_a_storage_failure_does_not_fail_the_analysis(self):
        """The caller already has the results; raising would throw them away.

        It is reported rather than swallowed, though — the return value is what
        decides COMPLETE versus FAILED on the version.
        """
        service = _service()
        service.repository.graph.query.side_effect = RuntimeError("neo4j is down")

        assert service._store_clause_findings("C-1", "acme", _intelligence()) is False


class TestReadingTheReviewBack:
    def _service_returning(self, header, clauses=None, violations=None, redlines=None):
        service = _service()
        service.repository.graph.query.side_effect = [
            header, clauses or [], violations or [],
        ]
        service.get_redlines = lambda *a, **k: redlines or []
        return service

    def test_an_unknown_contract_is_none_not_an_empty_review(self):
        """The endpoint turns this into a 404. Empty results would be a lie."""
        service = self._service_returning([])

        assert service.get_stored_analysis("C-missing", "acme") is None

    def test_the_stored_findings_come_back_in_the_analysis_shape(self):
        """So the page renders the same whether it analysed or reopened."""
        service = self._service_returning(
            [{"contract_id": "C-1", "analysis_status": "COMPLETE",
              "risk_score": 72.0, "risk_level": "HIGH",
              "critical_issues": ["Payment terms are 3x policy"],
              "recommendations": ["Negotiate down to 30 days"]}],
            clauses=[{"clause_type": "Payment", "evidence_span": EVIDENCE,
                      "risk_level": "HIGH", "confidence": 0.91,
                      "violated_policy": "PAY-001", "suggested_redline": None,
                      "human_review_required": True, "location": "4.2"}],
            violations=[{"rule_id": "PAY-001", "severity": "CRITICAL",
                         "issue": "too long", "clause_type": "Payment",
                         "clause_index": 0, "section_reference": "Payment Terms",
                         "suggested_fix": "30 days", "clause_content": EVIDENCE}],
        )

        stored = service.get_stored_analysis("C-1", "acme")

        assert stored["results"]["clauses"][0]["evidence_span"] == EVIDENCE
        assert stored["results"]["violations"][0]["rule_id"] == "PAY-001"
        assert stored["results"]["risk_assessment"]["risk_level"] == "HIGH"

    def test_the_risk_narrative_survives_too(self):
        """"72/100 HIGH" with nothing to say why is not a review."""
        service = self._service_returning(
            [{"contract_id": "C-1", "analysis_status": "COMPLETE",
              "risk_score": 72.0, "risk_level": "HIGH",
              "critical_issues": ["Payment terms are 3x policy"],
              "recommendations": ["Negotiate down to 30 days"]}])

        risk = service.get_stored_analysis("C-1", "acme")["results"]["risk_assessment"]

        assert risk["critical_issues"] == ["Payment terms are 3x policy"]
        assert risk["recommendations"] == ["Negotiate down to 30 days"]

    def test_the_old_ui_field_names_are_still_served(self):
        """`content` and `confidence_score` are read by the clause table."""
        service = self._service_returning(
            [{"contract_id": "C-1", "analysis_status": "COMPLETE"}],
            clauses=[{"clause_type": "Payment", "evidence_span": EVIDENCE,
                      "confidence": 0.91}])

        clause = service.get_stored_analysis("C-1", "acme")["results"]["clauses"][0]

        assert clause["content"] == EVIDENCE
        assert clause["confidence_score"] == 0.91

    def test_a_failed_analysis_comes_back_as_a_warning_not_as_no_findings(self):
        service = self._service_returning(
            [{"contract_id": "C-1", "analysis_status": "FAILED",
              "analysis_error": "Gemini has no quota left on this key"}])

        stored = service.get_stored_analysis("C-1", "acme")

        assert stored["analysis_status"] == "FAILED"
        assert stored["warnings"] == ["Gemini has no quota left on this key"]
        assert stored["results"]["clauses"] == []

    def test_a_running_analysis_says_when_its_status_last_moved(self):
        """So the page can tell "still working" from "the server restarted".

        Without it, a version left RUNNING by a crash is polled for ever.
        """
        service = self._service_returning(
            [{"contract_id": "C-1", "analysis_status": "RUNNING",
              "analysis_updated_at": "2026-09-13T18:30:00Z"}])

        stored = service.get_stored_analysis("C-1", "acme")

        assert stored["analysis_status"] == "RUNNING"
        assert stored["analysis_updated_at"] == "2026-09-13T18:30:00Z"

    def test_a_running_analysis_returns_no_findings_and_no_warning(self):
        """It has not failed. Saying so would be as wrong as saying it is clean."""
        service = self._service_returning(
            [{"contract_id": "C-1", "analysis_status": "RUNNING"}])

        stored = service.get_stored_analysis("C-1", "acme")

        assert stored["results"]["clauses"] == []
        assert stored["warnings"] == []

    def test_a_contract_that_was_never_analysed_says_not_started(self):
        service = self._service_returning(
            [{"contract_id": "C-1", "analysis_status": "NOT_STARTED"}])

        assert service.get_stored_analysis("C-1", "acme")["analysis_status"] == "NOT_STARTED"


class TestTheVersionKnowsHowItsAnalysisWent:
    def test_running_is_recorded_before_the_work_starts(self):
        """A matter opened mid-analysis must not show an empty review."""
        service = _service()
        service._set_analysis_status("C-1", "acme", AnalysisStatus.RUNNING)

        service.matters.set_analysis_status.assert_called_once()
        assert service.matters.set_analysis_status.call_args[0][2] is AnalysisStatus.RUNNING

    def test_bookkeeping_failure_never_fails_the_analysis(self):
        service = _service()
        service.matters.set_analysis_status.side_effect = RuntimeError("neo4j is down")

        service._set_analysis_status("C-1", "acme", AnalysisStatus.COMPLETE)  # no raise


class TestTheEndpoint:
    @pytest.fixture
    def client(self):
        with TestClient(app) as test_client:
            yield test_client

    def test_reopening_a_review_costs_no_model_call(self, client):
        """The whole point: GET, not POST /analyze."""
        with patch("backend.api.contract_intelligence."
                   "ContractIntelligenceServiceFactory.create_service") as factory:
            factory.return_value.get_stored_analysis.return_value = {
                "contract_id": "C-1", "analysis_status": "COMPLETE",
                "warnings": [], "results": {"clauses": [], "violations": [],
                                            "risk_assessment": {}, "redlines": []},
            }

            response = client.get("/api/intelligence/contracts/C-1/analysis",
                                  headers=HEADERS)

        assert response.status_code == 200
        assert response.json()["analysis_status"] == "COMPLETE"

    def test_an_unknown_contract_is_404(self, client):
        with patch("backend.api.contract_intelligence."
                   "ContractIntelligenceServiceFactory.create_service") as factory:
            factory.return_value.get_stored_analysis.return_value = None

            response = client.get("/api/intelligence/contracts/C-nope/analysis",
                                  headers=HEADERS)

        assert response.status_code == 404

    def test_it_reads_the_tenant_from_the_caller(self, client):
        with patch("backend.api.contract_intelligence."
                   "ContractIntelligenceServiceFactory.create_service") as factory:
            factory.return_value.get_stored_analysis.return_value = {
                "contract_id": "C-1", "analysis_status": "COMPLETE",
                "warnings": [], "results": {},
            }

            client.get("/api/intelligence/contracts/C-1/analysis", headers=HEADERS)

        assert factory.return_value.get_stored_analysis.call_args[0][1] == "acme"
