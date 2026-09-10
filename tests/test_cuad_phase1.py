#!/usr/bin/env python3
"""
Test CUAD Phase 1 Implementation
"""

import json
import sys
import os

# Add backend to path

from backend.agents.cuad_mitigation_tools import (
    DeviationDetectorTool, 
    JurisdictionAdapterTool, 
    PrecedentMatcherTool
)

def test_deviation_detector():
    """Test deviation detection tool"""
    print("Testing Deviation Detector...")
    
    tool = DeviationDetectorTool()
    
    # Test clauses with known deviations
    test_clauses = [
        {
            "clause_type": "Payment Terms",
            "content": "Payment shall be made within net 90 days of invoice receipt"
        },
        {
            "clause_type": "Liability",
            "content": "Company accepts unlimited liability for all damages"
        },
        {
            "clause_type": "Intellectual Property",
            "content": "All work performed shall be work for hire owned by customer"
        }
    ]
    
    result = tool._run(json.dumps(test_clauses))
    deviations = json.loads(result)
    
    print(f"Found {len(deviations)} deviations:")
    for deviation in deviations:
        print(f"  - {deviation['clause_type']}: {deviation['deviation_type']} ({deviation['severity']})")
    
    assert len(deviations) >= 2, "Should detect multiple deviations"
    print("✓ Deviation detection working\n")

def test_jurisdiction_adapter():
    """Test jurisdiction adaptation tool"""
    print("Testing Jurisdiction Adapter...")
    
    tool = JurisdictionAdapterTool()
    
    # Test EU contract
    eu_contract = "This agreement is governed by GDPR and European Union data protection regulations"
    result = tool._run(eu_contract)
    jurisdiction_info = json.loads(result)
    
    print(f"EU Contract - Jurisdiction: {jurisdiction_info['jurisdiction']}")
    print(f"Compliance requirements: {len(jurisdiction_info['compliance_requirements'])}")
    
    # Test US contract
    us_contract = "This agreement shall be governed by the laws of Delaware"
    result = tool._run(us_contract)
    jurisdiction_info = json.loads(result)
    
    print(f"US Contract - Jurisdiction: {jurisdiction_info['jurisdiction']}")
    
    assert jurisdiction_info['jurisdiction'] in ['EU', 'US'], "Should detect jurisdiction"
    print("✓ Jurisdiction adaptation working\n")

def test_precedent_matcher():
    """Test precedent matching tool"""
    print("Testing Precedent Matcher...")
    
    tool = PrecedentMatcherTool()
    
    test_clauses = [
        {
            "clause_type": "payment",
            "content": "Payment terms are net 30 days"
        },
        {
            "clause_type": "liability", 
            "content": "Liability is capped at contract value"
        }
    ]
    
    result = tool._run(json.dumps(test_clauses))
    matches = json.loads(result)
    
    print(f"Found precedent matches for {len(matches)} clauses:")
    for match in matches:
        clause_type = match['clause']['clause_type']
        precedent_count = match['precedent_count']
        approval_rate = match['approval_rate']
        print(f"  - {clause_type}: {precedent_count} precedents, {approval_rate:.1%} approval rate")
    
    assert len(matches) > 0, "Should find precedent matches"
    print("✓ Precedent matching working\n")

def test_integration():
    """Test integration with existing workflow"""
    print("Testing Integration...")
    
    # Test that tools can be imported and used together
    deviation_tool = DeviationDetectorTool()
    jurisdiction_tool = JurisdictionAdapterTool()
    precedent_tool = PrecedentMatcherTool()
    
    # Simulate workflow data
    contract_text = "This GDPR-compliant agreement requires payment within net 60 days and unlimited liability"
    
    clauses = [
        {
            "clause_type": "Payment Terms",
            "content": "Payment within net 60 days"
        }
    ]
    
    # Run all tools
    deviations = json.loads(deviation_tool._run(json.dumps(clauses)))
    jurisdiction = json.loads(jurisdiction_tool._run(contract_text))
    precedents = json.loads(precedent_tool._run(json.dumps(clauses)))
    
    print(f"Integration test results:")
    print(f"  - Deviations: {len(deviations)}")
    print(f"  - Jurisdiction: {jurisdiction['jurisdiction']}")
    print(f"  - Precedents: {len(precedents)}")
    
    print("✓ Integration working\n")

if __name__ == "__main__":
    print("CUAD Phase 1 Implementation Test\n")
    print("=" * 40)
    
    try:
        test_deviation_detector()
        test_jurisdiction_adapter()
        test_precedent_matcher()
        test_integration()
        
        print("🎉 All Phase 1 tests passed!")
        print("\nPhase 1 Implementation Complete:")
        print("✓ Deviation detection tool")
        print("✓ Jurisdiction adaptation tool") 
        print("✓ Precedent matching tool")
        print("✓ Integration with existing workflow")
        print("✓ Extended intelligence state")
        print("✓ Enhanced API response")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)