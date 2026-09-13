"""Multiple contracts, each resumable.

The failure cases were settled before any of this was built, for the reason
Increment 4's were: getting this wrong loses a reviewer's place, or files their
work against the wrong contract. `TestTheFailureCases` walks the table in the
progress tracker row by row; everything after it tests the pieces those rows
rest on.

Nothing here needs a database. The repository takes its graph as an argument,
so the statements it would send can be inspected directly — which is the only
way to assert the property that matters most about reference allocation: that
it happens in *one* statement.
"""
import pytest

from backend.domain.matter import (
    AnalysisStatus,
    InvalidTransition,
    MatterStatus,
    canonical_text,
    check_transition,
    counterparty_from_parties,
    derive_status,
    format_reference,
    is_reference,
    parse_status,
    source_sha256,
    suggest_title,
    type_code,
)
from backend.infrastructure.matter_repository import (
    AlreadyFiled,
    MatterClosed,
    MatterNotFound,
    MatterRepository,
)


class FakeGraph:
    """Records every statement it is handed and answers with canned rows.

    Deliberately not a MagicMock: the interesting assertions are about the
    Cypher itself — how many statements a write takes, and whether the tenant
    reaches the one that reads other people's data.
    """

    def __init__(self, *responses):
        self.calls = []
        self.responses = list(responses)

    def query(self, statement, params=None):
        self.calls.append((statement, params or {}))
        if not self.responses:
            return []
        answer = self.responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    @property
    def statements(self):
        return [statement for statement, _ in self.calls]

    @property
    def params(self):
        return [params for _, params in self.calls]

    def statements_matching(self, needle):
        return [s for s in self.statements if needle in s]


def repo(*responses):
    return MatterRepository(FakeGraph(*responses))


def counter_statements(repository):
    """Every statement that would take a reference number."""
    return repository.graph.statements_matching("Counter")


A_MATTER = {
    "matter_ref": "MSA-2026-0042",
    "title": "Master Services Agreement — Acme",
    "counterparty": "Acme",
    "contract_type": "Master Services Agreement",
    "status": "DRAFT",
    "created_at": "2026-09-01T09:00:00Z",
    "updated_at": "2026-09-01T09:00:00Z",
    "versions": [{"n": 1, "version_id": "UPLOADED_AAA_20260901"}],
}


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------

class TestTheFailureCases:
    """One test per row of the failure-case table, in the table's order."""

    def test_a_byte_identical_upload_makes_no_new_version(self):
        """It opens the matter that already holds it, naming the version."""
        repository = repo([{
            "version_id": "UPLOADED_AAA_20260901",
            "filename": "msa.pdf",
            "matter_ref": "MSA-2026-0042",
            "title": "Master Services Agreement — Acme",
            "matter_status": "DRAFT",
            "n": 1,
        }])

        twin = repository.version_by_source_hash("t", source_sha256("the same bytes"))

        assert twin["matter_ref"] == "MSA-2026-0042"
        assert twin["n"] == 1
        # And nothing was written.
        assert not repository.graph.statements_matching("CREATE")

    def test_choosing_new_contract_for_a_lookalike_is_honoured(self):
        """A new SOW off the same template is a different contract.

        There is no similarity matching in this increment at all, which is what
        makes the promise keepable: the only thing that can stop a new matter
        being created is an exact source match, and that is checked at upload,
        never here.
        """
        repository = repo(
            [{"version_id": "V-new"}],                      # unfiled, filable
            [{"n": 1}],                                     # counter
            [{"matter_ref": "SOW-2026-0001"}],              # created
        )

        created = repository.create_matter(
            "t", "V-new", title="SOW — Globex", contract_type="Statement of Work"
        )

        assert created["matter_ref"] == "SOW-2026-0001"
        assert created["n"] == 1

    def test_a_failed_extraction_burns_no_reference_number(self):
        """No version, no matter — and crucially, no gap in the sequence.

        A reference is the thing people say out loud. A sequence with holes in
        it invites the question "what happened to MSA-2026-0043?", and the
        honest answer must never be "a PDF failed to parse".
        """
        repository = repo([])  # the version lookup finds nothing

        with pytest.raises(AlreadyFiled):
            repository.create_matter("t", "V-never-extracted", title="whatever")

        assert not counter_statements(repository), (
            "a reference was allocated for a document that does not exist"
        )

    def test_a_new_round_on_a_closed_matter_is_refused_by_name(self):
        """Reopening is an explicit action, not a side effect of uploading."""
        repository = repo([{**A_MATTER, "status": "CLOSED"}])

        with pytest.raises(MatterClosed, match="MSA-2026-0042"):
            repository.attach_version("t", "MSA-2026-0042", "V-2")

        assert not repository.graph.statements_matching("CREATE (m)-[r:HAS_VERSION")

    def test_two_uploads_racing_for_a_reference_cannot_collide(self):
        """The counter is read and incremented in one statement.

        Read-then-write in two statements is the classic way to hand two
        concurrent uploads the same number. One statement means the second
        transaction waits on the lock the first holds over the counter node.
        """
        repository = repo([{"n": 42}])

        reference = repository.allocate_reference("t", "MSA", 2026)

        assert reference == "MSA-2026-0042"
        statement = repository.graph.statements[0]
        assert len(repository.graph.statements) == 1, "allocation must be one statement"
        assert "MERGE (c:Counter" in statement
        assert "SET c.n = c.n + 1" in statement
        assert "RETURN c.n" in statement

    def test_version_numbers_are_allocated_in_the_same_statement_too(self):
        """Same hazard, same fix: two rounds into one matter get 2 and 3."""
        repository = repo([A_MATTER], [{"matter_ref": "MSA-2026-0042", "n": 2}])

        filed = repository.attach_version("t", "MSA-2026-0042", "V-2")

        assert filed["n"] == 2
        write = repository.graph.statements[-1]
        assert "SET m.version_count = coalesce(m.version_count, 0) + 1" in write
        assert "CREATE (m)-[r:HAS_VERSION {n: m.version_count}]->(v)" in write, (
            "n must be written from the counter the same statement just incremented"
        )

    def test_an_analysis_that_fails_after_the_version_is_stored_says_so(self):
        """The version exists; the failure is recorded on it, not implied by silence."""
        repository = repo([])

        repository.set_analysis_status("t", "V-1", AnalysisStatus.FAILED,
                                       error="Gemini has no quota left on this key")

        statement, params = repository.graph.calls[0]
        assert params["status"] == "FAILED"
        assert "quota" in params["error"]
        assert "SET v.analysis_status" in statement

    def test_a_matter_opened_mid_analysis_shows_the_in_progress_state(self):
        repository = repo([])

        repository.set_analysis_status("t", "V-1", AnalysisStatus.RUNNING)

        assert repository.graph.params[0]["status"] == "RUNNING"

    def test_a_matter_with_no_versions_cannot_be_created(self):
        """A matter exists only because an extraction succeeded.

        `create_matter` takes the version it is filing; there is no call that
        makes an empty one.
        """
        repository = repo([])

        with pytest.raises(AlreadyFiled):
            repository.create_matter("t", "V-does-not-exist", title="Empty")

    def test_an_unknown_reference_reads_as_missing_not_forbidden(self):
        """404, not 403 — the same rule redlines follow.

        Every query is tenant-scoped, so another tenant's reference returns
        nothing and is indistinguishable from one that was never allocated.
        Answering 403 would confirm that it exists.
        """
        repository = repo([])

        assert repository.get_matter("t", "MSA-2026-9999") is None
        assert repository.graph.params[0]["tenant_id"] == "t"


# ---------------------------------------------------------------------------
# Reference numbers
# ---------------------------------------------------------------------------

class TestReferenceNumbers:
    """`MSA-2026-0042` is said out loud and typed into emails."""

    @pytest.mark.parametrize("contract_type,expected", [
        ("Master Services Agreement", "MSA"),
        ("master service agreement", "MSA"),
        ("MSA", "MSA"),
        ("Statement of Work", "SOW"),
        ("Mutual Non-Disclosure Agreement", "NDA"),
        ("MNDA", "NDA"),
        ("Data Processing Agreement", "DPA"),
        ("SaaS Subscription Agreement", "SAAS"),
    ])
    def test_the_familiar_types_get_their_familiar_codes(self, contract_type, expected):
        assert type_code(contract_type) == expected

    def test_an_unknown_type_still_reads_as_something(self):
        assert type_code("Consulting Agreement") == "CON"

    def test_nothing_at_all_falls_back_to_a_contract(self):
        assert type_code(None) == "CTR"
        assert type_code("   ") == "CTR"
        assert type_code("?!") == "CTR"

    def test_the_format_is_padded_to_four(self):
        assert format_reference("MSA", 2026, 42) == "MSA-2026-0042"
        assert format_reference("sow", 2026, 1) == "SOW-2026-0001"

    def test_the_ten_thousandth_contract_widens_rather_than_collides(self):
        """Padding is cosmetic; uniqueness is not."""
        assert format_reference("MSA", 2026, 10000) == "MSA-2026-10000"
        assert format_reference("MSA", 2026, 9999) != format_reference("MSA", 2026, 10000)

    def test_numbering_starts_at_one(self):
        with pytest.raises(ValueError):
            format_reference("MSA", 2026, 0)

    @pytest.mark.parametrize("value", ["MSA-2026-0042", "PO-2026-0001", "SAAS-2026-1234"])
    def test_real_references_are_recognised(self, value):
        assert is_reference(value)

    @pytest.mark.parametrize("value", [
        "", None, "MSA", "MSA-2026", "../../etc/passwd", "MSA-2026-42",
        "MATCH (n) DETACH DELETE n",
    ])
    def test_junk_never_reaches_a_query(self, value):
        assert not is_reference(value)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

class TestStatusIsDerivedWhereverItCan_Be:
    """Storing what the redlines already say would give it room to drift."""

    def test_a_matter_with_no_redlines_is_a_draft(self):
        assert derive_status("DRAFT", {"total": 0, "pending": 0}) is MatterStatus.DRAFT

    def test_pending_redlines_mean_in_review(self):
        assert derive_status("DRAFT", {"total": 6, "pending": 2}) is MatterStatus.IN_REVIEW

    def test_no_pending_redlines_mean_reviewed(self):
        assert derive_status("DRAFT", {"total": 6, "pending": 0}) is MatterStatus.REVIEWED

    @pytest.mark.parametrize("stored", ["AWAITING_COUNTERPARTY", "CLOSED"])
    def test_a_human_statement_is_never_overridden_by_a_count(self, stored):
        """"Sent to the counterparty" stays true however the redlines stand."""
        assert derive_status(stored, {"total": 6, "pending": 3}) is MatterStatus(stored)

    def test_an_unreadable_stored_status_renders_rather_than_crashes(self):
        """One bad row must not take out the whole matters list."""
        assert parse_status("something-else") is MatterStatus.DRAFT
        assert parse_status(None) is MatterStatus.DRAFT


class TestTransitionsAHumanMakes:
    def test_a_reviewed_matter_can_be_sent_to_the_counterparty(self):
        assert check_transition(MatterStatus.REVIEWED,
                                MatterStatus.AWAITING_COUNTERPARTY)

    def test_a_closed_matter_can_be_reopened(self):
        assert check_transition(MatterStatus.CLOSED, MatterStatus.DRAFT)

    def test_a_derived_status_cannot_be_set_by_hand(self):
        with pytest.raises(InvalidTransition, match="derived"):
            check_transition(MatterStatus.DRAFT, MatterStatus.REVIEWED)

    def test_a_closed_matter_does_not_jump_straight_back_to_the_counterparty(self):
        with pytest.raises(InvalidTransition, match="CLOSED"):
            check_transition(MatterStatus.CLOSED, MatterStatus.AWAITING_COUNTERPARTY)

    def test_setting_the_status_it_already_has_is_refused(self):
        """Silently succeeding would hide a stale page from the reviewer."""
        with pytest.raises(InvalidTransition, match="already"):
            check_transition(MatterStatus.CLOSED, MatterStatus.CLOSED)

    def test_only_a_closed_matter_refuses_new_rounds(self):
        for status in MatterStatus:
            assert status.accepts_new_versions is (status is not MatterStatus.CLOSED)


# ---------------------------------------------------------------------------
# Source identity
# ---------------------------------------------------------------------------

class TestTheOneAutomaticCase:
    """An exact match is not a judgement call, so it is never asked about."""

    def test_the_same_text_hashes_the_same(self):
        assert source_sha256("Payment within 30 days.") == source_sha256("Payment within 30 days.")

    def test_re_extraction_whitespace_does_not_fork_a_version(self):
        """PDF extraction is not byte-stable; line breaks and runs of spaces move."""
        assert source_sha256("Payment within\n30   days.") == source_sha256("Payment within 30 days.")

    def test_a_different_contract_is_a_different_hash(self):
        assert source_sha256("Payment within 30 days.") != source_sha256("Payment within 60 days.")

    def test_one_changed_word_is_enough(self):
        """Whitespace folding must not blunt it into a near-match."""
        assert source_sha256("thirty (30) days") != source_sha256("thirty (31) days")

    def test_empty_and_missing_text_do_not_crash(self):
        assert source_sha256(None) == source_sha256("")
        assert canonical_text(None) == ""


# ---------------------------------------------------------------------------
# Pre-filling the card
# ---------------------------------------------------------------------------

class TestPreFillingTheConfirmationCard:
    """The user corrects rather than types."""

    def test_the_party_that_is_not_us_is_offered(self):
        parties = [
            {"name": "Our Company Ltd", "role": "Service Provider"},
            {"name": "Acme Corporation", "role": "Customer"},
        ]

        assert counterparty_from_parties(parties) == "Acme Corporation"

    def test_with_no_roles_the_first_party_is_the_guess(self):
        parties = [{"name": "Acme Corporation", "role": ""}, {"name": "Globex", "role": ""}]

        assert counterparty_from_parties(parties) == "Acme Corporation"

    def test_no_parties_means_an_empty_field_not_an_error(self):
        assert counterparty_from_parties(None) == ""
        assert counterparty_from_parties([{"name": "  "}]) == ""

    def test_the_title_names_the_deal(self):
        assert suggest_title("Master Services Agreement", "Acme") == \
            "Master Services Agreement — Acme"

    def test_with_nothing_extracted_the_filename_stands_in(self):
        assert suggest_title("", "", "Acme MSA v3.PDF") == "Acme MSA v3"

    def test_there_is_always_a_title(self):
        assert suggest_title(None, None, None) == "Untitled contract"


# ---------------------------------------------------------------------------
# Reading matters back
# ---------------------------------------------------------------------------

class TestReadingMattersBack:
    def test_every_query_is_scoped_to_the_caller_s_tenant(self):
        """The duplicate check this replaces ran across the whole database.

        `MATCH (c:Contract) WHERE c.file_id CONTAINS $filename` had no tenant
        filter, so a filename collision handed another tenant's contract id back
        to the uploader.
        """
        repository = repo([], [], [], [], [])
        repository.list_matters("acme")
        repository.get_matter("acme", "MSA-2026-0042")
        repository.version_by_source_hash("acme", "abc")
        repository.matter_for_version("acme", "V-1")
        repository.review_counts("acme", "MSA-2026-0042")

        for statement, params in repository.graph.calls:
            assert params.get("tenant_id") == "acme", statement
            assert "tenant_id: $tenant_id" in statement, statement

    def test_a_filed_version_wins_over_a_loose_one(self):
        """A cancelled confirmation card must not shadow the real matter."""
        repository = repo([])
        repository.version_by_source_hash("t", "abc")

        statement = repository.graph.statements[0]
        assert "ORDER BY CASE WHEN m IS NULL THEN 1 ELSE 0 END" in statement

    def test_review_counts_come_back_per_version(self):
        repository = repo([
            {"n": 1, "total": 4, "pending": 0, "approved": 3, "modified": 1, "rejected": 0},
            {"n": 2, "total": 5, "pending": 2, "approved": 2, "modified": 0, "rejected": 1},
        ])

        counts = repository.review_counts("t", "MSA-2026-0042")

        assert counts[1]["pending"] == 0
        assert counts[2]["pending"] == 2
        assert derive_status("DRAFT", counts[2]) is MatterStatus.IN_REVIEW

    def test_a_version_with_no_redlines_counts_as_zero_not_one(self):
        """`count(rl)` over an OPTIONAL MATCH that found nothing must be 0."""
        repository = repo([{"n": 1, "total": 0, "pending": 0, "approved": 0,
                            "modified": 0, "rejected": 0}])

        assert repository.review_counts("t", "MSA-2026-0042")[1]["total"] == 0

    @pytest.mark.parametrize("method,args", [
        ("review_counts", ("t", "MSA-2026-0042")),
        ("list_matters", ("t",)),
    ])
    def test_an_empty_optional_match_is_not_counted_as_pending(self, method, args):
        """A null row's `coalesce(rl.status, 'PENDING')` is 'PENDING'.

        Found against a live database: a matter that had never been analysed
        reported one pending redline out of zero, because the OPTIONAL MATCH
        yields a null row and the coalesce then reads it as an undecided one.
        """
        repository = repo([])
        getattr(repository, method)(*args)

        pending = [s for s in repository.graph.statements if "PENDING" in s]
        assert pending, f"{method} does not count pending redlines any more"
        for statement in pending:
            assert "rl IS NOT NULL AND" in statement, (
                f"{method} counts a null row as a pending redline"
            )


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

class TestMigratingContractsThatPredateMatters:
    """The landing page must not be empty on a database full of work."""

    LEGACY = [
        {
            "version_id": "UPLOADED_AAA_20260801",
            "tenant_id": "default-tenant",
            "contract_type": "Master Services Agreement",
            "full_text": "Payment within ninety (90) days.",
            "summary": "An MSA with Acme",
            "upload_date": "2026-08-01T00:00:00Z",
            "parties": [{"name": "Acme Corporation", "role": "Customer"}],
        },
    ]

    def test_it_never_writes_to_a_redline(self):
        """Increment 4's decisions must survive this untouched.

        They hang off the same node the matter now points at — the migration
        adds a label and a parent and nothing else. A DELETE anywhere in these
        statements would be a decision silently thrown away.
        """
        repository = repo(
            self.LEGACY,
            [{"version_id": "UPLOADED_AAA_20260801"}],   # record_version
            [{"version_id": "UPLOADED_AAA_20260801"}],   # unfiled check
            [{"n": 1}],                                  # counter
            [{"matter_ref": "MSA-2026-0001"}],           # created
        )

        result = repository.migrate_legacy_contracts()

        assert result["migrated"] == 1
        for statement in repository.graph.statements:
            assert "Redline" not in statement
            assert "DELETE" not in statement
            assert "REMOVE" not in statement

    def test_it_only_looks_at_contracts_without_a_matter(self):
        """So re-running after a partial failure is safe."""
        repository = repo([])
        repository.migrate_legacy_contracts()

        assert "NOT (:Matter)-[:HAS_VERSION]->(c)" in repository.graph.statements[0]

    def test_references_are_allocated_oldest_first(self):
        repository = repo([])
        repository.migrate_legacy_contracts()

        assert "ORDER BY c.upload_date" in repository.graph.statements[0]

    def test_check_mode_writes_nothing(self):
        repository = repo(self.LEGACY)

        result = repository.migrate_legacy_contracts(dry_run=True)

        assert result["migrated"] == 0
        assert result["planned"][0]["title"] == "Master Services Agreement — Acme Corporation"
        assert len(repository.graph.statements) == 1, "a check must only read"

    def test_a_migrated_contract_matches_its_own_re_upload(self):
        """The hash is taken over the text the analysis has always read.

        Otherwise re-uploading a contract that predates matters would fork a
        second one beside it — exactly the bug this increment closes.
        """
        repository = repo(self.LEGACY)
        planned = repository.migrate_legacy_contracts(dry_run=True)["planned"][0]

        assert planned["source_sha256"] == source_sha256("Payment within ninety (90) days.")

    def test_one_bad_contract_does_not_stop_the_rest(self):
        repository = repo(
            self.LEGACY + [{**self.LEGACY[0], "version_id": "UPLOADED_BBB_20260802"}],
            RuntimeError("node is locked"),             # first record_version blows up
            [{"version_id": "UPLOADED_BBB_20260802"}],  # second: record_version
            [{"version_id": "UPLOADED_BBB_20260802"}],  # unfiled check
            [{"n": 1}],
            [{"matter_ref": "MSA-2026-0001"}],
        )

        result = repository.migrate_legacy_contracts()

        assert result["migrated"] == 1
        assert result["failed"][0]["version_id"] == "UPLOADED_AAA_20260801"
