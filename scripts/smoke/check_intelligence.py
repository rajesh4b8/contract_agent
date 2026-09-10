#!/usr/bin/env python3
import requests
import json
import time

def test_contract_intelligence():
    """Test the multi-agent contract intelligence system"""
    
    base_url = "http://localhost:8000"
    
    print("🧠 Testing Contract Intelligence Multi-Agent System")
    print("=" * 60)
    
    # Test 1: Check available models
    print("\n1. Testing available models...")
    try:
        response = requests.get(f"{base_url}/intelligence/models")
        if response.status_code == 200:
            models = response.json()
            print(f"✅ Available models: {models['available_models']}")
        else:
            print(f"❌ Models check failed: {response.status_code}")
    except Exception as e:
        print(f"❌ Models check error: {e}")
    
    # Test 2: Get dashboard summary
    print("\n2. Testing dashboard summary...")
    try:
        response = requests.get(f"{base_url}/intelligence/dashboard/summary")
        if response.status_code == 200:
            summary = response.json()
            print(f"✅ Dashboard summary: {summary}")
        else:
            print(f"❌ Dashboard failed: {response.status_code}")
    except Exception as e:
        print(f"❌ Dashboard error: {e}")
    
    # Test 3: Find a contract to analyze
    print("\n3. Finding contracts to analyze...")
    try:
        # Use existing contract search to find contracts
        search_response = requests.post(f"{base_url}/run/", json={
            "model": "gemini-2.5-flash",
            "prompt": "Find me any contract",
            "history": "[]"
        })
        
        if search_response.status_code == 200:
            print("✅ Contract search initiated")
            
            # For demo, we'll use a known contract ID pattern
            # In real scenario, you'd parse the search results
            test_contract_id = "salesforce_msa.pdf"  # Use the uploaded contract
            
            # Test 4: Analyze contract intelligence
            print(f"\n4. Analyzing contract intelligence for: {test_contract_id}")
            
            analysis_response = requests.post(
                f"{base_url}/intelligence/contracts/{test_contract_id}/analyze"
            )
            
            if analysis_response.status_code == 200:
                results = analysis_response.json()
                print("✅ Multi-agent analysis completed!")
                print(f"   Processing time: {results.get('processing_time', 'N/A')}s")
                print(f"   Model used: {results.get('model_used', 'N/A')}")
                
                # Display results summary
                if 'results' in results:
                    res = results['results']
                    print(f"\n📊 Analysis Results:")
                    print(f"   • Clauses extracted: {len(res.get('clauses', []))}")
                    print(f"   • Policy violations: {len(res.get('violations', []))}")
                    print(f"   • Risk score: {res.get('risk_assessment', {}).get('overall_risk_score', 'N/A')}/100")
                    print(f"   • Risk level: {res.get('risk_assessment', {}).get('risk_level', 'N/A')}")
                    print(f"   • Redlines generated: {len(res.get('redlines', []))}")
                    
                    # Show sample clause
                    if res.get('clauses'):
                        clause = res['clauses'][0]
                        print(f"\n📄 Sample Clause:")
                        print(f"   Type: {clause.get('clause_type', 'N/A')}")
                        print(f"   Risk: {clause.get('risk_level', 'N/A')}")
                        print(f"   Content: {clause.get('content', 'N/A')[:100]}...")
                    
                    # Show sample violation
                    if res.get('violations'):
                        violation = res['violations'][0]
                        print(f"\n⚠️  Sample Violation:")
                        print(f"   Type: {violation.get('clause_type', 'N/A')}")
                        print(f"   Severity: {violation.get('severity', 'N/A')}")
                        print(f"   Issue: {violation.get('issue', 'N/A')}")
                    
                    # Show sample redline
                    if res.get('redlines'):
                        redline = res['redlines'][0]
                        print(f"\n✏️  Sample Redline:")
                        print(f"   Priority: {redline.get('priority', 'N/A')}")
                        print(f"   Original: {redline.get('original_text', 'N/A')[:50]}...")
                        print(f"   Suggested: {redline.get('suggested_text', 'N/A')[:50]}...")
                
                # Test 5: Check analysis status
                print(f"\n5. Checking analysis status...")
                status_response = requests.get(
                    f"{base_url}/intelligence/contracts/{test_contract_id}/status"
                )
                
                if status_response.status_code == 200:
                    status = status_response.json()
                    print(f"✅ Status check successful:")
                    print(f"   Intelligence status: {status.get('intelligence_status', 'N/A')}")
                    print(f"   Risk score: {status.get('risk_score', 'N/A')}")
                    print(f"   Violations count: {status.get('violations_count', 'N/A')}")
                else:
                    print(f"❌ Status check failed: {status_response.status_code}")
                
            else:
                print(f"❌ Analysis failed: {analysis_response.status_code}")
                print(f"   Response: {analysis_response.text}")
        
        else:
            print(f"❌ Contract search failed: {search_response.status_code}")
    
    except Exception as e:
        print(f"❌ Analysis error: {e}")
    
    print("\n" + "=" * 60)
    print("🎯 Multi-Agent Contract Intelligence Test Complete!")
    print("\nThe system demonstrates:")
    print("• Clause Extraction Agent: Identifies and classifies contract clauses")
    print("• Policy Compliance Agent: Checks against company policies") 
    print("• Risk Assessment Agent: Calculates risk scores and prioritizes issues")
    print("• Redline Generation Agent: Suggests specific contract changes")
    print("\nAll agents coordinate through LangGraph workflows! 🚀")

if __name__ == "__main__":
    # Wait for containers to be ready
    print("Waiting for containers to start...")
    time.sleep(10)
    
    test_contract_intelligence()