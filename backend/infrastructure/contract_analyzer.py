from backend.domain.entities import IContractAnalyzer
from backend.shared.utils.contract_search_tool import CONTRACT_TYPES
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from langchain_core.output_parsers import PydanticOutputParser
from backend.shared.utils.message_content import content_to_text, strip_code_fence
import logging

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

class ContractParty(BaseModel):
    name: str
    role: str = "Unknown"

class ContractAnalysis(BaseModel):
    is_contract: bool = Field(description="Whether this is a legal contract")
    confidence_score: float = Field(description="Confidence 0.0-1.0")
    contract_type: str = Field(description="Type of contract")
    summary: str = Field(description="Brief contract summary")
    parties: List[ContractParty] = Field(default_factory=list)
    effective_date: Optional[str] = None
    end_date: Optional[str] = None
    total_amount: Optional[float] = None
    governing_law: Optional[str] = None
    key_terms: List[str] = Field(default_factory=list)

class LLMContractAnalyzer(IContractAnalyzer):
    """Contract analysis using LLM - reuses existing infrastructure"""
    
    def __init__(self, llm):
        self.llm = llm
        self.parser = PydanticOutputParser(pydantic_object=ContractAnalysis)
    
    def analyze_contract(self, text: str) -> Dict[str, Any]:
        """Analyze contract text and extract structured data"""
        analysis_text = text[:8000] if len(text) > 8000 else text
        
        prompt = f"""Analyze this document:

{analysis_text}

{self.parser.get_format_instructions()}

Use contract_type from: {CONTRACT_TYPES}"""
        
        response = self.llm.invoke(prompt)
        # Same fence handling as clause extraction: models wrap JSON in
        # ```json despite the format instructions, and inconsistently.
        result = self.parser.parse(strip_code_fence(content_to_text(response.content)))
        return result.dict()