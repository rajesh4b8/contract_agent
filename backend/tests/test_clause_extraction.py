"""Clause extraction must reflect the contract it was given.

The tool this covers used to build an extraction prompt, discard it, and return
two hardcoded clauses — so every contract produced an identical risk report.
The load-bearing assertions here are that the output *varies with the input* and
that every quoted span is genuinely present in the source.
"""
import json

import pytest

from backend.agents.intelligence_tools import ClauseDetectorTool
from backend.agents.intelligence_tools import parse_clause_result
from backend.shared.models.clause_finding import ClauseFinding, RiskLevel

ACME = """MASTER SERVICES AGREEMENT between Acme Corp and Northwind Ltd.
3. PAYMENT. Client shall pay each invoice within ninety (90) days of receipt.
8. LIABILITY. Provider's aggregate liability is limited to $50,000.
"""

SHUTTLE = """SHUTTLE SERVICES CONTRACT between the City and Blue Line Transit.
4. FARES. The Operator shall remit collected fares monthly.
9. TERMINATION. The City may terminate immediately for convenience.
"""


class FakeLLM:
    """Returns a canned payload and records the prompt it was given."""

    def __init__(self, payload):
        self.payload = payload
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)

        class Response:
            content = json.dumps(self.payload)

        return Response()


def _finding(clause_type, span, risk="MEDIUM", confidence=0.9):
    return {
        "clause_type": clause_type,
        "evidence_span": span,
        "risk_level": risk,
        "confidence": confidence,
        "location": "",
        "violated_policy": None,
        "suggested_redline": None,
        "human_review_required": False,
    }


def test_extracted_spans_come_from_the_contract():
    """Every clause must quote text that is actually in the document."""
    payload = {"clauses": [
        _finding("Payment Terms", "Client shall pay each invoice within ninety (90) days of receipt."),
        _finding("Liability", "Provider's aggregate liability is limited to $50,000.", risk="HIGH"),
    ]}
    tool = ClauseDetectorTool(llm=FakeLLM(payload))

    clauses = parse_clause_result(tool._run(ACME))[0]

    assert len(clauses) == 2
    for clause in clauses:
        assert clause["evidence_span"] in ACME, (
            f"{clause['evidence_span']!r} is not in the contract — this is the "
            "check that fails when extraction is fabricated"
        )


def test_different_contracts_produce_different_clauses():
    """The regression that mattered: identical output for unrelated documents."""
    acme_tool = ClauseDetectorTool(llm=FakeLLM({"clauses": [
        _finding("Payment Terms", "Client shall pay each invoice within ninety (90) days of receipt."),
    ]}))
    shuttle_tool = ClauseDetectorTool(llm=FakeLLM({"clauses": [
        _finding("Termination", "The City may terminate immediately for convenience."),
    ]}))

    acme = parse_clause_result(acme_tool._run(ACME))[0]
    shuttle = parse_clause_result(shuttle_tool._run(SHUTTLE))[0]

    assert acme != shuttle
    assert acme[0]["evidence_span"] not in SHUTTLE
    assert shuttle[0]["evidence_span"] not in ACME


def test_ungrounded_clauses_are_dropped():
    """A span the model invented never reaches policy checking or risk scoring."""
    payload = {"clauses": [
        _finding("Payment Terms", "Client shall pay each invoice within ninety (90) days of receipt."),
        _finding("Indemnification", "Provider indemnifies Client for all losses whatsoever."),
    ]}
    tool = ClauseDetectorTool(llm=FakeLLM(payload))

    clauses = parse_clause_result(tool._run(ACME))[0]

    assert [c["clause_type"] for c in clauses] == ["Payment Terms"]


def test_the_contract_text_actually_reaches_the_model():
    """Guards against a prompt being built and then ignored."""
    tool = ClauseDetectorTool(llm=(fake := FakeLLM({"clauses": []})))

    tool._run(ACME)

    assert "Northwind" in fake.prompts[0]
    assert "ninety (90) days" in fake.prompts[0]


def test_without_an_llm_it_refuses_rather_than_inventing():
    with pytest.raises(ValueError, match="requires an llm"):
        ClauseDetectorTool(llm=None)._run(ACME)


def test_a_failing_model_surfaces_the_error():
    """An unavailable model must not look like a contract with no clauses.

    Returning [] here would produce a clean, plausible, zero-risk report — the
    same failure mode as the hardcoded stub this replaced.
    """
    class Boom:
        def invoke(self, prompt):
            raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")

    with pytest.raises(RuntimeError, match="RESOURCE_EXHAUSTED"):
        ClauseDetectorTool(llm=Boom())._run(ACME)


def test_a_contract_with_no_notable_clauses_returns_empty():
    """The other side of that coin: genuinely finding nothing is not an error."""
    tool = ClauseDetectorTool(llm=FakeLLM({"clauses": []}))

    assert parse_clause_result(tool._run(ACME))[0] == []


def test_wire_format_keeps_the_keys_existing_consumers_read():
    finding = ClauseFinding(
        clause_type="Liability",
        evidence_span="Provider's aggregate liability is limited to $50,000.",
        risk_level=RiskLevel.HIGH,
        confidence=0.91,
    )

    wire = finding.to_wire()

    # New canonical schema from the design doc.
    for key in ("clause_type", "risk_level", "evidence_span", "confidence",
                "violated_policy", "suggested_redline", "human_review_required"):
        assert key in wire
    # Legacy aliases the service layer and UI still read.
    assert wire["content"] == finding.evidence_span
    assert wire["confidence_score"] == pytest.approx(0.91)


@pytest.mark.parametrize("given,expected", [
    (0.9, 0.9),      # already a fraction: untouched
    (0.0, 0.0),
    (1.0, 1.0),
    (85.0, 0.85),    # models often emit a percentage
    (95.0, 0.95),
    (150.0, 1.0),    # beyond a percentage: clamped
    (-1.0, 0.0),
])
def test_confidence_is_normalised_into_range(given, expected):
    finding = ClauseFinding(
        clause_type="X", evidence_span="y", risk_level=RiskLevel.LOW, confidence=given
    )
    assert finding.confidence == pytest.approx(expected)


def test_grounding_tolerates_pdf_whitespace():
    """Extracted PDF text carries erratic line breaks; matching must survive them."""
    finding = ClauseFinding(
        clause_type="Payment Terms",
        evidence_span="Client shall pay each invoice within ninety (90) days",
        risk_level=RiskLevel.HIGH,
        confidence=0.9,
    )
    wrapped = "Client shall pay each\n   invoice within ninety (90)\ndays of receipt."

    assert finding.is_grounded_in(wrapped)
