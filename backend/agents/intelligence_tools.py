from langchain_core.tools import BaseTool
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field
from typing import Type, Dict, Any, List
from backend.domain.chunking import WINDOW_BUDGET_CHARS, canonical, pack_windows
from backend.domain.entities import ContractClause, PolicyViolation, RiskAssessment, RedlineRecommendation
from backend.shared.models.clause_finding import (
    ClauseExtraction,
    PolicyAssessment,
    RedlineSet,
)
from backend.shared.debug import note
from backend.shared.utils.message_content import content_to_text, strip_code_fence
import concurrent.futures
import contextvars
import hashlib
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
#
# This used to be the *whole* budget: `contract_text[:12000]` and the rest of the
# document was silently discarded. On the real contracts in `data/` that meant
# 96% of the Shell MESA, 83% of the Salesforce MSA and 64% of the sample shuttle
# contract were never looked at — and the result was reported as a completed
# review. It is now the size of one window, and every window is analysed.
CLAUSE_EXTRACTION_WINDOW = WINDOW_BUDGET_CHARS

#: Windows analysed at once. The provider SDKs retry 429s with their own
#: backoff, so the ceiling here is about not provoking them in the first place:
#: four concurrent calls turns the Shell MESA's 30 windows into about eight
#: rounds rather than thirty.
CLAUSE_EXTRACTION_CONCURRENCY = 4

#: Clauses per policy-checking call. Analysing the whole contract means a long
#: one now yields dozens rather than a handful, and an overlong prompt is
#: truncated by the provider rather than refused — so the last clauses would go
#: unchecked while the report called them compliant.
POLICY_CHECK_BATCH = 25


class ClauseExtractionFailed(RuntimeError):
    """No window could be analysed.

    A distinct type because the difference matters downstream: a generic error
    was caught by the orchestrator, turned into an empty clause list, and then
    persisted over a perfectly good previous review. "The model was unavailable"
    and "this contract has no notable clauses" have to stay distinguishable all
    the way to storage.
    """


def parse_clause_result(result_json: str) -> tuple:
    """Read the tool's output as `(clauses, coverage)`.

    Tolerates the bare list older callers expect, so a caller that has not been
    updated still gets its clauses rather than a KeyError — and is reported as
    complete coverage, which is what a bare list has always meant.
    """
    parsed = json.loads(result_json)
    if isinstance(parsed, list):
        return parsed, {"windows": 1, "failed": 0, "complete": True}
    return (
        parsed.get("clauses", []),
        parsed.get("coverage") or {"windows": 1, "failed": 0, "complete": True},
    )


class ClauseDetectorTool(BaseTool):
    name: str = "clause_detector"
    description: str = "Detect and extract key contract clauses"
    args_schema: Type[BaseModel] = ClauseDetectorInput
    llm: Any = None

    #: The version's recorded chunking, when the caller knows it. Analysis must
    #: chunk the contract exactly as the upload did, or the windows are built
    #: from different boundaries than the stored `INCLUDES` membership and the
    #: finding-to-chunk mapping — the thing that makes Increment 9 possible — is
    #: quietly wrong.
    chunking_profile: Any = None

    #: Chunk hashes whose findings are already known and correct, from the
    #: previous round. A window built entirely from these is skipped: its text
    #: has not changed, so re-analysing it would spend a model call to be told
    #: the same thing — and risk being told something slightly different, which
    #: would look like a change the counterparty did not make.
    unchanged_hashes: Any = None

    #: Findings carried forward for those windows, so the result is still a
    #: review of the whole contract rather than of its changed parts.
    carried_findings: Any = None

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

        from backend.infrastructure.chunking.identity import identify_chunks

        # The version's own profile, not a fresh default. A matter whose first
        # round was chunked with different sizes — or by a different extractor —
        # has stored chunks this would otherwise fail to reproduce, and every
        # finding's chunk attribution would point at boundaries that exist
        # nowhere but here.
        chunked = identify_chunks(contract_text, self.chunking_profile)
        windows = pack_windows(chunked.chunks)
        if not windows:
            logger.info("No text to extract clauses from")
            return json.dumps([])

        # Windows whose every chunk is unchanged since the previous round.
        unchanged = set(self.unchanged_hashes or ())
        to_analyse = [
            w for w in windows
            if not unchanged or not set(w.chunk_hashes).issubset(unchanged)
        ]
        skipped = len(windows) - len(to_analyse)

        logger.info(
            f"Extracting clauses from {len(contract_text):,} characters "
            f"in {len(windows)} window(s)"
            + (f"; {skipped} unchanged since the last round and skipped" if skipped else "")
        )
        if skipped:
            note("analysis", "windows_skipped", skipped=skipped, total=len(windows))

        # Concurrently, but bounded. Serially, the Shell MESA's 30 windows would
        # be 30 sequential model calls; unbounded, they would be 30 at once and
        # the provider would start refusing them.
        results: List[List[Any]] = [[] for _ in windows]
        # (window index, chunk hash) per finding, parallel to `results`.
        provenance: List[List[tuple]] = [[] for _ in windows]

        if not to_analyse:
            # Nothing changed at all. Every finding is carried forward, and the
            # coverage is complete because the whole document is accounted for.
            return json.dumps({
                "clauses": list(self.carried_findings or []),
                "coverage": {"windows": len(windows), "failed": 0, "complete": True,
                             "skipped": skipped},
            })
        failures: List[str] = []
        workers = min(CLAUSE_EXTRACTION_CONCURRENCY, len(to_analyse))

        # Each call runs under a copy of this request's context. A
        # ThreadPoolExecutor does not propagate contextvars, and the correlation
        # id lives in one — so without this the debug events and log lines from
        # thirty concurrent model calls arrive unattributed, and two reviews
        # running at once interleave with no way to tell them apart. The same
        # copy the analysis worker already makes for itself.
        context = contextvars.copy_context()

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(context.copy().run, self._extract_window, window): window
                for window in to_analyse
            }
            for future in concurrent.futures.as_completed(futures):
                window = futures[future]
                try:
                    found = future.result()
                    results[window.index] = found
                    provenance[window.index] = [
                        (window.index, *self._source_chunk(chunked, window, clause))
                        for clause in found
                    ]
                except Exception as e:
                    # One window failing must not discard the other 29. The
                    # failure is recorded and re-raised only if *every* window
                    # failed, because "the model was unavailable" and "this
                    # contract has no notable clauses" are different reports and
                    # conflating them is how the original stub went unnoticed.
                    logger.error(f"Clause extraction failed on window "
                                 f"{window.index + 1}/{len(windows)}: {e}")
                    failures.append(str(e))

        if failures and len(failures) == len(to_analyse):
            raise ClauseExtractionFailed(
                f"Clause extraction failed on all {len(to_analyse)} window(s): {failures[0]}"
            )
        if failures:
            logger.warning(
                f"{len(failures)} of {len(to_analyse)} analysed windows failed; the "
                f"review covers {len(to_analyse) - len(failures)} of them"
            )

        # In window order, so clause_index is stable and reads front-to-back.
        merged = self._merge_findings(results, provenance)
        logger.info(
            f"Extracted {len(merged)} clauses from {len(contract_text):,} characters "
            f"across {len(windows)} window(s)"
        )

        wire = []
        for clause, (window_index, chunk_hash, chunk_order) in merged:
            payload = clause.to_wire()
            # Carried so Increment 9 can re-analyse only the windows whose
            # chunks changed. Without it, selecting affected findings means
            # matching text — which is ambiguous exactly where it matters, on
            # clauses whose wording repeats.
            payload["source_chunk"] = chunk_hash
            # The chunk's position in the version's membership list. The hash
            # alone is not an occurrence: the repository stores repeated
            # identical text as **one** `(:Chunk)` with several `INCLUDES {order}`
            # relationships, so two copies of the same paragraph share a hash and
            # are told apart only by where they sit.
            payload["source_chunk_order"] = chunk_order
            payload["source_window"] = window_index
            wire.append(payload)

        # Coverage travels beside the findings, not on them. Marking each
        # finding meant that a run where one window failed and every other
        # returned nothing produced an empty list with no marker anywhere —
        # indistinguishable from a clean contract, which is the single worst
        # thing this pipeline can report.
        # Findings for the windows that were skipped, so the result describes
        # the whole contract rather than only the part that moved.
        carried = [
            finding for finding in (self.carried_findings or [])
            if finding.get("source_chunk") in unchanged
        ]

        return json.dumps({
            "clauses": carried + wire,
            "coverage": {
                "windows": len(windows),
                "analysed": len(to_analyse),
                "skipped": skipped,
                "failed": len(failures),
                "complete": not failures,
            },
        })

    @staticmethod
    def _source_chunk(chunked, window, clause) -> tuple:
        """Where in the version a finding's evidence sits: `(hash, order)`.

        **The hash alone is not an occurrence.** `ChunkRepository` stores
        repeated identical text as one `(:Chunk)` with several
        `INCLUDES {order}` relationships, so two copies of the same paragraph
        share a hash and are distinguished only by position. Deduplicating on
        the hash would therefore still collapse them — the very thing keying on
        the chunk was meant to stop.

        A span that straddles a boundary inside the window belongs to the chunk
        it *starts* in, found by longest matching prefix. Blindly taking the
        window's first chunk pointed at unrelated text, and Increment 9 would
        then miss the window when the real source chunk changed.
        """
        in_window = [c for c in chunked.chunks if c.order in
                     range(window.first_order, window.last_order + 1)]
        fallback = (in_window[0].hash, in_window[0].order) if in_window else ("", None)

        span = canonical(getattr(clause, "evidence_span", "") or "")
        if not span:
            return fallback

        for chunk in in_window:
            if span in canonical(chunk.content):
                return chunk.hash, chunk.order

        # Straddles a boundary: attribute it to where it begins.
        best, best_len = fallback, 0
        for chunk in in_window:
            body = canonical(chunk.content)
            # The longest prefix of the span that this chunk ends with.
            limit = min(len(span), len(body))
            for size in range(limit, max(0, best_len), -1):
                if body.endswith(span[:size]):
                    best, best_len = (chunk.hash, chunk.order), size
                    break
        return best

    def _extract_window(self, window) -> List[Any]:
        """One model call over one window, grounded against that window's text."""
        parser = PydanticOutputParser(pydantic_object=ClauseExtraction)

        prompt = f"""You are a contract analyst. Extract the clauses that matter for
legal review from the contract extract below.

Focus on these clause types: {", ".join(CLAUSE_TYPES_OF_INTEREST)}.
Include a clause only if it is genuinely present. It is correct to return fewer
clauses, or none, rather than invent one. This is one part of a longer contract,
so it is entirely normal for a part to contain none of these clause types.

For each clause set `evidence_span` to the clause text copied EXACTLY from the
extract — same words, same order. Do not paraphrase or summarise. Judge
`risk_level` from the perspective of the party receiving this contract: unusual,
one-sided or open-ended terms are higher risk. Set `human_review_required` when
the clause is HIGH or CRITICAL risk, or when you are unsure.

Leave `violated_policy` and `suggested_redline` null.

CONTRACT EXTRACT:
{window.text}

{parser.get_format_instructions()}"""

        # No retry wrapper here: the provider SDKs already retry 429/503 with
        # their own backoff (google-genai walks 1s -> 17s before giving up).
        # Adding a second layer tripled a 34s failure into a 100s+ one.
        response = self.llm.invoke(prompt)
        extraction = parser.parse(strip_code_fence(content_to_text(response.content)))

        # Grounded against *this window*, not the whole contract. Checking
        # against the full text would let a finding invented for window 3 pass
        # because similar words happen to appear in window 17.
        grounded, ungrounded = [], []
        for clause in extraction.clauses:
            (grounded if clause.is_grounded_in(window.text) else ungrounded).append(clause)

        if ungrounded:
            logger.warning(
                f"Window {window.index + 1}: dropped {len(ungrounded)} ungrounded "
                f"clause(s) whose evidence span was absent: "
                f"{[c.clause_type for c in ungrounded]}"
            )
        return grounded

    @staticmethod
    def _merge_findings(per_window: List[List[Any]],
                        provenance: List[List[tuple]]) -> List[tuple]:
        """Flatten the windows' findings, dropping duplicate *reports*.

        Identity is the clause type, a hash of the canonical evidence span, and
        **the chunk occurrence the span sits in** — its hash *and* its position
        in the membership list. The occurrence is what makes this a report of
        one occurrence rather than of one wording: a contract can legitimately
        contain the same notice or payment provision twice, in two schedules,
        and those are two clauses a reviewer has to see. Keying on the text
        alone silently dropped the second — and because the policy layer is
        index-based precisely so that duplicate text can be told apart, dropping
        it could take a real violation with it.

        The canonical form is the same normalisation chunk identity uses, so a
        difference in whitespace or case does not read as a second finding.

        The first report wins, which is the earlier window, so a clause is
        attributed to where it starts.
        """
        seen = set()
        merged: List[tuple] = []
        for findings, sources in zip(per_window, provenance):
            for clause, source in zip(findings, sources):
                span = canonical(getattr(clause, "evidence_span", "") or "")
                key = (
                    (getattr(clause, "clause_type", "") or "").strip().casefold(),
                    hashlib.sha256(span.encode("utf-8")).hexdigest(),
                    # The occurrence, not the text: (hash, order). Repeated
                    # identical paragraphs share a hash and differ only in where
                    # they sit, so the order is what keeps them two findings.
                    source[1], source[2],
                )
                if key in seen:
                    continue
                seen.add(key)
                merged.append((clause, source))
        return merged

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
        # Config checks come first: an unseeded tenant must fail even when the
        # contract yielded no clauses, or "nothing to check" is indistinguishable
        # from "nothing configured to check against".
        if not self.rules:
            # No playbook seeded. Reporting zero violations would read as "this
            # contract is compliant", which is a different claim entirely.
            raise ValueError(
                "No policy rules available for this tenant. Seed a playbook with "
                "`make seed-playbook` before running compliance checks."
            )
        if self.llm is None:
            raise ValueError("PolicyCheckerTool requires an llm to evaluate clauses")
        if not clauses:
            return json.dumps([])

        by_id = {rule.id: rule for rule in self.rules}

        # Batched, for the same reason clause extraction is windowed. This used
        # to put every clause in one prompt, which was fine while a contract
        # yielded a handful — but now that the whole document is analysed, a long
        # one yields dozens, and a prompt that overflows gets truncated by the
        # provider rather than refused, so the last clauses would be silently
        # unchecked while the report claimed they were compliant.
        assessments = []
        for offset in range(0, len(clauses), POLICY_CHECK_BATCH):
            batch = clauses[offset:offset + POLICY_CHECK_BATCH]
            assessments.extend(self._check_batch(batch, offset))

        violations, discarded = [], []
        for finding in assessments:
            rule = by_id.get(finding.rule_id)
            if rule is None or not 0 <= finding.clause_index < len(clauses):
                discarded.append(finding.rule_id)
                continue
            clause = clauses[finding.clause_index]
            violations.append({
                "rule_id": rule.id,
                # Kept so clauses are stamped by index rather than by matching
                # text: two clauses can share an evidence span, and a text join
                # would cite a breach on both.
                "clause_index": finding.clause_index,
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
            f"Checked {len(clauses)} clauses against {len(self.rules)} rules "
            f"in {max(1, (len(clauses) + POLICY_CHECK_BATCH - 1) // POLICY_CHECK_BATCH)} "
            f"batch(es): {len(violations)} violations"
        )
        return json.dumps(violations)

    def _check_batch(self, batch: List[Dict[str, Any]], offset: int) -> List[Any]:
        """One model call over one batch of clauses.

        `offset` maps the batch's local clause numbering back to the caller's
        global list. Without it every batch after the first would cite breaches
        against clauses 0..n of the *whole* contract — attributing the wrong
        text to the wrong rule, which is worse than missing the breach.
        """
        parser = PydanticOutputParser(pydantic_object=PolicyAssessment)

        rules_block = "\n".join(
            f"- {r.id} [{r.severity}] ({r.section_reference}): {r.rule_text}"
            for r in self.rules
        )
        clauses_block = "\n".join(
            f"- clause {offset + i} [{c.get('clause_type', 'Unknown')}]: "
            f"{' '.join((c.get('evidence_span') or c.get('content') or '').split())}"
            for i, c in enumerate(batch)
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
        return list(assessment.violations)


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
# Redline Generation Agent Tools
class RedlineGeneratorInput(BaseModel):
    violations_json: str = Field(description="JSON string of policy violations")


class RedlineGeneratorTool(BaseTool):
    """Draft replacement language for each violated clause.

    This previously looked up a constant from a five-branch if/elif on clause
    type, so every payment violation in every contract got the same sentence.
    Those templates ignore the document's own defined terms, party names and
    numbering, which makes them something a lawyer has to rewrite rather than
    paste. The model now redrafts the specific clause, and the playbook's own
    wording is passed in as the target to hit.
    """

    name: str = "redline_generator"
    description: str = "Draft replacement language for clauses that breach policy"
    args_schema: Type[BaseModel] = RedlineGeneratorInput
    llm: Any = None

    def _run(self, violations_json: str) -> str:
        violations = json.loads(violations_json)
        if not violations:
            return json.dumps([])
        if self.llm is None:
            raise ValueError("RedlineGeneratorTool requires an llm to draft redlines")

        # Only violations that cite a rule can be remediated: the rule is what
        # defines what "fixed" means, and it supplies the priority.
        citable = [v for v in violations if v.get("rule_id")]
        if not citable:
            logger.warning("No violations cite a rule; nothing to redline")
            return json.dumps([])

        # Keyed by (rule, clause): the same rule can be breached by several
        # clauses, and keying on the rule alone would collapse them — every
        # suggestion for that rule would then attach to whichever breach came
        # last, persisting a redline against the wrong clause text.
        by_breach = {(v["rule_id"], v.get("clause_index")): v for v in citable}
        parser = PydanticOutputParser(pydantic_object=RedlineSet)

        block = "\n\n".join(
            f"BREACH {i}: rule {v['rule_id']}, clause_index {v.get('clause_index')}\n"
            f"RULE {v['rule_id']} requires: {v.get('suggested_fix') or '(no standard wording given)'}\n"
            f"WHAT IS WRONG: {v.get('issue', '')}\n"
            f"CURRENT CLAUSE: {' '.join((v.get('clause_content') or '').split())}"
            for i, v in enumerate(citable)
        )

        prompt = f"""You are a contract lawyer preparing redlines. For each breach
below, rewrite the clause so it complies with the rule.

{block}

Write `suggested_text` as language that could replace the current clause in the
document: keep the contract's own defined terms, party names, numbering and
drafting style, and change only what the rule requires. Do not copy the rule's
standard wording verbatim if the clause uses different defined terms — adapt it.
Keep every protection the current clause already provides that the rule does not
object to.

In `justification`, state what the rule requires and what you changed. Use
`rule_id` and `clause_index` exactly as written above, so each redline is matched
back to the breach it fixes — the same rule may appear more than once.

{parser.get_format_instructions()}"""

        response = self.llm.invoke(prompt)
        drafted = parser.parse(strip_code_fence(content_to_text(response.content)))

        redlines, unknown = [], []
        for suggestion in drafted.redlines:
            violation = by_breach.get((suggestion.rule_id, suggestion.clause_index))
            if violation is None:
                unknown.append(f"{suggestion.rule_id}@{suggestion.clause_index}")
                continue
            redlines.append({
                "rule_id": suggestion.rule_id,
                "clause_index": suggestion.clause_index,
                "clause_type": violation.get("clause_type", ""),
                "original_text": violation.get("clause_content", ""),
                "suggested_text": suggestion.suggested_text,
                "justification": suggestion.justification,
                # Priority follows the rule's severity so it cannot drift.
                "priority": violation.get("severity", "MEDIUM"),
            })

        if unknown:
            logger.warning(f"Discarded redlines citing unknown rule/clause pairs: {unknown}")

        logger.info(f"Drafted {len(redlines)} redlines for {len(citable)} violations")
        return json.dumps(redlines)
