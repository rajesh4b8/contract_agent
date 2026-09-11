from backend.agents.contract_intelligence_agents import ContractIntelligenceAgentFactory
from backend.domain.entities import ContractIntelligence, ContractClause, PolicyViolation, RiskAssessment, RedlineRecommendation
from backend.infrastructure.contract_repository import Neo4jContractRepository
from backend.llm_manager import LLMManager
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
    
    def analyze_contract_intelligence(self, contract_text: str, model: str = "gemini-2.5-flash",
                                      use_planning: bool = True,
                                      tenant_id: str = "default-tenant",
                                      contract_type: str = "general") -> ContractIntelligence:
        """Perform complete contract intelligence analysis using multi-agent system"""
        
        start_time = time.time()
        
        try:
            logger.info(f"Starting contract intelligence analysis with model: {model}")
            
            # Get LLM for the specified model
            llm = self._get_llm_for_model(model)
            
            # Create multi-agent orchestrator with error handling
            try:
                orchestrator = ContractIntelligenceAgentFactory.create_orchestrator(llm)
                # Run multi-agent analysis with optional planning
                analysis_result = orchestrator.analyze_contract(
                    contract_text, use_planning, tenant_id, contract_type
                )
            except ImportError as ie:
                logger.error(f"Import error in orchestrator: {ie}")
                raise Exception(f"Intelligence system not properly configured: {ie}")
            except Exception as oe:
                logger.error(f"Orchestrator creation failed: {oe}")
                raise Exception(f"Failed to initialize intelligence system: {oe}")
            
            # Convert to domain entities
            intelligence = self._convert_to_domain_entities(analysis_result)
            intelligence.processing_time = time.time() - start_time
            
            logger.info(f"Contract intelligence analysis completed in {intelligence.processing_time:.2f}s")
            return intelligence
            
        except Exception as e:
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
                processing_time=time.time() - start_time
            )
    
    async def analyze_contract_by_id(self, contract_id: str, tenant_id: str = "default-tenant", model: str = "gemini-2.5-flash", use_planning: bool = True) -> Optional[ContractIntelligence]:
        """Analyze contract intelligence for an existing contract by ID"""
        
        try:
            # Get contract text from database
            contract_data = await self.repository.get_contract_by_id(contract_id, tenant_id)
            
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
            
            # Perform analysis with optional planning
            intelligence = self.analyze_contract_intelligence(
                contract_text, model, use_planning,
                tenant_id=tenant_id,
                contract_type=contract_data.get("contract_type") or "general",
            )
            
            # Store intelligence results back to database
            self._store_intelligence_results(contract_id, tenant_id, intelligence)
            
            return intelligence
            
        except Exception as e:
            logger.error(f"Failed to analyze contract {contract_id}: {e}")
            return None
    

    def _store_redlines(self, contract_id: str, tenant_id: str, redlines,
                        replace: bool = True) -> None:
        """Persist redlines as (:Contract)-[:HAS_REDLINE]->(:Redline).

        Only `redlines_count` used to be stored, so the drafted language existed
        solely in the HTTP response and was gone on refresh — there was nothing
        for a reviewer to come back to, and nothing for Increment 4 to approve.

        Redlines for the contract are replaced wholesale: re-analysing supersedes
        the previous set rather than accumulating duplicates alongside it.

        `replace=False` when drafting failed. An empty list then means "we could
        not draft", not "none were needed", and wiping the stored set on the
        strength of a transient model error would destroy drafts a reviewer may
        already be working from.
        """
        if not replace:
            logger.warning(
                f"Redline drafting failed for {contract_id}; keeping the previously "
                f"stored redlines rather than replacing them"
            )
            return

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
        except Exception as e:
            # Non-fatal: the analysis itself succeeded and is already saved.
            logger.error(f"Failed to store redlines for {contract_id}: {e}")

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
        )
        
        # Add CUAD fields if present
        intelligence.cuad_deviations = analysis_result.get("cuad_deviations", [])
        intelligence.jurisdiction_info = analysis_result.get("jurisdiction_info", {})
        intelligence.precedent_matches = analysis_result.get("precedent_matches", [])
        
        return intelligence
    
    def _store_intelligence_results(self, contract_id: str, tenant_id: str, intelligence: ContractIntelligence):
        """Store intelligence analysis results in the database"""
        
        try:
            # Update contract with intelligence data including CUAD fields
            intelligence_data = {
                "risk_score": intelligence.risk_assessment.overall_risk_score,
                "risk_level": intelligence.risk_assessment.risk_level,
                "violations_count": len(intelligence.violations),
                "clauses_count": len(intelligence.clauses),
                "redlines_count": len(intelligence.redlines),
                "intelligence_status": "completed",
                "processing_time": intelligence.processing_time,
                # CUAD-specific fields
                "cuad_analysis_status": "completed",
                "deviation_count": len(intelligence.cuad_deviations),
                "jurisdiction_detected": intelligence.jurisdiction_info.get("jurisdiction", "unknown"),
                "industry_detected": intelligence.jurisdiction_info.get("industry", "general"),
                "precedent_matches": len(intelligence.precedent_matches),
                "semantic_analysis_enabled": True,
                "cache_enabled": True,
                "performance_optimized": True
            }
            
            # Store in Neo4j with CUAD fields
            query = """
            MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id})
            SET c.risk_score = $risk_score,
                c.risk_level = $risk_level,
                c.violations_count = $violations_count,
                c.clauses_count = $clauses_count,
                c.redlines_count = $redlines_count,
                c.intelligence_status = $intelligence_status,
                c.processing_time = $processing_time,
                c.cuad_analysis_status = $cuad_analysis_status,
                c.deviation_count = $deviation_count,
                c.jurisdiction_detected = $jurisdiction_detected,
                c.industry_detected = $industry_detected,
                c.precedent_matches = $precedent_matches,
                c.semantic_analysis_enabled = $semantic_analysis_enabled,
                c.cache_enabled = $cache_enabled,
                c.performance_optimized = $performance_optimized,
                c.intelligence_updated = datetime()
            RETURN c
            """
            
            self.repository.graph.query(query, {
                "contract_id": contract_id,
                "tenant_id": tenant_id,
                **intelligence_data
            })

            self._store_redlines(
                contract_id, tenant_id, intelligence.redlines,
                replace=intelligence.redlines_generated,
            )
            
            # Store performance metrics
            self._store_performance_metrics(contract_id, tenant_id, intelligence)
            
            logger.info(f"Stored intelligence results for contract: {contract_id}")
            
        except Exception as e:
            logger.error(f"Failed to store intelligence results for {contract_id}: {e}")
    
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