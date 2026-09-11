"""
Clause and CUAD Schema Migration
Creates Clause and ClauseType nodes with relationships
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from backend.infrastructure.contract_repository import Neo4jContractRepository
import logging

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

class ClauseSchemaMigration:
    """Migration for clause and CUAD classification schema"""
    
    def __init__(self):
        self.repository = Neo4jContractRepository()
    
    def migrate(self, include_sample_data: bool = False):
        """Apply clause schema migration.

        Sample data is opt-in. `_create_sample_clauses` uses CREATE, so running
        it against a database that already has sections injects duplicate
        fabricated clauses into real contracts on every upgrade.
        """
        try:
            self.migrate_schema_only()
            if include_sample_data:
                self._create_sample_clauses()
            logger.info("Clause schema migration completed")
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            raise

    def migrate_schema_only(self):
        """Indexes and (:ClauseType) nodes — no data. Safe to re-run."""
        self._create_clause_indexes()
        self._create_cuad_type_nodes()
    
    def _create_clause_indexes(self):
        """Create indexes for Clause nodes"""
        queries = [
            "CREATE INDEX clause_order IF NOT EXISTS FOR (cl:Clause) ON (cl.order)",
            "CREATE INDEX clause_section IF NOT EXISTS FOR (cl:Clause) ON (cl.section_id)",
            "CREATE INDEX clause_type IF NOT EXISTS FOR (cl:Clause) ON (cl.clause_type)",
            "CREATE INDEX clause_tenant IF NOT EXISTS FOR (cl:Clause) ON (cl.tenant_id)",
            "CREATE INDEX cuad_type IF NOT EXISTS FOR (ct:ClauseType) ON (ct.cuad_type)"
        ]
        
        for query in queries:
            try:
                self.repository.graph.query(query)
                print(f"✅ Created index: {query.split('FOR')[0].split('INDEX')[1].strip()}")
            except Exception as e:
                print(f"⚠️ Index may already exist: {e}")
    
    def _create_cuad_type_nodes(self):
        """Create CUAD clause type nodes"""
        cuad_types = [
            "Governing Law", "Termination For Convenience", "Non-Compete",
            "Exclusivity", "Minimum Commitment", "Cap On Liability", "Warranty Duration"
        ]
        
        for cuad_type in cuad_types:
            query = """
            MERGE (ct:ClauseType {cuad_type: $cuad_type})
            SET ct.description = $description,
                ct.category = $category,
                ct.created_at = datetime()
            """
            
            try:
                self.repository.graph.query(query, {
                    "cuad_type": cuad_type,
                    "description": f"CUAD clause type: {cuad_type}",
                    "category": "legal"
                })
                print(f"✅ Created CUAD type: {cuad_type}")
            except Exception as e:
                print(f"⚠️ CUAD type creation failed: {e}")
    
    def _create_sample_clauses(self):
        """Create sample clauses for testing"""
        # Get a sample section
        section_query = "MATCH (s:Section) RETURN s.section_id as section_id, s.tenant_id as tenant_id LIMIT 1"
        section_result = self.repository.graph.query(section_query)
        
        if not section_result:
            print("⚠️ No sections found for sample clauses")
            return
        
        section_id = section_result[0]["section_id"]
        tenant_id = section_result[0]["tenant_id"]
        
        # Create sample clauses
        clauses = [
            {
                "content": "This Agreement shall be governed by the laws of the State of California.",
                "clause_type": "obligation",
                "order": 0,
                "cuad_type": "Governing Law"
            },
            {
                "content": "Either party may terminate this Agreement for convenience with 30 days notice.",
                "clause_type": "condition",
                "order": 1,
                "cuad_type": "Termination For Convenience"
            },
            {
                "content": "The total liability shall not exceed the amount paid under this Agreement.",
                "clause_type": "obligation",
                "order": 2,
                "cuad_type": "Cap On Liability"
            }
        ]
        
        for clause in clauses:
            clause_query = """
            MATCH (s:Section {section_id: $section_id})
            CREATE (cl:Clause {
                clause_id: $clause_id,
                content: $content,
                clause_type: $clause_type,
                order: $order,
                start_position: 0,
                end_position: 0,
                confidence: 0.95,
                section_id: $section_id,
                tenant_id: $tenant_id,
                created_at: datetime()
            })
            CREATE (s)-[:CONTAINS_CLAUSE {order: $order}]->(cl)
            
            WITH cl
            MATCH (ct:ClauseType {cuad_type: $cuad_type})
            CREATE (cl)-[:CLASSIFIED_AS {
                confidence: 0.9,
                detected_by: 'migration',
                created_at: datetime()
            }]->(ct)
            
            RETURN cl.clause_id as clause_id
            """
            
            try:
                result = self.repository.graph.query(clause_query, {
                    "section_id": section_id,
                    "clause_id": f"{section_id}_clause_{clause['order']:03d}",
                    "content": clause["content"],
                    "clause_type": clause["clause_type"],
                    "order": clause["order"],
                    "tenant_id": tenant_id,
                    "cuad_type": clause["cuad_type"]
                })
                
                print(f"✅ Created clause: {clause['cuad_type']} (order: {clause['order']})")
                
            except Exception as e:
                print(f"⚠️ Clause creation failed: {e}")
    
    def verify_migration(self):
        """Verify migration was successful"""
        try:
            # Check counts
            queries = [
                ("Clause nodes", "MATCH (cl:Clause) RETURN count(cl) as count"),
                ("ClauseType nodes", "MATCH (ct:ClauseType) RETURN count(ct) as count"),
                ("CONTAINS_CLAUSE rels", "MATCH ()-[r:CONTAINS_CLAUSE]->() RETURN count(r) as count"),
                ("CLASSIFIED_AS rels", "MATCH ()-[r:CLASSIFIED_AS]->() RETURN count(r) as count")
            ]
            
            print(f"✅ Migration verification:")
            all_good = True
            
            for name, query in queries:
                result = self.repository.graph.query(query)
                count = result[0]["count"] if result else 0
                print(f"   - {name}: {count}")
                if count == 0:
                    all_good = False
            
            # Check order preservation
            order_result = self.repository.graph.query("""
                MATCH (s:Section)-[r:CONTAINS_CLAUSE]->(cl:Clause)
                RETURN cl.order as clause_order, r.order as rel_order
                ORDER BY cl.order ASC
                LIMIT 3
            """)
            
            if order_result:
                print("   - Sample clause orders:")
                for row in order_result:
                    print(f"     Clause order: {row['clause_order']}, Relationship order: {row['rel_order']}")
            
            return all_good
            
        except Exception as e:
            print(f"❌ Migration verification failed: {e}")
            return False

def run_migration(include_sample_data: bool = False):
    """Run the clause schema migration. Sample data is opt-in."""
    migration = ClauseSchemaMigration()
    migration.migrate(include_sample_data=include_sample_data)
    
    if migration.verify_migration():
        print("✅ Clause schema migration completed successfully")
    else:
        print("❌ Migration verification failed")

if __name__ == "__main__":
    run_migration()