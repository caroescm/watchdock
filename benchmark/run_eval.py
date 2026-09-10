"""
Runs every case in cases.py through three approaches (deterministic baseline,
single-prompt LLM, full Watchdoc pipeline) and reports precision/recall/F1 for each.
"""
import sys
import time

from baseline import deterministic_check
from cases import CASES
from single_prompt import single_prompt_check
from watchdoc.claims import check_claims_against_targets, extract_claims
from watchdoc.models import Finding
from watchdoc.parsing import lines_overlap


def line_matches(expected_line: str | None, findings: list[Finding]) -> bool:
    """True if any finding's line corresponds to the expected stale line, with
    the same tolerant match the pipeline's ensemble merge uses."""
    if not expected_line:
        return False
    return any(lines_overlap(expected_line, finding.line) for finding in findings)


def score_case(case: dict, findings: list[Finding]) -> list[str]:
    """Returns the list of outcomes ('TP', 'FP', 'FN', 'TN') one case
    contributes, given an approach's findings.

    A drift case whose findings miss the expected line is a false negative
    (the real stale line was not caught) *and*, if anything was flagged, a
    false positive (the lines that were flagged are wrong). Counting it as
    FN alone inflates precision."""
    predicted_drift = len(findings) > 0
    expected_drift = case["expected"]["drift"]

    if expected_drift:
        if line_matches(case["expected"]["stale_line"], findings):
            return ["TP"]
        return ["FN", "FP"] if predicted_drift else ["FN"]
    return ["FP"] if predicted_drift else ["TN"]


def compute_metrics(scores: list[str]) -> dict[str, float]:
    tp = scores.count("TP")
    fp = scores.count("FP")
    fn = scores.count("FN")
    tn = scores.count("TN")
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": precision, "recall": recall, "f1": f1}


def full_pipeline_check(diff, target_path: str, target_content: str) -> list[Finding]:
    """Watchdoc's detection stage on one target: claim extraction, then the
    per-claim ensemble. Raises if every claim failed, since then there is
    genuinely no result to score."""
    claims = extract_claims(diff)
    if not claims:
        return []
    findings, errors = check_claims_against_targets(claims, {target_path: target_content})
    if target_path in errors:
        raise RuntimeError(errors[target_path])
    return findings[target_path]


def run(case_ids: list[str] | None = None) -> None:
    """If case_ids is given, only run those cases (by id) instead of all of CASES."""
    cases = [c for c in CASES if c["id"] in case_ids] if case_ids else CASES
    approaches = {
        "baseline": lambda diff, path, content: deterministic_check(diff, content),
        "single_prompt": single_prompt_check,
        "full_pipeline": full_pipeline_check,
    }
    results: dict[str, list[str]] = {name: [] for name in approaches}

    for i, case in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {case['id']} ({case['category']})", flush=True)
        for name, check in approaches.items():
            t0 = time.time()
            findings = check(case["diff"], case["target_path"], case["target_content_before"])
            score = score_case(case, findings)
            results[name].extend(score)
            print(f"  {name}: {score} ({time.time() - t0:.1f}s)", flush=True)

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    for approach, scores in results.items():
        metrics = compute_metrics(scores)
        print(f"\n{approach}:")
        print(f"  TP={metrics['tp']} FP={metrics['fp']} FN={metrics['fn']} TN={metrics['tn']}")
        print(f"  precision={metrics['precision']:.2f} recall={metrics['recall']:.2f} f1={metrics['f1']:.2f}")


if __name__ == "__main__":
    # Optional: pass case ids as CLI args to run only a subset, e.g.
    #   python3 run_eval.py instr_semantic_01 doc_deterministic_01 clean_01
    run(sys.argv[1:] or None)
