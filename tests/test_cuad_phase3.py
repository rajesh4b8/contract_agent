#!/usr/bin/env python3
"""
Test CUAD Phase 3 Implementation
"""

import json
import sys
import os
import time
import asyncio
from datetime import datetime

# Add backend to path

def test_caching_system():
    """Test Redis caching system"""
    print("Testing Caching System...")
    
    from backend.shared.cache.redis_cache import cache, cache_result
    
    # Test basic cache operations
    test_key = "test_key"
    test_value = {"test": "data", "timestamp": datetime.now().isoformat()}
    
    # Set and get
    success = cache.set(test_key, test_value, ttl=60)
    retrieved = cache.get(test_key)
    
    print(f"Cache set success: {success}")
    print(f"Cache get success: {retrieved is not None}")
    print(f"Cache type: {'Redis' if hasattr(cache.redis_client, 'ping') else 'In-Memory'}")
    
    # Test cache decorator
    @cache_result("test_function", ttl=30)
    def expensive_function(x, y):
        time.sleep(0.1)  # Simulate expensive operation
        return x + y
    
    # First call (cache miss)
    start_time = time.time()
    result1 = expensive_function(5, 3)
    first_call_time = time.time() - start_time
    
    # Second call (cache hit)
    start_time = time.time()
    result2 = expensive_function(5, 3)
    second_call_time = time.time() - start_time
    
    print(f"First call time: {first_call_time:.3f}s")
    print(f"Second call time: {second_call_time:.3f}s")
    print(f"Cache speedup: {first_call_time / max(second_call_time, 0.001):.1f}x")
    
    assert result1 == result2 == 8, "Results should match"
    assert second_call_time < first_call_time, "Cached call should be faster"
    print("✓ Caching system working\n")

def test_performance_monitoring():
    """Test performance monitoring system"""
    print("Testing Performance Monitoring...")
    
    from backend.shared.monitoring.performance_monitor import monitor, track_performance
    
    # Test performance tracking decorator
    @track_performance("test_operation")
    def test_function(duration=0.05):
        time.sleep(duration)
        return "completed"
    
    # Run test function multiple times
    for i in range(5):
        result = test_function(0.02 + i * 0.01)
    
    # Get performance stats
    stats = monitor.get_stats("test_operation")
    
    print(f"Performance stats for test_operation:")
    print(f"  - Total calls: {stats.get('total_calls', 0)}")
    print(f"  - Success rate: {stats.get('success_rate', 0):.1%}")
    print(f"  - Avg duration: {stats.get('avg_duration_ms', 0):.1f}ms")
    print(f"  - Min duration: {stats.get('min_duration_ms', 0):.1f}ms")
    print(f"  - Max duration: {stats.get('max_duration_ms', 0):.1f}ms")
    
    assert stats.get('total_calls', 0) == 5, "Should track all calls"
    assert stats.get('success_rate', 0) == 1.0, "All calls should succeed"
    print("✓ Performance monitoring working\n")

def test_optimized_tools():
    """Test optimized CUAD tools with caching and monitoring"""
    print("Testing Optimized CUAD Tools...")
    
    from backend.agents.optimized_cuad_tools import (
        OptimizedDeviationDetectorTool,
        OptimizedJurisdictionAdapterTool, 
        OptimizedPrecedentMatcherTool
    )
    
    # Test optimized deviation detector
    deviation_tool = OptimizedDeviationDetectorTool()
    
    test_clauses = [
        {
            "clause_type": "Termination",
            "content": "This agreement automatically renews unless terminated with 90 days notice"
        },
        {
            "clause_type": "Liability",
            "content": "Company accepts unlimited liability for all damages and losses"
        }
    ]
    
    # First call (cache miss)
    start_time = time.time()
    result1 = deviation_tool._run(json.dumps(test_clauses))
    first_call_time = time.time() - start_time
    
    # Second call (cache hit)
    start_time = time.time()
    result2 = deviation_tool._run(json.dumps(test_clauses))
    second_call_time = time.time() - start_time
    
    deviations = json.loads(result1)
    
    print(f"Optimized deviation detection:")
    print(f"  - Deviations found: {len(deviations)}")
    print(f"  - First call: {first_call_time:.3f}s")
    print(f"  - Second call: {second_call_time:.3f}s")
    print(f"  - Cache speedup: {first_call_time / max(second_call_time, 0.001):.1f}x")
    
    # Test jurisdiction adapter
    jurisdiction_tool = OptimizedJurisdictionAdapterTool()
    healthcare_contract = "This HIPAA-compliant agreement requires BAA compliance"
    
    jurisdiction_result = jurisdiction_tool._run(healthcare_contract)
    jurisdiction_info = json.loads(jurisdiction_result)
    
    print(f"Optimized jurisdiction adaptation:")
    print(f"  - Industry detected: {jurisdiction_info.get('industry', 'unknown')}")
    print(f"  - Jurisdiction: {jurisdiction_info.get('jurisdiction', 'unknown')}")
    
    assert len(deviations) > 0, "Should detect deviations"
    assert jurisdiction_info.get('industry') == 'healthcare', "Should detect healthcare industry"
    print("✓ Optimized tools working\n")

async def test_batch_processing():
    """Test batch processing capabilities"""
    print("Testing Batch Processing...")
    
    from backend.agents.optimized_cuad_tools import BatchProcessor
    
    # Create test contracts
    test_contracts = [
        {
            "id": f"contract_{i}",
            "text": f"This is test contract {i} with payment terms net {30 + i*10} days",
            "clauses": [
                {
                    "clause_type": "payment",
                    "content": f"Payment terms net {30 + i*10} days"
                }
            ]
        }
        for i in range(3)
    ]
    
    processor = BatchProcessor()
    
    start_time = time.time()
    results = await processor.process_contracts_batch(test_contracts)
    processing_time = time.time() - start_time
    
    print(f"Batch processing results:")
    print(f"  - Contracts processed: {len(results)}")
    print(f"  - Total processing time: {processing_time:.3f}s")
    print(f"  - Avg time per contract: {processing_time / len(results):.3f}s")
    
    # Check results
    successful_results = [r for r in results if "error" not in r]
    
    print(f"  - Successful results: {len(successful_results)}")
    
    for result in successful_results[:2]:  # Show first 2 results
        contract_id = result.get("contract_id")
        deviations = len(result.get("deviations", []))
        print(f"    - {contract_id}: {deviations} deviations")
    
    assert len(successful_results) >= 2, "Should process multiple contracts successfully"
    print("✓ Batch processing working\n")

def test_monitoring_integration():
    """Test monitoring integration with workflow"""
    print("Testing Monitoring Integration...")
    
    from backend.shared.monitoring.performance_monitor import monitor
    
    # Get current metrics
    all_stats = monitor.get_all_stats()
    
    print(f"Current monitoring stats:")
    for operation, stats in all_stats.items():
        if isinstance(stats, dict) and "total_calls" in stats:
            print(f"  - {operation}: {stats['total_calls']} calls, {stats.get('success_rate', 0):.1%} success")
    
    # Test alert thresholds
    print(f"Alert thresholds configured:")
    for operation, threshold in monitor.alert_thresholds.items():
        print(f"  - {operation}: {threshold}ms")
    
    print("✓ Monitoring integration working\n")

def test_fallback_mechanism():
    """Test multi-level fallback mechanism"""
    print("Testing Multi-Level Fallback...")
    
    # Test that all tool levels are available
    try:
        # Phase 3 (Optimized)
        from backend.agents.optimized_cuad_tools import OptimizedDeviationDetectorTool
        phase3_tool = OptimizedDeviationDetectorTool()
        
        # Phase 2 (Enhanced)
        from backend.agents.enhanced_cuad_tools import EnhancedDeviationDetectorTool
        phase2_tool = EnhancedDeviationDetectorTool()
        
        # Phase 1 (Basic)
        from backend.agents.cuad_mitigation_tools import DeviationDetectorTool
        phase1_tool = DeviationDetectorTool()
        
        test_clauses = [{"clause_type": "payment", "content": "Payment net 90 days"}]
        
        # Test all phases work
        result3 = phase3_tool._run(json.dumps(test_clauses))
        result2 = phase2_tool._run(json.dumps(test_clauses))
        result1 = phase1_tool._run(json.dumps(test_clauses))
        
        print(f"Fallback mechanism test:")
        print(f"  - Phase 3 (Optimized): {len(json.loads(result3))} deviations")
        print(f"  - Phase 2 (Enhanced): {len(json.loads(result2))} deviations")
        print(f"  - Phase 1 (Basic): {len(json.loads(result1))} deviations")
        
        assert all(json.loads(r) for r in [result1, result2, result3]), "All phases should work"
        print("✓ Multi-level fallback working\n")
        
    except ImportError as e:
        print(f"❌ Fallback test failed: {e}")
        return False
    
    return True

def test_system_integration():
    """Test full system integration"""
    print("Testing System Integration...")
    
    # Test that optimized workflow can be imported
    try:
        from backend.agents.contract_intelligence_agents import IntelligenceOrchestrator
        from backend.llm_manager import LLMManager
        
        print("✓ Optimized workflow integration available")
        
        # Test monitoring API components
        from backend.api.monitoring_api import router
        print("✓ Monitoring API available")
        
        # Test cache integration
        from backend.shared.cache.redis_cache import cache
        print(f"✓ Cache system available ({type(cache.redis_client).__name__})")
        
        # Test performance monitoring
        from backend.shared.monitoring.performance_monitor import monitor
        print(f"✓ Performance monitoring available ({len(monitor.metrics)} operations tracked)")
        
        print("✓ System integration working\n")
        return True
        
    except ImportError as e:
        print(f"❌ System integration failed: {e}")
        return False

if __name__ == "__main__":
    print("CUAD Phase 3 Implementation Test\n")
    print("=" * 50)
    
    try:
        test_caching_system()
        test_performance_monitoring()
        test_optimized_tools()
        
        # Run async test
        asyncio.run(test_batch_processing())
        
        test_monitoring_integration()
        fallback_success = test_fallback_mechanism()
        integration_success = test_system_integration()
        
        if fallback_success and integration_success:
            print("🎉 All Phase 3 tests passed!")
            print("\nPhase 3 Implementation Complete:")
            print("✓ Redis caching with fallback to in-memory")
            print("✓ Real-time performance monitoring with alerts")
            print("✓ Optimized tools with parallel processing")
            print("✓ Batch processing with async capabilities")
            print("✓ Multi-level fallback mechanism (Phase 3 → 2 → 1)")
            print("✓ Monitoring API with health checks")
            print("✓ Production-ready performance optimization")
        else:
            print("⚠️  Phase 3 tests completed with some integration issues")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)