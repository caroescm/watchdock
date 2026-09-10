from watchdock.models import Finding, FindingType
from watchdock.parsing import (
    is_none_response,
    lines_overlap,
    normalize_line,
    parse_blocks,
    parse_claims,
    parse_findings,
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

    assert findings == [Finding(
        line="Always use `requests` for HTTP calls.",
        kind=FindingType.SEMANTIC_STALENESS,
        reason="Code now uses httpx instead.",
    )]
    assert findings[0].fix is None and findings[0].delivery is None


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

    assert [f.line for f in findings] == ["first stale line", "second stale line"]
    assert [f.kind for f in findings] == [FindingType.SEMANTIC_STALENESS, FindingType.BROKEN_REFERENCE]


def test_parse_findings_maps_free_text_type_onto_the_closed_vocabulary():
    broken = parse_findings("LINE: l\nTYPE: Broken Reference (file gone)\nREASON: r\n")
    assert broken[0].kind == FindingType.BROKEN_REFERENCE
    assert parse_findings("LINE: l\nTYPE: stale\nREASON: r\n")[0].kind == FindingType.SEMANTIC_STALENESS
    assert parse_findings("LINE: l\nREASON: r\n")[0].kind == FindingType.SEMANTIC_STALENESS


def test_parse_findings_drops_incomplete_blocks():
    """A LINE: block with no REASON: (malformed model output) must not crash
    downstream code or be treated as a real finding."""
    text = (
        "LINE: a real finding\n"
        "TYPE: semantic staleness\n"
        "REASON: has a reason\n"
        "\n"
        "LINE: an incomplete finding with no reason at all\n"
    )
    findings = parse_findings(text)

    assert [f.line for f in findings] == ["a real finding"]


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
    assert [f.line for f in parse_findings(text)] == ["real finding"]


def test_parse_findings_drops_placeholder_and_symbol_only_lines():
    """Seen in real replies: one sample echoed the prompt's format placeholder
    verbatim and another returned a bare `...` as a finding. Neither is a
    quotable line from any target file."""
    text = (
        "LINE: <exact quoted line, verbatim from the file above>\n"
        "TYPE: semantic staleness\n"
        "REASON: echoed template\n"
        "\n"
        "LINE: ...\n"
        "TYPE: semantic staleness\n"
        "REASON: ellipsis only\n"
        "\n"
        "LINE: a real finding\n"
        "TYPE: semantic staleness\n"
        "REASON: really wrong\n"
    )
    assert [f.line for f in parse_findings(text)] == ["a real finding"]


def test_parse_findings_keys_are_case_insensitive():
    findings = parse_findings("Line: l\ntype: broken reference\nReason: r\n")

    assert findings == [Finding(line="l", reason="r", kind=FindingType.BROKEN_REFERENCE)]


def test_parse_findings_folds_wrapped_reason():
    findings = parse_findings(
        "LINE: l\nTYPE: semantic staleness\nREASON: the flag was renamed to\n`--request-timeout` in v2.\n"
    )

    assert findings[0].reason == "the flag was renamed to `--request-timeout` in v2."


def test_parse_findings_value_containing_a_colon_is_not_a_key():
    findings = parse_findings("LINE: Note: run `make test` first\nREASON: r\n")

    assert findings[0].line == "Note: run `make test` first"


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
    text = (
        "CLAIM: The default config key `timeout` was renamed to\n"
        "`request_timeout` in config.yml, so docs naming `timeout` are stale.\n"
        "\n"
        "CLAIM: second claim\n"
    )
    claims = parse_claims(text)

    assert len(claims) == 2
    assert "renamed to `request_timeout`" in claims[0]


def test_parse_claims_returns_empty_when_format_ignored():
    """A non-NONE reply with no CLAIM: blocks at all used to fall back to
    treating the whole raw reply as one claim. That reply is sometimes the
    model's own leaked reasoning rather than anything about the diff, so
    parse_claims now reports it as empty and leaves recovery (e.g. a retry)
    to the caller — see claims.extract_claims."""
    freeform = "The change swaps requests for httpx which affects the README's install section."

    assert parse_claims(freeform) == []


def test_parse_claims_strips_a_leaked_thinking_block_before_looking_for_claims():
    text = "<think>Let me consider what this diff affects...</think>\nCLAIM: real claim here\n"

    assert parse_claims(text) == ["real claim here"]


def test_parse_findings_strips_a_leaked_thinking_block_before_looking_for_findings():
    text = "<think>reasoning about the file</think>\nLINE: stale line\nREASON: why\n"

    findings = parse_findings(text)

    assert len(findings) == 1
    assert findings[0].line == "stale line"


def test_is_none_response_true_when_only_a_thinking_block_precedes_none():
    assert is_none_response("<think>nothing relevant here</think>\nNONE")


def test_parse_blocks_is_one_parser_for_both_formats():
    """CLAIM and LINE/TYPE/REASON output go through the same rules: keys are
    case-insensitive, wrapped values fold into the last field, blank lines
    carry no structure, and a field before the first block is dropped."""
    text = (
        "TYPE: orphan, no LINE yet\n"
        "line: Always use `requests`\n"
        "  for HTTP calls.\n"
        "Type: semantic staleness\n"
        "\n"
        "REASON: Code now\n"
        "uses httpx.\n"
        "LINE: second\n"
        "REASON: r2\n"
    )
    blocks = parse_blocks(text, "LINE", ("LINE", "TYPE", "REASON"))

    assert blocks == [
        {"LINE": "Always use `requests` for HTTP calls.", "TYPE": "semantic staleness",
         "REASON": "Code now uses httpx."},
        {"LINE": "second", "REASON": "r2"},
    ]


def test_normalize_line_and_lines_overlap():
    assert normalize_line("  Use   `Requests`.  ") == "use `requests`."
    assert lines_overlap("Use `requests`.", "use `requests`")
    assert lines_overlap("use `requests`", "Use `requests`.")
    assert not lines_overlap("Use `requests`.", "Run `make test`.")
