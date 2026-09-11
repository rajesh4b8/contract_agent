"""The per-clause output contract.

Mirrors the schema in AI-Powered-Smart-Contract-Review-Guide.pdf ("Output
Schema"): every reviewed clause carries its type, risk level, the policy it
violates, the evidence span supporting that finding, a suggested redline, a
confidence score, and whether a human needs to look at it.

Two of those fields are filled in by later increments — ``violated_policy`` when
policy checks are grounded in a real playbook, ``suggested_redline`` when
redlines are LLM-generated. They are declared here so the shape is settled once.
"""
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ClauseFinding(BaseModel):
    """One clause the reviewer needs to see, and why."""

    clause_type: str = Field(
        description="Category, e.g. Payment Terms, Liability, Indemnification, "
                    "Termination, IP Ownership, Confidentiality"
    )
    evidence_span: str = Field(
        description="The clause text quoted VERBATIM from the contract. Copy it "
                    "exactly; do not paraphrase, summarise or re-word."
    )
    risk_level: RiskLevel = Field(description="LOW, MEDIUM, HIGH or CRITICAL")
    confidence: float = Field(description="Confidence in this finding, 0.0-1.0")
    location: str = Field(default="", description="Where it sits, e.g. 'Section 8'")
    violated_policy: Optional[str] = Field(
        default=None, description="Identifier of the playbook rule this breaches, if any"
    )
    suggested_redline: Optional[str] = Field(
        default=None, description="Proposed replacement language, if any"
    )
    human_review_required: bool = Field(
        default=False, description="True when a lawyer must review this clause"
    )

    @field_validator("confidence")
    @classmethod
    def _normalise_confidence(cls, v: float) -> float:
        """Coerce the model's answer into 0.0-1.0.

        Models are asked for a fraction but frequently return a percentage
        ("confidence": 85). A value in (1, 100] is read as a percentage; anything
        beyond that, or below zero, is clamped.
        """
        if 1.0 < v <= 100.0:
            v = v / 100.0
        return max(0.0, min(1.0, v))

    def is_grounded_in(self, source_text: str) -> bool:
        """Whether the evidence span is genuinely present in the contract.

        The one check that distinguishes a real extraction from a fabricated one.
        Whitespace is normalised because PDF text carries erratic line breaks.
        """
        return _normalise(self.evidence_span) in _normalise(source_text)

    def to_wire(self) -> Dict[str, Any]:
        """Serialise for the API and the graph.

        Also emits the legacy keys the service layer and the frontend still read
        (``content``, ``confidence_score``). Those aliases go away once the
        consumers are migrated; until then, dropping them would break the UI.
        """
        payload = self.model_dump(mode="json")
        payload["content"] = self.evidence_span
        payload["confidence_score"] = self.confidence
        return payload


class ClauseExtraction(BaseModel):
    """Wrapper so the parser has a single root object to target."""

    clauses: List[ClauseFinding] = Field(default_factory=list)


def _normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


class PolicyBreach(BaseModel):
    """One clause breaching one playbook rule, as reported by the model.

    Deliberately narrow: the model says *which* rule and *which* clause and why.
    Severity and the suggested redline come from the playbook rule itself, so
    they cannot drift between runs.
    """

    rule_id: str = Field(description="Id of the breached rule, exactly as given")
    clause_index: int = Field(description="Index of the breaching clause in the supplied list")
    issue: str = Field(description="What this clause does that the rule forbids")


class PolicyAssessment(BaseModel):
    """Wrapper so the parser has a single root object to target."""

    violations: List[PolicyBreach] = Field(default_factory=list)


class RedlineSuggestion(BaseModel):
    """Replacement language for one clause that breaches one rule.

    The model rewrites the clause in front of it. A generic template pasted from
    the playbook is what this replaced: it ignored the contract's own defined
    terms, party names and numbering, so it could not be pasted into the document.
    """

    rule_id: str = Field(description="Id of the rule being remediated, exactly as given")
    suggested_text: str = Field(
        description="Replacement language for this specific clause, written to fit "
                    "the contract's own defined terms and drafting style"
    )
    justification: str = Field(
        description="Why this change is needed, citing what the rule requires"
    )


class RedlineSet(BaseModel):
    """Wrapper so the parser has a single root object to target."""

    redlines: List[RedlineSuggestion] = Field(default_factory=list)
