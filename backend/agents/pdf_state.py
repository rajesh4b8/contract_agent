from typing import TypedDict, Optional
from backend.domain.value_objects import ProcessingResult, ContractData

class PDFProcessingState(TypedDict):
    file_path: str
    tenant_id: str
    # The public model id in use, so a failed node can name it in the error a
    # reviewer reads ("Gemini Flash has no quota left"). LangGraph drops keys
    # that are not declared here, so it has to live on the state.
    model_id: Optional[str]
    extracted_text: Optional[str]
    contract_data: Optional[ContractData]
    processing_result: Optional[ProcessingResult]
    messages: list