"""The scoring arithmetic, which has to be right for any number it produces to mean anything."""
import pytest

from backend.evaluation.scorer import (
    StabilityReport,
    coverage_variability,
    groundedness,
    micro_average,
    redline_coverage,
    score_detection,
)


class TestDetection:
    def test_a_perfect_run(self):
        score = score_detection({"PAY-001", "TRM-001"}, {"PAY-001", "TRM-001"})

        assert score.precision == 1.0
        assert score.recall == 1.0
        assert score.f1 == 1.0
        assert score.exact

    def test_a_missed_breach_costs_recall_not_precision(self):
        score = score_detection({"PAY-001", "TRM-001"}, {"PAY-001"})

        assert score.precision == 1.0
        assert score.recall == 0.5
        assert score.false_negatives == {"TRM-001"}

    def test_an_invented_breach_costs_precision_not_recall(self):
        score = score_detection({"PAY-001"}, {"PAY-001", "IPR-001"})

        assert score.precision == 0.5
        assert score.recall == 1.0
        assert score.false_positives == {"IPR-001"}

    def test_finding_nothing_on_a_compliant_contract_is_correct(self):
        """The case a demo never shows: being right by staying quiet."""
        score = score_detection(set(), set())

        assert score.precision == 1.0
        assert score.recall == 1.0
        assert score.exact

    def test_inventing_a_breach_on_a_compliant_contract_scores_zero_precision(self):
        score = score_detection(set(), {"LIA-001"})

        assert score.precision == 0.0
        assert not score.exact

    def test_finding_nothing_when_something_was_expected_scores_zero(self):
        score = score_detection({"PAY-001"}, set())

        assert score.precision == 0.0
        assert score.recall == 0.0
        assert score.f1 == 0.0


class TestMicroAverage:
    def test_it_pools_decisions_rather_than_averaging_averages(self):
        """A one-rule contract must not weigh as much as a six-rule one."""
        scores = [
            score_detection({"A", "B", "C", "D", "E", "F"}, {"A", "B", "C", "D", "E", "F"}),
            score_detection({"X"}, set()),
        ]

        pooled = micro_average(scores)

        assert pooled["true_positives"] == 6
        assert pooled["false_negatives"] == 1
        assert pooled["recall"] == pytest.approx(6 / 7)
        # A macro average would have reported (1.0 + 0.0) / 2 = 0.5.
        assert pooled["recall"] > 0.5

    def test_a_compliant_contract_does_not_inflate_the_score(self):
        """Raising nothing correctly adds no true positives to the pool."""
        pooled = micro_average([
            score_detection({"A"}, {"A"}),
            score_detection(set(), set()),
        ])

        assert pooled["true_positives"] == 1
        assert pooled["contracts"] == 2

    def test_exact_matches_are_counted(self):
        pooled = micro_average([
            score_detection({"A"}, {"A"}),
            score_detection({"B"}, {"C"}),
        ])

        assert pooled["exact_set_matches"] == 1

    def test_no_decisions_at_all_is_not_a_failure(self):
        pooled = micro_average([score_detection(set(), set())])

        assert pooled["precision"] == 1.0
        assert pooled["recall"] == 1.0


CONTRACT = "Customer shall pay each invoice within ninety (90) days of receipt."


class TestGroundedness:
    def test_a_quoted_clause_is_grounded(self):
        result = groundedness(
            [{"evidence_span": "pay each invoice within ninety (90) days"}], CONTRACT
        )

        assert result["rate"] == 1.0

    def test_an_invented_clause_is_not(self):
        result = groundedness([{"evidence_span": "Provider waives all rights."}], CONTRACT)

        assert result["rate"] == 0.0

    def test_whitespace_differences_do_not_count_against_it(self):
        result = groundedness(
            [{"evidence_span": "pay  each invoice\n within ninety (90) days"}], CONTRACT
        )

        assert result["rate"] == 1.0

    def test_the_legacy_content_field_is_accepted(self):
        result = groundedness([{"content": "ninety (90) days"}], CONTRACT)

        assert result["rate"] == 1.0

    def test_no_clauses_is_not_scored_as_a_failure(self):
        assert groundedness([], CONTRACT)["rate"] == 1.0

    def test_a_clause_with_no_evidence_is_not_grounded(self):
        """The empty string is a substring of every contract.

        Without an explicit guard a clause the extractor returned with a blank
        span scores as perfectly evidenced by the one metric that exists to
        catch exactly that.
        """
        result = groundedness([{"evidence_span": ""}], CONTRACT)

        assert result["rate"] == 0.0
        assert result["blank"] == 1

    def test_a_missing_evidence_field_is_not_grounded_either(self):
        result = groundedness([{"evidence_span": None, "content": None}, {}], CONTRACT)

        assert result["rate"] == 0.0
        assert result["blank"] == 2

    def test_blank_clauses_drag_down_a_real_one(self):
        result = groundedness(
            [{"evidence_span": "ninety (90) days"}, {"evidence_span": "   "}], CONTRACT
        )

        assert result["grounded"] == 1
        assert result["rate"] == 0.5


class TestRedlineCoverage:
    def test_every_violation_redlined(self):
        violations = [{"rule_id": "A", "clause_index": 0}]
        redlines = [{"rule_id": "A", "clause_index": 0}]

        assert redline_coverage(violations, redlines)["rate"] == 1.0

    def test_a_violation_with_no_redline_lowers_coverage(self):
        violations = [{"rule_id": "A", "clause_index": 0}, {"rule_id": "B", "clause_index": 1}]
        redlines = [{"rule_id": "A", "clause_index": 0}]

        assert redline_coverage(violations, redlines)["rate"] == 0.5

    def test_coverage_matches_on_the_clause_too_not_just_the_rule(self):
        """One rule can be breached twice; each breach needs its own redline."""
        violations = [{"rule_id": "A", "clause_index": 0}, {"rule_id": "A", "clause_index": 3}]
        redlines = [{"rule_id": "A", "clause_index": 0}]

        assert redline_coverage(violations, redlines)["rate"] == 0.5

    def test_nothing_to_redline_is_full_coverage(self):
        assert redline_coverage([], [])["rate"] == 1.0


class TestStability:
    def test_identical_runs_are_reported_as_stable(self):
        report = StabilityReport()
        report.add({"PAY-001", "TRM-001"})
        report.add({"PAY-001", "TRM-001"})

        summary = report.summarise()

        assert summary["identical"] is True
        assert summary["jaccard"] == 1.0
        assert summary["flapping_rules"] == []

    def test_a_rule_that_comes_and_goes_is_named(self):
        report = StabilityReport()
        report.add({"PAY-001", "TRM-001"})
        report.add({"PAY-001"})

        summary = report.summarise()

        assert summary["identical"] is False
        assert summary["stable_rules"] == ["PAY-001"]
        assert summary["flapping_rules"] == ["TRM-001"]
        assert summary["jaccard"] == pytest.approx(0.5)

    def test_a_single_run_cannot_say_anything_about_stability(self):
        report = StabilityReport()
        report.add({"PAY-001"})

        assert report.summarise()["identical"] is None

    def test_a_failed_run_is_reported_not_dropped(self):
        """Three requested passes of which two error out are not agreement.

        Silently scoring the survivors turns a flaky provider into a stable
        result — the reading that makes the metric worthless.
        """
        report = StabilityReport(requested=3)
        report.add({"PAY-001"})
        report.add_failure()
        report.add_failure()

        summary = report.summarise()

        assert summary["requested"] == 3
        assert summary["runs"] == 1
        assert summary["failed"] == 2
        assert summary["complete"] is False

    def test_runs_that_all_succeed_are_marked_complete(self):
        report = StabilityReport(requested=2)
        report.add({"PAY-001"})
        report.add({"PAY-001"})

        summary = report.summarise()

        assert summary["complete"] is True
        assert summary["failed"] == 0

    def test_failures_are_visible_even_when_every_run_failed(self):
        report = StabilityReport(requested=2)
        report.add_failure()
        report.add_failure()

        summary = report.summarise()

        assert summary["runs"] == 0
        assert summary["complete"] is False


class TestCoverageVariability:
    def test_identical_drafting_across_runs(self):
        runs = [{"covered": 2, "total": 2, "rate": 1.0}] * 3

        summary = coverage_variability(runs)

        assert summary["identical"] is True
        assert summary["min"] == summary["max"] == 1.0

    def test_drafting_that_moves_is_flagged_separately_from_detection(self):
        """The same violations found every time, drafted for only sometimes.

        Reporting the last run's coverage alone hides this, which is the whole
        reason drafting is tracked apart from detection.
        """
        runs = [
            {"covered": 2, "total": 2, "rate": 1.0},
            {"covered": 1, "total": 2, "rate": 0.5},
        ]

        summary = coverage_variability(runs)

        assert summary["identical"] is False
        assert summary["min"] == 0.5
        assert summary["max"] == 1.0
        assert summary["drafted"] == ["2/2", "1/2"]

    def test_no_runs_says_nothing(self):
        assert coverage_variability([])["identical"] is None
