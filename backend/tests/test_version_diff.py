"""What changed since last round.

The question this whole system exists to answer, and the payoff for Increment 7
giving chunks identities: a diff over hash lists rather than over text.

Everything interesting is in the interpretation, not the alignment. A `replace`
block is a clause *rewritten* or a clause *swapped for a different one*, and one
number decides which — get it wrong and "they softened the indemnity" becomes
"they deleted the indemnity and added something unrelated". A chunk that leaves
one place and appears in another has **moved**, and reporting that as a deletion
plus an insertion buries a real negotiation signal under two entries that each
look like something else.

Offline: similarity is supplied by the caller, so no database and no vectors.
"""
import difflib
from dataclasses import dataclass

import pytest

from backend.domain.chunking import ChunkingProfile
from backend.domain.version_diff import (
    MODIFIED_THRESHOLD,
    ChangeKind,
    cosine,
    diff_versions,
    profiles_comparable,
)


@dataclass
class Chunk:
    hash: str
    order: int
    heading: str = ""


def version(*hashes) -> list:
    return [Chunk(h, i) for i, h in enumerate(hashes)]


def kinds(report) -> list:
    return [(c.kind.value, c.from_hash or c.to_hash) for c in report.changes]


class TestNothingChanged:
    def test_an_identical_round_reports_no_substantive_change(self):
        chunks = version("a", "b", "c")

        report = diff_versions(chunks, version("a", "b", "c"))

        assert not report.has_substantive_changes
        assert report.summary["unchanged"] == 3
        assert report.changed_hashes == set()

    def test_and_every_chunk_is_reusable(self):
        """Which is what makes the re-analysis incremental."""
        report = diff_versions(version("a", "b", "c"), version("a", "b", "c"))

        assert report.unchanged_hashes == {"a", "b", "c"}


class TestAClauseRewritten:
    def _similar(self, score):
        return lambda old, new: score

    def test_close_enough_is_one_modification(self):
        report = diff_versions(version("a", "b", "c"), version("a", "b2", "c"),
                               similarity=self._similar(0.95))

        assert kinds(report) == [("UNCHANGED", "a"), ("MODIFIED", "b"), ("UNCHANGED", "c")]

    def test_it_carries_the_similarity_so_the_reviewer_can_judge(self):
        report = diff_versions(version("b"), version("b2"),
                               similarity=self._similar(0.913))

        assert report.changes[0].similarity == 0.913

    def test_not_close_enough_is_a_removal_and_an_addition(self):
        """Two different clauses, and the reviewer should see both texts."""
        report = diff_versions(version("a", "b", "c"), version("a", "z", "c"),
                               similarity=self._similar(0.2))

        assert kinds(report) == [
            ("UNCHANGED", "a"), ("REMOVED", "b"), ("ADDED", "z"), ("UNCHANGED", "c"),
        ]

    def test_the_threshold_is_where_it_says_it_is(self):
        just_over = diff_versions(version("b"), version("b2"),
                                  similarity=self._similar(MODIFIED_THRESHOLD))
        just_under = diff_versions(version("b"), version("b2"),
                                   similarity=self._similar(MODIFIED_THRESHOLD - 0.01))

        assert just_over.changes[0].kind is ChangeKind.MODIFIED
        assert just_under.changes[0].kind is ChangeKind.REMOVED

    def test_with_no_similarity_measured_it_shows_both_texts(self):
        """The safe reading. Claiming a link nothing checked is worse than
        showing the reviewer two entries."""
        report = diff_versions(version("b"), version("b2"), similarity=None)

        assert [c.kind for c in report.changes] == [ChangeKind.REMOVED, ChangeKind.ADDED]

    def test_a_chunk_with_no_vector_is_treated_the_same_way(self):
        report = diff_versions(version("b"), version("b2"),
                               similarity=lambda old, new: None)

        assert [c.kind for c in report.changes] == [ChangeKind.REMOVED, ChangeKind.ADDED]


class TestAClauseThatMoved:
    """Identical text in a new position. A relocated indemnity clause is a real
    negotiation signal, and two entries that each look like something else hide
    it."""

    def test_a_relocation_is_one_entry_not_two(self):
        report = diff_versions(version("a", "indemnity", "c", "d"),
                               version("a", "c", "d", "indemnity"))

        moves = report.of_kind(ChangeKind.MOVED)
        assert len(moves) == 1
        assert not report.of_kind(ChangeKind.REMOVED)
        assert not report.of_kind(ChangeKind.ADDED)

    def test_it_records_where_it_went(self):
        report = diff_versions(version("a", "indemnity", "c", "d"),
                               version("a", "c", "d", "indemnity"))

        move = report.of_kind(ChangeKind.MOVED)[0]
        assert move.from_order == 1
        assert move.to_order == 3

    def test_a_move_is_not_something_to_re_read(self):
        """The words are identical; only the position changed."""
        report = diff_versions(version("a", "indemnity", "c"),
                               version("indemnity", "a", "c"))

        assert all(not c.is_substantive for c in report.of_kind(ChangeKind.MOVED))

    def test_a_genuine_deletion_is_still_a_deletion(self):
        report = diff_versions(version("a", "b", "c"), version("a", "c"))

        assert kinds(report) == [("UNCHANGED", "a"), ("REMOVED", "b"), ("UNCHANGED", "c")]

    def test_a_genuine_insertion_is_still_an_insertion(self):
        report = diff_versions(version("a", "c"), version("a", "b", "c"))

        assert kinds(report) == [("UNCHANGED", "a"), ("ADDED", "b"), ("UNCHANGED", "c")]


class TestTheAutojunkTrap:
    """`difflib` discards any element appearing in more than 1% of a sequence
    longer than 200 — and on a long contract the repeated boilerplate is exactly
    what the alignment needs to anchor on.

    Measured on a 301-chunk document that is mostly one repeated clause with a
    single real edit: the default aligns 250 of 301 and reports the other 50 as
    changed. Fifty spurious changes in the report, and fifty unnecessary
    re-analyses behind it.
    """

    OLD = ["boiler"] * 250 + ["payment-90"] + ["boiler"] * 50
    NEW = ["boiler"] * 250 + ["payment-30"] + ["boiler"] * 50

    def test_the_default_would_misalign_a_long_contract(self):
        aligned = sum(
            i2 - i1 for tag, i1, i2, _, _
            in difflib.SequenceMatcher(None, self.OLD, self.NEW).get_opcodes()
            if tag == "equal"
        )

        assert aligned < len(self.OLD) - 1, (
            "this fixture no longer demonstrates the trap; find one that does"
        )

    def test_the_diff_turns_it_off(self):
        report = diff_versions(version(*self.OLD), version(*self.NEW),
                               similarity=lambda o, n: 0.9)

        # One real edit. Everything else is the same clause in the same place.
        assert report.summary["unchanged"] == 300
        assert report.summary["modified"] == 1

    def test_and_only_the_edited_chunk_needs_re_analysing(self):
        report = diff_versions(version(*self.OLD), version(*self.NEW),
                               similarity=lambda o, n: 0.9)

        assert report.changed_hashes == {"payment-30"}


class TestRefusingToDiffTheIncomparable:
    """Chunk identity means something only within one set of rules. Two versions
    chunked differently produce unrelated hashes, and a diff over them reports
    the whole contract as replaced — a confident, detailed, completely false
    answer, which is worse than refusing."""

    def test_the_same_profile_compares(self):
        assert profiles_comparable(ChunkingProfile(), ChunkingProfile())[0]

    @pytest.mark.parametrize("field,value", [
        ("strategy", "paragraph"),
        ("max_chunk_size", 4000),
        ("min_chunk_size", 100),
        ("normaliser_version", 99),
        ("chunker_version", 99),
    ])
    def test_a_different_rule_does_not(self, field, value):
        other = ChunkingProfile(**{field: value})

        comparable, reason = profiles_comparable(ChunkingProfile(), other)

        assert not comparable
        assert reason, "a refusal has to say why"

    def test_a_different_extractor_still_compares(self):
        """It changes the text and so the hashes, but the result is an honestly
        noisy diff rather than a meaningless one — and refusing to diff because
        the PDF library changed would help nobody."""
        comparable, _ = profiles_comparable(
            ChunkingProfile(extractor="pypdf"), ChunkingProfile(extractor="pdfplumber"))

        assert comparable

    def test_nothing_recorded_compares(self):
        """Anything uploaded before Increment 7 has no profile to contradict."""
        assert profiles_comparable(None, ChunkingProfile())[0]
        assert profiles_comparable(ChunkingProfile(), None)[0]

    def test_the_report_says_so_rather_than_inventing_changes(self):
        report = diff_versions(
            version("a", "b"), version("x", "y"),
            previous_profile=ChunkingProfile(),
            current_profile=ChunkingProfile(chunker_version=99),
        )

        assert report.comparable is False
        assert report.changes == []
        assert "cannot be compared" in report.incomparable_reason

    def test_but_it_still_says_everything_needs_analysing(self):
        """Nothing can be carried forward from an incomparable round."""
        report = diff_versions(
            version("a", "b"), version("x", "y"),
            previous_profile=ChunkingProfile(),
            current_profile=ChunkingProfile(chunker_version=99),
        )

        assert report.changed_hashes == {"x", "y"}
        assert report.unchanged_hashes == set()


class TestTheFirstRound:
    def test_everything_is_an_addition(self):
        report = diff_versions([], version("a", "b"))

        assert [c.kind for c in report.changes] == [ChangeKind.ADDED, ChangeKind.ADDED]

    def test_an_emptied_version_is_all_removals(self):
        report = diff_versions(version("a", "b"), [])

        assert [c.kind for c in report.changes] == [ChangeKind.REMOVED, ChangeKind.REMOVED]

    def test_two_empty_versions_are_no_changes(self):
        assert diff_versions([], []).changes == []


class TestCosine:
    def test_identical_vectors_are_one(self):
        assert cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors_are_zero(self):
        assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_scale_does_not_matter(self):
        assert cosine([1.0, 2.0], [2.0, 4.0]) == pytest.approx(1.0)

    @pytest.mark.parametrize("left,right", [
        ([], [1.0]), ([1.0], []), ([1.0, 2.0], [1.0]), ([0.0, 0.0], [1.0, 1.0]),
    ])
    def test_anything_unusable_is_none_rather_than_a_number(self, left, right):
        """A missing vector must not read as "completely different"."""
        assert cosine(left, right) is None
