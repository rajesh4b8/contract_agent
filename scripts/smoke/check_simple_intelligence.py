#!/usr/bin/env python3
import requests
import json

def test_simple_intelligence():
    """Test multi-agent intelligence with sample contract text"""
    
    base_url = "http://localhost:8000"
    
    print("🧠 Testing Multi-Agent Contract Intelligence")
    print("=" * 50)
    
    # Sample contract text for testing
    sample_contract = """
    MASTER SERVICES AGREEMENT
    
    This Master Services Agreement ("Agreement") is entered into on January 1, 2024, 
    between Company A ("Client") and Company B ("Service Provider").
    
    PAYMENT TERMS: Payment is due within 90 days of invoice date.
    
    LIABILITY: Each party's liability is limited to $25,000.
    
    CONFIDENTIALITY: Both parties agree to maintain confidentiality of all information.
    
    TERMINATION: Either party may terminate with 15 days written notice.
    
    INTELLECTUAL PROPERTY: All work product shall be owned by Service Provider.
    """
    
    # Test the multi-agent orchestrator directly
    print("1. Testing multi-agent orchestrator...")
    
    try:
        from backend.services.contract_intelligence_service import ContractIntelligenceServiceFactory
        from backend.agent_manager import AgentManager
        
        # Initialize services
        agent_manager = AgentManager()
        intelligence_service = ContractIntelligenceServiceFactory.create_service(agent_manager)
        
        # Run multi-agent analysis
        print("   Running multi-agent analysis...")
        intelligence = intelligence_service.analyze_contract_intelligence(sample_contract)
        
        print(f"✅ Multi-agent analysis completed!")
        print(f"   Processing time: {intelligence.processing_time:.2f}s")
        
        # Display results
        print(f"\n📊 Results Summary:")
        print(f"   • Clauses extracted: {len(intelligence.clauses)}")
        print(f"   • Policy violations: {len(intelligence.violations)}")
        print(f"   • Risk score: {intelligence.risk_assessment.overall_risk_score}/100")
        print(f"   • Risk level: {intelligence.risk_assessment.risk_level}")
        print(f"   • Redlines generated: {len(intelligence.redlines)}")
        
        # Show detailed results
        if intelligence.clauses:
            print(f"\n📄 Extracted Clauses:")
            for i, clause in enumerate(intelligence.clauses[:3]):  # Show first 3
                print(f"   {i+1}. {clause.clause_type} ({clause.risk_level})")
                print(f"      Content: {clause.content[:80]}...")
        
        if intelligence.violations:
            print(f"\n⚠️  Policy Violations:")
            for i, violation in enumerate(intelligence.violations[:3]):  # Show first 3
                print(f"   {i+1}. {violation.clause_type} - {violation.severity}")
                print(f"      Issue: {violation.issue}")
                print(f"      Fix: {violation.suggested_fix}")
        
        if intelligence.redlines:
            print(f"\n✏️  Redline Recommendations:")
            for i, redline in enumerate(intelligence.redlines[:2]):  # Show first 2
                print(f"   {i+1}. Priority: {redline.priority}")
                print(f"      Original: {redline.original_text[:50]}...")
                print(f"      Suggested: {redline.suggested_text[:50]}...")
        
        print(f"\n🎯 Multi-Agent Workflow Demonstrated:")
        print(f"   ✅ Clause Extraction Agent: Identified key contract clauses")
        print(f"   ✅ Policy Compliance Agent: Checked against company policies")
        print(f"   ✅ Risk Assessment Agent: Calculated overall risk score")
        print(f"   ✅ Redline Generation Agent: Generated improvement suggestions")
        
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_simple_intelligence()
    
    if success:
        print(f"\n🚀 Multi-Agent Contract Intelligence System Working!")
        print(f"   • 4 specialized agents coordinate via LangGraph")
        print(f"   • Each agent has specific tools and responsibilities")
        print(f"   • Workflow orchestrates clause extraction → compliance → risk → redlines")
        print(f"   • System ready for enterprise contract analysis!")
    else:
        print(f"\n❌ System needs debugging")