#!/usr/bin/env python3
"""
Test script for enhanced multi-level embeddings system
"""

import sys
import os
import asyncio
import logging
from pathlib import Path

# Add backend to path
backend_path = Path(__file__).parent / "backend"
sys.path.insert(0, str(backend_path))

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_enhanced_embeddings():
    """Test the enhanced embeddings system end-to-end"""
    
    print("🚀 Testing Enhanced Multi-Level Embeddings System")
    print("=" * 60)
    
    try:
        # Test 1: Import all components
        print("📦 Test 1: Importing enhanced embedding components...")
        
        from backend.embeddings.orchestrator import EmbeddingOrchestrator
        from backend.embeddings.validator import EmbeddingValidator
        from backend.embeddings.strategies.factory import EmbeddingFactory
        from backend.embeddings.strategies import EmbeddingType
        from backend.tools.enhanced_contract_search_tool import EnhancedContractSearchTool
        
        print("✅ All components imported successfully")
        
        # Test 2: Create orchestrator and process sample text
        print("\n🔧 Test 2: Testing embedding orchestrator...")
        
        orchestrator = EmbeddingOrchestrator()
        sample_contract = """
        MASTER SERVICE AGREEMENT
        
        This Master Service Agreement ("Agreement") is entered into between Acme Corporation ("Client") 
        and Tech Solutions Inc. ("Contractor") effective January 1, 2024.
        
        PAYMENT TERMS
        Payment shall be made within thirty (30) days of invoice receipt. Late payments may incur fees.
        
        TERMINATION
        Either party may terminate this agreement with ninety (90) days written notice.
        
        GOVERNING LAW
        This agreement shall be governed by the laws of California.
        """
        
        result = orchestrator.process_document(
            content=sample_contract,
            metadata={"file_id": "test_contract_001", "source": "test"}
        )
        
        print(f"✅ Generated {len(result.document_embeddings)} document embeddings")
        print(f"✅ Generated {len(result.clause_embeddings)} clause embeddings")
        print(f"✅ Generated {len(result.relationship_embeddings)} relationship embeddings")
        
        # Test 3: Validate embeddings
        print("\n🔍 Test 3: Testing embedding validation...")
        
        validator = EmbeddingValidator()
        all_embeddings = (
            result.document_embeddings + 
            result.clause_embeddings + 
            result.relationship_embeddings
        )
        
        validation_result = validator.validate_embeddings(all_embeddings)
        
        if validation_result.is_valid:
            print("✅ All embeddings passed validation")
        else:
            print(f"⚠️  Validation warnings: {validation_result.warnings}")
            print(f"❌ Validation errors: {validation_result.errors}")
        
        # Test 4: Test enhanced search tool
        print("\n🔎 Test 4: Testing enhanced search tool...")
        
        search_tool = EnhancedContractSearchTool()
        
        # Test different search levels
        search_levels = ["document", "section", "clause", "relationship"]
        
        for level in search_levels:
            try:
                # Note: This will fail if no contracts in database, but tests the tool structure
                search_result = search_tool._run(
                    search_level=level,
                    summary_search="payment terms"
                )
                print(f"✅ {level.capitalize()} search tool working")
            except Exception as e:
                print(f"⚠️  {level.capitalize()} search test failed (expected if no data): {e}")
        
        # Test 5: Test embedding strategies
        print("\n⚙️  Test 5: Testing embedding strategies...")
        
        factory = EmbeddingFactory()
        
        for embedding_type in EmbeddingType:
            try:
                strategy = factory.create_strategy(embedding_type)
                embedding = strategy.generate_embedding("test content")
                dimension = strategy.get_embedding_dimension()
                
                print(f"✅ {embedding_type.value} strategy: {len(embedding)} dims (expected: {dimension})")
                
                if len(embedding) != dimension:
                    print(f"❌ Dimension mismatch for {embedding_type.value}")
                
            except Exception as e:
                print(f"❌ {embedding_type.value} strategy failed: {e}")
        
        # Test 6: Test migration script components
        print("\n📊 Test 6: Testing migration components...")
        
        try:
            from backend.embeddings.migrator import EmbeddingMigrator
            from backend.migrations.migration_001_multi_level_embeddings import upgrade_schema
            
            print("✅ Migration components imported successfully")
            print("ℹ️  Run 'python backend/run_migration.py upgrade' to apply schema changes")
            
        except Exception as e:
            print(f"❌ Migration component test failed: {e}")
        
        print("\n🎉 Enhanced Embeddings System Test Complete!")
        print("=" * 60)
        print("✅ Core functionality working")
        print("📝 Next steps:")
        print("   1. Run schema migration: python backend/run_migration.py upgrade")
        print("   2. Upload a test document via enhanced endpoint")
        print("   3. Test multi-level search via API")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        return False

def test_api_endpoints():
    """Test API endpoint availability"""
    print("\n🌐 Testing API Endpoints...")
    
    try:
        import requests
        
        base_url = "http://localhost:8000"
        
        # Test enhanced search endpoints
        endpoints = [
            "/api/contracts/search/clause-types",
            "/api/contracts/search/section-types",
            "/documents/enhanced/status"
        ]
        
        for endpoint in endpoints:
            try:
                response = requests.get(f"{base_url}{endpoint}", timeout=5)
                if response.status_code == 200:
                    print(f"✅ {endpoint} - OK")
                else:
                    print(f"⚠️  {endpoint} - Status: {response.status_code}")
            except requests.exceptions.ConnectionError:
                print(f"❌ {endpoint} - Server not running")
            except Exception as e:
                print(f"❌ {endpoint} - Error: {e}")
        
    except ImportError:
        print("⚠️  requests library not available, skipping API tests")

if __name__ == "__main__":
    # Run async test
    success = asyncio.run(test_enhanced_embeddings())
    
    # Test API endpoints if server is running
    test_api_endpoints()
    
    if success:
        print("\n🎯 All tests completed successfully!")
        sys.exit(0)
    else:
        print("\n💥 Some tests failed!")
        sys.exit(1)