from typing import TypedDict, List, Any
from backend.domain.value_objects import ProcessingResult

class IntelligenceState(TypedDict):
    """Properly designed state following SRP - data separate from workflow"""
    
    # Input data
    contract_text: str
    tenant_id: str        # which playbook applies
    contract_type: str    # narrows which rules apply
    model_id: str         # public model id, so a failure can name the model
    
    # Processing results (structured data, not strings)
    extracted_clauses: List[dict]
    policy_violations: List[dict] 
    risk_data: dict
    redline_suggestions: List[dict]
    redline_generation_failed: str   # set when drafting errored; blocks overwrite
    # Set when a stage degraded instead of failing outright. LangGraph drops
    # state keys it was never told about, so `policy_check_failed` was being
    # written by the policy node and silently discarded before it could be
    # reported.
    policy_check_failed: str
    risk_calculation_failed: str
    
    # CUAD mitigation results (Phase 1 extension)
    cuad_deviations: List[dict]
    jurisdiction_info: dict
    precedent_matches: List[dict]
    
    # Pattern analysis results (NEW)
    pattern_used: str
    pattern_analysis: dict
    
    # Workflow metadata
    messages: List[Any]
    current_step: str
    processing_result: ProcessingResult
    is_complete: bool