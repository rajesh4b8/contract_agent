"""An analysis that could not run must not look like a contract with no findings.

When the model refused the call, the intelligence pipeline degraded all the way
to a complete-looking result: no clauses, no violations, risk "UNKNOWN", HTTP
200. A reviewer reading that page has no way to tell it apart from a clean
contract. These tests hold the line at every layer that used to swallow it.
"""
import asyncio
from unittest.mock import patch

import pytest

from backend.agents.planning.execution_engine import PlanExecutionEngine
from backend.agents.planning.planning_agent import (
    ExecutionPlan,
    ExecutionStep,
    PlanningStrategy,
    StepType,
)
from backend.application.services.contract_intelligence_service import (
    ContractIntelligenceService,
)
from backend.application.services.document_processing_service import (
    DocumentProcessingService,
)
from backend.domain.entities import (
    ContractIntelligence,
    DocumentProcessingRequest,
    RiskAssessment,
)
from backend.domain.value_objects import ProcessingResult, ProcessingStatus
from backend.shared.errors import LLMProviderError

QUOTA_MESSAGE = (
    "429 You exceeded your current quota "
    '[quota_id: "GenerateRequestsPerDayPerProjectPerModel-FreeTier"]'
)


class RefusingLLM:
    """A provider that is out of quota, counting how often it is asked."""

    def __init__(self):
        self.calls = 0

    def invoke(self, prompt):
        self.calls += 1
        raise Exception(QUOTA_MESSAGE)


def _plan(*steps: ExecutionStep) -> ExecutionPlan:
    return ExecutionPlan(
        plan_id="test-plan",
        query="analyse this contract",
        strategy=PlanningStrategy.SIMPLE,
        steps=list(steps),
        estimated_duration=10,
        confidence_score=0.9,
    )


class TestThePlannedWorkflow:
    """The default path: `use_planning=True`."""

    def test_a_refused_clause_extraction_stops_the_run(self):
        """Every later step reads the clauses, so there is nothing to salvage."""
        llm = RefusingLLM()
        engine = PlanExecutionEngine(llm, "gemini-flash")
        plan = _plan(ExecutionStep("step_1", StepType.EXTRACT_CLAUSES, "Extract clauses"))

        with pytest.raises(LLMProviderError) as caught:
            asyncio.run(engine.execute_plan(plan, "Some contract text"))

        assert caught.value.status_code == 429
        assert "quota" in caught.value.info.message.lower()

    def test_a_refusal_is_not_retried(self):
        """Three identical refusals half a minute apart help nobody, and the
        two extra calls come out of whatever allowance is left."""
        llm = RefusingLLM()
        engine = PlanExecutionEngine(llm, "gemini-flash")
        plan = _plan(ExecutionStep("step_1", StepType.EXTRACT_CLAUSES, "Extract clauses"))

        with pytest.raises(LLMProviderError):
            asyncio.run(engine.execute_plan(plan, "Some contract text"))

        assert llm.calls == 1

    def test_a_later_step_degrades_but_says_so(self):
        """A policy check that could not run is a gap in the report, not a pass."""
        engine = PlanExecutionEngine(RefusingLLM(), "gemini-flash")
        plan = _plan(ExecutionStep("step_1", StepType.CHECK_POLICIES, "Check policies"))

        async def refuse(self, step, context):
            raise Exception(QUOTA_MESSAGE)

        with patch(
            "backend.agents.planning.execution_engine.StepExecutor._execute_policy_check",
            refuse,
        ):
            results = asyncio.run(engine.execute_plan(plan, "Some contract text"))

        assert results["processing_complete"] is False
        assert len(results["warnings"]) == 1
        warning = results["warnings"][0]
        assert "Policy compliance could not be checked" in warning
        assert "quota" in warning.lower()

    def test_failed_drafting_does_not_claim_there_was_nothing_to_draft(self):
        """`redlines_generated` guards the stored redlines from being wiped."""
        engine = PlanExecutionEngine(RefusingLLM(), "gemini-flash")
        plan = _plan(ExecutionStep("step_1", StepType.GENERATE_REDLINES, "Draft redlines"))

        async def refuse(self, step, context):
            raise Exception(QUOTA_MESSAGE)

        with patch(
            "backend.agents.planning.execution_engine.StepExecutor._execute_redline_generation",
            refuse,
        ):
            results = asyncio.run(engine.execute_plan(plan, "Some contract text"))

        assert results["redlines_generated"] is False

    def test_a_clean_run_reports_no_warnings(self):
        engine = PlanExecutionEngine(RefusingLLM(), "gemini-flash")
        plan = _plan(ExecutionStep("step_1", StepType.ASSESS_RISK, "Assess risk"))

        results = asyncio.run(engine.execute_plan(plan, "Some contract text"))

        assert results["warnings"] == []
        assert results["processing_complete"] is True
        assert results["redlines_generated"] is True


class TestTheIntelligenceService:
    def test_a_refused_analysis_is_raised_not_returned_empty(self):
        service = ContractIntelligenceService(llm_manager=None)

        class RefusingOrchestrator:
            def analyze_contract(self, *args, **kwargs):
                raise Exception(QUOTA_MESSAGE)

        with patch.object(service, "_get_llm_for_model", return_value=object()), patch(
            "backend.application.services.contract_intelligence_service"
            ".ContractIntelligenceAgentFactory.create_orchestrator",
            return_value=RefusingOrchestrator(),
        ):
            with pytest.raises(LLMProviderError) as caught:
                service.analyze_contract_intelligence("contract text", model="gemini-flash")

        assert caught.value.status_code == 429

    def test_a_genuine_failure_still_degrades_to_an_empty_result(self):
        """Only provider refusals are re-raised; the old behaviour is intact."""
        service = ContractIntelligenceService(llm_manager=None)

        class BrokenOrchestrator:
            def analyze_contract(self, *args, **kwargs):
                raise KeyError("extracted_clauses")

        with patch.object(service, "_get_llm_for_model", return_value=object()), patch(
            "backend.application.services.contract_intelligence_service"
            ".ContractIntelligenceAgentFactory.create_orchestrator",
            return_value=BrokenOrchestrator(),
        ):
            intelligence = service.analyze_contract_intelligence("text", model="gemini-flash")

        assert intelligence.clauses == []
        # ...but the empty result now carries the reason it is empty.
        assert intelligence.warnings
        assert intelligence.redlines_generated is False


class TestTheUploadPath:
    """`final_result` is the only field the upload panel renders."""

    @staticmethod
    def _request():
        return DocumentProcessingRequest(
            file_path="/tmp/does-not-matter.pdf",
            filename="contract.pdf",
            tenant_id="default-tenant",
            processing_options={"model": "gemini-flash"},
        )

    def _run(self, agent):
        service = DocumentProcessingService(agent_manager=None)
        return asyncio.run(service._process_with_agent(agent, self._request()))

    def test_a_refused_upload_explains_itself(self):
        class RefusingAgent:
            async def ainvoke(self, state):
                raise Exception(QUOTA_MESSAGE)

        result = self._run(RefusingAgent())

        assert result["status"] == "error"
        assert "quota" in result["final_result"].lower()
        assert "gemini-flash" in result["final_result"]

    def test_the_reason_is_not_buried_under_a_second_prefix(self):
        """The panel already prefixes "Processing failed:"; one is enough."""
        class FailedAgent:
            async def ainvoke(self, state):
                return {
                    "processing_result": ProcessingResult(
                        status=ProcessingStatus.ERROR,
                        error="Google Gemini has no quota left on this API key.",
                    )
                }

        result = self._run(FailedAgent())

        assert result["final_result"] == "Google Gemini has no quota left on this API key."


class TestTheContractIntelligenceEntity:
    def test_warnings_default_to_none_reported(self):
        intelligence = ContractIntelligence(
            clauses=[],
            violations=[],
            risk_assessment=RiskAssessment(0.0, "LOW", [], []),
            redlines=[],
        )

        assert intelligence.warnings == []
