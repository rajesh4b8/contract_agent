"""Redlines must be drafted for the clause in hand and cite the rule they fix.

This replaced a five-branch `if/elif` on clause type that returned a constant
from the playbook, so every payment violation in every contract produced the
same sentence — language that ignores the document's defined terms and party
names, and so cannot actually be pasted into it.
"""
import json

import pytest

from backend.agents.intelligence_tools import RedlineGeneratorTool

VIOLATIONS = [
    {
        "rule_id": "PAY-001",
        "clause_index": 0,
        "clause_type": "Payment Terms",
        "issue": "Net 90 exceeds the 30-day limit",
        "severity": "CRITICAL",
        "suggested_fix": "Payment is due within thirty (30) days of invoice receipt.",
        "clause_content": "Customer shall pay each invoice within ninety (90) days of receipt.",
    },
    {
        "rule_id": "LIA-002",
        "clause_index": 1,
        "clause_type": "Liability",
        "issue": "Fixed cap below USD 100,000",
        "severity": "HIGH",
        "suggested_fix": "Liability shall not exceed fees paid under the applicable SOW.",
        "clause_content": "Provider's aggregate liability is limited to $50,000.",
    },
]


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)

        class Response:
            content = json.dumps(self.payload)

        return Response()


def _draft(rule_id, clause_index=0, text="Rewritten clause text.",
           why="Because the rule requires it."):
    return {"rule_id": rule_id, "clause_index": clause_index,
            "suggested_text": text, "justification": why}


def test_redlines_cite_the_rule_they_remediate():
    tool = RedlineGeneratorTool(llm=FakeLLM({"redlines": [_draft("PAY-001")]}))

    redlines = json.loads(tool._run(json.dumps(VIOLATIONS)))

    assert len(redlines) == 1
    assert redlines[0]["rule_id"] == "PAY-001"


def test_the_original_text_is_the_clause_not_the_model_s_words():
    """The 'before' side must be what the contract actually says."""
    tool = RedlineGeneratorTool(llm=FakeLLM({"redlines": [_draft("PAY-001")]}))

    redlines = json.loads(tool._run(json.dumps(VIOLATIONS)))

    assert redlines[0]["original_text"] == VIOLATIONS[0]["clause_content"]


def test_priority_follows_the_rule_severity():
    """Severity comes from the playbook, so priority cannot drift between runs."""
    tool = RedlineGeneratorTool(
        llm=FakeLLM({"redlines": [_draft("PAY-001"), _draft("LIA-002", 1)]})
    )

    redlines = json.loads(tool._run(json.dumps(VIOLATIONS)))
    by_rule = {r["rule_id"]: r for r in redlines}

    assert by_rule["PAY-001"]["priority"] == "CRITICAL"
    assert by_rule["LIA-002"]["priority"] == "HIGH"


def test_redlines_citing_unknown_rules_are_discarded():
    tool = RedlineGeneratorTool(
        llm=FakeLLM({"redlines": [_draft("PAY-001"), _draft("NOT-A-RULE", 0)]})
    )

    redlines = json.loads(tool._run(json.dumps(VIOLATIONS)))

    assert [r["rule_id"] for r in redlines] == ["PAY-001"]


def test_the_clause_and_the_target_wording_both_reach_the_model():
    """Without the clause text the model can only produce a template."""
    fake = FakeLLM({"redlines": []})

    RedlineGeneratorTool(llm=fake)._run(json.dumps(VIOLATIONS))

    prompt = fake.prompts[0]
    assert "ninety (90) days" in prompt, "the clause being rewritten must be shown"
    assert "thirty (30) days" in prompt, "the rule's target wording must be shown"
    assert "PAY-001" in prompt


def test_violations_without_a_rule_cannot_be_redlined():
    """The rule defines what 'fixed' means; without one there is no target."""
    uncited = [{"clause_type": "Liability", "clause_content": "Something.", "issue": "x"}]
    fake = FakeLLM({"redlines": []})

    assert json.loads(RedlineGeneratorTool(llm=fake)._run(json.dumps(uncited))) == []
    assert fake.prompts == [], "should not call the model with nothing to remediate"


def test_no_violations_means_no_redlines_and_no_model_call():
    fake = FakeLLM({"redlines": []})

    assert json.loads(RedlineGeneratorTool(llm=fake)._run(json.dumps([]))) == []
    assert fake.prompts == []


def test_without_an_llm_it_refuses_rather_than_templating():
    with pytest.raises(ValueError, match="requires an llm"):
        RedlineGeneratorTool(llm=None)._run(json.dumps(VIOLATIONS))


def test_a_failing_model_surfaces_the_error():
    class Boom:
        def invoke(self, prompt):
            raise RuntimeError("429 RESOURCE_EXHAUSTED")

    with pytest.raises(RuntimeError, match="RESOURCE_EXHAUSTED"):
        RedlineGeneratorTool(llm=Boom())._run(json.dumps(VIOLATIONS))


def test_one_rule_breached_by_two_clauses_gets_two_correct_redlines():
    """Keying the join on rule id alone silently mixed up the clauses.

    The policy checker can report the same rule against several clauses. With a
    rule-keyed lookup, every suggestion for that rule attached to whichever
    breach happened to be last, so a redline drafted for one clause was persisted
    against another clause's text.
    """
    same_rule = [
        {"rule_id": "LIA-002", "clause_index": 0, "clause_type": "Liability",
         "severity": "HIGH", "suggested_fix": "Cap at SOW fees.",
         "issue": "cap too low", "clause_content": "Cap is $10,000."},
        {"rule_id": "LIA-002", "clause_index": 1, "clause_type": "Indemnification",
         "severity": "HIGH", "suggested_fix": "Cap at SOW fees.",
         "issue": "cap too low", "clause_content": "Cap is $20,000."},
    ]
    tool = RedlineGeneratorTool(llm=FakeLLM({"redlines": [
        _draft("LIA-002", 0, text="Rewrite of the ten-thousand clause."),
        _draft("LIA-002", 1, text="Rewrite of the twenty-thousand clause."),
    ]}))

    redlines = json.loads(tool._run(json.dumps(same_rule)))

    assert len(redlines) == 2
    by_index = {r["clause_index"]: r for r in redlines}
    assert by_index[0]["original_text"] == "Cap is $10,000."
    assert by_index[1]["original_text"] == "Cap is $20,000."
    assert by_index[0]["clause_type"] == "Liability"
    assert by_index[1]["clause_type"] == "Indemnification"


def test_a_redline_for_a_clause_that_did_not_breach_that_rule_is_discarded():
    tool = RedlineGeneratorTool(llm=FakeLLM({"redlines": [_draft("PAY-001", 7)]}))

    assert json.loads(tool._run(json.dumps(VIOLATIONS))) == []


class TestPersistenceIsNotDestroyedByAFailedDraft:
    """A transient model error must not wipe redlines a reviewer is using.

    `_generate_redlines` catches exceptions and returns an empty list, which is
    indistinguishable from "no redlines were needed". Persistence replaced the
    stored set wholesale, so one rate-limited call destroyed the previous drafts.
    """

    def _service(self):
        from unittest.mock import MagicMock
        from backend.application.services.contract_intelligence_service import (
            ContractIntelligenceService,
        )

        service = ContractIntelligenceService.__new__(ContractIntelligenceService)
        service.repository = MagicMock()
        return service

    def test_a_failed_draft_leaves_the_stored_redlines_alone(self):
        service = self._service()

        service._store_redlines("C-1", "t", [], replace=False)

        service.repository.graph.query.assert_not_called()

    def test_a_successful_draft_replaces_them(self):
        service = self._service()

        service._store_redlines("C-1", "t", [], replace=True)

        assert service.repository.graph.query.called, "should clear the previous set"


def test_the_analysis_result_reports_whether_drafting_succeeded():
    """The flag persistence keys off must survive into the result."""
    from backend.domain.entities import ContractIntelligence, RiskAssessment

    intelligence = ContractIntelligence(
        clauses=[], violations=[],
        risk_assessment=RiskAssessment(0.0, "LOW", [], []),
        redlines=[],
    )

    assert intelligence.redlines_generated is True, "default is success"
