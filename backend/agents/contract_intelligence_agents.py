from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, AIMessage
from backend.agents.intelligence_state import IntelligenceState
from typing import Any
from backend.agents.intelligence_tools import (
    ClauseDetectorTool, ClauseExtractionFailed, PolicyCheckerTool, parse_clause_result, 
    RiskCalculatorTool, RedlineGeneratorTool
)
from backend.agents.agent_workflow_tracker import workflow_tracker
from backend.agents.planning.planning_agent import PlanningAgentFactory
from backend.agents.planning.execution_engine import PlanExecutionEngine
from backend.infrastructure.playbook_loader import (
    attach_violated_policy as _attach_violated_policy,
    load_rules_for_tenant,
)
from backend.shared.errors import describe_llm_error, raise_if_provider_error
from backend.shared.debug import note, trace_step
import json
import logging

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

def run_coroutine(coro):
    """Run a coroutine from sync code, whether or not a loop is already running.

    The LangGraph nodes are synchronous but execute inside FastAPI's event loop,
    so a bare ``asyncio.run`` raises "cannot be called from a running event
    loop". That failure killed the whole analysis — clauses included — whenever a
    contract was complex enough for a pattern to be selected.

    The context is copied across because ``ThreadPoolExecutor.submit`` does not
    carry ``contextvars`` into the worker. Without this the request's correlation
    id is lost for everything that runs inside, so the analysis's log lines — and
    the debug panel's events — arrive unattributed and cannot be tied back to the
    request that caused them.
    """
    import asyncio
    import concurrent.futures
    import contextvars

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    context = contextvars.copy_context()
    with concurrent.futures.ThreadPoolExecutor() as executor:
        return executor.submit(context.run, asyncio.run, coro).result()


def _stage_warnings(state: dict) -> list:
    """What silently degraded during a run, in words a reviewer can act on.

    A stage that fails on its own — the playbook check, the redline drafting —
    leaves an empty list behind, and an empty list is indistinguishable from
    "nothing to report". These warnings are what stop a rate-limited run from
    being read as a clean contract.
    """
    stages = (
        ("policy_check_failed", "Policy compliance could not be checked"),
        ("risk_calculation_failed", "The risk score could not be calculated"),
        ("redline_generation_failed", "Redlines could not be drafted"),
    )
    warnings = [f"{label}: {state[key]}" for key, label in stages if state.get(key)]

    # Not a stage that failed but a stage that only half ran. A long contract is
    # analysed in windows, and some of them can fail while the rest succeed —
    # which reads exactly like a complete review of a shorter contract unless
    # something says otherwise.
    if state.get("clause_extraction_incomplete"):
        coverage = state.get("clause_extraction_coverage") or {}
        warnings.append(
            f"Part of this contract could not be analysed: "
            f"{coverage.get('failed', 'some')} of {coverage.get('windows', 'its')} sections "
            f"failed, so the findings below do not cover the whole document. "
            f"Re-run the analysis."
        )
    return warnings


class IntelligenceOrchestrator:
    """Proper multi-agent orchestrator following SOLID principles"""
    
    def __init__(self, llm, model_id: str = None):
        self.llm = llm
        # The public model id behind `llm`, carried only so a failure can name
        # the model the reviewer picked rather than say "the AI provider".
        self.model_id = model_id
        self.workflow = self._build_workflow()
        self.planning_agent = PlanningAgentFactory.create_planning_agent()
        self.execution_engine = PlanExecutionEngine(llm, model_id)
    
    @staticmethod
    def _traced(name: str, node):
        """Report a graph node's timing to the debug panel.

        Wrapping at registration keeps the instrumentation in one place rather
        than threading a context manager through six node bodies that each
        handle their own failures differently.
        """
        def run(state: IntelligenceState) -> IntelligenceState:
            with trace_step("analysis", name) as step:
                result = node(state)
                if isinstance(result, dict):
                    # Keys from IntelligenceState. `.get` rather than indexing:
                    # each node only writes the slice it owns.
                    step.set(
                        clauses=len(result.get("extracted_clauses") or []),
                        violations=len(result.get("policy_violations") or []),
                        redlines=len(result.get("redline_suggestions") or []),
                    )
                return result

        return run

    def _build_workflow(self) -> StateGraph:
        """Build workflow with proper state management"""
        
        workflow = StateGraph(IntelligenceState)
        
        # Add nodes with descriptive names (no conflicts)
        workflow.add_node("clause_extraction", self._traced("clause_extraction", self._extract_clauses))
        workflow.add_node("pattern_analysis", self._traced("pattern_analysis", self._pattern_analysis))  # NEW: Pattern integration
        workflow.add_node("policy_checking", self._traced("policy_checking", self._check_policies))
        workflow.add_node("risk_calculation", self._traced("risk_calculation", self._calculate_risks))
        
        # NEW: CUAD mitigation step (Phase 1)
        workflow.add_node("cuad_mitigation", self._traced("cuad_mitigation", self._cuad_mitigation))
        
        workflow.add_node("redline_generation", self._traced("redline_generation", self._generate_redlines))
        
        # Define workflow with pattern and CUAD steps
        workflow.set_entry_point("clause_extraction")
        workflow.add_edge("clause_extraction", "pattern_analysis")  # NEW: Pattern step
        workflow.add_edge("pattern_analysis", "policy_checking")
        workflow.add_edge("policy_checking", "risk_calculation")
        workflow.add_edge("risk_calculation", "cuad_mitigation")
        workflow.add_edge("cuad_mitigation", "redline_generation")
        workflow.add_edge("redline_generation", END)
        
        return workflow.compile()
    
    def _extract_clauses(self, state: IntelligenceState) -> IntelligenceState:
        """Extract clauses - Single Responsibility"""
        text_len = len(state["contract_text"])
        execution = workflow_tracker.start_agent(
            "Clause Extraction Agent", 
            "Extract key contract clauses (Payment, Liability, IP, etc.)",
            f"Contract text ({text_len:,} characters)"
        )
        
        try:
            # The version's own chunking, so the windows are built from the same
            # boundaries the upload stored. Chunking it differently here would
            # make every finding's chunk attribution point at boundaries that
            # exist nowhere else.
            reusable = state.get("reusable") or {}
            tool = ClauseDetectorTool(
                llm=self.llm, chunking_profile=state.get("chunking_profile"),
                unchanged_chunks=reusable.get("unchanged_chunks"),
                carried_findings=reusable.get("findings"),
            )
            clauses_json = tool._run(state["contract_text"])
            clauses_list, coverage = parse_clause_result(clauses_json)

            # Coverage comes back beside the findings rather than stamped on
            # them. Reading it off the findings meant a run where one window
            # failed and the rest returned nothing looked exactly like a clean
            # contract — no findings and no marker, because there was nothing
            # left to stamp.
            incomplete = not coverage.get("complete", True)

            workflow_tracker.complete_agent(execution, f"Extracted {len(clauses_list)} clauses")

            return {**state, 
                "extracted_clauses": clauses_list,
                "clause_extraction_incomplete": incomplete,
                "clause_extraction_coverage": coverage,
                "current_step": "clause_extraction"
            }
        except ClauseExtractionFailed as e:
            # Not a contract with no clauses: an analysis that did not happen.
            # Flagged distinctly so `_convert_to_domain_entities` marks the
            # result unextractable and storage leaves the previous review alone
            # rather than overwriting it with nothing.
            workflow_tracker.error_agent(execution, f"Clause extraction failed: {e}")
            return {**state,
                "extracted_clauses": [],
                "clause_extraction_failed": str(e),
                "processing_result": {"status": "error", "error": f"Clause extraction failed: {e}"}
            }
        except Exception as e:
            # Everything downstream reads the clauses, so when the model itself
            # is unavailable the run cannot produce an analysis — only an empty
            # one that reads like a clean bill of health. Fail loudly instead;
            # the API turns this into the real reason and status code.
            raise_if_provider_error(e, self.model_id)
            workflow_tracker.error_agent(execution, f"Clause extraction failed: {e}")
            return {**state,
                "extracted_clauses": [],
                "clause_extraction_failed": str(e),
                "processing_result": {"status": "error", "error": f"Clause extraction failed: {e}"}
            }
    
    def _pattern_analysis(self, state: IntelligenceState) -> IntelligenceState:
        """Pattern-based analysis using ReACT or Chain-of-Thought"""
        from backend.agents.patterns.pattern_selector import PatternSelector
        from backend.agents.patterns.react_agent import ReACTAgent
        from backend.agents.patterns.chain_of_thought_agent import ChainOfThoughtAgent
        import asyncio
        
        # Select pattern based on complexity
        pattern = PatternSelector.select_pattern({
            'contract_text': state['contract_text'],
            'clauses': state['extracted_clauses'],
            'violations': state.get('policy_violations', [])
        })
        
        if pattern == "react":
            agent = ReACTAgent(max_iterations=3, llm=self.llm)
            result = run_coroutine(agent.execute({
                'contract_text': state['contract_text'],
                'clauses': state['extracted_clauses'],
                'contract_id': state.get('contract_id', 'unknown')
            }))
            
            return {**state,
                'pattern_analysis': result,
                'pattern_used': 'ReACT',
                'current_step': 'pattern_analysis'
            }
        
        elif pattern == "chain_of_thought":
            agent = ChainOfThoughtAgent()
            result = run_coroutine(agent.execute({
                'clauses': state['extracted_clauses'],
                'task_type': 'risk_assessment',
                'contract_id': state.get('contract_id', 'unknown')
            }))
            
            return {**state,
                'pattern_analysis': result,
                'pattern_used': 'Chain-of-Thought',
                'current_step': 'pattern_analysis'
            }
        
        # Standard workflow - no pattern
        logger.info("Using standard workflow, skipping pattern analysis")
        return {**state, 
            'pattern_used': 'Standard',
            'current_step': 'pattern_analysis'
        }
    
    def _check_policies(self, state: IntelligenceState) -> IntelligenceState:
        """Check policy compliance - Single Responsibility"""
        clause_count = len(state["extracted_clauses"])
        execution = workflow_tracker.start_agent(
            "Policy Compliance Agent",
            "Check clauses against company policies (Payment, Liability, IP, etc.)",
            f"{clause_count} extracted clauses"
        )
        
        try:
            rules = load_rules_for_tenant(
                state.get("tenant_id") or "default-tenant",
                state.get("contract_type") or "general",
            )
            tool = PolicyCheckerTool(llm=self.llm, rules=rules)
            clauses_json = json.dumps(state["extracted_clauses"])
            violations_json = tool._run(clauses_json)
            violations_list = json.loads(violations_json)

            # Stamp the breached rule back onto the clause, so a finding carries
            # its own provenance instead of the caller having to join on text.
            clauses = _attach_violated_policy(state["extracted_clauses"], violations_list)

            critical_count = len([v for v in violations_list if v.get("severity") == "CRITICAL"])
            workflow_tracker.complete_agent(
                execution,
                f"Found {len(violations_list)} violations ({critical_count} critical) "
                f"against {len(rules)} playbook rules"
            )

            return {**state,
                "extracted_clauses": clauses,
                "policy_violations": violations_list,
                "current_step": "policy_checking"
            }
        except ValueError as e:
            # A missing playbook or missing model is a configuration fault, not a
            # compliant contract. Swallowing it here would present an unseeded
            # tenant as clean, which is the exact failure this increment removes.
            workflow_tracker.error_agent(execution, f"Policy checking misconfigured: {e}")
            raise
        except Exception as e:
            reason = describe_llm_error(e, self.model_id, fallback=str(e))
            workflow_tracker.error_agent(execution, f"Policy checking failed: {reason}")
            return {**state,
                "policy_violations": [],
                "policy_check_failed": reason,
            }
    
    def _calculate_risks(self, state: IntelligenceState) -> IntelligenceState:
        """Calculate risks - Single Responsibility"""
        violation_count = len(state["policy_violations"])
        execution = workflow_tracker.start_agent(
            "Risk Assessment Agent",
            "Calculate overall contract risk score and recommendations",
            f"{len(state['extracted_clauses'])} clauses + {violation_count} violations"
        )
        
        try:
            tool = RiskCalculatorTool()
            clauses_json = json.dumps(state["extracted_clauses"])
            violations_json = json.dumps(state["policy_violations"])
            risk_json = tool._run(clauses_json, violations_json)
            risk_dict = json.loads(risk_json)
            
            risk_score = risk_dict.get("overall_risk_score", 0)
            risk_level = risk_dict.get("risk_level", "UNKNOWN")
            workflow_tracker.complete_agent(execution, f"Risk Score: {risk_score}/100 ({risk_level})")
            
            return {**state,
                "risk_data": risk_dict,
                "current_step": "risk_calculation"
            }
        except Exception as e:
            reason = describe_llm_error(e, self.model_id, fallback=str(e))
            workflow_tracker.error_agent(execution, f"Risk calculation failed: {reason}")
            return {**state,
                "risk_data": {"overall_risk_score": 50.0, "risk_level": "MEDIUM"},
                "risk_calculation_failed": reason,
            }
    
    def _generate_redlines(self, state: IntelligenceState) -> IntelligenceState:
        """Generate redlines - Single Responsibility"""
        violation_count = len(state["policy_violations"])
        execution = workflow_tracker.start_agent(
            "Redline Generation Agent",
            "Generate contract redline suggestions for policy violations",
            f"{violation_count} policy violations"
        )
        
        try:
            tool = RedlineGeneratorTool(llm=self.llm)
            violations_json = json.dumps(state["policy_violations"])
            redlines_json = tool._run(violations_json)
            redlines_list = json.loads(redlines_json)
            
            critical_redlines = len([r for r in redlines_list if r.get("priority") == "CRITICAL"])
            workflow_tracker.complete_agent(execution, f"Generated {len(redlines_list)} redlines ({critical_redlines} critical)")
            
            return {**state,
                "redline_suggestions": redlines_list,
                "is_complete": True,
                "processing_result": {"status": "success", "message": "Intelligence analysis completed"}
            }
        except Exception as e:
            # Flag the failure. An empty list here is indistinguishable from
            # "nothing needed redlining", and persistence would then delete
            # drafts a reviewer may already be working from.
            reason = describe_llm_error(e, self.model_id, fallback=str(e))
            workflow_tracker.error_agent(execution, f"Redline generation failed: {reason}")
            return {**state,
                "redline_suggestions": [],
                "redline_generation_failed": reason,
                "is_complete": True,
            }
    
    def _cuad_mitigation(self, state: IntelligenceState) -> IntelligenceState:
        """Enhanced CUAD mitigation analysis - Phase 2 implementation"""
        execution = workflow_tracker.start_agent(
            "Enhanced CUAD Mitigation Agent",
            "Advanced deviation detection, jurisdiction adaptation, and precedent analysis with ML",
            f"{len(state['extracted_clauses'])} clauses + {len(state['policy_violations'])} violations"
        )
        
        try:
            # Use optimized tools for Phase 3
            from backend.agents.optimized_cuad_tools import (
                OptimizedDeviationDetectorTool, OptimizedJurisdictionAdapterTool, OptimizedPrecedentMatcherTool
            )
            from backend.agents.feedback_learning_system import AdaptiveAnalyzer
            
            clauses_json = json.dumps(state["extracted_clauses"])
            
            # 1. Optimized deviation detection with caching and monitoring
            deviation_tool = OptimizedDeviationDetectorTool()
            deviations_json = deviation_tool._run(clauses_json)
            deviations = json.loads(deviations_json)
            
            # 2. Optimized jurisdiction adaptation with caching
            jurisdiction_tool = OptimizedJurisdictionAdapterTool()
            jurisdiction_json = jurisdiction_tool._run(state["contract_text"])
            jurisdiction_info = json.loads(jurisdiction_json)
            
            # 3. Optimized precedent matching with parallel processing
            precedent_tool = OptimizedPrecedentMatcherTool()
            precedents_json = precedent_tool._run(clauses_json)
            precedent_matches = json.loads(precedents_json)
            
            # 4. Apply learned patterns from legal team feedback
            adaptive_analyzer = AdaptiveAnalyzer()
            enhanced_clauses = []
            for clause in state["extracted_clauses"]:
                enhanced_analysis = adaptive_analyzer.enhance_analysis(clause, clause)
                enhanced_clauses.append(enhanced_analysis)
            
            # Deviations are keyword-matched heuristics, not playbook breaches, so
            # they stay out of policy_violations: everything in that list cites a
            # rule id, and mixing in findings that cannot would make the citation
            # meaningless. They are still returned, under cuad_analysis.deviations.
            enhanced_violations = state["policy_violations"]
            
            # Update risk data with enhanced CUAD insights
            enhanced_risk_data = dict(state["risk_data"])
            if deviations:
                deviation_risk = len([d for d in deviations if d.get("severity") in ["HIGH", "CRITICAL"]])
                enhanced_risk_data["cuad_deviation_risk"] = deviation_risk
                enhanced_risk_data["jurisdiction_compliance"] = jurisdiction_info.get("jurisdiction", "unknown")
                enhanced_risk_data["industry_risk_factors"] = jurisdiction_info.get("risk_factors", [])
                
                # Add precedent-based risk assessment
                if precedent_matches:
                    avg_approval_rate = sum(p.get("approval_rate", 0) for p in precedent_matches) / len(precedent_matches)
                    enhanced_risk_data["precedent_approval_rate"] = avg_approval_rate
            
            # Validate results
            from backend.validation.cuad_validator import validate_cuad_analysis
            
            validation_result = validate_cuad_analysis({
                "clauses": state["extracted_clauses"],
                "cuad_deviations": deviations,
                "risk_assessment": enhanced_risk_data,
                "policy_violations": enhanced_violations
            })
            
            workflow_tracker.complete_agent(
                execution, 
                f"Optimized analysis: {len(deviations)} deviations, jurisdiction: {jurisdiction_info.get('jurisdiction', 'unknown')} ({jurisdiction_info.get('industry', 'general')}), {len(precedent_matches)} precedent matches [validated: {validation_result.is_valid}, confidence: {validation_result.confidence_score:.2f}]"
            )
            
            return {**state,
                "extracted_clauses": enhanced_clauses,
                "policy_violations": enhanced_violations,
                "risk_data": enhanced_risk_data,
                "cuad_deviations": deviations,
                "jurisdiction_info": jurisdiction_info,
                "precedent_matches": precedent_matches,
                "validation_result": validation_result,
                "current_step": "cuad_mitigation"
            }
            
        except Exception as e:
            workflow_tracker.error_agent(execution, f"Optimized CUAD mitigation failed: {e}")
            # Fallback to Phase 2 tools, then Phase 1
            logger.warning(f"Falling back from Phase 3 tools: {e}")
            return self._cuad_mitigation_fallback_enhanced(state, execution)
    
    def _cuad_mitigation_fallback_enhanced(self, state: IntelligenceState, execution) -> IntelligenceState:
        """Enhanced fallback: Phase 2 -> Phase 1 tools"""
        try:
            # Try Phase 2 tools first
            from backend.agents.enhanced_cuad_tools import (
                EnhancedDeviationDetectorTool, EnhancedJurisdictionAdapterTool, EnhancedPrecedentMatcherTool
            )
            
            clauses_json = json.dumps(state["extracted_clauses"])
            
            deviation_tool = EnhancedDeviationDetectorTool()
            deviations = json.loads(deviation_tool._run(clauses_json))
            
            jurisdiction_tool = EnhancedJurisdictionAdapterTool()
            jurisdiction_info = json.loads(jurisdiction_tool._run(state["contract_text"]))
            
            precedent_tool = EnhancedPrecedentMatcherTool()
            precedent_matches = json.loads(precedent_tool._run(clauses_json))
            
            # Deviations stay out of policy_violations on every path — see the primary
            # branch above. Everything in that list cites a playbook rule id.
            enhanced_violations = state["policy_violations"]
            enhanced_risk_data = dict(state["risk_data"])
            
            workflow_tracker.complete_agent(execution, f"Phase 2 fallback completed: {len(deviations)} deviations")
            
            return {**state,
                "policy_violations": enhanced_violations,
                "risk_data": enhanced_risk_data,
                "cuad_deviations": deviations,
                "jurisdiction_info": jurisdiction_info,
                "precedent_matches": precedent_matches,
                "current_step": "cuad_mitigation"
            }
            
        except Exception as phase2_error:
            logger.warning(f"Phase 2 fallback failed, trying Phase 1: {phase2_error}")
            return self._cuad_mitigation_fallback(state, execution)
    
    def _cuad_mitigation_fallback(self, state: IntelligenceState, execution) -> IntelligenceState:
        """Fallback to Phase 1 CUAD tools if Phase 2 fails"""
        try:
            from backend.agents.cuad_mitigation_tools import (
                DeviationDetectorTool, JurisdictionAdapterTool, PrecedentMatcherTool
            )
            
            clauses_json = json.dumps(state["extracted_clauses"])
            
            deviation_tool = DeviationDetectorTool()
            deviations = json.loads(deviation_tool._run(clauses_json))
            
            jurisdiction_tool = JurisdictionAdapterTool()
            jurisdiction_info = json.loads(jurisdiction_tool._run(state["contract_text"]))
            
            precedent_tool = PrecedentMatcherTool()
            precedent_matches = json.loads(precedent_tool._run(clauses_json))
            
            # Deviations stay out of policy_violations on every path — see the primary
            # branch above. Everything in that list cites a playbook rule id.
            enhanced_violations = state["policy_violations"]
            enhanced_risk_data = dict(state["risk_data"])
            
            workflow_tracker.complete_agent(execution, f"Fallback completed: {len(deviations)} deviations")
            
            return {**state,
                "policy_violations": enhanced_violations,
                "risk_data": enhanced_risk_data,
                "cuad_deviations": deviations,
                "jurisdiction_info": jurisdiction_info,
                "precedent_matches": precedent_matches,
                "current_step": "cuad_mitigation"
            }
            
        except Exception as fallback_error:
            workflow_tracker.error_agent(execution, f"Fallback also failed: {fallback_error}")
            return {**state,
                "cuad_deviations": [],
                "jurisdiction_info": {},
                "precedent_matches": []
            }
    
    def analyze_contract(self, contract_text: str, use_planning: bool = True,
                         tenant_id: str = "default-tenant",
                         contract_type: str = "general",
                         chunking_profile: Any = None,
                         reusable: Any = None) -> dict:
        """Run analysis with optional autonomous planning"""
        note(
            "analysis",
            "started",
            path="planning" if use_planning else "traditional",
            chars=len(contract_text),
            model=self.model_id,
            tenant=tenant_id,
        )
        try:
            if use_planning:
                try:
                    # Use asyncio.run with proper event loop handling
                    import asyncio
                    try:
                        # Try to get current loop
                        loop = asyncio.get_running_loop()
                        # If we're in an event loop, create a task
                        import concurrent.futures
                        import contextvars

                        # copy_context, because submit() does not carry
                        # contextvars into the worker — without it the whole
                        # analysis runs with no correlation id, so its logs and
                        # its debug events cannot be tied to the request.
                        context = contextvars.copy_context()
                        with concurrent.futures.ThreadPoolExecutor() as executor:
                            future = executor.submit(
                                context.run,
                                asyncio.run,
                                self._analyze_with_planning(contract_text, tenant_id, contract_type,
                                                            chunking_profile, reusable),
                            )
                            return future.result()
                    except RuntimeError:
                        # No event loop running, safe to use asyncio.run
                        return asyncio.run(
                            self._analyze_with_planning(contract_text, tenant_id, contract_type,
                                                            chunking_profile, reusable)
                        )
                except Exception as planning_error:
                    # Retrying the whole analysis against a model that just
                    # refused the request only burns what is left of the quota
                    # and fails again a minute later, so provider failures skip
                    # the fallback and go straight back to the caller.
                    raise_if_provider_error(planning_error, self.model_id)
                    logger.error(f"Planning agent failed: {planning_error}, falling back to traditional workflow")
                    note(
                        "analysis",
                        "planning_fallback",
                        error_type=type(planning_error).__name__,
                        error=str(planning_error),
                    )
                    return self._analyze_traditional(contract_text, tenant_id, contract_type,
                                                     chunking_profile, reusable)
            else:
                return self._analyze_traditional(contract_text, tenant_id, contract_type,
                                                     chunking_profile, reusable)
            
        except Exception as e:
            # An empty analysis is a legitimate answer to "this contract has no
            # findings" and an illegitimate one to "the model refused us".
            # Never let the second masquerade as the first.
            raise_if_provider_error(e, self.model_id)
            logger.error(f"Analysis failed: {e}", exc_info=True)
            return {
                "clauses": [],
                "violations": [],
                "risk_assessment": {"overall_risk_score": 0, "risk_level": "UNKNOWN"},
                "redlines": [],
                "processing_complete": False
            }
    
    async def _analyze_with_planning(self, contract_text: str,
                                     tenant_id: str = "default-tenant",
                                     contract_type: str = "general",
                                     chunking_profile: Any = None,
                                     reusable: Any = None) -> dict:
        """Analyze contract using autonomous planning agent"""
        logger.info("🧠 STEP 1: Starting Planning Agent Analysis")
        
        try:
            # Step 1: Track planning agent
            planning_execution = workflow_tracker.start_agent(
                "Autonomous Planning Agent",
                "Analyze query and create optimal execution plan",
                "Contract analysis requirements"
            )
            
            # Step 2: Create execution plan
            logger.info("🧠 STEP 2: Creating execution plan")
            query = "Perform comprehensive contract analysis including clause extraction, policy compliance, risk assessment, and redline generation"
            execution_plan = self.planning_agent.create_execution_plan(query)
            logger.info(f"🧠 STEP 3: Plan created with {len(execution_plan.steps)} steps")
            
            # Complete planning agent tracking with detailed plan info
            step_details = " → ".join([f"{step.step_type.value.replace('_', ' ').title()}" for step in execution_plan.steps])
            workflow_tracker.complete_agent(
                planning_execution, 
                f"Created {execution_plan.strategy} plan: {step_details} (Est: {execution_plan.estimated_duration}s)"
            )
            
            # Step 2: Execute the planned workflow
            logger.info("🧠 STEP 4: Starting plan execution")
            results = await self.execution_engine.execute_plan(
                execution_plan, contract_text, tenant_id, contract_type,
                chunking_profile, reusable,
            )
            logger.info(f"🧠 STEP 5: Plan execution completed: {results.get('processing_complete')}")
            
            # Step 3: Provide feedback
            logger.info("🧠 STEP 6: Providing feedback to planning agent")
            success_rate = 1.0 if results.get("processing_complete") else 0.0
            self.planning_agent.adapt_plan_from_feedback(execution_plan.plan_id, {"success_rate": success_rate})
            
            logger.info("🧠 STEP 7: Planning agent analysis completed successfully")
            return results
            
        except Exception as e:
            # Mark planning agent as failed if we have the execution reference
            try:
                workflow_tracker.error_agent(planning_execution, f"Planning failed: {str(e)}")
            except:
                pass  # planning_execution might not be defined if error occurred early
            
            logger.error(f"🧠 PLANNING AGENT ERROR at step: {e}")
            import traceback
            logger.error(f"🧠 Full traceback: {traceback.format_exc()}")
            raise e
    
    def _analyze_traditional(self, contract_text: str,
                             tenant_id: str = "default-tenant",
                             contract_type: str = "general",
                             chunking_profile: Any = None,
                             reusable: Any = None) -> dict:
        """Traditional workflow analysis (fallback)"""
        # Start workflow tracking
        workflow_tracker.start_workflow()
        
        # Initialize proper state with CUAD fields
        initial_state = {
            "contract_text": contract_text,
            "tenant_id": tenant_id,
            "contract_type": contract_type,
            "model_id": self.model_id,
            "chunking_profile": chunking_profile,
            "reusable": reusable,
            "extracted_clauses": [],
            "policy_violations": [],
            "risk_data": {},
            "redline_suggestions": [],
            "cuad_deviations": [],
            "jurisdiction_info": {},
            "precedent_matches": [],
            "messages": [],
            "current_step": "",
            "processing_result": None,
            "is_complete": False
        }
        
        # Run workflow
        final_state = self.workflow.invoke(initial_state)
        
        # Complete workflow tracking
        workflow_tracker.complete_workflow()
        
        # Return structured results with CUAD data and validation
        return {
            "clauses": final_state["extracted_clauses"],
            # False when extraction never ran. An empty list then means "we do
            # not know", and persistence must not replace a stored review on the
            # strength of it.
            "clauses_extracted": not final_state.get("clause_extraction_failed"),
            "coverage": final_state.get("clause_extraction_coverage"),
            "violations": final_state["policy_violations"],
            "risk_assessment": final_state["risk_data"],
            "redlines": final_state["redline_suggestions"],
            "redlines_generated": not final_state.get("redline_generation_failed"),
            "warnings": _stage_warnings(final_state),
            "cuad_deviations": final_state.get("cuad_deviations", []),
            "jurisdiction_info": final_state.get("jurisdiction_info", {}),
            "precedent_matches": final_state.get("precedent_matches", []),
            "validation_result": final_state.get("validation_result"),
            "pattern_used": final_state.get("pattern_used", "Standard"),
            "pattern_analysis": final_state.get("pattern_analysis", {}),
            "processing_complete": final_state["is_complete"]
        }

class ContractIntelligenceAgentFactory:
    """Factory following proper design patterns"""
    
    @staticmethod
    def create_orchestrator(llm, model_id: str = None):
        """Create orchestrator with proper architecture"""
        return IntelligenceOrchestrator(llm, model_id)