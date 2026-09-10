"""
Test Suite for Audit Logging, Content Validation, and Error Tracking
"""

import pytest
from backend.infrastructure.audit_logger import AuditLogger, AuditEventType
from backend.infrastructure.content_validator import ContentValidationService, ValidationSeverity
from backend.infrastructure.error_tracker import ErrorTracker, ErrorCategory, ErrorSeverity, ErrorContext, error_tracking_context

@pytest.mark.integration  # writes to Neo4j
def test_audit_logger_basic():
    """Test basic audit logging functionality"""
    audit_logger = AuditLogger()
    
    audit_id = audit_logger.log_event(
        event_type=AuditEventType.DOCUMENT_UPLOAD,
        resource_id="test_contract_123",
        action="test_upload",
        status="success",
        metadata={"test": "data"}
    )
    
    assert audit_id != ""
    print(f"✅ Audit logged: {audit_id}")

@pytest.mark.integration  # writes to Neo4j
def test_audit_trail_retrieval():
    """Test audit trail retrieval"""
    audit_logger = AuditLogger()
    
    # Log multiple events
    for i in range(3):
        audit_logger.log_event(
            event_type=AuditEventType.DOCUMENT_ACCESS,
            resource_id="test_contract_456",
            action=f"access_{i}",
            status="success"
        )
    
    # Retrieve trail
    trail = audit_logger.get_audit_trail("test_contract_456", limit=10)
    
    assert len(trail) >= 3
    print(f"✅ Retrieved {len(trail)} audit events")

def test_content_validator_file_size():
    """Test file size validation"""
    validator = ContentValidationService()
    
    # Valid file size with full_text to pass content quality check
    result = validator.validate({
        "filename": "test.pdf",
        "file_size": 1024 * 1024,  # 1MB
        "full_text": "Valid contract content " * 20
    })
    
    assert result["is_valid"] == True
    print(f"✅ File size validation passed: {result['summary']}")
    
    # Invalid file size
    result = validator.validate({
        "filename": "test.pdf",
        "file_size": 100 * 1024 * 1024,  # 100MB
        "full_text": "Valid contract content " * 20
    })
    
    assert result["is_valid"] == False
    assert result["has_errors"] == True
    print(f"✅ File size validation correctly failed: {result['summary']}")

def test_content_validator_file_type():
    """Test file type validation"""
    validator = ContentValidationService()
    
    # Valid file type
    result = validator.validate({
        "filename": "contract.pdf",
        "file_size": 1024,
        "full_text": "Valid contract content " * 20
    })
    
    assert result["is_valid"] == True
    print(f"✅ File type validation passed")
    
    # Invalid file type
    result = validator.validate({
        "filename": "contract.docx",
        "file_size": 1024,
        "full_text": "Valid contract content " * 20
    })
    
    assert result["is_valid"] == False
    print(f"✅ File type validation correctly failed")

def test_content_validator_quality():
    """Test content quality validation"""
    validator = ContentValidationService()
    
    # Valid content
    result = validator.validate({
        "filename": "test.pdf",
        "file_size": 1024,
        "full_text": "This is a valid contract with sufficient content. " * 10
    })
    
    assert result["is_valid"] == True
    print(f"✅ Content quality validation passed")
    
    # Invalid content (too short)
    result = validator.validate({
        "filename": "test.pdf",
        "file_size": 1024,
        "full_text": "Short"
    })
    
    assert result["is_valid"] == False
    print(f"✅ Content quality validation correctly failed for short content")

def test_content_validator_structure():
    """Test contract structure validation"""
    validator = ContentValidationService()
    
    # Valid structure
    result = validator.validate({
        "filename": "test.pdf",
        "file_size": 1024,
        "full_text": "Valid content " * 20,
        "contract_type": "Service Agreement",
        "summary": "Test contract summary",
        "parties": [{"name": "Party A", "role": "Provider"}]
    })
    
    assert result["is_valid"] == True
    print(f"✅ Contract structure validation passed")
    
    # Missing required fields
    result = validator.validate({
        "filename": "test.pdf",
        "file_size": 1024,
        "full_text": "Valid content " * 20
    })
    
    assert result["has_warnings"] == True
    print(f"✅ Contract structure validation detected missing fields")

@pytest.mark.integration  # writes to Neo4j
def test_error_tracker_basic():
    """Test basic error tracking"""
    error_tracker = ErrorTracker()
    
    context = ErrorContext(
        operation="test_operation",
        resource_id="test_resource",
        metadata={"test": "data"}
    )
    
    try:
        raise ValueError("Test error")
    except Exception as e:
        error_id = error_tracker.track_error(
            error=e,
            category=ErrorCategory.VALIDATION_ERROR,
            severity=ErrorSeverity.MEDIUM,
            context=context
        )
        
        assert error_id != ""
        print(f"✅ Error tracked: {error_id}")

@pytest.mark.integration  # writes to Neo4j
def test_error_tracker_statistics():
    """Test error statistics retrieval"""
    error_tracker = ErrorTracker()
    
    # Track multiple errors
    for i in range(3):
        context = ErrorContext(operation=f"test_op_{i}")
        try:
            raise RuntimeError(f"Test error {i}")
        except Exception as e:
            error_tracker.track_error(
                error=e,
                category=ErrorCategory.PROCESSING_ERROR,
                severity=ErrorSeverity.HIGH,
                context=context
            )
    
    # Get statistics
    stats = error_tracker.get_error_statistics(hours=24)
    
    assert stats["total_errors"] >= 3
    print(f"✅ Error statistics: {stats}")

def test_error_tracking_context_manager():
    """Test error tracking context manager"""
    
    # Test successful operation
    with error_tracking_context(
        operation="test_success",
        category=ErrorCategory.PROCESSING_ERROR,
        resource_id="test_123"
    ) as ctx:
        result = "success"
    
    assert len(ctx.errors) == 0
    print(f"✅ Context manager tracked successful operation")
    
    # Test failed operation (suppressed)
    with error_tracking_context(
        operation="test_failure",
        category=ErrorCategory.PROCESSING_ERROR,
        resource_id="test_456",
        raise_on_error=False
    ) as ctx:
        raise ValueError("Test error")
    
    assert len(ctx.errors) == 1
    print(f"✅ Context manager tracked failed operation: {ctx.errors}")

def test_integration_validation_with_audit():
    """Test integration of validation with audit logging"""
    validator = ContentValidationService()
    audit_logger = AuditLogger()
    
    # Validate content
    validation_result = validator.validate({
        "filename": "test.pdf",
        "file_size": 1024,
        "full_text": "Test content " * 20
    })
    
    # Log validation result
    if not validation_result["is_valid"]:
        audit_id = audit_logger.log_event(
            event_type=AuditEventType.VALIDATION_FAILURE,
            resource_id="test.pdf",
            action="content_validation",
            status="failure",
            error_details=str(validation_result)
        )
        assert audit_id != ""
    
    print(f"✅ Validation and audit integration working")

if __name__ == "__main__":
    print("=== Testing Audit Logging ===")
    test_audit_logger_basic()
    test_audit_trail_retrieval()
    
    print("\n=== Testing Content Validation ===")
    test_content_validator_file_size()
    test_content_validator_file_type()
    test_content_validator_quality()
    test_content_validator_structure()
    
    print("\n=== Testing Error Tracking ===")
    test_error_tracker_basic()
    test_error_tracker_statistics()
    test_error_tracking_context_manager()
    
    print("\n=== Testing Integration ===")
    test_integration_validation_with_audit()
    
    print("\n✅ All tests passed!")
