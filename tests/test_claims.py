from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from watchdoc.claims import (
    check_claims_against_targets,
    extract_claims,
    merge_findings,
    pool_size,
)
from watchdoc.models import DiffEntry, Finding, FindingType


def _finding(line, reason="r", kind=FindingType.SEMANTIC_STALENESS):
    return Finding(line=line, reason=reason, kind=kind)


def test_extract_claims_makes_no_model_call_when_the_diff_is_fully_filtered():
    """Testable without an API key precisely because it must never reach the API."""
    with patch("watchdoc.claims.nim_client.chat_completion") as chat:
        assert extract_claims([DiffEntry("LICENSE", "+MIT License")]) == []

    chat.assert_not_called()


def test_merge_findings_deduplicates_identical_lines_across_samples():
    """The ensemble case: the same real finding shows up in some samples but
    not all. The union catches it once."""
    line = "Always use `requests` for HTTP calls."

    merged = merge_findings([[_finding(line, "r1")], [], [_finding(line, "r3")]])

    assert [f.line for f in merged] == [line]


def test_merge_findings_keeps_distinct_findings_in_first_seen_order():
    merged = merge_findings([[_finding("first stale line")], [_finding("second stale line")]])

    assert [f.line for f in merged] == ["first stale line", "second stale line"]


def test_merge_findings_prefers_the_fuller_quote_regardless_of_sample_order():
    """Two quotes of one line that overlap collapse to the longer one, which is
    the one most likely to match the file verbatim at delivery. The result
    doesn't depend on which sample arrived first."""
    short, long_ = _finding("use `requests`"), _finding("Always use `requests` for HTTP calls.")

    assert merge_findings([[short], [long_]]) == [long_]
    assert merge_findings([[long_], [short]]) == [long_]


def test_merge_findings_empty_input():
    assert merge_findings([[], [], []]) == []


def test_pool_size_is_bounded_by_tasks_then_by_explicit_cap_then_by_the_nim_cap():
    with patch("watchdoc.claims.nim_client.max_concurrent_requests", return_value=8):
        assert pool_size(18) == 8            # capped by the NIM gate
        assert pool_size(2) == 2             # no more threads than tasks
        assert pool_size(18, max_workers=4) == 4
        assert pool_size(0) == 1             # never a zero-sized pool


def test_check_claims_against_targets_runs_one_pool_sized_by_pool_size():
    """targets x claims x samples run through one pool, never one per layer."""
    with patch("watchdoc.claims.ThreadPoolExecutor", wraps=ThreadPoolExecutor) as pool, \
         patch("watchdoc.claims.check_claim_against_target", return_value=[]), \
         patch("watchdoc.claims.nim_client.max_concurrent_requests", return_value=8):
        check_claims_against_targets(["c1", "c2", "c3"], {"README.md": "a", "AGENTS.md": "b"}, n=3)   # 18 samples
        check_claims_against_targets(["c1"], {"README.md": "a"}, n=2)                                # 2 samples
        check_claims_against_targets(["c1"], {"README.md": "a", "AGENTS.md": "b"}, n=3, max_workers=4)

    assert [call.kwargs["max_workers"] for call in pool.call_args_list] == [8, 2, 4]


def test_check_claims_against_targets_unions_findings_across_claims():
    def fake_check(claim, target_path, target_content):
        if "first" in claim:
            return [_finding("stale line A")]
        return [_finding("stale line B", kind=FindingType.BROKEN_REFERENCE)]

    with patch("watchdoc.claims.check_claim_against_target", side_effect=fake_check):
        findings, errors = check_claims_against_targets(["first claim", "second claim"], {"README.md": "c"}, n=2)

    assert sorted(f.line for f in findings["README.md"]) == ["stale line A", "stale line B"]
    assert errors == {}


def test_a_failed_sample_is_a_missing_vote_not_a_fatal_error():
    """Seen live: ensemble samples hit connection errors and the whole check
    crashed with nothing posted. As long as one sample of a claim survives,
    its findings count."""
    with patch("watchdoc.claims.check_claim_against_target", side_effect=[
        ConnectionError("boom"), [_finding("a real finding")], [],
    ]):
        findings, errors = check_claims_against_targets(["claim"], {"README.md": "c"}, n=3)

    assert [f.line for f in findings["README.md"]] == ["a real finding"]
    assert errors == {}


def test_a_fully_failed_claim_is_dropped_but_the_other_claims_still_count():
    def fake_check(claim, target_path, target_content):
        if "doomed" in claim:
            raise ConnectionError("boom")
        return [_finding("stale line A")]

    with patch("watchdoc.claims.check_claim_against_target", side_effect=fake_check):
        findings, errors = check_claims_against_targets(["doomed claim", "healthy claim"], {"README.md": "c"}, n=2)

    assert [f.line for f in findings["README.md"]] == ["stale line A"]
    assert errors == {}


def test_a_target_whose_every_claim_failed_is_an_error_and_the_others_still_report():
    """Must never read as a clean 'no drift' for that target."""
    def fake_check(claim, target_path, target_content):
        if target_path == "AGENTS.md":
            raise ConnectionError("boom")
        return [_finding(f"{target_path}:{claim}")]

    with patch("watchdoc.claims.check_claim_against_target", side_effect=fake_check):
        findings, errors = check_claims_against_targets(["c1", "c2"], {"README.md": "a", "AGENTS.md": "b"}, n=2)

    assert sorted(f.line for f in findings["README.md"]) == ["README.md:c1", "README.md:c2"]
    assert "AGENTS.md" not in findings
    assert "2 claim(s) failed" in errors["AGENTS.md"]


def test_check_claims_against_targets_with_no_claims_returns_empty_findings_for_every_target():
    with patch("watchdoc.claims.check_claim_against_target") as check:
        findings, errors = check_claims_against_targets([], {"README.md": "a"}, n=3)

    assert findings == {"README.md": []} and errors == {}
    check.assert_not_called()
