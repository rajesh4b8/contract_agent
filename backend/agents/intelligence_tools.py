from langchain_core.tools import BaseTool
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field
from typing import Type, Dict, Any, List
from backend.domain.entities import ContractClause, PolicyViolation, RiskAssessment, RedlineRecommendation
from backend.shared.models.clause_finding import ClauseExtraction, PolicyAssessment
from backend.shared.utils.message_content import content_to_text, strip_code_fence
import json
import logging

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

# Company policy rules - merged existing with comprehensive internal policies
COMPANY_POLICIES = {
    "payment_terms": {
        "preferred_days": 30,
        "acceptable_days": 45,  # requires Delivery Director approval
        "red_flags": [60, 90],
        "redline_text": "Payment is due within thirty (30) days of invoice receipt."
    },
    "liability_cap": {
        "preferred_multiplier": 1,  # 1x total fees
        "acceptable_multiplier": 2,  # requires Legal approval
        "min_amount": 100000,  # legacy minimum
        "red_flags": ["unlimited", "indirect_damages", "consequential_damages"],
        "redline_text": "Our liability shall not exceed the total fees paid or payable under the applicable Statement of Work."
    },
    "indemnification": {
        "preferred_type": "mutual",
        "acceptable_scope": ["third_party_ip", "gross_negligence", "willful_misconduct"],
        "red_flags": ["broad_indemnification", "client_negligence", "open_ended_defense"],
        "redline_text": "Each party will indemnify the other solely for third-party claims arising from gross negligence, willful misconduct, or infringement of IP under this Agreement."
    },
    "termination": {
        "min_notice_days": 30,
        "payment_required": "work_in_progress",
        "red_flags": ["immediate_termination", "no_payment_wip"],
        "redline_text": "Either party may terminate this SOW with thirty (30) days' written notice. All completed work shall be payable upon termination."
    },
    "ip_ownership": {
        "company_retains": "pre_existing_ip",
        "client_owns": "deliverables",
        "red_flags": ["client_claims_company_ip", "assignment_without_carveouts"],
        "redline_text": "Client owns deliverables created specifically for the engagement. Company retains ownership of its pre-existing IP, reusable tools, and methodologies."
    },
    "confidentiality": {
        "required": True,
        "mutual": True,
        "redline_text": "Both parties agree to maintain confidentiality of all proprietary information."
    }
}

# Clause Extraction Agent Tools
class ClauseDetectorInput(BaseModel):
    contract_text: str = Field(description="Contract text to analyze for clauses")

CLAUSE_TYPES_OF_INTEREST = [
    "Payment Terms",
    "Liability",
    "Indemnification",
    "Confidentiality",
    "Termination",
    "IP Ownership",
]

# Characters of contract text sent to the model in one pass.
CLAUSE_EXTRACTION_WINDOW = 12000


class ClauseDetectorTool(BaseTool):
    name: str = "clause_detector"
    description: str = "Detect and extract key contract clauses"
    args_schema: Type[BaseModel] = ClauseDetectorInput
    llm: Any = None

    def _run(self, contract_text: str) -> str:
        """Extract clauses from the contract, returning a JSON array.

        Every returned clause quotes the contract verbatim in ``evidence_span``.
        Findings whose span is not actually present in the source are dropped:
        an ungrounded clause is a hallucination, and downstream policy checks and
        risk scores would inherit it.
        """
        if self.llm is None:
            # Refuse rather than invent. This tool previously returned two
            # hardcoded clauses regardless of input, which made every contract
            # produce an identical risk report.
            raise ValueError("ClauseDetectorTool requires an llm to extract clauses")

        text = contract_text[:CLAUSE_EXTRACTION_WINDOW]
        parser = PydanticOutputParser(pydantic_object=ClauseExtraction)

        prompt = f"""You are a contract analyst. Extract the clauses that matter for
legal review from the contract below.

Focus on these clause types: {", ".join(CLAUSE_TYPES_OF_INTEREST)}.
Include a clause only if it is genuinely present. It is correct to return fewer
clauses, or none, rather than invent one.

For each clause set `evidence_span` to the clause text copied EXACTLY from the
contract — same words, same order. Do not paraphrase or summarise. Judge
`risk_level` from the perspective of the party receiving this contract: unusual,
one-sided or open-ended terms are higher risk. Set `human_review_required` when
the clause is HIGH or CRITICAL risk, or when you are unsure.

Leave `violated_policy` and `suggested_redline` null.

CONTRACT:
{text}

{parser.get_format_instructions()}"""

        # No retry wrapper here: the provider SDKs already retry 429/503 with
        # their own backoff (google-genai walks 1s -> 17s before giving up).
        # Adding a second layer tripled a 34s failure into a 100s+ one.
        #
        # Failures propagate rather than returning an empty list. "The model was
        # unavailable" and "this contract has no notable clauses" produce very
        # different reports, and conflating them is how the original stub went
        # unnoticed. Callers already handle the exception.
        response = self.llm.invoke(prompt)
        extraction = parser.parse(strip_code_fence(content_to_text(response.content)))

        grounded, ungrounded = [], []
        for clause in extraction.clauses:
            (grounded if clause.is_grounded_in(text) else ungrounded).append(clause)

        if ungrounded:
            logger.warning(
                f"Dropped {len(ungrounded)} ungrounded clause(s) whose evidence span "
                f"was absent from the contract: "
                f"{[c.clause_type for c in ungrounded]}"
            )

        logger.info(f"Extracted {len(grounded)} clauses from {len(text):,} characters")
        return json.dumps([c.to_wire() for c in grounded])

# Policy Compliance Agent Tools
class PolicyCheckerInput(BaseModel):
    clauses_json: str = Field(description="JSON string of extracted clauses")


class PolicyCheckerTool(BaseTool):
    """Check clauses against the playbook rules loaded from the graph.

    This previously matched hardcoded keywords against an in-code dict, so a
    finding could say "payment terms exceed company policy" but could not name
    the rule, and changing policy meant changing Python. Rules now arrive as
    data and every violation carries the id of the rule that produced it.
    """

    name: str = "policy_checker"
    description: str = "Check clauses against the tenant's policy playbook"
    args_schema: Type[BaseModel] = PolicyCheckerInput
    llm: Any = None
    rules: List[Any] = Field(default_factory=list)

    def _run(self, clauses_json: str) -> str:
        clauses = json.loads(clauses_json)
        if not clauses:
            return json.dumps([])
        if not self.rules:
            # No playbook seeded. Reporting zero violations would read as "this
            # contract is compliant", which is a different claim entirely.
            raise ValueError(
                "No policy rules available for this tenant. Seed a playbook with "
                "`make seed-playbook` before running compliance checks."
            )
        if self.llm is None:
            raise ValueError("PolicyCheckerTool requires an llm to evaluate clauses")

        by_id = {rule.id: rule for rule in self.rules}
        parser = PydanticOutputParser(pydantic_object=PolicyAssessment)

        rules_block = "\n".join(
            f"- {r.id} [{r.severity}] ({r.section_reference}): {r.rule_text}"
            for r in self.rules
        )
        clauses_block = "\n".join(
            f"- clause {i} [{c.get('clause_type', 'Unknown')}]: "
            f"{' '.join((c.get('evidence_span') or c.get('content') or '').split())}"
            for i, c in enumerate(clauses)
        )

        prompt = f"""You are a contract compliance reviewer. Decide which clauses
breach which playbook rules.

PLAYBOOK RULES:
{rules_block}

CONTRACT CLAUSES:
{clauses_block}

Report one entry per genuine breach. Use `rule_id` exactly as written above and
`clause_index` for the clause number. A clause may breach more than one rule, and
most clauses breach none — returning an empty list is the correct answer for a
compliant contract. Do not report a breach merely because a topic is mentioned.
In `issue`, say specifically what the clause does that the rule forbids.

{parser.get_format_instructions()}"""

        response = self.llm.invoke(prompt)
        assessment = parser.parse(strip_code_fence(content_to_text(response.content)))

        violations, discarded = [], []
        for finding in assessment.violations:
            rule = by_id.get(finding.rule_id)
            if rule is None or not 0 <= finding.clause_index < len(clauses):
                discarded.append(finding.rule_id)
                continue
            clause = clauses[finding.clause_index]
            violations.append({
                "rule_id": rule.id,
                "clause_type": clause.get("clause_type", "Unknown"),
                "issue": finding.issue,
                # Severity comes from the playbook, not the model: it drives the
                # risk score and must not drift run to run.
                "severity": rule.severity,
                "suggested_fix": rule.redline_text,
                "clause_content": clause.get("evidence_span") or clause.get("content", ""),
                "section_reference": rule.section_reference,
            })

        if discarded:
            logger.warning(
                f"Discarded {len(discarded)} violation(s) citing unknown rules or "
                f"clauses: {discarded}"
            )

        logger.info(
            f"Checked {len(clauses)} clauses against {len(self.rules)} rules: "
            f"{len(violations)} violations"
        )
        return json.dumps(violations)


# Risk Assessment Agent Tools
class RiskCalculatorInput(BaseModel):
    clauses_json: str = Field(description="JSON string of clauses")
    violations_json: str = Field(description="JSON string of violations")

class RiskCalculatorTool(BaseTool):
    name: str = "risk_calculator"
    description: str = "Calculate overall contract risk score"
    args_schema: Type[BaseModel] = RiskCalculatorInput
    
    def _run(self, clauses_json: str, violations_json: str) -> str:
        """Calculate risk assessment"""
        try:
            clauses = json.loads(clauses_json)
            violations = json.loads(violations_json)
            
            # Calculate base risk from clauses
            risk_score = 30.0  # Base risk
            
            # Add risk from violations
            for violation in violations:
                severity = violation.get("severity", "LOW")
                if severity == "CRITICAL":
                    risk_score += 25
                elif severity == "HIGH":
                    risk_score += 15
                elif severity == "MEDIUM":
                    risk_score += 10
                else:
                    risk_score += 5
            
            # Cap at 100
            risk_score = min(risk_score, 100.0)
            
            # Determine risk level
            if risk_score >= 80:
                risk_level = "CRITICAL"
            elif risk_score >= 60:
                risk_level = "HIGH"
            elif risk_score >= 40:
                risk_level = "MEDIUM"
            else:
                risk_level = "LOW"
            
            # Generate recommendations
            recommendations = []
            if len(violations) > 0:
                recommendations.append("Address policy violations before signing")
            if risk_score > 70:
                recommendations.append("Requires legal review and approval")
            
            critical_issues = [v["issue"] for v in violations if v.get("severity") == "CRITICAL"]
            
            assessment = {
                "overall_risk_score": risk_score,
                "risk_level": risk_level,
                "critical_issues": critical_issues,
                "recommendations": recommendations
            }
            
            logger.info(f"Risk assessment: {risk_level} ({risk_score}/100)")
            return json.dumps(assessment)
            
        except Exception as e:
            logger.error(f"Risk calculation failed: {e}")
            return json.dumps({"overall_risk_score": 50.0, "risk_level": "MEDIUM", "critical_issues": [], "recommendations": []})

# Redline Generation Agent Tools
class RedlineGeneratorInput(BaseModel):
    violations_json: str = Field(description="JSON string of policy violations")

class RedlineGeneratorTool(BaseTool):
    name: str = "redline_generator"
    description: str = "Generate redline recommendations for violations"
    args_schema: Type[BaseModel] = RedlineGeneratorInput
    
    def _run(self, violations_json: str) -> str:
        """Generate redline recommendations"""
        try:
            violations = json.loads(violations_json)
            redlines = []
            
            for violation in violations:
                clause_type = violation.get("clause_type", "")
                issue = violation.get("issue", "")
                suggested_fix = violation.get("suggested_fix", "")
                original_text = violation.get("clause_content", "")
                
                if "payment" in clause_type.lower():
                    redlines.append({
                        "original_text": original_text,
                        "suggested_text": COMPANY_POLICIES["payment_terms"]["redline_text"],
                        "justification": "Aligns with company payment policy (Net 30 preferred)",
                        "priority": "HIGH"
                    })
                
                elif "liability" in clause_type.lower():
                    redlines.append({
                        "original_text": original_text,
                        "suggested_text": COMPANY_POLICIES["liability_cap"]["redline_text"],
                        "justification": "Caps liability at 1x SOW fees per company policy",
                        "priority": "CRITICAL"
                    })
                
                elif "indemnif" in clause_type.lower():
                    redlines.append({
                        "original_text": original_text,
                        "suggested_text": COMPANY_POLICIES["indemnification"]["redline_text"],
                        "justification": "Limits indemnification to mutual third-party claims only",
                        "priority": "CRITICAL"
                    })
                
                elif "terminat" in clause_type.lower():
                    redlines.append({
                        "original_text": original_text,
                        "suggested_text": COMPANY_POLICIES["termination"]["redline_text"],
                        "justification": "Ensures 30-day notice and payment for work-in-progress",
                        "priority": "HIGH"
                    })
                
                elif "ip" in clause_type.lower() or "intellectual property" in clause_type.lower():
                    redlines.append({
                        "original_text": original_text,
                        "suggested_text": COMPANY_POLICIES["ip_ownership"]["redline_text"],
                        "justification": "Protects company pre-existing IP and methodologies",
                        "priority": "CRITICAL"
                    })
            
            logger.info(f"Generated {len(redlines)} redline recommendations")
            return json.dumps(redlines)
            
        except Exception as e:
            logger.error(f"Redline generation failed: {e}")
            return json.dumps([])