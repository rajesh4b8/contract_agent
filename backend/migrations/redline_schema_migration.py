"""Schema for reviewer decisions on redlines.

`_store_redlines` MERGEs on `redline_id`, which is only safe if the database
enforces that the id is unique. Without a constraint two concurrent analyses can
each create the same logical redline; reads then return duplicates and a decision
becomes ambiguous — it is unclear which row a reviewer actually ruled on.

Single-property uniqueness is used rather than a composite node key, because node
keys are an Enterprise feature and the id already encodes contract, rule and
clause.
"""
from backend.shared.utils.contract_search_tool import graph
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

STATEMENTS = [
    ("redline_id uniqueness",
     "CREATE CONSTRAINT redline_id_unique IF NOT EXISTS "
     "FOR (r:Redline) REQUIRE r.redline_id IS UNIQUE"),
    ("redline status index",
     "CREATE INDEX redline_status IF NOT EXISTS FOR (r:Redline) ON (r.status)"),
    ("redline tenant index",
     "CREATE INDEX redline_tenant IF NOT EXISTS FOR (r:Redline) ON (r.tenant_id)"),
]


def find_duplicate_redlines() -> list:
    """Rows that would block the uniqueness constraint."""
    return graph.query(
        """
        MATCH (r:Redline)
        WITH r.redline_id AS redline_id, collect(r) AS rows
        WHERE size(rows) > 1
        RETURN redline_id, size(rows) AS count
        """
    )


def deduplicate_redlines() -> int:
    """Keep one row per id, preferring a decided one over a pending draft.

    A reviewer's ruling is the thing worth keeping; a duplicate PENDING draft is
    disposable.
    """
    removed = graph.query(
        """
        MATCH (r:Redline)
        WITH r.redline_id AS redline_id, collect(r) AS rows
        WHERE size(rows) > 1
        WITH redline_id, [x IN rows WHERE coalesce(x.status,'PENDING') <> 'PENDING'] AS decided,
             rows
        WITH coalesce(decided[0], rows[0]) AS keep, rows
        UNWIND rows AS candidate
        WITH keep, candidate WHERE candidate <> keep
        WITH collect(candidate) AS doomed
        FOREACH (x IN doomed | DETACH DELETE x)
        RETURN size(doomed) AS removed
        """
    )
    return removed[0]["removed"] if removed else 0


def run_migration():
    """Deduplicate, then constrain. Safe to re-run."""
    duplicates = find_duplicate_redlines()
    if duplicates:
        logger.warning(f"Found {len(duplicates)} duplicated redline id(s); removing extras")
        logger.info(f"Removed {deduplicate_redlines()} duplicate redline row(s)")

    for name, statement in STATEMENTS:
        try:
            graph.query(statement)
            logger.info(f"Applied: {name}")
        except Exception as e:
            logger.error(f"Could not apply {name}: {e}")
            raise


if __name__ == "__main__":
    run_migration()
