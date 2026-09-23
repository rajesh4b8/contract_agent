"""The change report: what moved between two rounds, and what it costs to know.

This is what the Product shape decision was for. A system that owns the *review*
rather than the contract can answer "what changed since last round, and did the
counterparty accept our redline?" — and a system that owns the contract cannot,
because it has only the latest text.

Two things come out of one diff:

* **the report** a reviewer reads — modified, added, removed and moved clauses,
  with the findings attached to each;
* **the work list** for re-analysis — the windows holding changed chunks.
  Everything else keeps the findings and, more importantly, the *decisions* it
  already had.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.domain.version_diff import Change, ChangeKind, ChangeReport, diff_versions
from backend.infrastructure.chunk_repository import ChunkRepository
from backend.infrastructure.matter_repository import MatterRepository
from backend.shared.debug import trace_step
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)


class VersionNotInMatter(LookupError):
    """The matter exists; that version number does not belong to it."""


class ChangeReportService:
    """Compare two rounds of one matter."""

    def __init__(self, matters: Optional[MatterRepository] = None,
                 chunks: Optional[ChunkRepository] = None):
        self.matters = matters or MatterRepository()
        self.chunks = chunks or ChunkRepository()

    def compare(self, tenant_id: str, matter_ref: str,
                from_n: Optional[int] = None, to_n: Optional[int] = None) -> Dict[str, Any]:
        """The change report between two versions of a matter.

        Defaults to the last two rounds, because that is the question being
        asked nine times out of ten and making the reviewer name them is
        friction for its own sake.
        """
        matter = self.matters.get_matter(tenant_id, matter_ref)
        if matter is None:
            # 404 for an unknown matter and another tenant's alike — the same
            # rule the rest of the API follows.
            raise LookupError(matter_ref)

        versions = sorted(
            (v for v in (matter.get("versions") or []) if v.get("version_id")),
            key=lambda v: v.get("n") or 0,
        )
        if len(versions) < 2:
            return {
                "matter_ref": matter_ref,
                "from_version": None,
                "to_version": versions[-1]["n"] if versions else None,
                "comparable": False,
                "reason": "There is only one round of this contract so far, "
                          "so there is nothing to compare it with.",
                "changes": [],
                "summary": {},
            }

        by_n = {v["n"]: v for v in versions}
        to_n = to_n or versions[-1]["n"]
        from_n = from_n or (versions[-2]["n"] if to_n == versions[-1]["n"]
                            else max((n for n in by_n if n < to_n), default=None))

        for n in (from_n, to_n):
            if n not in by_n:
                raise VersionNotInMatter(f"{matter_ref} has no version {n}")
        if from_n == to_n:
            raise VersionNotInMatter("a version cannot be compared with itself")
        if from_n > to_n:
            from_n, to_n = to_n, from_n

        return self._compare_versions(tenant_id, matter_ref, by_n[from_n], by_n[to_n])

    def _compare_versions(self, tenant_id: str, matter_ref: str,
                          earlier: Dict[str, Any], later: Dict[str, Any]) -> Dict[str, Any]:
        earlier_id, later_id = earlier["version_id"], later["version_id"]

        with trace_step("diff", "load_membership", matter_ref=matter_ref) as step:
            previous = self.chunks.version_membership(tenant_id, earlier_id)
            current = self.chunks.version_membership(tenant_id, later_id)
            step.set(previous=len(previous), current=len(current))

        similarity = self.chunks.similarity_lookup(
            tenant_id, [c.hash for c in previous] + [c.hash for c in current]
        )

        with trace_step("diff", "compare", matter_ref=matter_ref) as step:
            report = diff_versions(
                previous, current, similarity=similarity,
                previous_profile=self.chunks.profile_for_version(tenant_id, earlier_id),
                current_profile=self.chunks.profile_for_version(tenant_id, later_id),
            )
            step.set(comparable=report.comparable, **report.summary)

        # Keyed on the occurrence, not the hash. Two copies of one paragraph
        # share a hash, and `INCLUDES.text` is per-version, so a hash-keyed map
        # lets one version's rendering overwrite the other's — and `from_text`
        # then shows the text the clause was changed *to*.
        previous_text = {(c.hash, c.order): c.text for c in previous}
        current_text = {(c.hash, c.order): c.text for c in current}

        findings = self._findings_by_chunk(tenant_id, later_id)
        previous_findings = self._findings_by_chunk(tenant_id, earlier_id)

        return {
            "matter_ref": matter_ref,
            "from_version": earlier["n"],
            "to_version": later["n"],
            "comparable": report.comparable,
            "reason": report.incomparable_reason,
            "summary": report.summary,
            "changes": [
                self._render(change, previous_text, current_text,
                             findings, previous_findings)
                for change in report.changes
                # An unchanged chunk is the majority of any contract and is not
                # what the reviewer opened this page for. It is counted in the
                # summary and left out of the list.
                if change.kind is not ChangeKind.UNCHANGED
            ],
            "unchanged": len(report.unchanged),
        }

    @staticmethod
    def _render(change: Change,
                previous_text: Dict[tuple, str], current_text: Dict[tuple, str],
                findings: Dict[tuple, List[dict]],
                previous_findings: Dict[tuple, List[dict]]) -> Dict[str, Any]:
        """One change, with the text on both sides and the findings on it.

        Both texts, for every kind that has two. A word-level diff is only
        possible with the before and the after, and a reviewer reading "modified"
        needs to see what modified means.

        Every lookup is by `(hash, order)`. A repeated clause has one hash and
        several occurrences, and a hash-keyed lookup attaches one occurrence's
        findings — and one version's wording — to all of them.
        """
        from_key = (change.from_hash, change.from_order)
        to_key = (change.to_hash, change.to_order)
        return {
            "kind": change.kind.value,
            "heading": change.heading,
            "from_order": change.from_order,
            "to_order": change.to_order,
            "from_text": previous_text.get(from_key, ""),
            "to_text": current_text.get(to_key, ""),
            "similarity": change.similarity,
            "needs_review": change.is_substantive,
            # What the analysis says about this text now, and what it said
            # before — which is how "did they accept our redline?" gets answered.
            "findings": findings.get(to_key, []),
            "previous_findings": previous_findings.get(from_key, []),
        }

    def _findings_by_chunk(self, tenant_id: str, version_id: str) -> Dict[tuple, List[dict]]:
        """The stored findings, grouped by the chunk occurrence they came from.

        Increment 8 stamps every finding with `source_chunk` and
        `source_chunk_order`; this is the first thing to read them, and the
        reason both exist.
        """
        try:
            rows = self.chunks.graph.query(
                """
                MATCH (c:Contract {file_id: $version_id, tenant_id: $tenant_id})
                      -[:HAS_FINDING]->(f:ClauseFinding)
                WHERE f.source_chunk IS NOT NULL
                RETURN f.source_chunk AS chunk, f.source_chunk_order AS chunk_order,
                       f.clause_type AS clause_type,
                       f.risk_level AS risk_level, f.violated_policy AS violated_policy,
                       f.evidence_span AS evidence_span
                ORDER BY f.position
                """,
                {"version_id": version_id, "tenant_id": tenant_id},
            )
        except Exception as e:
            # The diff is still worth showing without them.
            logger.warning(f"Could not load findings for {version_id}: {e}")
            return {}

        grouped: Dict[tuple, List[dict]] = {}
        for row in rows:
            order = row.get("chunk_order")
            grouped.setdefault((row["chunk"], int(order) if order is not None else None),
                               []).append({
                "clause_type": row.get("clause_type"),
                "risk_level": row.get("risk_level"),
                "violated_policy": row.get("violated_policy"),
                "evidence_span": row.get("evidence_span"),
            })
        return grouped

    def windows_needing_analysis(self, tenant_id: str, matter_ref: str,
                                 earlier_id: str, later_id: str) -> ChangeReport:
        """The diff, for deciding what to re-analyse rather than for reading.

        `report.changed` is the work list: a window whose chunks are all
        unchanged has nothing new to say, and its findings — and the decisions
        made on them — carry forward untouched.
        """
        previous = self.chunks.version_membership(tenant_id, earlier_id)
        current = self.chunks.version_membership(tenant_id, later_id)
        similarity = self.chunks.similarity_lookup(
            tenant_id, [c.hash for c in previous] + [c.hash for c in current]
        )
        return diff_versions(
            previous, current, similarity=similarity,
            previous_profile=self.chunks.profile_for_version(tenant_id, earlier_id),
            current_profile=self.chunks.profile_for_version(tenant_id, later_id),
        )
