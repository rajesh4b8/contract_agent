#!/usr/bin/env python3
"""Score the review pipeline against the labelled contracts in evaluation/.

    python scripts/evaluate_pipeline.py                      # one pass
    python scripts/evaluate_pipeline.py --runs 3             # also measure stability
    python scripts/evaluate_pipeline.py --model gemini-flash-lite
    python scripts/evaluate_pipeline.py --json out.json      # machine-readable too

Needs the stack running (`make run`) and the playbook seeded
(`make seed-playbook`). Every contract is uploaded fresh, so a run costs real
model calls — with `--runs 3` that is nine analyses.
"""
# Run directly (`python scripts/evaluate_pipeline.py`) from anywhere: Python puts
# this file's directory on sys.path, not the repo root, so `backend.*` would
# not resolve without this.
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import argparse
import json
import time
from typing import Any, Dict, List

import requests
import yaml

from backend.infrastructure.text_extractors import TextExtractionService
from backend.evaluation.scorer import (
    StabilityReport,
    groundedness,
    micro_average,
    redline_coverage,
    score_detection,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "evaluation"
HEADERS = {"X-User-Role": "LEGAL_REVIEWER", "X-Tenant-ID": "default-tenant"}


def analyse(base_url: str, pdf: pathlib.Path, model: str | None, timeout: int) -> Dict[str, Any]:
    """Upload a contract and analyse it, returning the results plus timings."""
    started = time.monotonic()
    with pdf.open("rb") as handle:
        params = {"model": model} if model else {}
        upload = requests.post(
            f"{base_url}/api/documents/upload", params=params,
            files={"file": (pdf.name, handle, "application/pdf")},
            headers=HEADERS, timeout=timeout,
        )
    upload.raise_for_status()
    body = upload.json()
    if body.get("status") != "success":
        raise RuntimeError(f"upload failed: {body.get('details')}")
    contract_id = body["contract_id"]
    upload_seconds = time.monotonic() - started

    started = time.monotonic()
    params = {"model": model} if model else {}
    analysis = requests.post(
        f"{base_url}/api/intelligence/contracts/{contract_id}/analyze",
        params=params, headers=HEADERS, timeout=timeout,
    )
    analysis.raise_for_status()
    results = analysis.json().get("results", {})
    return {
        "contract_id": contract_id,
        "results": results,
        "upload_seconds": upload_seconds,
        "analysis_seconds": time.monotonic() - started,
    }


def evaluate(base_url: str, model: str | None, runs: int, timeout: int) -> Dict[str, Any]:
    labels = yaml.safe_load((EVAL_DIR / "labels.yaml").read_text())["contracts"]
    per_contract: List[Dict[str, Any]] = []
    scores = []

    for label in labels:
        pdf = EVAL_DIR / "contracts" / label["file"]
        # Grounding must be judged against what the system actually saw. The
        # source .txt differs from the PDF-extracted text in punctuation and
        # line breaks, and scoring against it reported real clauses as
        # hallucinated — a measurement bug that looked like a product bug.
        text = TextExtractionService().extract_with_fallback(str(pdf))
        expected = set(label["expected_rules"])

        stability = StabilityReport()
        latest: Dict[str, Any] = {}
        timings: List[float] = []

        for attempt in range(runs):
            print(f"  {pdf.name}  run {attempt + 1}/{runs} ...", end="", flush=True)
            try:
                outcome = analyse(base_url, pdf, model, timeout)
            except Exception as e:
                print(f" FAILED ({e})")
                continue
            latest = outcome["results"]
            found = {v.get("rule_id") for v in latest.get("violations", []) if v.get("rule_id")}
            stability.add(found)
            timings.append(outcome["analysis_seconds"])
            print(f" {outcome['analysis_seconds']:.0f}s  found {sorted(found) or 'nothing'}")

        if not stability.runs:
            per_contract.append({"file": label["file"], "error": "every run failed"})
            continue

        # Score the last run; stability is reported separately.
        found = stability.runs[-1]
        score = score_detection(expected, found)
        scores.append(score)

        per_contract.append({
            "file": label["file"],
            "expected": sorted(expected),
            "found": sorted(found),
            "missed": sorted(score.false_negatives),
            "invented": sorted(score.false_positives),
            "precision": score.precision,
            "recall": score.recall,
            "f1": score.f1,
            "exact": score.exact,
            "groundedness": groundedness(latest.get("clauses", []), text),
            "redline_coverage": redline_coverage(
                latest.get("violations", []), latest.get("redlines", [])
            ),
            "stability": stability.summarise(),
            "seconds": {"mean": sum(timings) / len(timings), "runs": len(timings)},
        })

    return {
        "model": model or "(server default)",
        "runs_per_contract": runs,
        "overall": micro_average(scores) if scores else {},
        "contracts": per_contract,
    }


def render(report: Dict[str, Any]) -> None:
    print(f"\n{'=' * 72}")
    print(f"  Contract review evaluation — model: {report['model']}")
    print(f"{'=' * 72}\n")

    for entry in report["contracts"]:
        if "error" in entry:
            print(f"  {entry['file']:16} {entry['error']}")
            continue

        verdict = "exact" if entry["exact"] else "off"
        print(f"  {entry['file']:16} {verdict:6} "
              f"P {entry['precision']:.2f}  R {entry['recall']:.2f}  F1 {entry['f1']:.2f}"
              f"   {entry['seconds']['mean']:.0f}s")
        if entry["missed"]:
            print(f"                   missed:   {', '.join(entry['missed'])}")
        if entry["invented"]:
            print(f"                   invented: {', '.join(entry['invented'])}")
        grounded = entry["groundedness"]
        print(f"                   grounded {grounded['grounded']}/{grounded['total']}"
              f"   redlined {entry['redline_coverage']['covered']}/{entry['redline_coverage']['total']}")
        stability = entry["stability"]
        if stability.get("identical") is not None:
            state = "identical" if stability["identical"] else (
                f"varies (jaccard {stability['jaccard']:.2f}, "
                f"flapping: {', '.join(stability['flapping_rules']) or 'none'})")
            print(f"                   across {stability['runs']} runs: {state}")
        print()

    overall = report["overall"]
    if overall:
        print(f"  {'-' * 68}")
        print(f"  Overall   precision {overall['precision']:.2f}   "
              f"recall {overall['recall']:.2f}   F1 {overall['f1']:.2f}")
        print(f"            {overall['true_positives']} correct, "
              f"{overall['false_positives']} invented, {overall['false_negatives']} missed")
        print(f"            {overall['exact_set_matches']}/{overall['contracts']} contracts exactly right\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--model", default=None, help="model id; omit for the server default")
    parser.add_argument("--runs", type=int, default=1, help="passes per contract, for stability")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--json", type=pathlib.Path, help="also write the full report here")
    args = parser.parse_args()

    try:
        requests.get(f"{args.base_url}/", timeout=10).raise_for_status()
    except Exception:
        print(f"No backend at {args.base_url}. Start it with `make run`.")
        return 1

    report = evaluate(args.base_url, args.model, args.runs, args.timeout)
    render(report)

    if args.json:
        args.json.write_text(json.dumps(report, indent=2))
        print(f"  Full report: {args.json}\n")

    # Non-zero when the pipeline invented a breach that is not in the contract:
    # a false positive on a compliant clause is the failure that costs trust.
    return 1 if report.get("overall", {}).get("false_positives", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
