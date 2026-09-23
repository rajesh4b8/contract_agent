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
    ClauseExtractionFailed,
    PolicyCheckerTool,
    parse_clause_result,
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

        assert parse_clause_result(ClauseDetectorTool(llm=llm)._run("   "))[0] == []
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
            [[(0, "chunk-a", 0)], [(1, "chunk-a", 0)]],   # the same chunk
        )

        assert len(merged) == 1

    def test_the_same_wording_in_two_places_is_two_findings(self):
        """A contract can legitimately carry the same notice provision in two
        schedules, and a reviewer has to see both.

        Identical text is **one** `(:Chunk)` node with two `INCLUDES {order}`
        relationships, so the two occurrences share a hash and are told apart
        only by position — which is why the order is part of the key.
        """
        finding = _finding("Payment Terms", "Net thirty (30) days.")

        merged = ClauseDetectorTool._merge_findings(
            [[finding], [finding]],
            [[(0, "chunk-a", 4)], [(1, "chunk-a", 19)]],   # same hash, two places
        )

        assert len(merged) == 2, "two occurrences of one paragraph collapsed into one"

    def test_whitespace_and_case_do_not_make_a_second_finding(self):
        """The same normalisation chunk identity uses."""
        merged = ClauseDetectorTool._merge_findings(
            [[_finding("Payment Terms", "Net thirty (30) days.")],
             [_finding("payment terms", "NET  THIRTY (30)" + chr(10) + "DAYS.")]],
            [[(0, "chunk-a", 0)], [(1, "chunk-a", 0)]],
        )

        assert len(merged) == 1

    def test_genuinely_different_clauses_both_survive(self):
        merged = ClauseDetectorTool._merge_findings(
            [[_finding("Payment Terms", "Payment is due in thirty (30) days.")],
             [_finding("Liability", "Liability is capped at the fees paid.")]],
            [[(0, "chunk-a", 0)], [(1, "chunk-b", 1)]],
        )

        assert len(merged) == 2

    def test_the_same_text_under_two_clause_types_is_two_findings(self):
        span = "Each party shall indemnify the other and keep information secret."
        merged = ClauseDetectorTool._merge_findings(
            [[_finding("Indemnification", span)], [_finding("Confidentiality", span)]],
            [[(0, "chunk-a", 0)], [(1, "chunk-a", 0)]],
        )

        assert len(merged) == 2

    def test_the_earlier_window_wins(self):
        """So a clause is attributed to where it starts."""
        first = _finding("Payment Terms", "Net thirty (30) days.", location="Section 2")
        later = _finding("Payment Terms", "Net thirty (30) days.", location="Section 9")

        merged = ClauseDetectorTool._merge_findings(
            [[first], [later]], [[(0, "chunk-a", 0)], [(1, "chunk-a", 0)]])

        assert merged[0][0].location == "Section 2"
        assert merged[0][1] == (0, "chunk-a", 0)


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

        result = parse_clause_result(ClauseDetectorTool(llm=llm)._run(text))[0]

        assert result
        assert result[0]["source_chunk"], "no chunk identity on the finding"
        assert result[0]["source_window"] == 0

    def test_the_chunk_is_one_the_document_actually_has(self):
        text = contract(sections=40)
        span = "1. OBLIGATION 1."
        llm = RecordingLLM(clauses_for=lambda p: [clause(span)] if span in p else [])

        result = parse_clause_result(ClauseDetectorTool(llm=llm)._run(text))[0]

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

        result = parse_clause_result(
            ClauseDetectorTool(llm=llm, chunking_profile=profile)._run(text))[0]

        hashes = {c.hash for c in identify_chunks(text, profile).chunks}
        assert result[0]["source_chunk"] in hashes


class TestPartialCoverageIsVisibleNotJustLogged:
    """A review covering 29 of 30 windows must not be persisted and rendered as
    a complete one — the failure existing only in a log line is exactly how a
    degraded run gets read as a clean contract."""

    def test_coverage_is_reported_beside_the_findings(self):
        text = contract(sections=40)
        span = "1. OBLIGATION 1."
        llm = RecordingLLM(
            clauses_for=lambda p: [clause(span)] if span in p else [],
            fail_on=lambda p: "OBLIGATION 39." in p,
        )

        _clauses, coverage = parse_clause_result(ClauseDetectorTool(llm=llm)._run(text))

        assert coverage["complete"] is False
        assert coverage["failed"] == 1

    def test_a_failed_window_is_visible_even_with_no_findings_at_all(self):
        """The case that made marking each finding useless: one window fails,
        every other returns nothing, and an empty list with no marker anywhere
        is indistinguishable from a clean contract."""
        text = contract(sections=40)
        llm = RecordingLLM(clauses_for=lambda p: [],
                           fail_on=lambda p: "OBLIGATION 39." in p)

        clauses, coverage = parse_clause_result(ClauseDetectorTool(llm=llm)._run(text))

        assert clauses == []
        assert coverage["complete"] is False

    def test_a_complete_run_says_so(self):
        text = contract(sections=40)
        span = "1. OBLIGATION 1."
        llm = RecordingLLM(clauses_for=lambda p: [clause(span)] if span in p else [])

        _clauses, coverage = parse_clause_result(ClauseDetectorTool(llm=llm)._run(text))

        assert coverage["complete"] is True
        assert coverage["failed"] == 0

    def test_an_older_caller_reading_a_bare_list_still_works(self):
        clauses, coverage = parse_clause_result(json.dumps([{"clause_type": "X"}]))

        assert len(clauses) == 1
        assert coverage["complete"] is True


class TestGroundingIsPerWindow:
    def test_a_finding_must_appear_in_its_own_window(self):
        """Checking against the whole contract would let a span invented for
        window 3 pass because similar words happen to appear in window 17."""
        llm = RecordingLLM(
            clauses_for=lambda p: [clause("This sentence is in no window at all.")])

        result = parse_clause_result(ClauseDetectorTool(llm=llm)._run(contract(sections=40)))[0]

        assert result == []

    def test_a_grounded_finding_survives(self):
        text = contract(sections=40)
        real = "1. OBLIGATION 1."
        llm = RecordingLLM(clauses_for=lambda p: [clause(real)] if real in p else [])

        result = parse_clause_result(ClauseDetectorTool(llm=llm)._run(text))[0]

        assert len(result) == 1


class TestOneBadWindowDoesNotDiscardTheRest:
    def test_the_surviving_windows_still_report(self):
        text = contract(sections=40)
        marker = "OBLIGATION 2."
        llm = RecordingLLM(
            clauses_for=lambda p: [clause(marker)] if marker in p else [],
            fail_on=lambda p: "OBLIGATION 39." in p,
        )

        result = parse_clause_result(ClauseDetectorTool(llm=llm)._run(text))[0]

        assert len(result) == 1, "one failed window discarded the others"

    def test_but_every_window_failing_is_an_error_not_an_empty_review(self):
        """"The model was unavailable" and "this contract has no notable
        clauses" are different reports, and conflating them is how the original
        hardcoded stub went unnoticed."""
        llm = RecordingLLM(fail_on=lambda p: True)

        with pytest.raises(ClauseExtractionFailed, match="all"):
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


# ==========================================================================
# Second review round: coverage, provenance and failure propagation
# ==========================================================================

class TestAnExtractionThatNeverRanIsNotAnEmptyReview:
    """The generic `RuntimeError` was caught by the orchestrator, turned into an
    empty clause list, and then persisted over a perfectly good previous review.
    "The model was unavailable" and "this contract has no notable clauses" have
    to stay distinguishable all the way to storage."""

    def test_every_window_failing_raises_its_own_type(self):
        llm = RecordingLLM(fail_on=lambda p: True)

        with pytest.raises(ClauseExtractionFailed):
            ClauseDetectorTool(llm=llm)._run(contract(sections=40))

    def test_the_orchestrator_marks_the_result_unextractable(self):
        from backend.agents.contract_intelligence_agents import IntelligenceOrchestrator

        orchestrator = IntelligenceOrchestrator.__new__(IntelligenceOrchestrator)
        orchestrator.llm = RecordingLLM(fail_on=lambda p: True)
        orchestrator.model_id = "stub"

        state = orchestrator._extract_clauses({
            "contract_text": contract(sections=40), "chunking_profile": None,
        })

        assert state["extracted_clauses"] == []
        assert state.get("clause_extraction_failed"), (
            "an unrunnable extraction looks like a clean contract to storage"
        )

    def test_and_storage_then_leaves_the_previous_review_alone(self):
        """The Increment 6 guarantee, restated where Increment 8 could break it."""
        from unittest.mock import MagicMock

        from backend.application.services.contract_intelligence_service import (
            ContractIntelligenceService,
        )

        service = ContractIntelligenceService.__new__(ContractIntelligenceService)
        service.repository = MagicMock()
        service.repository.graph.query.return_value = []
        service.matters = MagicMock()

        intelligence = service._convert_to_domain_entities({
            "clauses": [], "violations": [], "redlines": [],
            "risk_assessment": {}, "clauses_extracted": False,
        })

        assert intelligence.clauses_extracted is False
        assert service._store_intelligence_results("C-1", "t", intelligence) is False
        assert service.repository.graph.query.call_count == 0


class TestCoverageSurvivesBothWorkflowsAndStorage:
    def test_the_traditional_path_reads_the_coverage_channel(self):
        import inspect

        from backend.agents import contract_intelligence_agents

        source = inspect.getsource(contract_intelligence_agents._extract_clauses
                                   if hasattr(contract_intelligence_agents, "_extract_clauses")
                                   else contract_intelligence_agents.IntelligenceOrchestrator
                                   ._extract_clauses)
        assert "parse_clause_result" in source
        assert 'coverage.get("complete"' in source

    def test_the_planning_path_reports_it_too(self):
        """Its warnings came only from failed *steps*, and a partial window run
        is a step that succeeded."""
        import inspect

        from backend.agents.planning import execution_engine

        source = inspect.getsource(execution_engine)
        assert "clause_extraction_coverage" in source
        assert "could not be analysed" in source

    def test_the_warning_is_persisted_with_the_review(self):
        """It lived only in the HTTP response, and the read-back rebuilt
        warnings from `analysis_error` alone — so a partial review was saved
        COMPLETE and reopened with nothing to say so."""
        import inspect

        from backend.application.services import contract_intelligence_service

        source = inspect.getsource(contract_intelligence_service)
        assert '"analysis_warnings": list(intelligence.warnings or [])' in source
        assert "c.analysis_warnings" in source


class TestProvenanceIdentifiesAnOccurrence:
    """`ChunkRepository` stores repeated identical text as **one** `(:Chunk)`
    with several `INCLUDES {order}` relationships, so the hash alone cannot tell
    two copies of a paragraph apart."""

    def test_a_finding_carries_the_membership_order(self):
        text = contract(sections=40)
        span = "1. OBLIGATION 1."
        llm = RecordingLLM(clauses_for=lambda p: [clause(span)] if span in p else [])

        result = parse_clause_result(ClauseDetectorTool(llm=llm)._run(text))[0]

        assert result[0]["source_chunk_order"] == 0

    def test_a_span_crossing_a_boundary_lands_where_it_starts(self):
        """Blindly taking the window's first chunk pointed at unrelated text,
        and Increment 9 would then miss the window when the real source chunk
        changed."""
        chunked = identify_chunks(contract(sections=40))
        windows = pack_windows(chunked.chunks)
        window = windows[0]

        tail = window.text[-60:]
        straddling = _finding("Payment Terms", tail + " and text that is not in it.")

        _hash, order = ClauseDetectorTool._source_chunk(chunked, window, straddling)

        assert order == window.last_order, (
            f"attributed to chunk {order}, not the one the span starts in "
            f"({window.last_order})"
        )

    def test_an_empty_span_still_gets_a_defensible_chunk(self):
        chunked = identify_chunks(contract(sections=40))
        window = pack_windows(chunked.chunks)[0]

        chunk_hash, order = ClauseDetectorTool._source_chunk(
            chunked, window, _finding("Payment Terms", ""))

        assert order == window.first_order
        assert chunk_hash


class TestTheStepBudgetScalesWithTheDocument:
    """The flat 30s was sized for a single truncated model call. Extraction is
    now one call per window, four at a time, so a 30-window contract needs about
    eight rounds — and the flat budget would time it out every time while
    reporting it as a step failure rather than what it is. Caught on a live run,
    not by a test, which is why there is one now."""

    def _step(self, step_type):
        from backend.agents.planning.planning_agent import ExecutionStep

        return ExecutionStep(
            step_id="s1", step_type=step_type, description="d",
            dependencies=[], timeout_seconds=30,
        )

    def _executor(self):
        from backend.agents.planning.execution_engine import StepExecutor

        return StepExecutor(llm=None, model_id="stub")

    def test_a_short_contract_keeps_the_planned_budget(self):
        from backend.agents.planning.planning_agent import StepType

        budget = self._executor()._step_budget(
            self._step(StepType.EXTRACT_CLAUSES),
            {"contract_text": contract(sections=2)},
        )

        assert budget >= 30

    def test_a_long_contract_gets_more(self):
        from backend.agents.planning.planning_agent import StepType

        short = self._executor()._step_budget(
            self._step(StepType.EXTRACT_CLAUSES),
            {"contract_text": contract(sections=2)})
        long = self._executor()._step_budget(
            self._step(StepType.EXTRACT_CLAUSES),
            {"contract_text": contract(sections=200)})

        assert long > short, "a twenty-window contract gets a one-call budget"

    def test_policy_checking_scales_with_the_clause_count(self):
        from backend.agents.planning.planning_agent import StepType

        few = self._executor()._step_budget(
            self._step(StepType.CHECK_POLICIES), {"extracted_clauses": [{}] * 3})
        many = self._executor()._step_budget(
            self._step(StepType.CHECK_POLICIES),
            {"extracted_clauses": [{}] * (POLICY_CHECK_BATCH * 6)})

        assert many > few

    def test_it_is_still_bounded(self):
        """However long the document, a step running this long is stuck rather
        than working."""
        from backend.agents.planning.execution_engine import MAX_STEP_BUDGET_SECONDS
        from backend.agents.planning.planning_agent import StepType

        budget = self._executor()._step_budget(
            self._step(StepType.EXTRACT_CLAUSES),
            {"contract_text": contract(sections=4000)})

        assert budget <= MAX_STEP_BUDGET_SECONDS

    def test_an_unrelated_step_is_untouched(self):
        from backend.agents.planning.planning_agent import StepType

        budget = self._executor()._step_budget(
            self._step(StepType.ASSESS_RISK), {"contract_text": contract(sections=200)})

        assert budget == 30


class TestThePlannedPathAlsoRefusesToPersistNothing:
    """The traditional orchestrator was fixed first; the planned path — which is
    the default — still reported an empty review as COMPLETE and persisted it
    over whatever was there before. Observed live, with extraction timed out and
    the version saved as a finished analysis."""

    def _engine(self):
        from backend.agents.planning.execution_engine import PlanExecutionEngine

        engine = PlanExecutionEngine(llm=None, model_id="stub")
        engine.execution_context = {}
        engine.step_failures = []
        return engine

    def test_a_failed_extraction_step_marks_the_result_unextractable(self):
        from backend.agents.planning.execution_engine import ExecutionResult
        from backend.agents.planning.planning_agent import ExecutionStep, StepType

        engine = self._engine()
        step = ExecutionStep(step_id="s1", step_type=StepType.EXTRACT_CLAUSES,
                             description="d", dependencies=[])
        engine.step_failures = [(step, ExecutionResult(
            step_id="s1", success=False, output_data=None,
            execution_time_ms=0, confidence_score=0.0,
            error_message="Step timed out after 90 seconds"))]

        result = engine._format_final_results()

        assert result["clauses_extracted"] is False

    def test_a_clean_run_is_extractable(self):
        assert self._engine()._format_final_results()["clauses_extracted"] is True

    def test_a_hard_failure_is_too(self):
        assert self._engine()._format_error_results("boom")["clauses_extracted"] is False


class TestOnlyChangedWindowsAreReAnalysed:
    """Increment 9's payoff, and the reason 7 and 8 were built the way they
    were: chunk identity makes "unchanged" decidable, per-finding provenance
    makes the carried findings addressable.

    Reuse is keyed on the **occurrence** — `(hash, order)` — not the hash. A
    contract can carry the same paragraph twice, and one of the two changing
    must not mark both as reusable.

    Skipping is not only cheaper. Re-running a model over identical text risks a
    slightly different answer, which on a change report reads as an edit the
    counterparty never made.
    """

    @staticmethod
    def keys(window):
        return {(h, window.first_order + i) for i, h in enumerate(window.chunk_hashes)}

    def _carried(self, window, count=2):
        return [
            {"clause_type": "Payment Terms", "evidence_span": f"finding {i}",
             "risk_level": "LOW", "source_chunk": h, "source_chunk_order": o,
             "source_window": window.index}
            for i, (h, o) in enumerate(sorted(self.keys(window))[:count])
        ]

    def test_an_unchanged_window_is_not_sent_to_the_model(self):
        text = contract(sections=40)
        windows = pack_windows(identify_chunks(text).chunks)
        llm = RecordingLLM()

        ClauseDetectorTool(llm=llm, unchanged_chunks=self.keys(windows[0]),
                           carried_findings=[])._run(text)

        assert len(llm.prompts) == len(windows) - 1

    def test_a_partly_changed_window_is_still_analysed(self):
        """One changed chunk makes the whole window worth re-reading."""
        text = contract(sections=40)
        windows = pack_windows(identify_chunks(text).chunks)
        all_but_one = set(sorted(self.keys(windows[0]))[:-1])
        llm = RecordingLLM()

        ClauseDetectorTool(llm=llm, unchanged_chunks=all_but_one,
                           carried_findings=[])._run(text)

        assert len(llm.prompts) == len(windows)

    def test_a_re_analysed_windows_findings_are_not_also_carried(self):
        """Filtering on "unchanged" alone carried the old findings for unchanged
        chunks *inside* a window the model had just re-analysed — so the same
        clause arrived twice, once carried and once freshly extracted, and the
        stale copy kept the previous round's position."""
        text = contract(sections=40)
        windows = pack_windows(identify_chunks(text).chunks)
        # Every chunk of window 1 is unchanged except one, so the window runs —
        # but its unchanged chunks are still in the reusable set.
        unchanged = self.keys(windows[0]) | set(sorted(self.keys(windows[1]))[:-1])
        carried = self._carried(windows[1])
        llm = RecordingLLM()

        clauses, _ = parse_clause_result(ClauseDetectorTool(
            llm=llm, unchanged_chunks=unchanged, carried_findings=carried,
        )._run(text))

        assert all(c.get("evidence_span") not in {"finding 0", "finding 1"}
                   for c in clauses), "a re-analysed window's old findings came too"

    def test_the_skipped_windows_findings_are_carried_forward(self):
        """Otherwise the result would describe only the part that moved."""
        text = contract(sections=40)
        windows = pack_windows(identify_chunks(text).chunks)
        carried = self._carried(windows[0])
        llm = RecordingLLM()

        clauses, _coverage = parse_clause_result(ClauseDetectorTool(
            llm=llm, unchanged_chunks=self.keys(windows[0]), carried_findings=carried,
        )._run(text))

        assert [c["evidence_span"] for c in clauses] == [c["evidence_span"] for c in carried]

    def test_nothing_changed_at_all_costs_no_model_call(self):
        text = contract(sections=40)
        windows = pack_windows(identify_chunks(text).chunks)
        everything = set().union(*(self.keys(w) for w in windows))
        carried = self._carried(windows[0])
        llm = RecordingLLM()

        clauses, coverage = parse_clause_result(ClauseDetectorTool(
            llm=llm, unchanged_chunks=everything, carried_findings=carried,
        )._run(text))

        assert llm.prompts == []
        assert clauses == carried
        assert coverage["complete"] is True

    def test_a_first_round_analyses_everything(self):
        text = contract(sections=40)
        windows = pack_windows(identify_chunks(text).chunks)
        llm = RecordingLLM()

        ClauseDetectorTool(llm=llm)._run(text)

        assert len(llm.prompts) == len(windows)

    def test_a_carried_finding_for_a_chunk_that_did_change_is_dropped(self):
        """It described text that is no longer there."""
        text = contract(sections=40)
        windows = pack_windows(identify_chunks(text).chunks)
        stale = [{"clause_type": "X", "evidence_span": "gone",
                  "source_chunk": "a-hash-this-version-does-not-have",
                  "source_chunk_order": 999}]
        llm = RecordingLLM()

        clauses, _ = parse_clause_result(ClauseDetectorTool(
            llm=llm, unchanged_chunks=self.keys(windows[0]), carried_findings=stale,
        )._run(text))

        assert all(c.get("evidence_span") != "gone" for c in clauses)

    def test_one_of_two_identical_paragraphs_changing_does_not_reuse_both(self):
        """They share a hash and are told apart only by position."""
        text = contract(sections=40)
        windows = pack_windows(identify_chunks(text).chunks)
        keys = sorted(self.keys(windows[0]))
        # The same hash at a different order is a different occurrence.
        pretend = {(keys[0][0], keys[0][1])}
        llm = RecordingLLM()

        ClauseDetectorTool(llm=llm, unchanged_chunks=pretend,
                           carried_findings=[])._run(text)

        assert len(llm.prompts) == len(windows), (
            "one unchanged occurrence marked a whole window reusable"
        )

    def test_the_coverage_reports_what_was_skipped(self):
        text = contract(sections=40)
        windows = pack_windows(identify_chunks(text).chunks)
        llm = RecordingLLM()

        _clauses, coverage = parse_clause_result(ClauseDetectorTool(
            llm=llm, unchanged_chunks=self.keys(windows[0]), carried_findings=[],
        )._run(text))

        assert coverage["skipped"] == 1
        assert coverage["analysed"] == len(windows) - 1
