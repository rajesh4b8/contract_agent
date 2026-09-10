"""Load a playbook from YAML into the graph as (:PolicyDocument)-[:HAS_RULE]->(:PolicyRule).

Policy used to live in a Python dict, so a violation could only say "payment terms
exceed company policy" — it could not cite a rule, and changing policy meant
changing code. Rules now come from a reviewable YAML file with stable ids, and
every finding carries the id of the rule that produced it.

Seeding is idempotent: rules are MERGEd on (tenant_id, rule id), so re-running
after editing the playbook updates in place rather than duplicating.
"""
import pathlib
from dataclasses import dataclass
from typing import Any, Dict, List

import yaml

from backend.shared.utils.contract_search_tool import graph
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_PLAYBOOK = (
    pathlib.Path(__file__).resolve().parents[2] / "data" / "playbooks" / "default_playbook.yaml"
)

REQUIRED_RULE_FIELDS = ("id", "rule_text", "rule_type", "severity", "section_reference")
VALID_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}


@dataclass(frozen=True)
class LoadedRule:
    id: str
    rule_text: str
    rule_type: str
    applies_to: List[str]
    severity: str
    section_reference: str
    redline_text: str = ""


@dataclass(frozen=True)
class Playbook:
    name: str
    version: str
    tenant_id: str
    rules: List[LoadedRule]


def parse_playbook(path: pathlib.Path = DEFAULT_PLAYBOOK) -> Playbook:
    """Read and validate a playbook file. Raises on anything malformed.

    Validation is strict on purpose: a silently dropped rule means contracts
    stop being checked against it, with nothing in the output to say so.
    """
    data: Dict[str, Any] = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a YAML mapping")

    raw_rules = data.get("rules")
    if not raw_rules:
        raise ValueError(f"{path} defines no rules")

    seen: set[str] = set()
    rules: List[LoadedRule] = []
    for raw in raw_rules:
        missing = [f for f in REQUIRED_RULE_FIELDS if not raw.get(f)]
        if missing:
            raise ValueError(f"rule {raw.get('id', '<no id>')!r} is missing {missing}")
        if raw["severity"] not in VALID_SEVERITIES:
            raise ValueError(
                f"rule {raw['id']!r} has severity {raw['severity']!r}; "
                f"expected one of {sorted(VALID_SEVERITIES)}"
            )
        if raw["id"] in seen:
            raise ValueError(f"duplicate rule id {raw['id']!r}")
        seen.add(raw["id"])

        rules.append(LoadedRule(
            id=raw["id"],
            rule_text=" ".join(raw["rule_text"].split()),
            rule_type=raw["rule_type"],
            applies_to=raw.get("applies_to") or ["general"],
            severity=raw["severity"],
            section_reference=raw["section_reference"],
            redline_text=" ".join((raw.get("redline_text") or "").split()),
        ))

    return Playbook(
        name=data.get("name", path.stem),
        version=str(data.get("version", "1.0")),
        tenant_id=data.get("tenant_id", "default-tenant"),
        rules=rules,
    )


def seed_playbook(path: pathlib.Path = DEFAULT_PLAYBOOK, tenant_id: str | None = None) -> Playbook:
    """Write the playbook into Neo4j. Safe to re-run."""
    playbook = parse_playbook(path)
    tenant = tenant_id or playbook.tenant_id

    graph.query(
        """
        MERGE (p:PolicyDocument {id: $policy_id, tenant_id: $tenant_id})
        SET p.name = $name,
            p.version = $version,
            p.document_type = 'playbook',
            p.source = 'seed',
            p.updated_at = datetime()
        """,
        {
            "policy_id": f"playbook_{tenant}",
            "tenant_id": tenant,
            "name": playbook.name,
            "version": playbook.version,
        },
    )

    for rule in playbook.rules:
        graph.query(
            """
            MATCH (p:PolicyDocument {id: $policy_id, tenant_id: $tenant_id})
            MERGE (r:PolicyRule {id: $rule_id, tenant_id: $tenant_id})
            SET r.rule_text = $rule_text,
                r.rule_type = $rule_type,
                r.applies_to = $applies_to,
                r.severity = $severity,
                r.section_reference = $section_reference,
                r.redline_text = $redline_text,
                r.updated_at = datetime()
            MERGE (p)-[:HAS_RULE]->(r)
            """,
            {
                "policy_id": f"playbook_{tenant}",
                "tenant_id": tenant,
                "rule_id": rule.id,
                "rule_text": rule.rule_text,
                "rule_type": rule.rule_type,
                "applies_to": rule.applies_to,
                "severity": rule.severity,
                "section_reference": rule.section_reference,
                "redline_text": rule.redline_text,
            },
        )

    # Rules deleted from the file must stop being enforced. Without this a
    # lawyer could remove a rule from the playbook and it would keep firing,
    # because MERGE only ever adds.
    removed = graph.query(
        """
        MATCH (p:PolicyDocument {id: $policy_id, tenant_id: $tenant_id})-[:HAS_RULE]->(r:PolicyRule)
        WHERE NOT r.id IN $current_ids
        WITH collect(r) AS stale
        FOREACH (rule IN stale | DETACH DELETE rule)
        RETURN size(stale) AS removed
        """,
        {
            "policy_id": f"playbook_{tenant}",
            "tenant_id": tenant,
            "current_ids": [rule.id for rule in playbook.rules],
        },
    )
    retired = removed[0]["removed"] if removed else 0

    logger.info(
        f"Seeded playbook {playbook.name!r} v{playbook.version} "
        f"({len(playbook.rules)} rules) for tenant {tenant!r}"
        + (f"; retired {retired} rule(s) no longer in the file" if retired else "")
    )
    return playbook


def load_rules_for_tenant(tenant_id: str, contract_type: str = "general") -> List[LoadedRule]:
    """Fetch the rules a contract of this type should be checked against.

    Reached through (:PolicyDocument)-[:HAS_RULE]->(:PolicyRule) rather than by a
    tenant_id copied onto the rule: the relationship is the source of truth, it
    excludes orphaned rules, and it also picks up rules written by the policy
    upload path, which sets tenant_id on the document but not on each rule.
    """
    records = graph.query(
        """
        MATCH (p:PolicyDocument {tenant_id: $tenant_id})-[:HAS_RULE]->(r:PolicyRule)
        WHERE $contract_type IN r.applies_to OR 'general' IN r.applies_to
        RETURN DISTINCT r.id AS id, r.rule_text AS rule_text, r.rule_type AS rule_type,
               r.applies_to AS applies_to, r.severity AS severity,
               r.section_reference AS section_reference,
               coalesce(r.redline_text, '') AS redline_text
        ORDER BY CASE r.severity
                   WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2
                   WHEN 'MEDIUM' THEN 3 ELSE 4 END, r.id
        """,
        {"tenant_id": tenant_id, "contract_type": contract_type},
    )
    return [LoadedRule(**record) for record in records]


def attach_violated_policy(clauses: List[Dict[str, Any]],
                           violations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Record on each clause which playbook rule(s) it breaches.

    Attachment is by clause index, not by matching text: two clauses can carry
    the same evidence span, and joining on the text would cite a breach found in
    one of them against all of them.
    """
    by_index: Dict[int, List[str]] = {}
    for violation in violations:
        rule_id = violation.get("rule_id")
        index = violation.get("clause_index")
        if rule_id is not None and isinstance(index, int):
            by_index.setdefault(index, []).append(rule_id)

    stamped = []
    for i, clause in enumerate(clauses):
        rule_ids = by_index.get(i)
        stamped.append({**clause, "violated_policy": ", ".join(rule_ids) if rule_ids else None})
    return stamped
