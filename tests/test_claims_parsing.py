from unittest.mock import patch

import pytest

from claims import (
    parse_findings,
    parse_claims,
    format_diff,
    _is_relevant,
    extract_claims,
    _merge_findings,
    check_claim_against_target_ensemble,
    check_claims_against_target,
)


def test_parse_findings_none_response():
    assert parse_findings("NONE") == []
    assert parse_findings("none") == []
    assert parse_findings("  NONE  ") == []


def test_parse_findings_single_finding():
    text = (
        "LINE: Always use `requests` for HTTP calls.\n"
        "TYPE: semantic staleness\n"
        "REASON: Code now uses httpx instead.\n"
    )
    findings = parse_findings(text)

    assert len(findings) == 1
    assert findings[0]["line"] == "Always use `requests` for HTTP calls."
    assert findings[0]["type"] == "semantic staleness"
    assert findings[0]["reason"] == "Code now uses httpx instead."


def test_parse_findings_multiple_findings():
    text = (
        "LINE: first stale line\n"
        "TYPE: semantic staleness\n"
        "REASON: first reason\n"
        "\n"
        "LINE: second stale line\n"
        "TYPE: broken reference\n"
        "REASON: second reason\n"
    )
    findings = parse_findings(text)

    assert len(findings) == 2
    assert findings[0]["line"] == "first stale line"
    assert findings[1]["line"] == "second stale line"


def test_parse_findings_drops_incomplete_blocks():
    """A LINE: block with no REASON: (malformed model output) must not crash
    downstream code or be treated as a real finding — this is a regression
    test for the KeyError bug found during M3 end-to-end testing."""
    text = (
        "LINE: a real finding\n"
        "TYPE: semantic staleness\n"
        "REASON: has a reason\n"
        "\n"
        "LINE: an incomplete finding with no reason at all\n"
    )
    findings = parse_findings(text)

    assert len(findings) == 1
    assert findings[0]["line"] == "a real finding"


def test_parse_findings_filters_unaffected_na_lines():
    text = (
        "LINE: real finding\n"
        "TYPE: semantic staleness\n"
        "REASON: really wrong\n"
        "\n"
        "LINE: unrelated line\n"
        "TYPE: N/A (unaffected)\n"
        "REASON: not related to the diff\n"
    )
    findings = parse_findings(text)

    assert len(findings) == 1
    assert findings[0]["line"] == "real finding"


def test_format_diff_joins_multiple_files():
    diff = [
        {"filename": "a.py", "patch": "-old\n+new"},
        {"filename": "b.py", "patch": "-foo\n+bar"},
    ]
    result = format_diff(diff)

    assert "--- a.py ---" in result
    assert "--- b.py ---" in result
    assert "-old" in result
    assert "+bar" in result


def test_is_relevant_filters_test_and_boilerplate_files():
    assert _is_relevant("tests/foo_test.py") is False
    assert _is_relevant("test/bar.js") is False
    assert _is_relevant("LICENSE") is False
    assert _is_relevant(".gitignore") is False
    assert _is_relevant("package-lock.json") is False
    assert _is_relevant("some_dir.egg-info/PKG-INFO") is False


def test_is_relevant_keeps_real_source_files():
    assert _is_relevant("src/main.py") is True
    assert _is_relevant("index.js") is True
    assert _is_relevant("README.md") is True


def test_extract_claims_short_circuits_without_api_call_when_diff_fully_filtered():
    """No network call should happen at all if every file in the diff is
    irrelevant (test files, LICENSE, etc.) — this is testable without a
    live API key precisely because it should never reach the API."""
    diff = [{"filename": "LICENSE", "patch": "+MIT License"}]
    assert extract_claims(diff) == "NONE"


def test_merge_findings_deduplicates_identical_lines_across_samples():
    """Simulates the ensemble case: 3 independent samples where the same
    real finding shows up in some but not all of them — union should catch
    it without duplicating it."""
    sample_a = [{"line": "Always use `requests` for HTTP calls.", "type": "semantic staleness", "reason": "r1"}]
    sample_b = []  # this sample missed it
    sample_c = [{"line": "Always use `requests` for HTTP calls.", "type": "semantic staleness", "reason": "r3"}]

    merged = _merge_findings([sample_a, sample_b, sample_c])

    assert len(merged) == 1


def test_merge_findings_keeps_distinct_findings_from_different_samples():
    sample_a = [{"line": "first stale line", "type": "semantic staleness", "reason": "r1"}]
    sample_b = [{"line": "second stale line", "type": "broken reference", "reason": "r2"}]

    merged = _merge_findings([sample_a, sample_b])

    assert len(merged) == 2


def test_merge_findings_empty_input():
    assert _merge_findings([[], [], []]) == []


def test_ensemble_degrades_gracefully_when_one_sample_fails():
    """Regression test for a real triggered run: all 3 ensemble samples hit
    a connection error, the client's single retry didn't recover, and the
    whole check crashed with nothing posted. A dropped sample should be
    treated as a missing vote, not a fatal error, as long as at least one
    sample succeeds."""
    with patch("claims.check_claim_against_target", side_effect=[
        ConnectionError("boom"),
        "LINE: a real finding\nTYPE: semantic staleness\nREASON: r\n",
        "NONE",
    ]):
        findings = check_claim_against_target_ensemble("claims", "README.md", "content", n=3)

    assert len(findings) == 1
    assert findings[0]["line"] == "a real finding"


def test_ensemble_raises_when_all_samples_fail():
    """If every sample fails there's genuinely no result to report — this
    must raise rather than silently behave like a clean 'no drift' NONE."""
    with patch("claims.check_claim_against_target", side_effect=ConnectionError("boom")):
        with pytest.raises(RuntimeError, match="README.md"):
            check_claim_against_target_ensemble("claims", "README.md", "content", n=3)


def test_parse_claims_none_response():
    assert parse_claims("NONE") == []
    assert parse_claims("none") == []
    assert parse_claims("  NONE  ") == []
    assert parse_claims("") == []


def test_parse_claims_multiple_blocks():
    text = (
        "CLAIM: The HTTP client changed from requests to httpx, contradicting any doc that names requests.\n"
        "\n"
        "CLAIM: The `--verbose` flag was removed from the CLI.\n"
    )
    claims = parse_claims(text)

    assert len(claims) == 2
    assert claims[0].startswith("The HTTP client changed")
    assert claims[1] == "The `--verbose` flag was removed from the CLI."


def test_parse_claims_folds_wrapped_continuation_lines():
    """A claim the model wraps across multiple lines must stay one claim,
    not be truncated at the first newline."""
    text = (
        "CLAIM: The default config key `timeout` was renamed to\n"
        "`request_timeout` in config.yml, so docs naming `timeout` are stale.\n"
        "\n"
        "CLAIM: second claim\n"
    )
    claims = parse_claims(text)

    assert len(claims) == 2
    assert "renamed to `request_timeout`" in claims[0]


def test_parse_claims_falls_back_to_whole_output_when_format_ignored():
    """If the model ignores the CLAIM: format entirely, the whole output must
    become a single claim rather than silently parsing to [] — [] means
    'no drift', which would be a false all-clear caused by a format miss."""
    freeform = "The change swaps requests for httpx which affects the README's install section."
    claims = parse_claims(freeform)

    assert claims == [freeform]


def test_check_claims_against_target_unions_findings_across_claims():
    """Each claim runs its own ensemble; findings from different claims must
    be unioned (and deduped) in the final result."""
    def fake_check(claim, target_path, target_content):
        if "first" in claim:
            return "LINE: stale line A\nTYPE: semantic staleness\nREASON: r1\n"
        return "LINE: stale line B\nTYPE: broken reference\nREASON: r2\n"

    with patch("claims.check_claim_against_target", side_effect=fake_check):
        findings = check_claims_against_target(
            ["first claim", "second claim"], "README.md", "content", n=2
        )

    lines = sorted(f["line"] for f in findings)
    assert lines == ["stale line A", "stale line B"]


def test_check_claims_against_target_drops_a_fully_failed_claim():
    """One claim's entire ensemble failing must not take down the other
    claims' results — a missing vote, not a fatal error."""
    def fake_check(claim, target_path, target_content):
        if "doomed" in claim:
            raise ConnectionError("boom")
        return "LINE: stale line A\nTYPE: semantic staleness\nREASON: r1\n"

    with patch("claims.check_claim_against_target", side_effect=fake_check):
        findings = check_claims_against_target(
            ["doomed claim", "healthy claim"], "README.md", "content", n=2
        )

    assert len(findings) == 1
    assert findings[0]["line"] == "stale line A"


def test_check_claims_against_target_raises_when_every_claim_fails():
    with patch("claims.check_claim_against_target", side_effect=ConnectionError("boom")):
        with pytest.raises(RuntimeError, match="README.md"):
            check_claims_against_target(["a", "b"], "README.md", "content", n=2)
