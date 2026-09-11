"""Reviewer decisions on redlines.

This is the first increment where a wrong failure mode loses *human* work rather
than machine output, so the failure cases are tested first and in more detail
than the happy path.
"""
import pytest

from backend.domain.redline_decision import (
    InvalidDecision,
    RedlineStatus,
    build_decision,
)
from backend.governance.rbac import Permission, RBACManager, UserRole

ORIGINAL = "Customer shall pay each invoice within ninety (90) days."
SUGGESTED = "Customer shall pay each invoice within thirty (30) days."


class TestWhatTheReviewerActuallyAgreedTo:
    """`final_text` is resolved once, so consumers never re-derive it."""

    def test_approved_takes_the_suggestion_as_drafted(self):
        decision = build_decision(
            RedlineStatus.APPROVED, original_text=ORIGINAL, suggested_text=SUGGESTED
        )

        assert decision.final_text == SUGGESTED

    def test_rejected_keeps_the_contract_as_written(self):
        decision = build_decision(
            RedlineStatus.REJECTED, original_text=ORIGINAL, suggested_text=SUGGESTED
        )

        assert decision.final_text == ORIGINAL

    def test_modified_takes_the_reviewer_s_own_wording(self):
        mine = "Customer shall pay each invoice within forty-five (45) days."

        decision = build_decision(
            RedlineStatus.MODIFIED,
            original_text=ORIGINAL, suggested_text=SUGGESTED, edited_text=mine,
        )

        assert decision.final_text == mine

    def test_the_note_is_kept_and_trimmed(self):
        decision = build_decision(
            RedlineStatus.REJECTED, original_text=ORIGINAL, suggested_text=SUGGESTED,
            note="  Counterparty will not move on this.  ",
        )

        assert decision.note == "Counterparty will not move on this."


class TestDecisionsThatCannotBeApplied:
    def test_modified_without_replacement_text_is_refused(self):
        """There is nothing to apply, so silently falling back would be wrong."""
        with pytest.raises(InvalidDecision, match="edited_text"):
            build_decision(
                RedlineStatus.MODIFIED, original_text=ORIGINAL, suggested_text=SUGGESTED
            )

    def test_modified_with_only_whitespace_is_refused(self):
        with pytest.raises(InvalidDecision, match="edited_text"):
            build_decision(
                RedlineStatus.MODIFIED, original_text=ORIGINAL,
                suggested_text=SUGGESTED, edited_text="   \n  ",
            )

    @pytest.mark.parametrize("status", [RedlineStatus.APPROVED, RedlineStatus.REJECTED])
    def test_edited_text_with_approve_or_reject_is_refused(self, status):
        """Ambiguous: did they mean to substitute their wording, or not?

        Guessing either way silently discards something the reviewer typed.
        """
        with pytest.raises(InvalidDecision, match="MODIFIED"):
            build_decision(
                status, original_text=ORIGINAL, suggested_text=SUGGESTED,
                edited_text="something they typed",
            )

    def test_pending_is_not_a_decision(self):
        with pytest.raises(InvalidDecision, match="starting state"):
            build_decision(
                RedlineStatus.PENDING, original_text=ORIGINAL, suggested_text=SUGGESTED
            )


class TestDecidedRedlinesSurviveReanalysis:
    """A model re-run must not discard a judgement a lawyer already made."""

    def test_pending_is_the_only_undecided_state(self):
        assert not RedlineStatus.PENDING.is_decided
        for status in (RedlineStatus.APPROVED, RedlineStatus.MODIFIED, RedlineStatus.REJECTED):
            assert status.is_decided, f"{status} should count as decided"


class TestWhoMayDecide:
    """Running an analysis is not the same as accepting its output."""

    @pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.LEGAL_REVIEWER])
    def test_reviewers_and_admins_may_approve(self, role):
        assert RBACManager.has_permission(role, Permission.APPROVE_REDLINE)

    @pytest.mark.parametrize("role", [UserRole.VIEWER, UserRole.AUDITOR])
    def test_viewers_and_auditors_may_not(self, role):
        assert not RBACManager.has_permission(role, Permission.APPROVE_REDLINE)

    def test_approval_is_not_covered_by_analyze(self):
        """VIEWER holds ANALYZE, so reusing it as the guard would let them approve."""
        assert RBACManager.has_permission(UserRole.VIEWER, Permission.ANALYZE)
        assert not RBACManager.has_permission(UserRole.VIEWER, Permission.APPROVE_REDLINE)


class TestTheServiceLayer:
    """Lookup, tenant scoping and the decision round-trip."""

    def _service(self, found_rows=None, previous_status="PENDING", update_rows=None):
        from unittest.mock import MagicMock
        from backend.application.services.contract_intelligence_service import (
            ContractIntelligenceService,
        )

        service = ContractIntelligenceService.__new__(ContractIntelligenceService)
        service.repository = MagicMock()
        if update_rows is None:
            update_rows = [{
                "redline_id": "R-1", "rule_id": "PAY-001", "clause_index": 0,
                "status": "APPROVED", "final_text": SUGGESTED, "decision_note": "",
                "decided_by": "LEGAL_REVIEWER", "decided_at": "2026-09-10T00:00:00Z",
                # The write reports the status it replaced, read in the same
                # statement rather than by a separate earlier query.
                "previous_status": previous_status,
            }]
        service.repository.graph.query.side_effect = [
            found_rows if found_rows is not None else [],
            update_rows,
        ]
        service._audit_decision = lambda *a, **k: None
        return service

    def test_an_unknown_redline_raises_rather_than_no_op(self):
        service = self._service(found_rows=[])

        with pytest.raises(LookupError, match="R-missing"):
            service.record_redline_decision("R-missing", "t", RedlineStatus.APPROVED)

    def test_a_decision_reports_the_state_it_replaced(self):
        """So a first ruling is distinguishable from a change of mind."""
        service = self._service(found_rows=[
            {"original_text": ORIGINAL, "suggested_text": SUGGESTED, "status": "PENDING"}
        ])

        result = service.record_redline_decision("R-1", "t", RedlineStatus.APPROVED)

        assert result["previous_status"] == "PENDING"
        assert result["status"] == "APPROVED"

    def test_deciding_twice_is_allowed_and_shows_the_prior_ruling(self):
        service = self._service(found_rows=[
            {"original_text": ORIGINAL, "suggested_text": SUGGESTED, "status": "REJECTED"}
        ], previous_status="REJECTED")

        result = service.record_redline_decision("R-1", "t", RedlineStatus.APPROVED)

        assert result["previous_status"] == "REJECTED"

    def test_an_invalid_decision_never_reaches_the_database(self):
        service = self._service(found_rows=[
            {"original_text": ORIGINAL, "suggested_text": SUGGESTED, "status": "PENDING"}
        ])

        with pytest.raises(InvalidDecision):
            service.record_redline_decision("R-1", "t", RedlineStatus.MODIFIED)

        # One call to look it up, and no write.
        assert service.repository.graph.query.call_count == 1


    def test_a_row_deleted_between_validation_and_write_raises(self):
        """Re-analysis can drop a pending draft mid-decision.

        Indexing into an empty result was a 500; a missing row is a lookup
        failure, which the endpoint turns into a 404.
        """
        service = self._service(
            found_rows=[{"original_text": ORIGINAL, "suggested_text": SUGGESTED,
                         "status": "PENDING"}],
            update_rows=[],
        )

        with pytest.raises(LookupError, match="no longer exists"):
            service.record_redline_decision("R-1", "t", RedlineStatus.APPROVED)

    def test_the_decision_is_written_in_one_statement(self):
        """Two queries let concurrent reviewers both believe they were first."""
        service = self._service(found_rows=[
            {"original_text": ORIGINAL, "suggested_text": SUGGESTED, "status": "PENDING"}
        ])

        service.record_redline_decision("R-1", "t", RedlineStatus.APPROVED)

        write = service.repository.graph.query.call_args_list[-1][0][0]
        assert "SET r.status" in write
        assert "previous_status" in write, "the write must report what it replaced"
