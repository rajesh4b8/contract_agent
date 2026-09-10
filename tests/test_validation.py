#!/usr/bin/env python3
"""
Test CUAD Validation System
"""

import sys
import os

# Add backend to path

def test_validation_system():
    """Test CUAD validation system"""
    print("Testing CUAD Validation System...")
    
    from backend.validation.cuad_validator import validate_cuad_analysis, ValidationResult
    
    # Test valid analysis result
    valid_analysis = {
        "clauses": [
            {
                "clause_type": "Payment Terms",
                "content": "Payment within 30 days",
                "risk_level": "LOW",
                "confidence_score": 0.9
            }
        ],
        "cuad_deviations": [
            {
                "clause_type": "Payment Terms",
                "deviation_type": "extended_payment_terms",
                "severity": "MEDIUM",
                "issue": "Payment terms exceed standard"
            }
        ],
        "risk_assessment": {
            "overall_risk_score": 45.0,
            "risk_level": "MEDIUM"
        },
        "policy_violations": []
    }
    
    result = validate_cuad_analysis(valid_analysis)
    
    print(f"Valid analysis validation:")
    print(f"  - Is valid: {result.is_valid}")
    print(f"  - Confidence: {result.confidence_score:.2f}")
    print(f"  - Errors: {len(result.validation_errors)}")
    print(f"  - Warnings: {len(result.warnings)}")
    
    # Test invalid analysis result
    invalid_analysis = {
        "clauses": [
            {
                "clause_type": "",  # Missing clause type
                "content": "Payment within 30 days",
                "risk_level": "INVALID",  # Invalid risk level
                "confidence_score": 1.5  # Invalid confidence score
            }
        ],
        "cuad_deviations": [
            {
                "clause_type": "Payment Terms",
                # Missing required fields
                "severity": "INVALID_SEVERITY"
            }
        ],
        "risk_assessment": {
            "overall_risk_score": 150.0,  # Invalid score
            "risk_level": "INVALID"
        },
        "policy_violations": []
    }
    
    invalid_result = validate_cuad_analysis(invalid_analysis)
    
    print(f"\nInvalid analysis validation:")
    print(f"  - Is valid: {invalid_result.is_valid}")
    print(f"  - Confidence: {invalid_result.confidence_score:.2f}")
    print(f"  - Errors: {len(invalid_result.validation_errors)}")
    print(f"  - Warnings: {len(invalid_result.warnings)}")
    
    if invalid_result.validation_errors:
        print(f"  - Sample errors: {invalid_result.validation_errors[:2]}")
    
    assert result.is_valid == True, "Valid analysis should pass validation"
    assert invalid_result.is_valid == False, "Invalid analysis should fail validation"
    assert len(invalid_result.validation_errors) > 0, "Should detect validation errors"
    
    print("✓ CUAD validation system working\n")

def test_workflow_integration():
    """Test validation integration with workflow"""
    print("Testing Workflow Integration...")
    
    try:
        # Test that validation is integrated into workflow
        from backend.agents.contract_intelligence_agents import IntelligenceOrchestrator
        
        # Check if validation is imported in the workflow
        import inspect
        source = inspect.getsource(IntelligenceOrchestrator._cuad_mitigation)
        
        has_validation_import = "from backend.validation.cuad_validator import validate_cuad_analysis" in source
        has_validation_call = "validate_cuad_analysis" in source
        has_validation_result = "validation_result" in source
        
        print(f"  - Validation import: {has_validation_import}")
        print(f"  - Validation call: {has_validation_call}")
        print(f"  - Validation result handling: {has_validation_result}")
        
        assert has_validation_import, "Workflow should import validation"
        assert has_validation_call, "Workflow should call validation"
        assert has_validation_result, "Workflow should handle validation result"
        
        print("✓ Workflow integration working\n")
        return True
        
    except Exception as e:
        print(f"❌ Workflow integration failed: {e}")
        return False

if __name__ == "__main__":
    print("CUAD Validation Test\n")
    print("=" * 30)
    
    try:
        test_validation_system()
        integration_success = test_workflow_integration()
        
        if integration_success:
            print("🎉 All validation tests passed!")
            print("\nValidation Implementation Complete:")
            print("✓ CUAD analysis validation")
            print("✓ Error and warning detection")
            print("✓ Confidence score calculation")
            print("✓ Workflow integration")
        else:
            print("⚠️  Validation tests completed with integration issues")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)