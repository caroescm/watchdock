from run_eval import compute_metrics, score_case
from watchdock.models import Finding


def _case(drift, stale_line=None):
    return {"expected": {"drift": drift, "stale_line": stale_line}}


def test_drift_case_with_the_right_line_is_a_true_positive():
    assert score_case(_case(True, "Use `requests`."), [Finding(line="use `requests`.", reason="r")]) == ["TP"]


def test_drift_case_with_nothing_flagged_is_a_false_negative_only():
    assert score_case(_case(True, "Use `requests`."), []) == ["FN"]


def test_drift_case_with_the_wrong_line_is_both_a_miss_and_a_false_alarm():
    """The stale line was not caught (FN) and an unrelated line was flagged
    (FP). Scoring it FN alone hid the false alarm and inflated precision."""
    outcomes = score_case(_case(True, "Use `requests`."), [Finding(line="Run `make test`.", reason="r")])

    assert sorted(outcomes) == ["FN", "FP"]


def test_clean_case_outcomes():
    assert score_case(_case(False), []) == ["TN"]
    assert score_case(_case(False), [Finding(line="anything", reason="r")]) == ["FP"]


def test_compute_metrics_counts_every_outcome():
    metrics = compute_metrics(["TP", "TP", "FN", "FP", "TN"])

    assert (metrics["tp"], metrics["fp"], metrics["fn"], metrics["tn"]) == (2, 1, 1, 1)
    assert metrics["precision"] == 2 / 3 and metrics["recall"] == 2 / 3
