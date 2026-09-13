#!/usr/bin/env python3
"""Give every pre-existing contract a single-version matter.

    python scripts/migrate_matters.py --check           # show what would happen
    python scripts/migrate_matters.py                   # migrate every tenant
    python scripts/migrate_matters.py --tenant acme     # one tenant only

Contracts uploaded before Increment 6 have no matter, so they would not appear
on the matters list at all — the landing page would be empty on a database full
of work. This gives each of them a matter of its own, with a reference number
allocated in upload order, and marks the contract as version 1.

**Nothing is deleted or overwritten.** The redline decisions recorded in
Increment 4 hang off the same node the matter now points at; the migration adds
a label and a parent and touches no `(:Redline)`. Re-running is safe: contracts
that already belong to a matter are skipped.
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
    try:
        result = repository.migrate_legacy_contracts(args.tenant, dry_run=args.check)
    except Exception as e:
        print(f"Migration failed: {e}")
        return 1

    if args.check:
        planned = result["planned"]
        print(f"{len(planned)} contract(s) would get a matter:")
        for item in planned:
            print(f"  {item['version_id']:34} {item['tenant_id']:18} "
                  f"{item['title']}")
        return 0

    print(f"Created {result['migrated']} matter(s):")
    for created in result["created"]:
        print(f"  {created['matter_ref']:16} v{created['n']}  {created['version_id']}")

    if result["failed"]:
        print(f"\n{len(result['failed'])} contract(s) could not be migrated:")
        for failure in result["failed"]:
            print(f"  {failure['version_id']}: {failure['error']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
