from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import asyncio
import logging
from datetime import datetime
import time
from functools import wraps
from backend.agents.planning.planning_agent import ExecutionPlan, ExecutionStep, StepType
from backend.agents.intelligence_tools import (
    ClauseDetectorTool, PolicyCheckerTool, 
    RiskCalculatorTool, RedlineGeneratorTool
)
from backend.agents.agent_workflow_tracker import workflow_tracker
from backend.infrastructure.playbook_loader import (
    attach_violated_policy as _attach_violated_policy,
    load_rules_for_tenant,
)
from backend.shared.errors import LLMErrorInfo, LLMProviderError, classify_llm_error
from backend.shared.debug import atrace_step, note
import json

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

@dataclass
class ExecutionResult:
    step_id: str
    success: bool
    output_data: Any
    execution_time_ms: int
    confidence_score: float
    error_message: Optional[str] = None
    # Set when the step failed because the model provider refused the call —
    # a spent quota, a rejected key. Carried rather than flattened to a string
    # so the engine can decide to stop and the API can answer with its status.
    provider_failure: Optional[LLMErrorInfo] = None

class StepExecutor:
    """Execute individual analysis steps"""
    
    def __init__(self, llm=None, model_id: Optional[str] = None):
        self.llm = llm
        self.model_id = model_id
        self.tools = {
            StepType.EXTRACT_CLAUSES: ClauseDetectorTool(llm=llm),
            # Rules are per-tenant, so this tool is rebuilt per run in
            # _execute_policy_check rather than held here.
            StepType.CHECK_POLICIES: PolicyCheckerTool(llm=llm),
            StepType.ASSESS_RISK: RiskCalculatorTool(),
            StepType.GENERATE_REDLINES: RedlineGeneratorTool(llm=llm)
        }
    
    async def execute_step(self, step: ExecutionStep, context: Dict[str, Any]) -> ExecutionResult:
        """Execute a single analysis step with timeout and retry"""
        start_time = datetime.now()
        
        # Implement timeout
        try:
            async with atrace_step(
                "analysis",
                step.step_type.value,
                step_id=step.step_id,
                timeout_s=step.timeout_seconds,
                model=self.model_id,
            ) as traced:
                result = await asyncio.wait_for(
                    self._execute_step_with_retry(step, context),
                    timeout=step.timeout_seconds
                )
                traced.set(success=result.success, error=result.error_message)
                return result
        except asyncio.TimeoutError:
            # The step's own 30s budget, not the provider's. Naming it separately
            # matters: a timeout here and a provider refusal look identical from
            # the UI but need completely different fixes.
            note(
                "analysis",
                step.step_type.value,
                "error",
                step_id=step.step_id,
                error_type="StepTimeout",
                error=f"exceeded the step's own {step.timeout_seconds}s budget",
            )
            return ExecutionResult(
                step_id=step.step_id,
                success=False,
                output_data=None,
                execution_time_ms=step.timeout_seconds * 1000,
                confidence_score=0.0,
                error_message=f"Step timed out after {step.timeout_seconds} seconds"
            )
    
    async def _execute_step_with_retry(self, step: ExecutionStep, context: Dict[str, Any]) -> ExecutionResult:
        """Execute step with retry mechanism"""
        logger.info(f"🔧 STEP EXEC 1: Starting step {step.step_id} with retry mechanism")
        max_retries = 2
        start_time = datetime.now()
        
        # Track step execution
        execution = workflow_tracker.start_agent(
            f"Planned {step.step_type.value.replace('_', ' ').title()} Step",
            step.description,
            self._get_input_summary(step, context)
        )
        
        for attempt in range(max_retries + 1):
            try:
                if attempt > 0:
                    logger.info(f"Retrying step {step.step_id}, attempt {attempt + 1}")
                    note(
                        "analysis",
                        f"{step.step_type.value}.retry",
                        step_id=step.step_id,
                        attempt=attempt + 1,
                        of=max_retries + 1,
                        backoff_s=attempt * 0.5,
                    )
                    await asyncio.sleep(attempt * 0.5)  # Exponential backoff
                
                logger.info(f"🔧 STEP EXEC 2: Executing {step.step_type} for {step.step_id}")
                
                if step.step_type == StepType.EXTRACT_CLAUSES:
                    result = await self._execute_clause_extraction(step, context)
                elif step.step_type == StepType.CHECK_POLICIES:
                    result = await self._execute_policy_check(step, context)
                elif step.step_type == StepType.ASSESS_RISK:
                    result = await self._execute_risk_assessment(step, context)
                elif step.step_type == StepType.GENERATE_REDLINES:
                    result = await self._execute_redline_generation(step, context)
                elif step.step_type == StepType.VALIDATE_RESULTS:
                    result = await self._execute_validation(step, context)
                elif step.step_type == StepType.CUAD_MITIGATION:
                    result = await self._execute_cuad_mitigation(step, context)
                else:
                    raise ValueError(f"Unknown step type: {step.step_type}")
                
                logger.info(f"🔧 STEP EXEC 3: Step {step.step_id} execution completed successfully")
                
                execution_time = int((datetime.now() - start_time).total_seconds() * 1000)
                workflow_tracker.complete_agent(execution, self._get_output_summary(result))
                
                return ExecutionResult(
                    step_id=step.step_id,
                    success=True,
                    output_data=result,
                    execution_time_ms=execution_time,
                    confidence_score=0.9
                )
                
            except Exception as e:
                # Retrying a refusal is not a retry. A spent quota or a rejected
                # key answers the same way three times in a row, half a minute
                # later, so these give up at once and report why.
                provider_failure = classify_llm_error(e, self.model_id)
                if provider_failure is not None:
                    execution_time = int((datetime.now() - start_time).total_seconds() * 1000)
                    workflow_tracker.error_agent(execution, provider_failure.message)
                    note(
                        "analysis",
                        f"{step.step_type.value}.provider_refused",
                        step_id=step.step_id,
                        kind=provider_failure.failure.value,
                        provider=provider_failure.provider,
                        detail=provider_failure.message,
                    )
                    logger.error(f"Step {step.step_id} hit a provider failure: {provider_failure.detail}")
                    return ExecutionResult(
                        step_id=step.step_id,
                        success=False,
                        output_data=None,
                        execution_time_ms=execution_time,
                        confidence_score=0.0,
                        error_message=provider_failure.message,
                        provider_failure=provider_failure,
                    )

                if attempt == max_retries:  # Last attempt failed
                    execution_time = int((datetime.now() - start_time).total_seconds() * 1000)
                    workflow_tracker.error_agent(execution, str(e))
                    
                    return ExecutionResult(
                        step_id=step.step_id,
                        success=False,
                        output_data=None,
                        execution_time_ms=execution_time,
                        confidence_score=0.0,
                        error_message=f"Failed after {max_retries + 1} attempts: {str(e)}"
                    )
                else:
                    logger.warning(f"Step {step.step_id} attempt {attempt + 1} failed: {e}")
                    continue  # Retry
    
    async def _execute_clause_extraction(self, step: ExecutionStep, context: Dict[str, Any]) -> List[Dict]:
        """Execute clause extraction with enhanced planning context"""
        contract_text = context.get("contract_text", "")
        tool = self.tools[StepType.EXTRACT_CLAUSES]
        result_json = tool._run(contract_text)
        return json.loads(result_json)
    
    async def _execute_policy_check(self, step: ExecutionStep, context: Dict[str, Any]) -> List[Dict]:
        """Check clauses against the tenant's playbook.

        Rules are loaded per run: they are tenant- and contract-type-specific, so
        a tool built once at construction would check every tenant against
        whichever playbook happened to be loaded first — or, as before, against
        none at all.
        """
        clauses = context.get("extracted_clauses", [])
        rules = load_rules_for_tenant(
            context.get("tenant_id") or "default-tenant",
            context.get("contract_type") or "general",
        )
        tool = PolicyCheckerTool(llm=self.llm, rules=rules)
        result_json = tool._run(json.dumps(clauses))
        violations = json.loads(result_json)

        # Same provenance stamping the graph path does, so both routes produce
        # clauses that cite the rules they breach.
        context["extracted_clauses"] = _attach_violated_policy(clauses, violations)
        return violations
    
    async def _execute_risk_assessment(self, step: ExecutionStep, context: Dict[str, Any]) -> Dict:
        """Execute risk assessment with enhanced analysis"""
        clauses = context.get("extracted_clauses", [])
        violations = context.get("policy_violations", [])
        tool = self.tools[StepType.ASSESS_RISK]
        result_json = tool._run(json.dumps(clauses), json.dumps(violations))
        return json.loads(result_json)
    
    async def _execute_redline_generation(self, step: ExecutionStep, context: Dict[str, Any]) -> List[Dict]:
        """Execute redline generation with comprehensive context"""
        violations = context.get("policy_violations", [])
        tool = self.tools[StepType.GENERATE_REDLINES]
        result_json = tool._run(json.dumps(violations))
        return json.loads(result_json)
    
    async def _execute_validation(self, step: ExecutionStep, context: Dict[str, Any]) -> Dict:
        """Execute cross-validation of results"""
        # Validate consistency between risk assessment and policy violations
        risk_data = context.get("risk_data", {})
        violations = context.get("policy_violations", [])
        
        validation_score = 1.0
        issues = []
        
        # Check if high-risk score aligns with critical violations
        risk_score = risk_data.get("overall_risk_score", 0)
        critical_violations = len([v for v in violations if v.get("severity") == "CRITICAL"])
        
        if risk_score > 80 and critical_violations == 0:
            validation_score -= 0.3
            issues.append("High risk score without critical violations")
        
        if risk_score < 40 and critical_violations > 2:
            validation_score -= 0.3
            issues.append("Low risk score with multiple critical violations")
        
        return {
            "validation_score": max(0.0, validation_score),
            "issues": issues,
            "validated_at": datetime.now().isoformat()
        }
    
    async def _execute_cuad_mitigation(self, step: ExecutionStep, context: Dict[str, Any]) -> Dict:
        """Execute enhanced CUAD mitigation analysis"""
        try:
            # Try optimized Phase 3 tools first
            from backend.agents.optimized_cuad_tools import (
                OptimizedDeviationDetectorTool, OptimizedJurisdictionAdapterTool, OptimizedPrecedentMatcherTool
            )
            from backend.agents.feedback_learning_system import AdaptiveAnalyzer
            
            clauses = context.get("extracted_clauses", [])
            contract_text = context.get("contract_text", "")
            
            # Run optimized CUAD tools
            deviation_tool = OptimizedDeviationDetectorTool()
            jurisdiction_tool = OptimizedJurisdictionAdapterTool()
            precedent_tool = OptimizedPrecedentMatcherTool()
            
            deviations = json.loads(deviation_tool._run(json.dumps(clauses)))
            jurisdiction_info = json.loads(jurisdiction_tool._run(contract_text))
            precedent_matches = json.loads(precedent_tool._run(json.dumps(clauses)))
            
            # Apply adaptive learning
            adaptive_analyzer = AdaptiveAnalyzer()
            enhanced_clauses = []
            for clause in clauses:
                enhanced_analysis = adaptive_analyzer.enhance_analysis(clause, clause)
                enhanced_clauses.append(enhanced_analysis)
            
            return {
                "cuad_deviations": deviations,
                "jurisdiction_info": jurisdiction_info,
                "precedent_matches": precedent_matches,
                "enhanced_clauses": enhanced_clauses,
                "analysis_method": "optimized_phase3"
            }
            
        except Exception as e:
            logger.warning(f"Optimized CUAD tools failed, falling back to enhanced tools: {e}")
            
            # Try Phase 2 tools
            try:
                from backend.agents.enhanced_cuad_tools import (
                    EnhancedDeviationDetectorTool, EnhancedJurisdictionAdapterTool, EnhancedPrecedentMatcherTool
                )
                
                clauses = context.get("extracted_clauses", [])
                contract_text = context.get("contract_text", "")
                
                deviation_tool = EnhancedDeviationDetectorTool()
                jurisdiction_tool = EnhancedJurisdictionAdapterTool()
                precedent_tool = EnhancedPrecedentMatcherTool()
                
                deviations = json.loads(deviation_tool._run(json.dumps(clauses)))
                jurisdiction_info = json.loads(jurisdiction_tool._run(contract_text))
                precedent_matches = json.loads(precedent_tool._run(json.dumps(clauses)))
                
                return {
                    "cuad_deviations": deviations,
                    "jurisdiction_info": jurisdiction_info,
                    "precedent_matches": precedent_matches,
                    "analysis_method": "enhanced_phase2_fallback"
                }
                
            except Exception as e2:
                logger.warning(f"Enhanced CUAD tools also failed, falling back to Phase 1: {e2}")
            
            # Fallback to Phase 1 tools
            from backend.agents.cuad_mitigation_tools import (
                DeviationDetectorTool, JurisdictionAdapterTool, PrecedentMatcherTool
            )
            
            clauses = context.get("extracted_clauses", [])
            contract_text = context.get("contract_text", "")
            
            deviation_tool = DeviationDetectorTool()
            jurisdiction_tool = JurisdictionAdapterTool()
            precedent_tool = PrecedentMatcherTool()
            
            deviations = json.loads(deviation_tool._run(json.dumps(clauses)))
            jurisdiction_info = json.loads(jurisdiction_tool._run(contract_text))
            precedent_matches = json.loads(precedent_tool._run(json.dumps(clauses)))
            
            return {
                "cuad_deviations": deviations,
                "jurisdiction_info": jurisdiction_info,
                "precedent_matches": precedent_matches,
                "analysis_method": "fallback_phase1"
            }
    
    def _get_input_summary(self, step: ExecutionStep, context: Dict[str, Any]) -> str:
        """Get human-readable input summary for tracking"""
        if step.step_type == StepType.EXTRACT_CLAUSES:
            text_len = len(context.get("contract_text", ""))
            return f"Contract text ({text_len:,} characters)"
        elif step.step_type == StepType.CHECK_POLICIES:
            clause_count = len(context.get("extracted_clauses", []))
            return f"{clause_count} extracted clauses"
        elif step.step_type == StepType.ASSESS_RISK:
            clauses = len(context.get("extracted_clauses", []))
            violations = len(context.get("policy_violations", []))
            return f"{clauses} clauses + {violations} violations"
        elif step.step_type == StepType.GENERATE_REDLINES:
            violation_count = len(context.get("policy_violations", []))
            return f"{violation_count} policy violations"
        elif step.step_type == StepType.VALIDATE_RESULTS:
            return "Cross-validation of analysis results"
        return "Analysis context"
    
    def _get_output_summary(self, result: Any) -> str:
        """Get human-readable output summary for tracking"""
        if isinstance(result, list):
            return f"Generated {len(result)} items"
        elif isinstance(result, dict):
            if "overall_risk_score" in result:
                score = result["overall_risk_score"]
                level = result.get("risk_level", "UNKNOWN")
                return f"Risk Score: {score}/100 ({level})"
            elif "validation_score" in result:
                score = result["validation_score"]
                return f"Validation Score: {score:.2f}"
            else:
                return f"Analysis result with {len(result)} fields"
        return "Analysis completed"

# What each step's absence means, said plainly. The planner's own
# descriptions read as instructions ("Generate comprehensive redlines"), which
# is the wrong voice for a failure notice.
_STEP_FAILURE_LABELS = {
    StepType.EXTRACT_CLAUSES: "Clauses could not be extracted",
    StepType.CHECK_POLICIES: "Policy compliance could not be checked",
    StepType.ASSESS_RISK: "The risk score could not be calculated",
    StepType.GENERATE_REDLINES: "Redlines could not be drafted",
    StepType.VALIDATE_RESULTS: "Results could not be cross-validated",
    StepType.CUAD_MITIGATION: "CUAD deviation analysis did not complete",
}


class PlanExecutionEngine:
    """Execute planned analysis workflows with dependency management"""
    
    def __init__(self, llm=None, model_id: Optional[str] = None):
        self.step_executor = StepExecutor(llm, model_id)
        self.execution_context: Dict[str, Any] = {}
        self.step_failures: List[tuple] = []  # (ExecutionStep, ExecutionResult)
    
    async def execute_plan(self, plan: ExecutionPlan, contract_text: str,
                           tenant_id: str = "default-tenant",
                           contract_type: str = "general") -> Dict[str, Any]:
        """Execute the complete analysis plan"""
        logger.info(f"🚀 EXEC STEP 1: Starting plan execution {plan.plan_id} with {len(plan.steps)} steps")
        logger.info(f"🚀 EXEC STEP 2: Contract text length: {len(contract_text)} characters")
        note(
            "analysis",
            "plan",
            plan_id=plan.plan_id,
            steps=len(plan.steps),
            chars=len(contract_text),
            tenant=tenant_id,
            sequence=" → ".join(s.step_type.value for s in plan.steps),
        )
        
        # Initialize execution context
        self.execution_context = {
            "contract_text": contract_text,
            # Which playbook the policy step checks against.
            "tenant_id": tenant_id,
            "contract_type": contract_type,
            "plan_id": plan.plan_id,
            "execution_start": datetime.now()
        }
        
        # Don't reset workflow tracker - planning agent already started it
        # workflow_tracker.start_workflow()
        
        step_results: Dict[str, ExecutionResult] = {}
        self.step_failures = []
        
        try:
            # Execute steps respecting dependencies
            logger.info(f"🚀 EXEC STEP 3: Starting step execution loop")
            for i, step in enumerate(plan.steps):
                logger.info(f"🚀 EXEC STEP 4.{i+1}: Processing step {step.step_id} ({step.step_type})")
                
                # Wait for dependencies
                await self._wait_for_dependencies(step, step_results)
                logger.info(f"🚀 EXEC STEP 4.{i+1}a: Dependencies satisfied for {step.step_id}")
                
                # Execute step
                logger.info(f"🚀 EXEC STEP 4.{i+1}b: Executing step {step.step_id}")
                result = await self.step_executor.execute_step(step, self.execution_context)
                step_results[step.step_id] = result
                logger.info(f"🚀 EXEC STEP 4.{i+1}c: Step {step.step_id} completed, success: {result.success}")
                
                # Update context with results
                if result.success:
                    self._update_context_with_result(step, result)
                    logger.info(f"🚀 EXEC STEP 4.{i+1}d: Context updated for {step.step_id}")
                else:
                    logger.error(f"🚀 EXEC ERROR: Step {step.step_id} failed: {result.error_message}")
                    self.step_failures.append((step, result))

                    # Clause extraction is the one step nothing can proceed
                    # without: every later step reads its output, so when the
                    # provider refuses it the run produces an empty analysis
                    # that reads like a clean contract. Stop and say why.
                    if (step.step_type == StepType.EXTRACT_CLAUSES
                            and result.provider_failure is not None):
                        workflow_tracker.complete_workflow()
                        raise LLMProviderError(result.provider_failure)

                    # Everything else degrades: the partial analysis is still
                    # worth having, as long as the gap is reported.
            
            # Complete workflow tracking
            workflow_tracker.complete_workflow()
            
            # Return final results in expected format
            return self._format_final_results()
            
        except LLMProviderError:
            raise
        except Exception as e:
            logger.error(f"Plan execution failed: {e}")
            workflow_tracker.complete_workflow()
            return self._format_error_results(str(e))
    
    async def _wait_for_dependencies(self, step: ExecutionStep, step_results: Dict[str, ExecutionResult]):
        """Wait for step dependencies to complete"""
        for dep_id in step.dependencies:
            while dep_id not in step_results:
                await asyncio.sleep(0.1)  # Wait for dependency
            
            if not step_results[dep_id].success:
                logger.warning(f"Dependency {dep_id} failed for step {step.step_id}")
    
    def _update_context_with_result(self, step: ExecutionStep, result: ExecutionResult):
        """Update execution context with step results"""
        if step.step_type == StepType.EXTRACT_CLAUSES:
            self.execution_context["extracted_clauses"] = result.output_data
        elif step.step_type == StepType.CHECK_POLICIES:
            self.execution_context["policy_violations"] = result.output_data
        elif step.step_type == StepType.ASSESS_RISK:
            self.execution_context["risk_data"] = result.output_data
        elif step.step_type == StepType.GENERATE_REDLINES:
            self.execution_context["redline_suggestions"] = result.output_data
        elif step.step_type == StepType.VALIDATE_RESULTS:
            self.execution_context["validation_results"] = result.output_data
        elif step.step_type == StepType.CUAD_MITIGATION:
            cuad_data = result.output_data
            self.execution_context["cuad_deviations"] = cuad_data.get("cuad_deviations", [])
            self.execution_context["jurisdiction_info"] = cuad_data.get("jurisdiction_info", {})
            self.execution_context["precedent_matches"] = cuad_data.get("precedent_matches", [])
    
    def _format_final_results(self) -> Dict[str, Any]:
        """Format results in the expected contract intelligence format"""
        return {
            "clauses": self.execution_context.get("extracted_clauses", []),
            "violations": self.execution_context.get("policy_violations", []),
            "risk_assessment": self.execution_context.get("risk_data", {}),
            "redlines": self.execution_context.get("redline_suggestions", []),
            "cuad_deviations": self.execution_context.get("cuad_deviations", []),
            "jurisdiction_info": self.execution_context.get("jurisdiction_info", {}),
            "precedent_matches": self.execution_context.get("precedent_matches", []),
            "validation": self.execution_context.get("validation_results", {}),
            "warnings": self._failure_warnings(),
            # Drafting is the step whose empty output must not be mistaken for
            # "nothing needed changing" — the stored redlines are replaced on
            # the strength of this flag.
            "redlines_generated": not self._step_failed(StepType.GENERATE_REDLINES),
            "processing_complete": not self.step_failures,
            "planned_execution": True
        }

    def _step_failed(self, step_type: StepType) -> bool:
        return any(step.step_type == step_type for step, _ in self.step_failures)

    def _failure_warnings(self) -> List[str]:
        """One line per step that degraded, in words a reviewer can act on.

        Plan step ids are "step_2a"; what a reviewer needs to know is that the
        policy check did not run, and why.
        """
        return [
            f"{_STEP_FAILURE_LABELS.get(step.step_type, step.description)}: {result.error_message}"
            for step, result in self.step_failures
        ]
    
    def _format_error_results(self, error_message: str) -> Dict[str, Any]:
        """Format error results"""
        return {
            "clauses": [],
            "violations": [],
            "risk_assessment": {"overall_risk_score": 0, "risk_level": "UNKNOWN"},
            "redlines": [],
            "warnings": [f"The analysis did not complete: {error_message}"],
            # No redlines were drafted, so the stored set must survive: an
            # empty list here means "we do not know", not "none needed".
            "redlines_generated": False,
            "processing_complete": False,
            "error": error_message
        }