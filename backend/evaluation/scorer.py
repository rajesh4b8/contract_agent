"""Scoring for contract review runs.

The playbook makes this measurable in a way generic clause-type accuracy is not:
for a labelled contract there is an objectively correct set of rules a review
should raise, so precision and recall over rule ids mean something specific.

Everything here is a pure function over a run's output. The harness that calls a
live API lives in scripts/evaluate_pipeline.py; the arithmetic lives here so the
part that has to be right can be tested without a stack running.
"""
from dataclasses import dataclass, field
from statistics import mean, pstdev
from typing import Any, Dict, Iterable, List, Sequence, Set


@dataclass(frozen=True)
class DetectionScore:
    """How well a run identified the rules a contract actually breaches."""

    expected: Set[str]
    found: Set[str]

    @property
    def true_positives(self) -> Set[str]:
        return self.expected & self.found

    @property
    def false_positives(self) -> Set[str]:
        """Raised but not actually breached — the expensive kind of wrong.

        A reviewer who is sent to look at a compliant clause loses trust in the
        whole queue faster than one who has to catch a miss.
        """
        return self.found - self.expected

    @property
    def false_negatives(self) -> Set[str]:
        return self.expected - self.found

    @property
    def precision(self) -> float:
        if not self.found:
            # Raising nothing on a compliant contract is exactly right, so it
            # scores 1.0; raising nothing when something was expected scores 0.
            return 1.0 if not self.expected else 0.0
        return len(self.true_positives) / len(self.found)

    @property
    def recall(self) -> float:
        if not self.expected:
            return 1.0
        return len(self.true_positives) / len(self.expected)

    @property
    def f1(self) -> float:
        total = self.precision + self.recall
        return 2 * self.precision * self.recall / total if total else 0.0

    @property
    def exact(self) -> bool:
        """Whether the run got the set exactly right."""
        return self.expected == self.found


def score_detection(expected: Iterable[str], found: Iterable[str]) -> DetectionScore:
    return DetectionScore(expected=set(expected), found=set(found))


def micro_average(scores: Sequence[DetectionScore]) -> Dict[str, float]:
    """Pool every rule decision across contracts, rather than averaging averages.

    Macro-averaging would let a one-rule contract weigh as heavily as a six-rule
    one, and would hand a perfect 1.0 to the compliant fixture for doing nothing.
    """
    tp = sum(len(s.true_positives) for s in scores)
    fp = sum(len(s.false_positives) for s in scores)
    fn = sum(len(s.false_negatives) for s in scores)

    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "exact_set_matches": sum(1 for s in scores if s.exact),
        "contracts": len(scores),
    }


def groundedness(clauses: Sequence[Dict[str, Any]], contract_text: str) -> Dict[str, float]:
    """Share of extracted clauses whose evidence really is in the contract.

    The extractor already drops ungrounded spans, so a number below 1.0 here
    means that guard has a hole — which is worth knowing rather than assuming.

    A clause that carries no evidence at all counts as ungrounded, not grounded.
    The containment test alone would pass it — the empty string is a substring of
    every contract — so a clause the extractor returned with a blank span would
    score as perfectly evidenced by the one metric meant to catch that.
    """
    if not clauses:
        return {"grounded": 0, "total": 0, "blank": 0, "rate": 1.0}

    source = _normalise(contract_text)
    evidence = [
        _normalise(clause.get("evidence_span") or clause.get("content") or "")
        for clause in clauses
    ]
    blank = sum(1 for span in evidence if not span)
    grounded = sum(1 for span in evidence if span and span in source)
    return {
        "grounded": grounded,
        "total": len(clauses),
        "blank": blank,
        "rate": grounded / len(clauses),
    }


def redline_coverage(violations: Sequence[Dict], redlines: Sequence[Dict]) -> Dict[str, float]:
    """Share of violations that produced replacement language.

    A violation with no redline leaves the reviewer to draft it themselves,
    which is most of the work the product claims to remove.
    """
    if not violations:
        return {"covered": 0, "total": 0, "rate": 1.0}

    breaches = {(v.get("rule_id"), v.get("clause_index")) for v in violations}
    drafted = {(r.get("rule_id"), r.get("clause_index")) for r in redlines}
    covered = len(breaches & drafted)
    return {"covered": covered, "total": len(breaches), "rate": covered / len(breaches)}


@dataclass
class StabilityReport:
    """How much the answer moves when the same contract is reviewed twice.

    Worth measuring explicitly: the pipeline is several non-deterministic model
    calls, and a score from a single run says nothing about whether the next run
    agrees. A reviewer who sees a different set of findings on a re-run cannot
    trust either.
    """

    runs: List[Set[str]] = field(default_factory=list)
    requested: int = 0
    failed: int = 0

    def add(self, found: Iterable[str]) -> None:
        self.runs.append(set(found))

    def add_failure(self) -> None:
        """Record a run that never produced an answer.

        A dropped failure is worse than a reported one: three requested passes
        of which two error out would otherwise summarise as a single run and be
        read as agreement. Stability measured over the runs that happened to
        succeed is exactly the number a flaky provider makes look good.
        """
        self.failed += 1

    def summarise(self) -> Dict[str, Any]:
        base = {
            "runs": len(self.runs),
            "requested": self.requested or len(self.runs) + self.failed,
            "failed": self.failed,
        }
        base["complete"] = base["runs"] == base["requested"]

        if len(self.runs) < 2:
            return {**base, "identical": None, "jaccard": None}

        always = set.intersection(*self.runs)
        ever = set.union(*self.runs)
        pairs = [
            len(a & b) / len(a | b) if (a | b) else 1.0
            for i, a in enumerate(self.runs) for b in self.runs[i + 1:]
        ]
        return {
            **base,
            "identical": all(r == self.runs[0] for r in self.runs),
            "jaccard": mean(pairs) if pairs else 1.0,
            "stable_rules": sorted(always),
            "flapping_rules": sorted(ever - always),
            "count_spread": pstdev([len(r) for r in self.runs]),
        }


def coverage_variability(coverages: Sequence[Dict[str, float]]) -> Dict[str, Any]:
    """How much redline drafting moved across runs of the same contract.

    Kept separate from detection stability because the two are separate
    reliabilities: a run can find the same violations every time and still fail
    to draft language for one of them. Reporting only the last run's coverage
    hides exactly that, which is the variance worth knowing about.
    """
    rates = [c["rate"] for c in coverages]
    if not rates:
        return {"runs": 0, "identical": None, "rates": [], "min": None, "max": None}
    return {
        "runs": len(rates),
        "identical": all(r == rates[0] for r in rates),
        "rates": rates,
        "min": min(rates),
        "max": max(rates),
        "drafted": [f"{c['covered']}/{c['total']}" for c in coverages],
    }


def _normalise(text: str) -> str:
    return " ".join(text.split()).casefold()
