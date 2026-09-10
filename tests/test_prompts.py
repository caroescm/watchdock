from watchdock.prompts import check_claim_prompt, draft_fix_prompt, extract_claims_prompt


def test_extract_claims_prompt_has_no_leaked_indentation():
    """Two continuation lines of the opening paragraph used to sit inside the
    f-string with the source's indentation, so sixteen spaces led every
    extraction prompt's second and third lines."""
    prompt = extract_claims_prompt("--- a.py ---\n-old\n+new")

    assert not any(line.startswith(" ") for line in prompt.splitlines() if line.strip())
    assert "-old\n+new" in prompt
    assert "CLAIM:" in prompt and "NONE" in prompt


def test_check_claim_prompt_carries_claim_file_and_format():
    prompt = check_claim_prompt("requests -> httpx", "README.md", "Always use `requests`.")

    assert "requests -> httpx" in prompt
    assert "`README.md`" in prompt
    assert "Always use `requests`." in prompt
    assert "LINE:" in prompt and "TYPE:" in prompt and "REASON:" in prompt


def test_draft_fix_prompt_carries_line_and_reason():
    prompt = draft_fix_prompt("README.md", "Always use `requests`.", "Code uses httpx.")

    assert "`README.md`" in prompt
    assert "Always use `requests`." in prompt
    assert "Code uses httpx." in prompt
