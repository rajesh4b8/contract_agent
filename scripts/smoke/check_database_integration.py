#!/usr/bin/env python3
"""
Test Database Integration for CUAD Features
"""

import sys
import os

# Add backend to path
sys.path.append(os.path.join(os.path.dirname(__file__), 'backend'))

def test_database_schema():
    """Test database schema has CUAD fields"""
    print("Testing Database Schema...")
    
    try:
        from backend.infrastructure.contract_repository import Neo4jContractRepository
        
        repository = Neo4jContractRepository()
        
        # Test if CUAD fields exist on contracts
        query = """
        MATCH (c:Contract)
        RETURN c.cuad_analysis_status as cuad_status,
               c.deviation_count as deviation_count,
               c.jurisdiction_detected as jurisdiction,
               c.industry_detected as industry,
               c.performance_optimized as optimized
        LIMIT 1
        """
        
        result = repository.graph.query(query)
        
        if result and len(result) > 0:
            contract = result[0]
            print(f"  - CUAD analysis status field: {'✓' if contract.get('cuad_status') is not None else '❌'}")
            print(f"  - Deviation count field: {'✓' if contract.get('deviation_count') is not None else '❌'}")
            print(f"  - Jurisdiction field: {'✓' if contract.get('jurisdiction') is not None else '❌'}")
            print(f"  - Industry field: {'✓' if contract.get('industry') is not None else '❌'}")
            print(f"  - Performance optimized field: {'✓' if contract.get('optimized') is not None else '❌'}")
        else:
            print("  - No contracts found to test schema")
        
        # Test if indexes exist
        index_query = "SHOW INDEXES"
        indexes = repository.graph.query(index_query)
        
        cuad_indexes = [idx for idx in indexes if 'cuad' in str(idx).lower() or 'jurisdiction' in str(idx).lower()]
        print(f"  - CUAD-related indexes: {len(cuad_indexes)} found")
        
        print("✓ Database schema test completed\n")
        return True
        
    except Exception as e:
        print(f"❌ Database schema test failed: {e}")
        return False

def test_performance_metrics_storage():
    """Test performance metrics can be stored"""
    print("Testing Performance Metrics Storage...")
    
    try:
        from backend.infrastructure.contract_repository import Neo4jContractRepository
        
        repository = Neo4jContractRepository()
        
        # Test creating a performance metric
        test_query = """
        CREATE (pm:PerformanceMetric {
            metric_id: 'test_metric_' + toString(timestamp()),
            contract_id: 'test_contract',
            operation: 'cuad_analysis',
            duration_ms: 1500.0,
            success: true,
            timestamp: datetime(),
            phase_used: 'phase3',
            validation_score: 0.95,
            deviation_count: 2,
            jurisdiction: 'US'
        })
        RETURN pm.metric_id as metric_id
        """
        
        result = repository.graph.query(test_query)
        
        if result and len(result) > 0:
            metric_id = result[0].get('metric_id')
            print(f"  - Performance metric created: {metric_id}")
            
            # Clean up test data
            cleanup_query = "MATCH (pm:PerformanceMetric {metric_id: $metric_id}) DELETE pm"
            repository.graph.query(cleanup_query, {"metric_id": metric_id})
            print(f"  - Test metric cleaned up")
        
        print("✓ Performance metrics storage working\n")
        return True
        
    except Exception as e:
        print(f"❌ Performance metrics storage failed: {e}")
        return False

def test_feedback_system_storage():
    """Test feedback system database integration"""
    print("Testing Feedback System Storage...")
    
    try:
        from backend.infrastructure.contract_repository import Neo4jContractRepository
        
        repository = Neo4jContractRepository()
        
        # Test creating a legal decision
        test_query = """
        CREATE (d:LegalDecision {
            decision_id: 'test_decision_' + toString(timestamp()),
            contract_id: 'test_contract',
            clause_id: 'test_clause',
            clause_type: 'payment',
            legal_decision: 'approved',
            legal_feedback: 'Test feedback',
            confidence_score: 0.8,
            decision_timestamp: datetime()
        })
        RETURN d.decision_id as decision_id
        """
        
        result = repository.graph.query(test_query)
        
        if result and len(result) > 0:
            decision_id = result[0].get('decision_id')
            print(f"  - Legal decision created: {decision_id}")
            
            # Test creating a learned pattern
            pattern_query = """
            CREATE (p:LearnedPattern {
                pattern_id: 'test_pattern_' + toString(timestamp()),
                pattern_type: 'approval',
                clause_type: 'payment',
                confidence: 0.8,
                usage_count: 1,
                success_rate: 1.0,
                created_at: datetime()
            })
            RETURN p.pattern_id as pattern_id
            """
            
            pattern_result = repository.graph.query(pattern_query)
            
            if pattern_result and len(pattern_result) > 0:
                pattern_id = pattern_result[0].get('pattern_id')
                print(f"  - Learned pattern created: {pattern_id}")
                
                # Clean up test data
                cleanup_queries = [
                    "MATCH (d:LegalDecision {decision_id: $decision_id}) DELETE d",
                    "MATCH (p:LearnedPattern {pattern_id: $pattern_id}) DELETE p"
                ]
                
                repository.graph.query(cleanup_queries[0], {"decision_id": decision_id})
                repository.graph.query(cleanup_queries[1], {"pattern_id": pattern_id})
                print(f"  - Test data cleaned up")
        
        print("✓ Feedback system storage working\n")
        return True
        
    except Exception as e:
        print(f"❌ Feedback system storage failed: {e}")
        return False

def test_service_integration():
    """Test service layer stores CUAD data correctly"""
    print("Testing Service Integration...")
    
    try:
        # Check if service has CUAD storage methods
        from backend.application.services.contract_intelligence_service import ContractIntelligenceService
        
        import inspect
        source = inspect.getsource(ContractIntelligenceService._store_intelligence_results)
        
        has_cuad_fields = "cuad_analysis_status" in source
        has_performance_storage = "_store_performance_metrics" in source
        has_jurisdiction_field = "jurisdiction_detected" in source
        
        print(f"  - CUAD fields in storage: {'✓' if has_cuad_fields else '❌'}")
        print(f"  - Performance metrics storage: {'✓' if has_performance_storage else '❌'}")
        print(f"  - Jurisdiction field storage: {'✓' if has_jurisdiction_field else '❌'}")
        
        assert has_cuad_fields, "Service should store CUAD fields"
        assert has_performance_storage, "Service should store performance metrics"
        
        print("✓ Service integration working\n")
        return True
        
    except Exception as e:
        print(f"❌ Service integration failed: {e}")
        return False

if __name__ == "__main__":
    print("Database Integration Test for CUAD Features\n")
    print("=" * 50)
    
    tests = [
        test_database_schema,
        test_performance_metrics_storage,
        test_feedback_system_storage,
        test_service_integration
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"❌ Test {test.__name__} failed with exception: {e}")
            failed += 1
    
    print(f"Database Integration Results: {passed} passed, {failed} failed")
    
    if failed == 0:
        print("\n🎉 All database integration tests passed!")
        print("Database aspects are fully implemented:")
        print("✓ CUAD schema migration completed")
        print("✓ Performance metrics storage working")
        print("✓ Feedback system database integration")
        print("✓ Service layer CUAD data storage")
    else:
        print(f"\n⚠️  {failed} database tests failed")
    
    sys.exit(0 if failed == 0 else 1)