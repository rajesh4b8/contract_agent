#!/usr/bin/env python3
"""Give every pre-existing contract a single-version matter.

    python scripts/migrate_matters.py --check           # show what would happen
    python scripts/migrate_matters.py                   # migrate every tenant
    python scripts/migrate_matters.py --tenant acme     # one tenant only

Contracts uploaded before Increment 6 have no matter, so they would not appear
on the matters list at all — the landing page would be empty on a database full
of work.

Repeat uploads of one document become **rounds of one matter**, not separate
matters. The duplicate check never fired before this increment, so re-uploading
a contract silently created a second unrelated `Contract`; on the development
database that turned 8 distinct documents into 52 contracts, one of them
uploaded 18 times. Filing those as 52 matters would put the old bug on the
landing page. Nothing is discarded — every copy keeps its own redline decisions
under its own version number.

**Nothing is deleted or overwritten.** The redline decisions recorded in
Increment 4 hang off the same node the matter now points at; the migration adds
a label and a parent and touches no `(:Redline)`. Re-running is safe: contracts
that already belong to a matter are skipped, and so are versions uploaded since
this increment — one sitting unfiled is a confirmation card the reviewer has not
answered yet, and auto-filing it would burn a reference number on a decision
they never made.
"""
# Run directly from anywhere: Python puts this file's directory on sys.path,
# not the repo root, so `backend.*` would not resolve without this.
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import argparse

from backend.infrastructure.matter_repository import MatterRepository


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tenant", default=None,
                        help="migrate only this tenant (default: every tenant)")
    parser.add_argument("--check", action="store_true",
                        help="list what would be created, without writing")
    args = parser.parse_args()

    repository = MatterRepository()

    if not args.check:
        # The uniqueness constraints the concurrency guarantees rest on. Created
        # here as well as at startup so a database migrated by hand still gets
        # them.
        created_constraints = repository.ensure_constraints()
        if created_constraints:
            print(f"Constraints in place: {', '.join(created_constraints)}\n")

    try:
        result = repository.migrate_legacy_contracts(args.tenant, dry_run=args.check)
    except Exception as e:
        print(f"Migration failed: {e}")
        return 1

    if args.check:
        planned = result["planned"]
        contracts = sum(len(item["version_ids"]) for item in planned)
        print(f"{contracts} contract(s) would become {len(planned)} matter(s):")
        for item in planned:
            rounds = len(item["version_ids"])
            print(f"  {item['tenant_id']:18} {rounds:3} version(s)  {item['title']}")
            for n, version_id in enumerate(item["version_ids"], start=1):
                print(f"      v{n:<3} {version_id}")
        return 0

    print(f"Created {result['migrated']} matter(s) holding {result['versions']} version(s):")
    for created in result["created"]:
        print(f"  {created['matter_ref']:16} {created['versions']:3} version(s)")

    if result["failed"]:
        print(f"\n{len(result['failed'])} document(s) could not be migrated:")
        for failure in result["failed"]:
            print(f"  {failure['version_id']}: {failure['error']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
