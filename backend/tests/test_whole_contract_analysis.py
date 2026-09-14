"""Analysing the whole contract, not its first 12,000 characters.

`ClauseDetectorTool` sent `contract_text[:12000]` to the model and reported the
result as a completed review. On the contracts in `data/` that meant 96% of the
Shell MESA, 83% of the Salesforce MSA and 64% of the sample shuttle contract
were never looked at — and every clause finding, policy check, risk score and
redline on a long contract came from its first few pages.

`make eval` could not see any of it, because all three evaluation fixtures fit
inside the window. That is what Increment 5's "1.00 says the fixtures are not
yet hard enough" was pointing at.

Offline: the model is a stub that records what it was asked.
"""
import json

import pytest

from backend.agents.intelligence_tools import (
    CLAUSE_EXTRACTION_CONCURRENCY,
    POLICY_CHECK_BATCH,
    ClauseDetectorTool,
    PolicyCheckerTool,
)
from backend.domain.chunking import WINDOW_BUDGET_CHARS, pack_windows
from backend.infrastructure.chunking.identity import identify_chunks


def contract(sections: int = 40, marker_at: int = None, marker: str = "") -> str:
    """A contract long enough to need several windows.

    `marker` is planted in a late section so a test can prove the model was
    shown text the old code would have discarded.
    """
    out = []
    for n in range(1, sections + 1):
        body = (f"Section {n} sets out the obligations of the parties in respect of "
                f"matter number {n}, in terms agreed between them. " * 6)
        if marker_at == n:
            body += marker
        out.append(f"{n}. OBLIGATION {n}. {body}")
    return "\n\n".join(out)


class RecordingLLM:
    """A model that records every prompt and answers with canned clauses."""

    def __init__(self, clauses_for=None, fail_on=None):
        self.prompts = []
        self._clauses_for = clauses_for or (lambda prompt: [])
        self._fail_on = fail_on or (lambda prompt: False)

    def invoke(self, prompt):
        self.prompts.append(prompt)
        if self._fail_on(prompt):
            raise RuntimeError("the provider refused this one")

        clauses = self._clauses_for(prompt)
        return type("Response", (), {"content": json.dumps({"clauses": clauses})})()


def clause(span, clause_type="Payment Terms", risk="HIGH"):
    return {
        "clause_type": clause_type,
        "evidence_span": span,
        "risk_level": risk,
        "confidence": 0.9,
        "location": "",
        "violated_policy": None,
        "suggested_redline": None,
        "human_review_required": True,
    }


class TestTheWholeDocumentReachesTheModel:
    def test_a_long_contract_is_analysed_in_windows(self):
        text = contract(sections=40)
        llm = RecordingLLM()

        ClauseDetectorTool(llm=llm)._run(text)

        assert len(text) > WINDOW_BUDGET_CHARS, "the fixture is too short to test this"
        assert len(llm.prompts) > 1, "still one call — the tail is being discarded"

    def test_text_beyond_the_old_window_is_actually_sent(self):
        """The regression guard. If anyone reinstates the truncation, the marker
        stops arriving and this fails immediately."""
        marker = "THE COUNTERPARTY MAY TERMINATE FOR CONVENIENCE ON SEVEN DAYS NOTICE."
        text = contract(sections=40, marker_at=38, marker=marker)
        assert text.index(marker) > WINDOW_BUDGET_CHARS, "plant the marker later"
        llm = RecordingLLM()

        ClauseDetectorTool(llm=llm)._run(text)

        assert any(marker in prompt for prompt in llm.prompts), (
            "text past the first window never reached the model"
        )

    def test_every_window_is_covered(self):
        text = contract(sections=40)
        llm = RecordingLLM()

        ClauseDetectorTool(llm=llm)._run(text)

        expected = len(pack_windows(identify_chunks(text).chunks))
        assert len(llm.prompts) == expected

    def test_a_short_contract_still_takes_one_call(self):
        """No new cost for the documents that always fitted."""
        llm = RecordingLLM()

        ClauseDetectorTool(llm=llm)._run(contract(sections=3))

        assert len(llm.prompts) == 1

    def test_an_empty_contract_calls_nothing(self):
        llm = RecordingLLM()

        assert json.loads(ClauseDetectorTool(llm=llm)._run("   ")) == []
        assert llm.prompts == []


class TestWindowsAreWholeChunks:
    """Not a tidiness preference: because a window is a set of chunk identities,
    every finding maps back to the chunks it came from — which is what makes
    Increment 9's incremental re-analysis possible."""

    def test_no_chunk_is_split_across_windows(self):
        chunks = identify_chunks(contract(sections=40)).chunks
        windows = pack_windows(chunks)

        packed = [h for w in windows for h in w.chunk_hashes]
        assert packed == [c.hash for c in chunks], "a chunk was split or reordered"

    def test_every_chunk_lands_in_exactly_one_window(self):
        chunks = identify_chunks(contract(sections=40)).chunks
        windows = pack_windows(chunks)

        packed = [h for w in windows for h in w.chunk_hashes]
        assert len(packed) == len(set(packed)) == len(chunks)

    def test_windows_respect_the_budget(self):
        windows = pack_windows(identify_chunks(contract(sections=40)).chunks)

        assert all(w.size <= WINDOW_BUDGET_CHARS for w in windows)

    def test_a_chunk_larger_than_the_budget_gets_its_own_window(self):
        """Splitting it would cost the chunk-to-finding mapping for the sake of
        a limit the model will usually tolerate."""
        huge = "1. ENORMOUS. " + ("text " * 5000)
        chunks = identify_chunks(huge).chunks

        windows = pack_windows(chunks, budget=1000)

        assert len(windows) == len(chunks)
        assert all(len(w.chunk_hashes) == 1 for w in windows)

    def test_windows_are_numbered_in_reading_order(self):
        windows = pack_windows(identify_chunks(contract(sections=40)).chunks)

        assert [w.index for w in windows] == list(range(len(windows)))
        assert [w.first_order for w in windows] == sorted(w.first_order for w in windows)

    def test_nothing_to_pack_is_no_windows(self):
        assert pack_windows([]) == []


class TestFindingsAreMergedAcrossWindows:
    """Duplicate *reports* collapse; duplicate *occurrences* do not.

    Identity is the clause type, the canonical evidence span, and the chunk the
    span sits in. Keying on text alone silently dropped the second of two
    legitimately repeated provisions — and because the policy layer is
    index-based precisely so duplicate text can be told apart, dropping one
    could take a real violation with it.
    """

    def test_the_same_occurrence_reported_twice_appears_once(self):
        finding = _finding("Payment Terms", "Net thirty (30) days.")

        merged = ClauseDetectorTool._merge_findings(
            [[finding], [finding]],
            [[(0, "chunk-a")], [(1, "chunk-a")]],   # the same chunk
        )

        assert len(merged) == 1

    def test_the_same_wording_in_two_places_is_two_findings(self):
        """A contract can legitimately carry the same notice provision in two
        schedules, and a reviewer has to see both."""
        finding = _finding("Payment Terms", "Net thirty (30) days.")

        merged = ClauseDetectorTool._merge_findings(
            [[finding], [finding]],
            [[(0, "chunk-a")], [(1, "chunk-b")]],   # different chunks
        )

        assert len(merged) == 2

    def test_whitespace_and_case_do_not_make_a_second_finding(self):
        """The same normalisation chunk identity uses."""
        merged = ClauseDetectorTool._merge_findings(
            [[_finding("Payment Terms", "Net thirty (30) days.")],
             [_finding("payment terms", "NET  THIRTY (30)" + chr(10) + "DAYS.")]],
            [[(0, "chunk-a")], [(1, "chunk-a")]],
        )

        assert len(merged) == 1

    def test_genuinely_different_clauses_both_survive(self):
        merged = ClauseDetectorTool._merge_findings(
            [[_finding("Payment Terms", "Payment is due in thirty (30) days.")],
             [_finding("Liability", "Liability is capped at the fees paid.")]],
            [[(0, "chunk-a")], [(1, "chunk-b")]],
        )

        assert len(merged) == 2

    def test_the_same_text_under_two_clause_types_is_two_findings(self):
        span = "Each party shall indemnify the other and keep information secret."
        merged = ClauseDetectorTool._merge_findings(
            [[_finding("Indemnification", span)], [_finding("Confidentiality", span)]],
            [[(0, "chunk-a")], [(1, "chunk-a")]],
        )

        assert len(merged) == 2

    def test_the_earlier_window_wins(self):
        """So a clause is attributed to where it starts."""
        first = _finding("Payment Terms", "Net thirty (30) days.", location="Section 2")
        later = _finding("Payment Terms", "Net thirty (30) days.", location="Section 9")

        merged = ClauseDetectorTool._merge_findings(
            [[first], [later]], [[(0, "chunk-a")], [(1, "chunk-a")]])

        assert merged[0][0].location == "Section 2"
        assert merged[0][1] == (0, "chunk-a")


def _finding(clause_type, span, location=""):
    from backend.shared.models.clause_finding import ClauseFinding

    return ClauseFinding(clause_type=clause_type, evidence_span=span,
                         risk_level="HIGH", confidence=0.9, location=location)


class TestAFindingRemembersWhereItCameFrom:
    """Increment 9 re-analyses only the windows whose chunks changed. Without
    the chunk on each finding, selecting the affected ones means matching text —
    ambiguous exactly where it matters, on wording that repeats."""

    def test_every_finding_carries_its_chunk_and_window(self):
        text = contract(sections=40)
        span = "1. OBLIGATION 1."
        llm = RecordingLLM(clauses_for=lambda p: [clause(span)] if span in p else [])

        result = json.loads(ClauseDetectorTool(llm=llm)._run(text))

        assert result
        assert result[0]["source_chunk"], "no chunk identity on the finding"
        assert result[0]["source_window"] == 0

    def test_the_chunk_is_one_the_document_actually_has(self):
        text = contract(sections=40)
        span = "1. OBLIGATION 1."
        llm = RecordingLLM(clauses_for=lambda p: [clause(span)] if span in p else [])

        result = json.loads(ClauseDetectorTool(llm=llm)._run(text))

        hashes = {c.hash for c in identify_chunks(text).chunks}
        assert result[0]["source_chunk"] in hashes


class TestTheVersionsOwnChunkingIsUsed:
    """A matter whose first round was chunked with different sizes has stored
    chunks a fresh default profile cannot reproduce — and then every finding's
    chunk attribution points at boundaries that exist nowhere but here."""

    #: One section long enough that `max_chunk_size` decides where it splits,
    #: which is the only thing the profile actually changes.
    LONG_SECTION = "1. OBLIGATIONS.\n\n" + "\n\n".join(
        f"Paragraph {n} of the obligations, stated at some length so that the "
        f"section as a whole comfortably exceeds the smaller chunk size." 
        for n in range(40)
    )

    def test_the_profile_reaches_the_chunker(self):
        from backend.domain.chunking import ChunkingProfile

        small = identify_chunks(self.LONG_SECTION, ChunkingProfile(max_chunk_size=600))
        large = identify_chunks(self.LONG_SECTION, ChunkingProfile(max_chunk_size=9000))
        assert small.hashes != large.hashes, "the fixture does not exercise the profile"

        llm = RecordingLLM(clauses_for=lambda p: [])
        ClauseDetectorTool(
            llm=llm, chunking_profile=ChunkingProfile(max_chunk_size=600),
        )._run(self.LONG_SECTION)

        # The prompts are the windows, and the windows are built from the
        # profile's chunks.
        assert len(llm.prompts) == len(
            pack_windows(small.chunks)), "a different profile's chunks were used"

    def test_findings_land_on_chunks_the_profile_produces(self):
        from backend.domain.chunking import ChunkingProfile

        profile = ChunkingProfile(max_chunk_size=600)
        text = self.LONG_SECTION
        span = "Paragraph 0 of the obligations"
        llm = RecordingLLM(clauses_for=lambda p: [clause(span)] if span in p else [])

        result = json.loads(
            ClauseDetectorTool(llm=llm, chunking_profile=profile)._run(text))

        hashes = {c.hash for c in identify_chunks(text, profile).chunks}
        assert result[0]["source_chunk"] in hashes


class TestPartialCoverageIsVisibleNotJustLogged:
    """A review covering 29 of 30 windows must not be persisted and rendered as
    a complete one — the failure existing only in a log line is exactly how a
    degraded run gets read as a clean contract."""

    def test_findings_are_marked_when_a_window_failed(self):
        text = contract(sections=40)
        span = "1. OBLIGATION 1."
        llm = RecordingLLM(
            clauses_for=lambda p: [clause(span)] if span in p else [],
            fail_on=lambda p: "OBLIGATION 39." in p,
        )

        result = json.loads(ClauseDetectorTool(llm=llm)._run(text))

        assert result
        assert all(c.get("coverage_incomplete") for c in result)

    def test_a_complete_run_is_not_marked(self):
        text = contract(sections=40)
        span = "1. OBLIGATION 1."
        llm = RecordingLLM(clauses_for=lambda p: [clause(span)] if span in p else [])

        result = json.loads(ClauseDetectorTool(llm=llm)._run(text))

        assert result
        assert not any(c.get("coverage_incomplete") for c in result)


class TestGroundingIsPerWindow:
    def test_a_finding_must_appear_in_its_own_window(self):
        """Checking against the whole contract would let a span invented for
        window 3 pass because similar words happen to appear in window 17."""
        llm = RecordingLLM(
            clauses_for=lambda p: [clause("This sentence is in no window at all.")])

        result = json.loads(ClauseDetectorTool(llm=llm)._run(contract(sections=40)))

        assert result == []

    def test_a_grounded_finding_survives(self):
        text = contract(sections=40)
        real = "1. OBLIGATION 1."
        llm = RecordingLLM(clauses_for=lambda p: [clause(real)] if real in p else [])

        result = json.loads(ClauseDetectorTool(llm=llm)._run(text))

        assert len(result) == 1


class TestOneBadWindowDoesNotDiscardTheRest:
    def test_the_surviving_windows_still_report(self):
        text = contract(sections=40)
        marker = "OBLIGATION 2."
        llm = RecordingLLM(
            clauses_for=lambda p: [clause(marker)] if marker in p else [],
            fail_on=lambda p: "OBLIGATION 39." in p,
        )

        result = json.loads(ClauseDetectorTool(llm=llm)._run(text))

        assert len(result) == 1, "one failed window discarded the others"

    def test_but_every_window_failing_is_an_error_not_an_empty_review(self):
        """"The model was unavailable" and "this contract has no notable
        clauses" are different reports, and conflating them is how the original
        hardcoded stub went unnoticed."""
        llm = RecordingLLM(fail_on=lambda p: True)

        with pytest.raises(RuntimeError, match="all"):
            ClauseDetectorTool(llm=llm)._run(contract(sections=40))


class TestTheCallsAreBounded:
    def test_concurrency_is_capped(self):
        """Unbounded, the Shell MESA's 30 windows would be 30 calls at once and
        the provider would start refusing them."""
        assert 1 < CLAUSE_EXTRACTION_CONCURRENCY <= 8


class TestPolicyCheckingIsBatchedToo:
    def _rule(self):
        from backend.infrastructure.playbook_loader import LoadedRule

        return LoadedRule(id="PAY-001", rule_text="Payment within 30 days.",
                          rule_type="mandatory", applies_to=["general"],
                          severity="CRITICAL", section_reference="Payment",
                          redline_text="Net 30.")

    def _clauses(self, n):
        return [{"clause_type": "Payment Terms", "evidence_span": f"clause {i} text"}
                for i in range(n)]

    def test_a_long_contracts_clauses_are_split_across_calls(self):
        """An overlong prompt is truncated by the provider rather than refused,
        so the last clauses would go unchecked while the report called them
        compliant."""
        llm = RecordingLLM(clauses_for=lambda p: [])
        llm.invoke = lambda prompt: (
            llm.prompts.append(prompt),
            type("R", (), {"content": json.dumps({"violations": []})})(),
        )[1]

        PolicyCheckerTool(llm=llm, rules=[self._rule()])._run(
            json.dumps(self._clauses(POLICY_CHECK_BATCH * 3)))

        assert len(llm.prompts) == 3

    def test_clause_numbering_stays_global_across_batches(self):
        """Without the offset, every batch after the first would cite breaches
        against clauses 0..n of the whole contract — the wrong text against the
        wrong rule, which is worse than missing the breach."""
        llm = RecordingLLM()
        llm.invoke = lambda prompt: (
            llm.prompts.append(prompt),
            type("R", (), {"content": json.dumps({"violations": []})})(),
        )[1]

        PolicyCheckerTool(llm=llm, rules=[self._rule()])._run(
            json.dumps(self._clauses(POLICY_CHECK_BATCH * 2)))

        assert f"clause {POLICY_CHECK_BATCH} " in llm.prompts[1], (
            "the second batch restarted its numbering at zero"
        )
        assert "clause 0 " not in llm.prompts[1]

    def test_a_short_contract_still_takes_one_call(self):
        llm = RecordingLLM()
        llm.invoke = lambda prompt: (
            llm.prompts.append(prompt),
            type("R", (), {"content": json.dumps({"violations": []})})(),
        )[1]

        PolicyCheckerTool(llm=llm, rules=[self._rule()])._run(json.dumps(self._clauses(3)))

        assert len(llm.prompts) == 1
