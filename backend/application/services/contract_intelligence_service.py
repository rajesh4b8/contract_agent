from backend.agents.contract_intelligence_agents import ContractIntelligenceAgentFactory
from backend.domain.entities import ContractIntelligence, ContractClause, PolicyViolation, RiskAssessment, RedlineRecommendation
from backend.domain.matter import AnalysisStatus
from backend.infrastructure.contract_repository import Neo4jContractRepository
from backend.infrastructure.matter_repository import MatterRepository
from backend.llm_manager import LLMManager
from backend.shared.errors import LLMProviderError, raise_if_provider_error
from backend.shared.debug import atrace_step, note, trace_step
import asyncio
import json
import logging
import time
from typing import Dict, Any, Optional

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

class ContractIntelligenceService:
    """Service for contract intelligence analysis using multi-agent system"""
    
    def __init__(self, llm_manager: LLMManager):
        self.llm_manager = llm_manager
        self.repository = Neo4jContractRepository()
        # Versions carry the analysis status, so a matter opened mid-analysis
        # shows "running" rather than an empty review.
        self.matters = MatterRepository()
    
    def analyze_contract_intelligence(self, contract_text: str, model: str = "gemini-2.5-flash",
                                      use_planning: bool = True,
                                      tenant_id: str = "default-tenant",
                                      contract_type: str = "general",
                                      chunking_profile=None) -> ContractIntelligence:
        """Perform complete contract intelligence analysis using multi-agent system"""
        
        start_time = time.time()
        
        try:
            logger.info(f"Starting contract intelligence analysis with model: {model}")
            
            # Get LLM for the specified model
            llm = self._get_llm_for_model(model)
            
            # Create multi-agent orchestrator with error handling
            try:
                orchestrator = ContractIntelligenceAgentFactory.create_orchestrator(llm, model)
                # Run multi-agent analysis with optional planning
                analysis_result = orchestrator.analyze_contract(
                    contract_text, use_planning, tenant_id, contract_type,
                    chunking_profile,
                )
            except ImportError as ie:
                logger.error(f"Import error in orchestrator: {ie}")
                raise Exception(f"Intelligence system not properly configured: {ie}")
            except LLMProviderError:
                # Already explained, and re-wrapping it below would bury the
                # explanation inside "Failed to initialize intelligence system".
                raise
            except Exception as oe:
                logger.error(f"Orchestrator creation failed: {oe}")
                raise Exception(f"Failed to initialize intelligence system: {oe}")
            
            # Convert to domain entities
            intelligence = self._convert_to_domain_entities(analysis_result)
            intelligence.processing_time = time.time() - start_time
            
            logger.info(f"Contract intelligence analysis completed in {intelligence.processing_time:.2f}s")
            return intelligence
            
        except Exception as e:
            # A model that refused the request produces no analysis, not an
            # analysis with no findings. Everything below renders as "no
            # clauses, no violations, risk UNKNOWN", which a reviewer reads as
            # a clean contract — so provider failures go back to the caller.
            raise_if_provider_error(e, model)
            logger.error(f"Contract intelligence analysis failed: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            # Return empty result on failure
            return ContractIntelligence(
                clauses=[],
                violations=[],
                risk_assessment=RiskAssessment(
                    overall_risk_score=0.0,
                    risk_level="UNKNOWN",
                    critical_issues=[],
                    recommendations=["Analysis failed - manual review required"]
                ),
                redlines=[],
                redlines_generated=False,
                clauses_extracted=False,
                warnings=[f"The analysis did not complete: {e}"],
                processing_time=time.time() - start_time
            )
    
    async def analyze_contract_by_id(self, contract_id: str, tenant_id: str = "default-tenant", model: str = "gemini-2.5-flash", use_planning: bool = True) -> Optional[ContractIntelligence]:
        """Analyze contract intelligence for an existing contract by ID"""
        
        try:
            # Get contract text from database
            async with atrace_step("analysis", "load_contract", contract_id=contract_id) as step:
                contract_data = await self.repository.get_contract_by_id(contract_id, tenant_id)
                step.set(found=bool(contract_data))

            if not contract_data:
                logger.error(f"Contract not found: {contract_id}")
                return None
            
            # Use full text if available, otherwise fallback to summary
            contract_text = contract_data.get("full_text", "")
            logger.info(f"Contract {contract_id}: full_text length = {len(contract_text)}")
            
            if not contract_text.strip():
                contract_text = contract_data.get("summary", "") + " " + contract_data.get("contract_scope", "")
                logger.info(f"Contract {contract_id}: fallback text length = {len(contract_text)}")
            
            if not contract_text.strip():
                logger.error(f"No text content found for contract: {contract_id}")
                logger.error(f"Contract data keys: {list(contract_data.keys())}")
                return None

            # Marked running *before* the work starts. A two-minute analysis is
            # long enough that a reviewer will open the matter while it is under
            # way, and an empty review is the one thing that page must never
            # show them — it reads as "this contract is clean".
            self._set_analysis_status(contract_id, tenant_id, AnalysisStatus.RUNNING)
            
            # Perform analysis with optional planning.
            #
            # On a worker thread, because this is a minute of synchronous work —
            # three sequential model calls — reached from a coroutine. Run
            # in-line it blocks the event loop for its whole duration, and while
            # it is blocked the server answers nothing at all: not the debug
            # stream, not the 500ms `/api/workflow/status` poll this very page
            # is making, not another user's request. Measured before this
            # change: a trivial `/api/debug/status` call took 6.4s to answer
            # because it waited for the analysis to finish.
            #
            # `to_thread` copies the context, so the correlation id still
            # reaches the analysis and its debug events.
            # The version's own chunking, read back from the graph. Analysis has
            # to reproduce the boundaries the upload stored, or the windows it
            # builds describe a division of the document that exists nowhere
            # else — and every finding's chunk attribution with them.
            profile = None
            try:
                from backend.infrastructure.chunk_repository import ChunkRepository

                profile = ChunkRepository().profile_for_version(tenant_id, contract_id)
            except Exception as e:
                logger.warning(f"Could not read the chunking profile for {contract_id}: {e}")

            intelligence = await asyncio.to_thread(
                self.analyze_contract_intelligence,
                contract_text, model, use_planning,
                tenant_id,
                contract_data.get("contract_type") or "general",
                profile,
            )

            # Store intelligence results back to database
            with trace_step("analysis", "store_results", contract_id=contract_id) as step:
                stored = self._store_intelligence_results(contract_id, tenant_id, intelligence)
                step.set(
                    clauses=len(intelligence.clauses or []),
                    violations=len(intelligence.violations or []),
                    stored=stored,
                )

            # The version outlives the analysis. COMPLETE is claimed only when
            # the review was actually written down — an analysis that ran
            # perfectly and then failed to save is, to whoever reopens the
            # matter, indistinguishable from one that never ran. Either way the
            # reason is recorded, so reopening shows a warning rather than a
            # clean bill of health.
            if stored:
                self._set_analysis_status(contract_id, tenant_id, AnalysisStatus.COMPLETE)
            else:
                reason = "; ".join(intelligence.warnings or []) or (
                    "the analysis did not complete"
                    if not intelligence.clauses_extracted
                    else "the analysis ran but its results could not be saved"
                )
                self._set_analysis_status(
                    contract_id, tenant_id, AnalysisStatus.FAILED, error=reason,
                )
                # And the caller is told, so the response carries the warning
                # rather than presenting an unsaved review as a saved one.
                intelligence.warnings = list(intelligence.warnings or []) + (
                    [] if not intelligence.clauses_extracted
                    else ["The analysis ran but its results could not be saved; "
                          "reopening this version will not show them."]
                )

            note(
                "analysis",
                "completed",
                contract_id=contract_id,
                clauses=len(intelligence.clauses or []),
                violations=len(intelligence.violations or []),
                processing_s=round(intelligence.processing_time or 0, 2),
            )
            return intelligence
            
        except Exception as e:
            # None means "no such contract" to the caller, which answers 404.
            # A quota failure is not a missing contract.
            self._set_analysis_status(
                contract_id, tenant_id, AnalysisStatus.FAILED, error=str(e)
            )
            raise_if_provider_error(e, model)
            logger.error(f"Failed to analyze contract {contract_id}: {e}")
            return None

    def _set_analysis_status(self, contract_id: str, tenant_id: str,
                             status: AnalysisStatus, error: str = "") -> None:
        """Best-effort bookkeeping — never the reason an analysis fails."""
        try:
            self.matters.set_analysis_status(tenant_id, contract_id, status, error=error)
        except Exception as e:
            logger.warning(f"Could not record analysis status for {contract_id}: {e}")
    

    def _store_redlines(self, contract_id: str, tenant_id: str, redlines,
                        replace: bool = True) -> bool:
        """Persist redlines as (:Contract)-[:HAS_REDLINE]->(:Redline).

        Only `redlines_count` used to be stored, so the drafted language existed
        solely in the HTTP response and was gone on refresh — there was nothing
        for a reviewer to come back to, and nothing for Increment 4 to approve.

        Redlines for the contract are replaced wholesale: re-analysing supersedes
        the previous set rather than accumulating duplicates alongside it.

        `replace=False` when drafting failed. An empty list then means "we could
        not draft", not "none were needed", and wiping the stored set on the
        strength of a transient model error would destroy drafts a reviewer may
        already be working from. That is a deliberate skip, not a failure, so it
        returns True.

        Returns whether the stored redlines match what the caller was handed.
        A swallowed write failure here used to leave the version COMPLETE with
        the drafted language missing — the response promised redlines that
        reopening the matter would not show.
        """
        if not replace:
            logger.warning(
                f"Redline drafting failed for {contract_id}; keeping the previously "
                f"stored redlines rather than replacing them"
            )
            return True

        try:
            # A redline is identified by the breach it fixes, not by its
            # position in the list. Positional ids ("_000") are not stable
            # between runs: the same breach could be written under a different
            # id, or two redlines could collide on one.
            for redline in redlines:
                redline_id = self._redline_id(contract_id, redline)
                self.repository.graph.query(
                    """
                    MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id})
                    MERGE (r:Redline {redline_id: $redline_id, tenant_id: $tenant_id})
                    // A reviewed redline is left exactly as the reviewer left it.
                    // Re-running the model is not grounds for discarding their
                    // judgement, nor for quietly changing the text they approved.
                    ON CREATE SET r.status = 'PENDING'
                    WITH c, r
                    WHERE coalesce(r.status, 'PENDING') = 'PENDING'
                    SET r.rule_id = $rule_id,
                        r.clause_index = $clause_index,
                        r.clause_type = $clause_type,
                        r.original_text = $original_text,
                        r.suggested_text = $suggested_text,
                        r.justification = $justification,
                        r.priority = $priority,
                        r.updated_at = datetime()
                    MERGE (c)-[:HAS_REDLINE]->(r)
                    """,
                    {
                        "contract_id": contract_id,
                        "tenant_id": tenant_id,
                        "redline_id": redline_id,
                        "rule_id": redline.rule_id,
                        "clause_index": redline.clause_index,
                        "clause_type": redline.clause_type,
                        "original_text": redline.original_text,
                        "suggested_text": redline.suggested_text,
                        "justification": redline.justification,
                        "priority": redline.priority,
                    },
                )

            # Drop undecided drafts for breaches this run no longer reports —
            # the clause may have been re-extracted differently, and a stale
            # draft would sit in the reviewer's queue forever.
            self.repository.graph.query(
                """
                MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id})
                      -[:HAS_REDLINE]->(r:Redline)
                WHERE coalesce(r.status, 'PENDING') = 'PENDING'
                  AND NOT r.redline_id IN $current_ids
                WITH collect(r) AS stale
                FOREACH (redline IN stale | DETACH DELETE redline)
                """,
                {
                    "contract_id": contract_id,
                    "tenant_id": tenant_id,
                    "current_ids": [self._redline_id(contract_id, r) for r in redlines],
                },
            )

            logger.info(f"Stored {len(redlines)} redlines for contract {contract_id}")
            note("analysis", "store_redlines", contract_id=contract_id, redlines=len(redlines))
            return True
        except Exception as e:
            # Not raised — the analysis itself succeeded and the caller has it —
            # but reported, so the version is marked FAILED rather than claiming
            # a complete review whose redlines are not there.
            logger.error(f"Failed to store redlines for {contract_id}: {e}")
            note(
                "analysis",
                "store_redlines",
                "error",
                contract_id=contract_id,
                error_type=type(e).__name__,
                error=str(e),
            )
            return False

    def _store_clause_findings(self, contract_id: str, tenant_id: str,
                               intelligence: ContractIntelligence) -> bool:
        """Persist the findings themselves, not just how many there were.

        This is the gap that made a review un-reopenable. `_store_intelligence_results`
        wrote `clauses_count`, `violations_count` and `risk_score` — three numbers
        — so "open this review again" meant re-running a two-minute analysis and
        hoping the model said the same thing twice. The evidence spans, the rule
        citations and the risk narrative existed only in one HTTP response.

        Findings are replaced wholesale on each run. Unlike redlines they carry
        no human decision — a reviewer rules on a redline, never on a finding —
        so there is nothing here to preserve across a re-analysis, and keeping
        superseded findings would double-count the violations on the matter list.

        An analysis that did not run never reaches here — `_store_intelligence_results`
        returns before calling this — because an empty list would then mean "we do
        not know", and wiping a good stored review on the strength of a transient
        model failure would lose exactly what this method exists to keep.

        Returns whether the write succeeded, so the caller can mark the version
        FAILED rather than claiming a review it does not have.
        """
        risk = intelligence.risk_assessment
        summary = {
            "risk_score": risk.overall_risk_score,
            "risk_level": risk.risk_level,
            "violations_count": len(intelligence.violations or []),
            "clauses_count": len(intelligence.clauses or []),
            "redlines_count": len(intelligence.redlines or []),
            "intelligence_status": "completed",
            "processing_time": intelligence.processing_time,
            "cuad_analysis_status": "completed",
            "deviation_count": len(intelligence.cuad_deviations or []),
            "jurisdiction_detected": (intelligence.jurisdiction_info or {}).get(
                "jurisdiction", "unknown"),
            "industry_detected": (intelligence.jurisdiction_info or {}).get(
                "industry", "general"),
            "precedent_matches": len(intelligence.precedent_matches or []),
            "semantic_analysis_enabled": True,
            "cache_enabled": True,
            "performance_optimized": True,
            # The narrative half of the risk assessment. Only the score and the
            # level used to be kept, so a reopened review showed "72/100 HIGH"
            # with nothing to say why.
            "critical_issues": list(risk.critical_issues or []),
            "risk_recommendations": list(risk.recommendations or []),
        }

        findings = [
            {
                "finding_id": self._finding_id(contract_id, index, clause),
                "tenant_id": tenant_id,
                "index": index,
                "clause_type": clause.clause_type or "",
                "risk_level": clause.risk_level or "LOW",
                "confidence_score": float(clause.confidence_score or 0.0),
                "evidence_span": clause.evidence_span or clause.content or "",
                "location": clause.location or "",
                "violated_policy": clause.violated_policy,
                "suggested_redline": clause.suggested_redline,
                "human_review_required": bool(clause.human_review_required),
                # The chunk this finding came from. Increment 9 re-analyses the
                # windows whose chunks changed, and reads that from here.
                "source_chunk": clause.source_chunk,
                "source_window": clause.source_window,
            }
            for index, clause in enumerate(intelligence.clauses or [])
        ]

        violations = [
            {
                "violation_id": f"{contract_id}_{v.rule_id or 'UNCITED'}_{index}",
                "tenant_id": tenant_id,
                "index": index,
                "rule_id": v.rule_id,
                "clause_index": v.clause_index,
                "section_reference": v.section_reference or "",
                "clause_type": v.clause_type or "",
                "issue": v.issue or "",
                "severity": v.severity or "LOW",
                "suggested_fix": v.suggested_fix or "",
                "clause_content": v.clause_content or "",
            }
            for index, v in enumerate(intelligence.violations or [])
        ]

        try:
            # **One statement.** Summary, findings and violations are replaced
            # together or not at all.
            #
            # They used to be three `graph.query` calls, and every call is its
            # own auto-commit transaction: a failure between them left the new
            # score beside the new clauses and the *previous* run's violations,
            # marked FAILED — a review that is not a review of anything. The
            # score is written here rather than by the caller for exactly that
            # reason.
            #
            # The deletes run on the row that reaches them, so an analysis that
            # legitimately found nothing still clears the previous run.
            self.repository.graph.query(
                """
                MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id})
                SET c += $summary,
                    c.intelligence_updated = datetime()
                WITH c
                OPTIONAL MATCH (c)-[:HAS_FINDING]->(old_finding:ClauseFinding)
                DETACH DELETE old_finding
                WITH DISTINCT c
                OPTIONAL MATCH (c)-[:HAS_VIOLATION]->(old_violation:PolicyViolation)
                DETACH DELETE old_violation
                WITH DISTINCT c
                CALL (c) {
                    UNWIND $findings AS f
                    CREATE (c)-[:HAS_FINDING]->(:ClauseFinding {
                        finding_id: f.finding_id,
                        tenant_id: f.tenant_id,
                        position: f.index,
                        clause_type: f.clause_type,
                        risk_level: f.risk_level,
                        confidence_score: f.confidence_score,
                        evidence_span: f.evidence_span,
                        location: f.location,
                        violated_policy: f.violated_policy,
                        suggested_redline: f.suggested_redline,
                        human_review_required: f.human_review_required,
                        source_chunk: f.source_chunk,
                        source_window: f.source_window,
                        created_at: datetime()
                    })
                }
                CALL (c) {
                    UNWIND $violations AS v
                    CREATE (c)-[:HAS_VIOLATION]->(:PolicyViolation {
                        violation_id: v.violation_id,
                        tenant_id: v.tenant_id,
                        position: v.index,
                        rule_id: v.rule_id,
                        clause_index: v.clause_index,
                        section_reference: v.section_reference,
                        clause_type: v.clause_type,
                        issue: v.issue,
                        severity: v.severity,
                        suggested_fix: v.suggested_fix,
                        clause_content: v.clause_content,
                        created_at: datetime()
                    })
                }
                RETURN count(c) AS updated
                """,
                {
                    "contract_id": contract_id,
                    "tenant_id": tenant_id,
                    "summary": summary,
                    "findings": findings,
                    "violations": violations,
                },
            )

            logger.info(
                f"Stored {len(findings)} findings and {len(violations)} violations "
                f"for contract {contract_id}"
            )
            note("analysis", "store_findings", contract_id=contract_id,
                 findings=len(findings), violations=len(violations))
            return True
        except Exception as e:
            # Non-fatal, like redline storage: the caller already has the
            # analysis, and failing the request would throw it away entirely.
            # The version is marked FAILED, so nobody reads the gap as good news.
            logger.error(f"Failed to store findings for {contract_id}: {e}")
            note("analysis", "store_findings", "error", contract_id=contract_id,
                 error_type=type(e).__name__, error=str(e))
            return False

    @staticmethod
    def _finding_id(contract_id: str, index: int, clause) -> str:
        """Stable within a version: the same evidence keeps the same id.

        Keyed on the normalised evidence span rather than the position, for the
        reason the redline ids are — re-extraction reorders clauses, and an id
        that moves with the ordering is no id at all. The index is the
        tie-breaker for two findings quoting identical text.
        """
        import hashlib

        text = getattr(clause, "evidence_span", "") or getattr(clause, "content", "") or ""
        normalised = " ".join(text.split()).casefold()
        digest = hashlib.sha1(normalised.encode("utf-8")).hexdigest()[:10]
        return f"{contract_id}_{clause.clause_type or 'CLAUSE'}_{digest}_{index}"

    def get_stored_analysis(self, contract_id: str, tenant_id: str = "default-tenant") -> Optional[dict]:
        """Read a completed review back, in the shape the analyse endpoint returns.

        This is what makes a review resumable: reopening a matter serves the
        stored findings instead of spending two minutes and a model call
        re-deriving them, and a reviewer who refreshes the page keeps their place.

        Returns None when there is no such contract for this tenant — an
        unanalysed one answers with empty results and its analysis status, which
        is a different thing and the caller must be able to tell them apart.
        """
        header = self.repository.graph.query(
            """
            MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id})
            RETURN c.file_id AS contract_id,
                   coalesce(c.analysis_status, 'NOT_STARTED') AS analysis_status,
                   c.analysis_error AS analysis_error,
                   toString(c.analysis_updated_at) AS analysis_updated_at,
                   c.intelligence_status AS intelligence_status,
                   toString(c.intelligence_updated) AS analysed_at,
                   c.processing_time AS processing_time,
                   c.risk_score AS risk_score,
                   c.risk_level AS risk_level,
                   coalesce(c.critical_issues, []) AS critical_issues,
                   coalesce(c.risk_recommendations, []) AS recommendations,
                   c.contract_type AS contract_type,
                   c.summary AS summary
            """,
            {"contract_id": contract_id, "tenant_id": tenant_id},
        )
        if not header:
            return None
        row = header[0]

        clauses = self.repository.graph.query(
            """
            MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id})
                  -[:HAS_FINDING]->(f:ClauseFinding)
            RETURN f.clause_type AS clause_type,
                   f.risk_level AS risk_level,
                   f.evidence_span AS evidence_span,
                   f.confidence_score AS confidence,
                   f.violated_policy AS violated_policy,
                   f.suggested_redline AS suggested_redline,
                   f.human_review_required AS human_review_required,
                   f.location AS location,
                   f.source_chunk AS source_chunk,
                   f.source_window AS source_window
            ORDER BY f.position
            """,
            {"contract_id": contract_id, "tenant_id": tenant_id},
        )

        violations = self.repository.graph.query(
            """
            MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id})
                  -[:HAS_VIOLATION]->(v:PolicyViolation)
            RETURN v.rule_id AS rule_id,
                   v.clause_index AS clause_index,
                   v.section_reference AS section_reference,
                   v.clause_type AS clause_type,
                   v.issue AS issue,
                   v.severity AS severity,
                   v.suggested_fix AS suggested_fix,
                   v.clause_content AS clause_content
            ORDER BY v.position
            """,
            {"contract_id": contract_id, "tenant_id": tenant_id},
        )

        redlines = self.get_redlines(contract_id, tenant_id)

        warnings = []
        if row.get("analysis_error"):
            # Surfaced as a warning, never as an absence of findings.
            warnings.append(str(row["analysis_error"]))

        return {
            "contract_id": contract_id,
            "analysis_status": row.get("analysis_status") or "NOT_STARTED",
            # When the status last moved. A version left RUNNING by a server
            # that restarted mid-analysis would otherwise poll for ever; the
            # page uses this to say it looks stuck rather than "still working".
            "analysis_updated_at": row.get("analysis_updated_at"),
            "analysed_at": row.get("analysed_at"),
            "processing_time": row.get("processing_time"),
            "contract_type": row.get("contract_type"),
            "summary": row.get("summary") or "",
            "warnings": warnings,
            "results": {
                "clauses": [
                    {
                        **clause,
                        # The UI still reads these older names.
                        "content": clause.get("evidence_span") or "",
                        "confidence_score": clause.get("confidence") or 0.0,
                    }
                    for clause in clauses
                ],
                "violations": violations,
                "risk_assessment": {
                    "overall_risk_score": row.get("risk_score") or 0.0,
                    "risk_level": row.get("risk_level") or "UNKNOWN",
                    "critical_issues": list(row.get("critical_issues") or []),
                    "recommendations": list(row.get("recommendations") or []),
                },
                "redlines": redlines,
            },
        }

    def get_redlines(self, contract_id: str, tenant_id: str = "default-tenant") -> list:
        """Read back the stored redlines for a contract."""
        return self.repository.graph.query(
            """
            MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id})
                  -[:HAS_REDLINE]->(r:Redline)
            RETURN r.redline_id AS redline_id, r.rule_id AS rule_id,
                   r.clause_index AS clause_index,
                   r.clause_type AS clause_type, r.original_text AS original_text,
                   r.suggested_text AS suggested_text, r.justification AS justification,
                   r.priority AS priority,
                   coalesce(r.status, 'PENDING') AS status,
                   r.final_text AS final_text,
                   r.decision_note AS decision_note,
                   r.decided_by AS decided_by,
                   toString(r.decided_at) AS decided_at
            ORDER BY r.redline_id
            """,
            {"contract_id": contract_id, "tenant_id": tenant_id},
        )



    @staticmethod
    def _redline_id(contract_id: str, redline) -> str:
        """Stable id: one breach of one rule on one clause has one redline.

        Keyed on the clause *text*, not its position. `clause_index` is an offset
        into whatever list the last extraction produced — re-extraction can
        return the same clauses in a different order, or find one more, and every
        index after that point shifts. The redline would then get a new id, be
        recreated as PENDING, and the reviewer's decision would be orphaned on a
        row nothing reads: exactly the guarantee this increment exists to provide,
        broken by a reordering.

        Hashing the normalised clause text keeps the id stable across reordering
        and across whitespace differences from re-extraction.
        """
        import hashlib

        normalised = " ".join((redline.original_text or "").split()).casefold()
        digest = hashlib.sha1(normalised.encode("utf-8")).hexdigest()[:10]
        return f"{contract_id}_{redline.rule_id}_{digest}"

    def record_redline_decision(self, redline_id: str, tenant_id: str,
                                status, edited_text=None, note: str = "",
                                decided_by: str = "unknown") -> dict:
        """Apply a reviewer's decision to one redline.

        Returns the updated redline, including the previous status so the caller
        can tell a first decision from a change of mind. Raises LookupError when
        the redline does not exist *for this tenant* — a foreign id is reported
        as missing rather than forbidden, so the endpoint does not confirm that
        someone else's redline exists.
        """
        from backend.domain.redline_decision import build_decision

        found = self.repository.graph.query(
            """
            MATCH (r:Redline {redline_id: $redline_id, tenant_id: $tenant_id})
            RETURN r.original_text AS original_text, r.suggested_text AS suggested_text,
                   coalesce(r.status, 'PENDING') AS status
            """,
            {"redline_id": redline_id, "tenant_id": tenant_id},
        )
        if not found:
            raise LookupError(f"No redline {redline_id!r} for this tenant")

        current = found[0]
        # Validate before writing: an unapplicable decision must never reach the
        # database, and the reviewer gets a specific reason instead of a 500.
        decision = build_decision(
            status,
            original_text=current["original_text"] or "",
            suggested_text=current["suggested_text"] or "",
            edited_text=edited_text,
            note=note,
            decided_by=decided_by,
        )

        # Read the prior status and write the new one in a single statement. Two
        # separate queries let concurrent reviewers both observe PENDING, so the
        # second decision silently replaced the first while both responses
        # claimed to be the first. It also left a window where re-analysis could
        # delete the row between the read and the write.
        updated = self.repository.graph.query(
            """
            MATCH (r:Redline {redline_id: $redline_id, tenant_id: $tenant_id})
            WITH r, coalesce(r.status, 'PENDING') AS previous_status
            SET r.status = $status,
                r.final_text = $final_text,
                r.decision_note = $note,
                r.decided_by = $decided_by,
                r.decided_at = datetime()
            RETURN r.redline_id AS redline_id, r.rule_id AS rule_id,
                   r.clause_index AS clause_index, r.status AS status,
                   r.final_text AS final_text, r.decision_note AS decision_note,
                   r.decided_by AS decided_by, toString(r.decided_at) AS decided_at,
                   previous_status
            """,
            {
                "redline_id": redline_id,
                "tenant_id": tenant_id,
                "status": decision.status.value,
                "final_text": decision.final_text,
                "note": decision.note,
                "decided_by": decision.decided_by,
            },
        )
        if not updated:
            # The row went away between the two statements — a concurrent
            # re-analysis dropping a pending draft, most likely.
            raise LookupError(f"Redline {redline_id!r} no longer exists")

        result = dict(updated[0])
        logger.info(
            f"Redline {redline_id}: {result['previous_status']} -> {decision.status.value} "
            f"by {decision.decided_by}"
        )
        self._audit_decision(redline_id, tenant_id, result, decision)
        return result

    def _audit_decision(self, redline_id: str, tenant_id: str, result: dict, decision) -> None:
        """Record the decision in the audit trail.

        A ruling on contract language is exactly what an audit log is for, and
        the API advertises one. Deciding twice overwrites the redline's own
        fields, so without this the earlier ruling would leave no trace.
        """
        try:
            from backend.infrastructure.audit_logger import AuditLogger, AuditEventType

            AuditLogger().log_event(
                event_type=AuditEventType.USER_INTERACTION,
                resource_id=redline_id,
                action=f"redline_{decision.status.value.lower()}",
                status="success",
                user_id=decision.decided_by,
                tenant_id=tenant_id,
                metadata={
                    "rule_id": result.get("rule_id"),
                    "previous_status": result.get("previous_status"),
                    "new_status": decision.status.value,
                    "note": decision.note,
                },
            )
        except Exception as e:
            # Never fail a recorded decision because the audit write failed.
            logger.error(f"Could not audit decision on {redline_id}: {e}")

    def redline_review_summary(self, contract_id: str, tenant_id: str) -> dict:
        """Counts by status, for showing review progress."""
        rows = self.repository.graph.query(
            """
            MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id})
                  -[:HAS_REDLINE]->(r:Redline)
            RETURN coalesce(r.status, 'PENDING') AS status, count(r) AS count
            """,
            {"contract_id": contract_id, "tenant_id": tenant_id},
        )
        counts = {row["status"]: row["count"] for row in rows}
        return {
            "total": sum(counts.values()),
            "pending": counts.get("PENDING", 0),
            "approved": counts.get("APPROVED", 0),
            "modified": counts.get("MODIFIED", 0),
            "rejected": counts.get("REJECTED", 0),
        }

    def _get_llm_for_model(self, model: str):
        """Get a raw chat model for the requested id.

        `llm_manager.agents[...]` holds *compiled LangGraph agents* for the chat
        endpoint, not chat models — they have no `.invoke(prompt)` for a plain
        string and no structured-output support. The intelligence tools need the
        underlying model, so build it from the central catalogue instead of
        reaching into the agent (the old `._llm` lookup never matched, so this
        silently returned the compiled agent).
        """
        from backend.shared.config.models import build_llm, normalize_model_id

        model = normalize_model_id(model)
        try:
            return build_llm(model)
        except Exception as e:
            logger.warning(f"Could not build LLM for '{model}' ({e}); falling back to default")
            return build_llm(None)
    
    def _convert_to_domain_entities(self, analysis_result: Dict[str, Any]) -> ContractIntelligence:
        """Convert analysis results to domain entities"""
        
        # Convert clauses
        clauses = []
        for clause_data in analysis_result.get("clauses", []):
            evidence_span = clause_data.get("evidence_span") or clause_data.get("content", "")
            clauses.append(ContractClause(
                clause_type=clause_data.get("clause_type", ""),
                content=evidence_span,
                risk_level=clause_data.get("risk_level", "LOW"),
                confidence_score=clause_data.get("confidence_score", 0.0),
                location=clause_data.get("location", ""),
                evidence_span=evidence_span,
                violated_policy=clause_data.get("violated_policy"),
                suggested_redline=clause_data.get("suggested_redline"),
                human_review_required=clause_data.get("human_review_required", False),
                source_chunk=clause_data.get("source_chunk"),
                source_window=clause_data.get("source_window"),
            ))
        
        # Convert violations
        violations = []
        for violation_data in analysis_result.get("violations", []):
            violations.append(PolicyViolation(
                clause_type=violation_data.get("clause_type", ""),
                issue=violation_data.get("issue", ""),
                severity=violation_data.get("severity", "LOW"),
                suggested_fix=violation_data.get("suggested_fix", ""),
                clause_content=violation_data.get("clause_content", ""),
                rule_id=violation_data.get("rule_id"),
                clause_index=violation_data.get("clause_index"),
                section_reference=violation_data.get("section_reference", ""),
            ))
        
        # Convert risk assessment
        risk_data = analysis_result.get("risk_assessment", {})
        risk_assessment = RiskAssessment(
            overall_risk_score=risk_data.get("overall_risk_score", 0.0),
            risk_level=risk_data.get("risk_level", "LOW"),
            critical_issues=risk_data.get("critical_issues", []),
            recommendations=risk_data.get("recommendations", [])
        )
        
        # Convert redlines
        redlines = []
        for redline_data in analysis_result.get("redlines", []):
            redlines.append(RedlineRecommendation(
                original_text=redline_data.get("original_text", ""),
                suggested_text=redline_data.get("suggested_text", ""),
                justification=redline_data.get("justification", ""),
                priority=redline_data.get("priority", "LOW"),
                rule_id=redline_data.get("rule_id"),
                clause_index=redline_data.get("clause_index"),
                clause_type=redline_data.get("clause_type", "")
            ))
        
        # Create ContractIntelligence with CUAD data
        intelligence = ContractIntelligence(
            clauses=clauses,
            violations=violations,
            risk_assessment=risk_assessment,
            redlines=redlines,
            redlines_generated=analysis_result.get("redlines_generated", True),
            warnings=analysis_result.get("warnings", []) or [],
        )
        
        # Add CUAD fields if present
        intelligence.cuad_deviations = analysis_result.get("cuad_deviations", [])
        intelligence.jurisdiction_info = analysis_result.get("jurisdiction_info", {})
        intelligence.precedent_matches = analysis_result.get("precedent_matches", [])
        
        return intelligence
    
    def _store_intelligence_results(self, contract_id: str, tenant_id: str,
                                    intelligence: ContractIntelligence) -> bool:
        """Store intelligence analysis results in the database.

        Returns whether the review was actually written, which is what decides
        the version's COMPLETE / FAILED status. Claiming completion on the
        strength of the in-memory result alone marks a review complete that
        whoever reopens it will find empty.

        **Nothing is written at all when the analysis did not run.** The failure
        path builds a result with risk 0.0, level "UNKNOWN" and every count at
        zero; storing that overwrites a good previous review with numbers that
        read, on the matters list, as a contract with nothing wrong with it.
        """
        if not intelligence.clauses_extracted:
            logger.warning(
                f"Analysis of {contract_id} did not complete; keeping the previously "
                f"stored review rather than replacing it with an empty one"
            )
            note("analysis", "store_results", "skipped", contract_id=contract_id,
                 reason="the analysis did not complete")
            return False

        try:
            # The score, the findings and the violations in one statement, so a
            # failure cannot leave a new score beside the previous run's
            # violations. See `_store_clause_findings`.
            if not self._store_clause_findings(contract_id, tenant_id, intelligence):
                return False

            # Redlines are separate on purpose: they carry human decisions and
            # so have preserve-rather-than-replace semantics of their own. But a
            # failure here still means the review on disk is not the review the
            # caller was handed, so it counts against completion.
            if not self._store_redlines(
                contract_id, tenant_id, intelligence.redlines,
                replace=intelligence.redlines_generated,
            ):
                return False

            # Metrics are telemetry. Losing them does not make the review wrong,
            # so they are the one thing here that cannot fail the save.
            self._store_performance_metrics(contract_id, tenant_id, intelligence)

            logger.info(f"Stored intelligence results for contract: {contract_id}")
            return True
            
        except Exception as e:
            # Reported, not raised: the caller already has the analysis and
            # failing the request would throw it away. But the version is marked
            # FAILED rather than COMPLETE, so reopening says the review could not
            # be saved instead of showing nothing and implying all is well.
            logger.error(f"Failed to store intelligence results for {contract_id}: {e}")
            note("analysis", "store_results", "error", contract_id=contract_id,
                 error_type=type(e).__name__, error=str(e))
            return False
    
    def _store_performance_metrics(self, contract_id: str, tenant_id: str, intelligence: ContractIntelligence):
        """Store performance metrics in database"""
        try:
            # Get validation result if available
            validation_result = getattr(intelligence, 'validation_result', None)
            
            # Store performance metric
            metric_query = """
            CREATE (pm:PerformanceMetric {
                metric_id: randomUUID(),
                contract_id: $contract_id,
                tenant_id: $tenant_id,
                operation: 'cuad_analysis',
                duration_ms: $duration_ms,
                success: $success,
                timestamp: datetime(),
                phase_used: 'phase3',
                validation_score: $validation_score,
                deviation_count: $deviation_count,
                jurisdiction: $jurisdiction
            })
            """
            
            self.repository.graph.query(metric_query, {
                "contract_id": contract_id,
                "tenant_id": tenant_id,
                "duration_ms": intelligence.processing_time * 1000,
                "success": True,
                "validation_score": validation_result.confidence_score if validation_result else 0.0,
                "deviation_count": len(intelligence.cuad_deviations),
                "jurisdiction": intelligence.jurisdiction_info.get("jurisdiction", "unknown")
            })
            
        except Exception as e:
            logger.warning(f"Failed to store performance metrics: {e}")

class ContractIntelligenceServiceFactory:
    """Factory for creating contract intelligence service"""
    
    @staticmethod
    def create_service(llm_manager: LLMManager) -> ContractIntelligenceService:
        """Create a new contract intelligence service"""
        return ContractIntelligenceService(llm_manager)