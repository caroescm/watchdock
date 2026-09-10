"""
Runs every case in cases.py through three approaches (deterministic baseline,
single-prompt LLM, full Watchdoc pipeline) and reports precision/recall/F1 for each.
"""
import sys
import time

from cases import CASES
from baseline import deterministic_check
from single_prompt import single_prompt_check
from watchdoc.claims import extract_claims, check_claims_against_target


def normalize(text):
    return " ".join(text.lower().split())


def line_matches(expected_line, findings):
    """True if any finding's line corresponds to the expected stale line
    (tolerant of minor wording/quoting differences via substring match)."""
    if not expected_line:
        return False
    expected_norm = normalize(expected_line)
    for finding in findings:
        found_norm = normalize(finding.line)
        if expected_norm in found_norm or found_norm in expected_norm:
            return True
    return False


def score_case(case, findings):
    """Returns 'TP', 'FP', 'FN', or 'TN' for one case given an approach's findings."""
    predicted_drift = len(findings) > 0
    expected_drift = case["expected"]["drift"]

    if expected_drift:
        correct = line_matches(case["expected"]["stale_line"], findings)
        return "TP" if correct else "FN"
    else:
        return "FP" if predicted_drift else "TN"


def compute_metrics(scores):
    tp = scores.count("TP")
    fp = scores.count("FP")
    fn = scores.count("FN")
    tn = scores.count("TN")
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": precision, "recall": recall, "f1": f1}


def run(case_ids=None):
    """If case_ids is given, only run those cases (by id) instead of all of CASES."""
    cases = [c for c in CASES if c["id"] in case_ids] if case_ids else CASES
    results = {"baseline": [], "single_prompt": [], "full_pipeline": []}

    for i, case in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {case['id']} ({case['category']})", flush=True)
        diff = case["diff"]
        target_path = case["target_path"]
        target_content = case["target_content_before"]

        # --- Baseline (no LLM) ---
        t0 = time.time()
        baseline_findings = deterministic_check(diff, target_content)
        score = score_case(case, baseline_findings)
        results["baseline"].append(score)
        print(f"  baseline: {score} ({time.time()-t0:.1f}s)", flush=True)

        # --- Single-prompt LLM ---
        t0 = time.time()
        sp_findings = single_prompt_check(diff, target_path, target_content)
        score = score_case(case, sp_findings)
        results["single_prompt"].append(score)
        print(f"  single_prompt: {score} ({time.time()-t0:.1f}s)", flush=True)

        # --- Full pipeline (extract_claims -> per-claim 3x-ensemble checks, unioned) ---
        t0 = time.time()
        claims = extract_claims(diff)
        if not claims:
            full_findings = []
        else:
            full_findings = check_claims_against_target(claims, target_path, target_content)
        score = score_case(case, full_findings)
        results["full_pipeline"].append(score)
        print(f"  full_pipeline: {score} ({time.time()-t0:.1f}s)", flush=True)

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
    case_ids = sys.argv[1:] or None
    run(case_ids)
