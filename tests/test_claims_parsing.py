from claims import parse_findings, format_diff


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
