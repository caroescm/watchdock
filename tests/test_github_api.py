import pytest

from watchdoc.github_api import detect_pr_origin_from_data, get_pr_context
from watchdoc.models import Origin


def test_human_pr_no_trailers():
    assert detect_pr_origin_from_data(["Fix typo in README"], "caroescm") == Origin.HUMAN


def test_agent_pr_claude_trailer():
    messages = ["Add feature\n\nCo-Authored-By: Claude <noreply@anthropic.com>"]
    assert detect_pr_origin_from_data(messages, "caroescm") == Origin.AGENT


def test_agent_pr_cursor_trailer():
    messages = ["Fix bug\n\nCo-Authored-By: Cursor <cursor@example.com>"]
    assert detect_pr_origin_from_data(messages, "caroescm") == Origin.AGENT


def test_agent_pr_bot_login():
    assert detect_pr_origin_from_data(["Bump dependency"], "dependabot[bot]") == Origin.AGENT


def test_ambiguous_defaults_to_human():
    """Never default to auto-commit when origin is unclear."""
    assert detect_pr_origin_from_data([], "") == Origin.HUMAN


def test_trailer_detection_is_case_insensitive():
    messages = ["Add feature\n\nCO-AUTHORED-BY: CLAUDE <noreply@anthropic.com>"]
    assert detect_pr_origin_from_data(messages, "caroescm") == Origin.AGENT


def test_one_agent_commit_among_many_human_commits_still_flags_agent():
    messages = [
        "Human commit one",
        "Human commit two",
        "Agent commit\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
    ]
    assert detect_pr_origin_from_data(messages, "caroescm") == Origin.AGENT


def test_get_pr_context_without_event_payload_or_pr_number_explains_what_to_do(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)

    with pytest.raises(RuntimeError, match="PR #12"):
        get_pr_context()
