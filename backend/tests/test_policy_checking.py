"""Policy checks must cite the playbook rule they came from.

This replaced keyword matching against an in-code `COMPANY_POLICIES` dict. That
version could report "payment terms exceed company policy" but could not say
*which* rule, and policy could only change by editing Python. The properties
worth protecting are that findings cite a real rule, that severity comes from
the playbook rather than the model, and that a missing playbook is an error
rather than a clean bill of health.
"""
import json

import pytest

from backend.agents.contract_intelligence_agents import _attach_violated_policy
from backend.agents.intelligence_tools import PolicyCheckerTool
from backend.infrastructure.playbook_loader import LoadedRule

RULES = [
    LoadedRule(
        id="PAY-001",
        rule_text="Payment is due within thirty (30) days. Net 60 or longer is prohibited.",
        rule_type="mandatory",
        applies_to=["general"],
        severity="CRITICAL",
        section_reference="Payment Terms",
        redline_text="Payment is due within thirty (30) days of invoice receipt.",
    ),
    LoadedRule(
        id="LIA-002",
        rule_text="A fixed liability cap below USD 100,000 is unacceptable.",
        rule_type="mandatory",
        applies_to=["general"],
        severity="HIGH",
        section_reference="Limitation of Liability",
        redline_text="Liability shall not exceed fees paid under the applicable SOW.",
    ),
]

CLAUSES = [
    {"clause_type": "Payment Terms", "evidence_span": "Customer shall pay each invoice within ninety (90) days."},
    {"clause_type": "Liability", "evidence_span": "Provider's liability is limited to $50,000."},
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


def _breach(rule_id, clause_index, issue="breaches the rule"):
    return {"rule_id": rule_id, "clause_index": clause_index, "issue": issue}


def test_violations_cite_the_rule_that_produced_them():
    tool = PolicyCheckerTool(
        llm=FakeLLM({"violations": [_breach("PAY-001", 0, "Net 90 exceeds the 30-day limit")]}),
        rules=RULES,
    )

    violations = json.loads(tool._run(json.dumps(CLAUSES)))

    assert len(violations) == 1
    assert violations[0]["rule_id"] == "PAY-001"
    assert violations[0]["section_reference"] == "Payment Terms"
    assert violations[0]["issue"] == "Net 90 exceeds the 30-day limit"


def test_severity_comes_from_the_playbook_not_the_model():
    """Severity drives the risk score, so it must not drift between runs."""
    tool = PolicyCheckerTool(
        llm=FakeLLM({"violations": [
            # The model is not asked for severity, but even if it volunteered one
            # the rule's value is what must be used.
            {"rule_id": "LIA-002", "clause_index": 1, "issue": "cap is $50k",
             "severity": "LOW"},
        ]}),
        rules=RULES,
    )

    violations = json.loads(tool._run(json.dumps(CLAUSES)))

    assert violations[0]["severity"] == "HIGH", "must be the rule's severity, not the model's"


def test_the_suggested_fix_is_the_rule_s_redline():
    tool = PolicyCheckerTool(llm=FakeLLM({"violations": [_breach("PAY-001", 0)]}), rules=RULES)

    violations = json.loads(tool._run(json.dumps(CLAUSES)))

    assert violations[0]["suggested_fix"] == RULES[0].redline_text


def test_violations_citing_unknown_rules_are_discarded():
    """A model that invents a rule id must not create an uncitable finding."""
    tool = PolicyCheckerTool(
        llm=FakeLLM({"violations": [_breach("PAY-001", 0), _breach("MADE-UP-999", 1)]}),
        rules=RULES,
    )

    violations = json.loads(tool._run(json.dumps(CLAUSES)))

    assert [v["rule_id"] for v in violations] == ["PAY-001"]


def test_violations_pointing_at_no_such_clause_are_discarded():
    tool = PolicyCheckerTool(llm=FakeLLM({"violations": [_breach("PAY-001", 99)]}), rules=RULES)

    assert json.loads(tool._run(json.dumps(CLAUSES))) == []


def test_a_compliant_contract_yields_no_violations():
    tool = PolicyCheckerTool(llm=FakeLLM({"violations": []}), rules=RULES)

    assert json.loads(tool._run(json.dumps(CLAUSES))) == []


def test_rules_and_clauses_both_reach_the_model():
    fake = FakeLLM({"violations": []})
    PolicyCheckerTool(llm=fake, rules=RULES)._run(json.dumps(CLAUSES))

    prompt = fake.prompts[0]
    assert "PAY-001" in prompt and "LIA-002" in prompt
    assert "ninety (90) days" in prompt


def test_no_playbook_is_an_error_not_a_clean_bill_of_health():
    """Zero rules must not silently render as "no violations found"."""
    tool = PolicyCheckerTool(llm=FakeLLM({"violations": []}), rules=[])

    with pytest.raises(ValueError, match="(?i)seed a playbook"):
        tool._run(json.dumps(CLAUSES))


def test_no_clauses_short_circuits_without_calling_the_model():
    fake = FakeLLM({"violations": []})
    tool = PolicyCheckerTool(llm=fake, rules=RULES)

    assert json.loads(tool._run(json.dumps([]))) == []
    assert fake.prompts == []


class TestAttachViolatedPolicy:
    def test_clause_records_the_rule_it_breaches(self):
        violations = [{"rule_id": "PAY-001", "clause_index": 0}]

        stamped = _attach_violated_policy(CLAUSES, violations)

        assert stamped[0]["violated_policy"] == "PAY-001"
        assert stamped[1]["violated_policy"] is None

    def test_multiple_breaches_are_listed(self):
        violations = [
            {"rule_id": "LIA-001", "clause_index": 1},
            {"rule_id": "LIA-002", "clause_index": 1},
        ]

        stamped = _attach_violated_policy(CLAUSES, violations)

        assert stamped[1]["violated_policy"] == "LIA-001, LIA-002"

    def test_identical_clause_text_does_not_spread_a_citation(self):
        """Attachment is by index, not by text.

        Two clauses can carry the same evidence span — a repeated boilerplate
        paragraph, or the same span extracted under two clause types. Joining on
        the text would cite a breach found in one against both.
        """
        duplicated = [
            {"clause_type": "Liability", "evidence_span": "Liability is capped at $50,000."},
            {"clause_type": "Indemnification", "evidence_span": "Liability is capped at $50,000."},
        ]

        stamped = _attach_violated_policy(duplicated, [{"rule_id": "LIA-002", "clause_index": 0}])

        assert stamped[0]["violated_policy"] == "LIA-002"
        assert stamped[1]["violated_policy"] is None, "citation leaked to an identical clause"

    def test_a_violation_without_an_index_is_ignored(self):
        stamped = _attach_violated_policy(CLAUSES, [{"rule_id": "PAY-001"}])

        assert all(c["violated_policy"] is None for c in stamped)

    def test_original_clause_fields_survive(self):
        stamped = _attach_violated_policy(CLAUSES, [])

        assert stamped[0]["clause_type"] == "Payment Terms"
        assert stamped[0]["evidence_span"] == CLAUSES[0]["evidence_span"]


def test_violations_carry_the_clause_index_for_attachment():
    tool = PolicyCheckerTool(llm=FakeLLM({"violations": [_breach("PAY-001", 0)]}), rules=RULES)

    violations = json.loads(tool._run(json.dumps(CLAUSES)))

    assert violations[0]["clause_index"] == 0


def test_an_unseeded_tenant_fails_even_with_no_clauses():
    """Config faults must not hide behind an empty contract."""
    tool = PolicyCheckerTool(llm=FakeLLM({"violations": []}), rules=[])

    with pytest.raises(ValueError, match="(?i)seed a playbook"):
        tool._run(json.dumps([]))


def test_every_violation_can_cite_a_rule():
    """The guarantee this increment exists to provide.

    CUAD keyword deviations used to be merged into policy_violations. They are
    heuristics with no playbook rule behind them, so a consumer could not tell a
    cited breach from a guess. They are now returned separately under
    cuad_analysis.deviations.
    """
    tool = PolicyCheckerTool(
        llm=FakeLLM({"violations": [_breach("PAY-001", 0), _breach("LIA-002", 1)]}),
        rules=RULES,
    )

    violations = json.loads(tool._run(json.dumps(CLAUSES)))

    assert violations, "expected violations for this fixture"
    for violation in violations:
        assert violation["rule_id"], f"violation without a rule id: {violation}"
        assert violation["rule_id"] in {r.id for r in RULES}
