#!/usr/bin/env python3
"""
Migration runner script for multi-level embeddings
Usage: python run_migration.py [command]
Commands: upgrade, downgrade, sample, migrate_contracts
"""

import sys
import os
import logging
from pathlib import Path

# Add backend to path
backend_path = Path(__file__).parent
sys.path.insert(0, str(backend_path))

from migrations.multi_level_embeddings import upgrade_schema, downgrade_schema, create_sample_data
from embeddings.migrator import EmbeddingMigrator

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

def main():
    if len(sys.argv) < 2:
        print("Usage: python run_migration.py [upgrade|downgrade|sample|migrate_contracts]")
        sys.exit(1)
    
    command = sys.argv[1].lower()
    
    try:
        if command == "upgrade":
            # Every migration that has a runnable entry point, not just the
            # embeddings one. clause_schema_migration seeds the (:ClauseType)
            # nodes that CLASSIFIED_AS matches against — without it, every CUAD
            # classification is silently dropped at write time.
            logger.info("Running schema upgrade...")
            upgrade_schema()

            from migrations.section_schema_migration import run_migration as section_migration
            from migrations.clause_schema_migration import run_migration as clause_migration
            from migrations.audit_error_schema_migration import run_migration as audit_migration
            from migrations.phase2_phase3_schema import run_migration as phase_migration

            failures = []
            for name, migrate in [
                ("sections", section_migration),
                ("clauses + clause types", clause_migration),
                ("audit + error tracking", audit_migration),
                ("phase 2/3 schema", phase_migration),
            ]:
                logger.info(f"Running migration: {name}")
                try:
                    migrate()
                except Exception as e:
                    # Keep going so a later migration still gets a chance, but
                    # remember the failure: exiting 0 after a partial upgrade
                    # would let CI and callers proceed as if the schema were
                    # fully installed.
                    logger.error(f"Migration {name!r} failed: {e}")
                    failures.append(name)

            if failures:
                logger.error(f"Schema upgrade incomplete; failed: {', '.join(failures)}")
                sys.exit(1)

            logger.info("Schema upgrade completed successfully!")
            
        elif command == "downgrade":
            logger.info("Running schema downgrade...")
            downgrade_schema()
            logger.info("Schema downgrade completed successfully!")
            
        elif command == "sample":
            logger.info("Creating sample data...")
            create_sample_data()
            logger.info("Sample data created successfully!")
            
        elif command == "migrate_contracts":
            logger.info("Migrating existing contracts to multi-level embeddings...")
            migrator = EmbeddingMigrator()
            
            # Get batch size from command line or use default
            batch_size = 5
            if len(sys.argv) > 2:
                try:
                    batch_size = int(sys.argv[2])
                except ValueError:
                    logger.warning(f"Invalid batch size '{sys.argv[2]}', using default: {batch_size}")
            
            stats = migrator.migrate_existing_contracts(batch_size=batch_size)
            
            logger.info("Migration completed!")
            logger.info(f"Total contracts: {stats['total_contracts']}")
            logger.info(f"Successful: {stats['successful']}")
            logger.info(f"Failed: {stats['failed']}")
            
            if stats['errors']:
                logger.error("Errors encountered:")
                for error in stats['errors']:
                    logger.error(f"  - {error}")
        
        elif command == "rollback":
            logger.info("Rolling back contract migrations...")
            migrator = EmbeddingMigrator()
            migrator.rollback_migration()
            logger.info("Rollback completed!")
            
        else:
            logger.error(f"Unknown command: {command}")
            print("Available commands: upgrade, downgrade, sample, migrate_contracts, rollback")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"Migration failed: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()