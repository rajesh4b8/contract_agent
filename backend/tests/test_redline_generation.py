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
        "clause_type": "Payment Terms",
        "issue": "Net 90 exceeds the 30-day limit",
        "severity": "CRITICAL",
        "suggested_fix": "Payment is due within thirty (30) days of invoice receipt.",
        "clause_content": "Customer shall pay each invoice within ninety (90) days of receipt.",
    },
    {
        "rule_id": "LIA-002",
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


def _draft(rule_id, text="Rewritten clause text.", why="Because the rule requires it."):
    return {"rule_id": rule_id, "suggested_text": text, "justification": why}


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
        llm=FakeLLM({"redlines": [_draft("PAY-001"), _draft("LIA-002")]})
    )

    redlines = json.loads(tool._run(json.dumps(VIOLATIONS)))
    by_rule = {r["rule_id"]: r for r in redlines}

    assert by_rule["PAY-001"]["priority"] == "CRITICAL"
    assert by_rule["LIA-002"]["priority"] == "HIGH"


def test_redlines_citing_unknown_rules_are_discarded():
    tool = RedlineGeneratorTool(
        llm=FakeLLM({"redlines": [_draft("PAY-001"), _draft("NOT-A-RULE")]})
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
