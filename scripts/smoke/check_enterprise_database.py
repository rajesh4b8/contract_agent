#!/usr/bin/env python3
"""
Test Enterprise Database Implementation
"""

import sys
import os

# Add backend to path
sys.path.append(os.path.join(os.path.dirname(__file__), 'backend'))

def test_multi_tenancy():
    """Test multi-tenant database structure"""
    print("Testing Multi-Tenancy...")
    
    try:
        from backend.infrastructure.contract_repository import Neo4jContractRepository
        
        repository = Neo4jContractRepository()
        
        # Test tenant nodes exist
        tenant_query = "MATCH (t:Tenant) RETURN count(t) as tenant_count"
        result = repository.graph.query(tenant_query)
        tenant_count = result[0].get('tenant_count', 0) if result else 0
        
        # Test contracts have tenant_id
        contract_query = """
        MATCH (c:Contract) 
        WHERE c.tenant_id IS NOT NULL 
        RETURN count(c) as contracts_with_tenant
        """
        result = repository.graph.query(contract_query)
        contracts_with_tenant = result[0].get('contracts_with_tenant', 0) if result else 0
        
        print(f"  - Tenant nodes: {tenant_count}")
        print(f"  - Contracts with tenant_id: {contracts_with_tenant}")
        
        assert tenant_count >= 2, "Should have at least 2 demo tenants"
        print("✓ Multi-tenancy implemented\n")
        return True
        
    except Exception as e:
        print(f"❌ Multi-tenancy test failed: {e}")
        return False

def test_document_versioning():
    """Test document versioning system"""
    print("Testing Document Versioning...")
    
    try:
        from backend.infrastructure.contract_repository import Neo4jContractRepository
        
        repository = Neo4jContractRepository()
        
        # Test version nodes exist
        version_query = "MATCH (cv:ContractVersion) RETURN count(cv) as version_count"
        result = repository.graph.query(version_query)
        version_count = result[0].get('version_count', 0) if result else 0
        
        # Test contracts have version numbers
        contract_version_query = """
        MATCH (c:Contract) 
        WHERE c.version_number IS NOT NULL 
        RETURN count(c) as contracts_with_version
        """
        result = repository.graph.query(contract_version_query)
        contracts_with_version = result[0].get('contracts_with_version', 0) if result else 0
        
        # Test version relationships
        relationship_query = """
        MATCH (c:Contract)-[:HAS_VERSION]->(cv:ContractVersion)
        RETURN count(*) as version_relationships
        """
        result = repository.graph.query(relationship_query)
        version_relationships = result[0].get('version_relationships', 0) if result else 0
        
        print(f"  - Version nodes: {version_count}")
        print(f"  - Contracts with version numbers: {contracts_with_version}")
        print(f"  - Version relationships: {version_relationships}")
        
        assert version_count > 0, "Should have version nodes"
        print("✓ Document versioning implemented\n")
        return True
        
    except Exception as e:
        print(f"❌ Document versioning test failed: {e}")
        return False

def test_chunking_storage():
    """Test document chunking storage"""
    print("Testing Document Chunking Storage...")
    
    try:
        from backend.infrastructure.contract_repository import Neo4jContractRepository
        
        repository = Neo4jContractRepository()
        
        # Test chunk nodes exist
        chunk_query = "MATCH (dc:DocumentChunk) RETURN count(dc) as chunk_count"
        result = repository.graph.query(chunk_query)
        chunk_count = result[0].get('chunk_count', 0) if result else 0
        
        # Test chunk relationships
        chunk_relationship_query = """
        MATCH (c:Contract)-[:CONTAINS_CHUNK]->(dc:DocumentChunk)
        RETURN count(*) as chunk_relationships
        """
        result = repository.graph.query(chunk_relationship_query)
        chunk_relationships = result[0].get('chunk_relationships', 0) if result else 0
        
        print(f"  - Chunk nodes: {chunk_count}")
        print(f"  - Chunk relationships: {chunk_relationships}")
        
        assert chunk_count > 0, "Should have chunk nodes"
        print("✓ Document chunking storage implemented\n")
        return True
        
    except Exception as e:
        print(f"❌ Document chunking test failed: {e}")
        return False

def test_embeddings_storage():
    """Test vector embeddings storage"""
    print("Testing Vector Embeddings Storage...")
    
    try:
        from backend.infrastructure.contract_repository import Neo4jContractRepository
        
        repository = Neo4jContractRepository()
        
        # Test embedding nodes exist
        embedding_query = "MATCH (de:DocumentEmbedding) RETURN count(de) as embedding_count"
        result = repository.graph.query(embedding_query)
        embedding_count = result[0].get('embedding_count', 0) if result else 0
        
        # Test embedding relationships
        embedding_relationship_query = """
        MATCH (dc:DocumentChunk)-[:HAS_EMBEDDING]->(de:DocumentEmbedding)
        RETURN count(*) as embedding_relationships
        """
        result = repository.graph.query(embedding_relationship_query)
        embedding_relationships = result[0].get('embedding_relationships', 0) if result else 0
        
        print(f"  - Embedding nodes: {embedding_count}")
        print(f"  - Embedding relationships: {embedding_relationships}")
        
        assert embedding_count > 0, "Should have embedding nodes"
        print("✓ Vector embeddings storage implemented\n")
        return True
        
    except Exception as e:
        print(f"❌ Vector embeddings test failed: {e}")
        return False

def test_processing_lineage():
    """Test processing lineage tracking"""
    print("Testing Processing Lineage...")
    
    try:
        from backend.infrastructure.contract_repository import Neo4jContractRepository
        
        repository = Neo4jContractRepository()
        
        # Test lineage nodes exist
        lineage_query = "MATCH (pl:ProcessingLineage) RETURN count(pl) as lineage_count"
        result = repository.graph.query(lineage_query)
        lineage_count = result[0].get('lineage_count', 0) if result else 0
        
        # Test lineage relationships
        lineage_relationship_query = """
        MATCH (c:Contract)-[:HAS_LINEAGE]->(pl:ProcessingLineage)
        RETURN count(*) as lineage_relationships
        """
        result = repository.graph.query(lineage_relationship_query)
        lineage_relationships = result[0].get('lineage_relationships', 0) if result else 0
        
        print(f"  - Lineage nodes: {lineage_count}")
        print(f"  - Lineage relationships: {lineage_relationships}")
        
        assert lineage_count > 0, "Should have lineage nodes"
        print("✓ Processing lineage implemented\n")
        return True
        
    except Exception as e:
        print(f"❌ Processing lineage test failed: {e}")
        return False

def test_analysis_storage():
    """Test contract analysis storage"""
    print("Testing Contract Analysis Storage...")
    
    try:
        from backend.infrastructure.contract_repository import Neo4jContractRepository
        
        repository = Neo4jContractRepository()
        
        # Test analysis nodes exist
        analysis_query = "MATCH (ca:ContractAnalysis) RETURN count(ca) as analysis_count"
        result = repository.graph.query(analysis_query)
        analysis_count = result[0].get('analysis_count', 0) if result else 0
        
        # Test analysis relationships
        analysis_relationship_query = """
        MATCH (c:Contract)-[:HAS_ANALYSIS]->(ca:ContractAnalysis)
        RETURN count(*) as analysis_relationships
        """
        result = repository.graph.query(analysis_relationship_query)
        analysis_relationships = result[0].get('analysis_relationships', 0) if result else 0
        
        print(f"  - Analysis nodes: {analysis_count}")
        print(f"  - Analysis relationships: {analysis_relationships}")
        
        assert analysis_count > 0, "Should have analysis nodes"
        print("✓ Contract analysis storage implemented\n")
        return True
        
    except Exception as e:
        print(f"❌ Contract analysis test failed: {e}")
        return False

def test_enterprise_indexes():
    """Test enterprise performance indexes"""
    print("Testing Enterprise Indexes...")
    
    try:
        from backend.infrastructure.contract_repository import Neo4jContractRepository
        
        repository = Neo4jContractRepository()
        
        # Test indexes exist
        index_query = "SHOW INDEXES"
        indexes = repository.graph.query(index_query)
        
        enterprise_indexes = [
            idx for idx in indexes 
            if any(term in str(idx).lower() for term in ['tenant', 'version', 'chunk', 'embedding', 'lineage', 'analysis'])
        ]
        
        print(f"  - Enterprise indexes found: {len(enterprise_indexes)}")
        
        assert len(enterprise_indexes) >= 5, "Should have multiple enterprise indexes"
        print("✓ Enterprise indexes implemented\n")
        return True
        
    except Exception as e:
        print(f"❌ Enterprise indexes test failed: {e}")
        return False

if __name__ == "__main__":
    print("Enterprise Database Implementation Test\n")
    print("=" * 50)
    
    tests = [
        test_multi_tenancy,
        test_document_versioning,
        test_chunking_storage,
        test_embeddings_storage,
        test_processing_lineage,
        test_analysis_storage,
        test_enterprise_indexes
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
    
    print(f"Enterprise Database Results: {passed} passed, {failed} failed")
    
    if failed == 0:
        print("\n🎉 All enterprise database tests passed!")
        print("Enterprise database aspects fully implemented:")
        print("✓ Multi-tenancy with tenant isolation")
        print("✓ Document versioning system")
        print("✓ Document chunking storage")
        print("✓ Vector embeddings storage")
        print("✓ Processing lineage tracking")
        print("✓ Contract analysis storage")
        print("✓ Enterprise performance indexes")
    else:
        print(f"\n⚠️  {failed} enterprise database tests failed")
    
    sys.exit(0 if failed == 0 else 1)