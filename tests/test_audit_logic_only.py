import sys
import os
from unittest.mock import MagicMock

# Define a fake AuditEventType since we can't import it easily without triggering transformers
class FakeAuditEventType:
    USER_INTERACTION = "user_interaction"
    AGENT_TOOL_CALL = "agent_tool_call"
    AGENT_THOUGHT = "agent_thought"
    MODEL_GUARD_CHECK = "model_guard_check"

# Mock the module
sys.modules['backend.infrastructure.audit_logger'] = MagicMock()
sys.modules['backend.infrastructure.audit_logger'].AuditEventType = FakeAuditEventType

# Now we can import AgentAuditService (it will use our mocked audit_logger module)
# But wait, AgentAuditService imports from .audit_logger which is backend.infrastructure.audit_logger
# So we need to make sure the path is right

# Let's just define the class here for a purely logical test if needed, 
# but I want to test the actual file.

# Add project root to path

from backend.infrastructure.agent_audit_service import AgentAuditService

def test_logic():
    mock_logger = MagicMock()
    service = AgentAuditService(audit_logger=mock_logger)
    
    service.log_user_interaction("u1", "hello", "s1")
    assert mock_logger.log_event.called
    args = mock_logger.log_event.call_args[1]
    assert args['event_type'] == "user_interaction"
    assert args['resource_id'] == "s1"
    
    print("Logic test passed!")

if __name__ == "__main__":
    test_logic()
