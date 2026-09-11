from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from datetime import datetime

@dataclass
class DocumentProcessingRequest:
    file_path: str
    filename: str
    tenant_id: str
    user_id: Optional[str] = None
    processing_options: Dict[str, Any] = None

@dataclass 
class ContractExtractionResult:
    contract_data: Dict[str, Any]
    confidence_score: float
    validation_errors: List[str]
    requires_human_review: bool
    extracted_text: str = ""
    tenant_id: str = ""

# Domain interfaces (Interface Segregation Principle)
class ITextExtractor(ABC):
    @abstractmethod
    def extract_text(self, file_path: str) -> str:
        pass

class IContractAnalyzer(ABC):
    @abstractmethod
    async def analyze_contract(self, text: str) -> Dict[str, Any]:
        pass

class IContractRepository(ABC):
    @abstractmethod
    async def store_contract(self, contract_data: Dict[str, Any], tenant_id: str) -> str:
        pass
    
    @abstractmethod
    async def get_contract_by_id(self, contract_id: str, tenant_id: str) -> Dict[str, Any]:
        pass

class IDocumentProcessor(ABC):
    @abstractmethod
    async def process_document(self, request: DocumentProcessingRequest) -> ContractExtractionResult:
        pass

# Contract Intelligence Entities
@dataclass
class ContractClause:
    """A clause as reported to the reviewer.

    Field names track the design doc's output schema. ``content`` is the older
    name for ``evidence_span`` and is kept while the UI still reads it.
    """
    clause_type: str  # Payment, Liability, IP, Confidentiality, Termination
    content: str
    risk_level: str  # LOW, MEDIUM, HIGH, CRITICAL
    confidence_score: float
    location: str = ""
    evidence_span: str = ""          # verbatim quote from the contract
    violated_policy: Optional[str] = None    # playbook rule id (Increment 2)
    suggested_redline: Optional[str] = None  # proposed language (Increment 3)
    human_review_required: bool = False

@dataclass
class PolicyViolation:
    """A clause breaching a playbook rule.

    ``rule_id`` is the citation: it names the playbook entry that produced this
    finding, which is what makes the result auditable rather than an opinion.
    """
    clause_type: str
    issue: str
    severity: str  # LOW, MEDIUM, HIGH, CRITICAL — taken from the rule, not the model
    suggested_fix: str
    clause_content: str = ""
    rule_id: Optional[str] = None     # playbook rule this cites
    section_reference: str = ""       # where in the playbook it sits

@dataclass
class RiskAssessment:
    overall_risk_score: float  # 0-100
    risk_level: str  # LOW, MEDIUM, HIGH, CRITICAL
    critical_issues: List[str]
    recommendations: List[str]

@dataclass
class RedlineRecommendation:
    """Proposed replacement language for a clause that breaches a rule."""
    original_text: str
    suggested_text: str
    justification: str
    priority: str  # LOW, MEDIUM, HIGH, CRITICAL — follows the rule's severity
    rule_id: Optional[str] = None   # the rule this remediates
    clause_index: Optional[int] = None  # which clause it rewrites
    clause_type: str = ""

@dataclass
class ContractIntelligence:
    # redlines_generated is False when drafting errored. An empty redlines list
    # then means "we do not know", not "none needed" — persistence must not
    # overwrite good drafts on the strength of it.
    clauses: List[ContractClause]
    violations: List[PolicyViolation]
    risk_assessment: RiskAssessment
    redlines: List[RedlineRecommendation]
    processing_time: float = 0.0
    redlines_generated: bool = True
    
    # CUAD mitigation fields (Phase 1)
    cuad_deviations: List[Dict[str, Any]] = None
    jurisdiction_info: Dict[str, Any] = None
    precedent_matches: List[Dict[str, Any]] = None
    
    def __post_init__(self):
        if self.cuad_deviations is None:
            self.cuad_deviations = []
        if self.jurisdiction_info is None:
            self.jurisdiction_info = {}
        if self.precedent_matches is None:
            self.precedent_matches = []

@dataclass
class AgentMessage:
    agent_id: str
    message_type: str
    data: Dict[str, Any]
    timestamp: str