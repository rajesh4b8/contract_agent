"""The change report as a reviewer receives it, and the decisions it preserves.

`test_version_diff.py` covers the alignment. This is the layer above: reading
the two versions' membership out of the graph, attaching findings to each
change, refusing an incomparable pair — and the part that matters most to a
reviewer, which is never being asked to approve the same wording twice.
"""
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.application.services.change_report_service import (
    ChangeReportService,
    VersionNotInMatter,
)
from backend.domain.chunking import ChunkingProfile
from backend.infrastructure.chunk_repository import MemberChunk
from backend.main import app

HEADERS = {"X-User-Role": "LEGAL_REVIEWER", "X-Tenant-ID": "acme"}

MATTER = {
    "matter_ref": "MSA-2026-0042",
    "title": "Acme MSA",
    "status": "DRAFT",
    "versions": [
        {"n": 1, "version_id": "V-1"},
        {"n": 2, "version_id": "V-2"},
        {"n": 3, "version_id": "V-3"},
    ],
}


def members(*specs):
    return [MemberChunk(hash=h, order=i, heading=f"{i + 1}.", text=t)
            for i, (h, t) in enumerate(specs)]


def service(previous, current, profile=None, findings=None):
    matters = MagicMock()
    matters.get_matter.return_value = MATTER
    chunks = MagicMock()
    chunks.version_membership.side_effect = [previous, current]
    chunks.profile_for_version.return_value = profile or ChunkingProfile()
    chunks.similarity_lookup.return_value = lambda old, new: 1.0
    chunks.graph.query.return_value = findings or []
    return ChangeReportService(matters=matters, chunks=chunks)


class TestWhatTheReviewerSees:
    def test_it_compares_the_last_two_rounds_by_default(self):
        """The question nine times out of ten; making them name the versions is
        friction for its own sake."""
        svc = service(members(("a", "one")), members(("a", "one")))

        report = svc.compare("acme", "MSA-2026-0042")

        assert (report["from_version"], report["to_version"]) == (2, 3)

    def test_a_named_pair_is_honoured(self):
        svc = service(members(("a", "one")), members(("a", "one")))

        report = svc.compare("acme", "MSA-2026-0042", 1, 3)

        assert (report["from_version"], report["to_version"]) == (1, 3)

    def test_the_order_does_not_matter(self):
        svc = service(members(("a", "one")), members(("a", "one")))

        report = svc.compare("acme", "MSA-2026-0042", 3, 1)

        assert (report["from_version"], report["to_version"]) == (1, 3)

    def test_unchanged_clauses_are_counted_not_listed(self):
        """They are most of any contract, and not what the page was opened for."""
        svc = service(members(("a", "one"), ("b", "two")),
                      members(("a", "one"), ("b", "two")))

        report = svc.compare("acme", "MSA-2026-0042")

        assert report["changes"] == []
        assert report["unchanged"] == 2
        assert report["summary"]["unchanged"] == 2

    def test_a_modification_carries_both_texts(self):
        """A word-level diff needs the before and the after."""
        svc = service(members(("a", "Net ninety (90) days.")),
                      members(("b", "Net forty-five (45) days.")))

        change = svc.compare("acme", "MSA-2026-0042")["changes"][0]

        assert change["kind"] == "MODIFIED"
        assert change["from_text"] == "Net ninety (90) days."
        assert change["to_text"] == "Net forty-five (45) days."

    def test_an_addition_has_only_the_new_text(self):
        svc = service(members(("a", "one")), members(("a", "one"), ("b", "two")))

        added = [c for c in svc.compare("acme", "MSA-2026-0042")["changes"]
                 if c["kind"] == "ADDED"][0]
        assert added["to_text"] == "two"
        assert added["from_text"] == ""

    def test_a_move_is_not_flagged_for_review(self):
        svc = service(members(("a", "one"), ("b", "two"), ("c", "three")),
                      members(("b", "two"), ("c", "three"), ("a", "one")))

        moved = [c for c in svc.compare("acme", "MSA-2026-0042")["changes"]
                 if c["kind"] == "MOVED"]
        assert moved and all(not c["needs_review"] for c in moved)


class TestFindingsTravelWithTheChange:
    """Increment 8 stamps every finding with the chunk it came from. This is the
    first thing to read it — and answering "did they accept our redline?" is
    exactly what it is for."""

    def test_a_changed_clause_carries_what_the_analysis_now_says(self):
        svc = service(
            members(("a", "Net ninety (90) days.")),
            members(("b", "Net thirty (30) days.")),
            findings=[{"chunk": "b", "chunk_order": 0, "clause_type": "Payment Terms",
                       "risk_level": "LOW", "violated_policy": None,
                       "evidence_span": "Net thirty (30) days."}],
        )

        change = svc.compare("acme", "MSA-2026-0042")["changes"][0]

        assert change["findings"][0]["clause_type"] == "Payment Terms"

    def test_a_findings_failure_does_not_lose_the_diff(self):
        matters = MagicMock()
        matters.get_matter.return_value = MATTER
        chunks = MagicMock()
        chunks.version_membership.side_effect = [members(("a", "one")),
                                                 members(("b", "two"))]
        chunks.profile_for_version.return_value = ChunkingProfile()
        chunks.similarity_lookup.return_value = lambda old, new: 1.0
        chunks.graph.query.side_effect = RuntimeError("neo4j is down")

        report = ChangeReportService(matters=matters, chunks=chunks).compare(
            "acme", "MSA-2026-0042")

        assert report["changes"], "the diff was thrown away over its annotations"


class TestRefusingRatherThanInventing:
    def test_incomparable_rounds_are_refused_with_a_reason(self):
        matters = MagicMock()
        matters.get_matter.return_value = MATTER
        chunks = MagicMock()
        chunks.version_membership.side_effect = [members(("a", "one")),
                                                 members(("x", "other"))]
        chunks.profile_for_version.side_effect = [
            ChunkingProfile(), ChunkingProfile(chunker_version=99)]
        chunks.similarity_lookup.return_value = lambda old, new: 1.0
        chunks.graph.query.return_value = []

        report = ChangeReportService(matters=matters, chunks=chunks).compare(
            "acme", "MSA-2026-0042")

        assert report["comparable"] is False
        assert report["changes"] == []
        assert report["reason"]

    def test_one_round_has_nothing_to_compare_with(self):
        matters = MagicMock()
        matters.get_matter.return_value = {**MATTER, "versions": [{"n": 1, "version_id": "V-1"}]}

        report = ChangeReportService(matters=matters, chunks=MagicMock()).compare(
            "acme", "MSA-2026-0042")

        assert report["comparable"] is False
        assert "only one round" in report["reason"]

    def test_a_version_the_matter_does_not_have(self):
        svc = service(members(("a", "one")), members(("a", "one")))

        with pytest.raises(VersionNotInMatter, match="9"):
            svc.compare("acme", "MSA-2026-0042", 1, 9)

    def test_a_version_against_itself(self):
        svc = service(members(("a", "one")), members(("a", "one")))

        with pytest.raises(VersionNotInMatter, match="itself"):
            svc.compare("acme", "MSA-2026-0042", 2, 2)

    def test_an_unknown_matter(self):
        matters = MagicMock()
        matters.get_matter.return_value = None

        with pytest.raises(LookupError):
            ChangeReportService(matters=matters, chunks=MagicMock()).compare(
                "acme", "MSA-2026-9999")


class TestTheEndpoint:
    @pytest.fixture
    def client(self):
        with TestClient(app) as test_client:
            yield test_client

    @pytest.fixture
    def compare(self, monkeypatch):
        from backend.api import matters as matters_api

        fake = MagicMock()
        monkeypatch.setattr(matters_api, "ChangeReportService", lambda **kw: fake)
        return fake

    def test_it_answers_with_the_report(self, client, compare):
        compare.compare.return_value = {"matter_ref": "MSA-2026-0042", "changes": []}

        response = client.get("/api/matters/MSA-2026-0042/changes", headers=HEADERS)

        assert response.status_code == 200
        assert response.json()["matter_ref"] == "MSA-2026-0042"

    def test_the_version_pair_reaches_the_service(self, client, compare):
        compare.compare.return_value = {}

        client.get("/api/matters/MSA-2026-0042/changes?from=1&to=3", headers=HEADERS)

        assert compare.compare.call_args[0][2:] == (1, 3)

    def test_an_unknown_matter_is_404(self, client, compare):
        compare.compare.side_effect = LookupError("nope")

        response = client.get("/api/matters/MSA-2026-9999/changes", headers=HEADERS)

        assert response.status_code == 404

    def test_an_unknown_version_is_404_that_says_which(self, client, compare):
        compare.compare.side_effect = VersionNotInMatter("MSA-2026-0042 has no version 9")

        response = client.get("/api/matters/MSA-2026-0042/changes?to=9", headers=HEADERS)

        assert response.status_code == 404
        assert "version 9" in response.json()["detail"]

    def test_junk_in_the_reference_never_reaches_a_query(self, client, compare):
        response = client.get("/api/matters/..%2F..%2Fetc/changes", headers=HEADERS)

        assert response.status_code == 404
        compare.compare.assert_not_called()


class TestADecisionIsMadeOnceNotEveryRound:
    """The promise of holding the review rather than the contract: a reviewer
    who approved wording in round 1 is not asked to approve the identical
    wording again in round 2.

    Scoping the redline id to the version broke that on every single round —
    the same clause breaching the same rule got a new id, was recreated
    PENDING, and the earlier decision was orphaned on a row nothing reads.
    """

    def _service(self, matter_ref="MSA-2026-0042"):
        from backend.application.services.contract_intelligence_service import (
            ContractIntelligenceService,
        )

        svc = ContractIntelligenceService.__new__(ContractIntelligenceService)
        svc.repository = MagicMock()
        svc.repository.graph.query.return_value = []
        svc.matters = MagicMock()
        svc.matters.matter_for_version.return_value = (
            {"matter_ref": matter_ref, "n": 1} if matter_ref else None
        )
        return svc

    def _redline(self, text="Net ninety (90) days.", rule="PAY-001"):
        from backend.domain.entities import RedlineRecommendation

        return RedlineRecommendation(original_text=text, suggested_text="Net 30.",
                                     justification="", priority="CRITICAL",
                                     rule_id=rule, clause_index=0, clause_type="Payment")

    def test_the_id_is_the_same_in_every_round_of_one_matter(self):
        svc = self._service()
        redline = self._redline()

        first = svc._redline_id(svc._redline_scope("V-1", "acme"), redline)
        second = svc._redline_id(svc._redline_scope("V-2", "acme"), redline)

        assert first == second, "round 2 would ask for the decision again"
        assert first.startswith("acme|MSA-2026-0042"), (
            "reference numbers are per tenant, so the id must be too"
        )

    def test_an_unfiled_version_falls_back_to_its_own_id(self):
        """It has no matter yet, so there is nothing to be stable across."""
        svc = self._service(matter_ref=None)

        assert svc._redline_scope("V-1", "acme") == "V-1"

    def test_a_lookup_failure_does_not_lose_the_redlines(self):
        svc = self._service()
        svc.matters.matter_for_version.side_effect = RuntimeError("neo4j is down")

        assert svc._redline_scope("V-1", "acme") == "V-1"

    def test_the_new_round_is_linked_before_the_status_guard(self):
        """Found on a live database: with the relationship MERGEd *after*
        `WHERE status = 'PENDING'`, a redline the reviewer had already decided
        was filtered out and never attached to the new round — so round 2
        showed nothing where round 1 had an approved redline."""
        svc = self._service()

        svc._store_redlines("V-2", "acme", [self._redline()])

        write = [c.args[0] for c in svc.repository.graph.query.call_args_list
                 if "MERGE (r:Redline" in c.args[0]][0]
        link = write.index("MERGE (c)-[:HAS_REDLINE]->(r)")
        guard = write.index("WHERE coalesce(r.status, 'PENDING') = 'PENDING'")
        assert link < guard, "a decided redline never reaches the new version"

    def test_a_stale_draft_is_unlinked_not_deleted_out_from_under_another_round(self):
        """Now that a redline is scoped to the matter, the same node can be
        referenced by several rounds — and deleting it because this round no
        longer reports the breach would rewrite an earlier round's review."""
        svc = self._service()

        svc._store_redlines("V-2", "acme", [self._redline()])

        cleanup = [c.args[0] for c in svc.repository.graph.query.call_args_list
                   if "NOT r.redline_id IN $current_ids" in c.args[0]][0]
        assert "DELETE link" in cleanup
        assert "WHERE NOT (:Contract)-[:HAS_REDLINE]->(r)" in cleanup


class TestTheChangeReportUi:
    """Static checks, as `test_frontend_navigation.py` does — `make test` stays
    Python-only and these are two bugs a reviewer would hit on their first
    click."""

    @staticmethod
    def _source():
        import pathlib

        return (pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
                / "components/features/matters/ChangeReport.tsx").read_text()

    def test_a_version_cannot_be_compared_with_itself(self):
        """The server answers 404, so an ordinary click would put the page into
        an error state."""
        source = self._source()

        assert "numbers.filter((n) => n !== to)" in source

    def test_choosing_a_colliding_pair_repairs_itself(self):
        assert "if (next === from)" in self._source()

    def test_it_follows_a_newly_uploaded_round(self):
        """A new round arrives by `refresh()` updating `versions` in place,
        without remounting — so without this the page goes on comparing the pair
        that were the latest two when it first rendered."""
        source = self._source()

        assert "[versions.length, latest]" in source
