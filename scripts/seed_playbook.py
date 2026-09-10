#!/usr/bin/env python3
"""Seed the policy playbook into Neo4j.

    python scripts/seed_playbook.py                       # default playbook
    python scripts/seed_playbook.py --file my.yaml        # a different one
    python scripts/seed_playbook.py --tenant acme         # a different tenant
    python scripts/seed_playbook.py --check               # validate only, no writes

Idempotent — re-run after editing the playbook to update the rules in place.
"""
# Run directly (`python scripts/seed_playbook.py`) from anywhere: Python puts
# this file's directory on sys.path, not the repo root, so `backend.*` would
# not resolve without this.
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import argparse

from backend.infrastructure.playbook_loader import (
    DEFAULT_PLAYBOOK,
    parse_playbook,
    seed_playbook,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=pathlib.Path, default=DEFAULT_PLAYBOOK)
    parser.add_argument("--tenant", default=None, help="override the playbook's tenant_id")
    parser.add_argument("--check", action="store_true",
                        help="validate the file without writing to the database")
    args = parser.parse_args()

    if not args.file.exists():
        print(f"No such playbook: {args.file}")
        return 1

    try:
        playbook = parse_playbook(args.file) if args.check else seed_playbook(args.file, args.tenant)
    except Exception as e:
        print(f"Playbook {'invalid' if args.check else 'seeding failed'}: {e}")
        return 1

    verb = "Validated" if args.check else "Seeded"
    print(f"{verb} {playbook.name!r} v{playbook.version} "
          f"for tenant {args.tenant or playbook.tenant_id!r}")
    for rule in playbook.rules:
        print(f"  {rule.id:9} {rule.severity:9} {rule.section_reference}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
