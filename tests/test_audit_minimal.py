import sys
import os
from unittest.mock import MagicMock, patch

# Add project root to path

# Mock Neo4jContractRepository to avoid heavy imports and database connection
mock_repo = MagicMock()
with patch('backend.infrastructure.contract_repository.Neo4jContractRepository', return_value=mock_repo):
    from backend.infrastructure.audit_logger import AuditLogger, AuditEventType
    from backend.infrastructure.agent_audit_service import AgentAuditService

def test_minimal_audit_service():
    """Test the specialized agent audit service directly with mocks"""
    # Create the service with a mocked logger
    mock_audit_logger = MagicMock()
    agent_audit = AgentAuditService(audit_logger=mock_audit_logger)
    session_id = "test_session_123"
    
    # 1. Test user interaction logging
    agent_audit.log_user_interaction(
        user_id="test_user", 
        prompt="What is the termination clause?", 
        session_id=session_id
    )
    assert mock_audit_logger.log_event.called
    print("✅ User interaction logged")

    # 2. Test tool execution logging
    mock_audit_logger.log_event.reset_mock()
    agent_audit.log_tool_execution(
        tool_name="ContractSearchTool",
        args={"query": "termination"},
        result="Section 5",
        session_id=session_id
    )
    assert mock_audit_logger.log_event.called
    print("✅ Tool execution logged")

    # 3. Test model decision logging
    mock_audit_logger.log_event.reset_mock()
    agent_audit.log_model_decision(
        rationale="Searching...",
        session_id=session_id
    )
    assert mock_audit_logger.log_event.called
    print("✅ Model decision logged")

    # 4. Test guard check logging
    mock_audit_logger.log_event.reset_mock()
    agent_audit.log_guard_check(
        guard_name="Prompt Guard",
        is_safe=True,
        violation_type=None,
        session_id=session_id
    )
    assert mock_audit_logger.log_event.called
    print("✅ Guard check logged")

if __name__ == "__main__":
    try:
        test_minimal_audit_service()
        print("\nMinimal audit verification tests passed!")
    except Exception as e:
        print(f"\nMinimal audit verification tests FAILED: {e}")
        sys.exit(1)
