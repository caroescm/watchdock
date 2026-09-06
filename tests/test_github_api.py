from github_api import detect_pr_origin_from_data


def test_human_pr_no_trailers():
    assert detect_pr_origin_from_data(["Fix typo in README"], "caroescm") == "human"


def test_agent_pr_claude_trailer():
    messages = ["Add feature\n\nCo-Authored-By: Claude <noreply@anthropic.com>"]
    assert detect_pr_origin_from_data(messages, "caroescm") == "agent"


def test_agent_pr_cursor_trailer():
    messages = ["Fix bug\n\nCo-Authored-By: Cursor <cursor@example.com>"]
    assert detect_pr_origin_from_data(messages, "caroescm") == "agent"


def test_agent_pr_bot_login():
    assert detect_pr_origin_from_data(["Bump dependency"], "dependabot[bot]") == "agent"


def test_ambiguous_defaults_to_human():
    """Never default to auto-commit when origin is unclear."""
    assert detect_pr_origin_from_data([], "") == "human"


def test_trailer_detection_is_case_insensitive():
    messages = ["Add feature\n\nCO-AUTHORED-BY: CLAUDE <noreply@anthropic.com>"]
    assert detect_pr_origin_from_data(messages, "caroescm") == "agent"


def test_one_agent_commit_among_many_human_commits_still_flags_agent():
    messages = [
        "Human commit one",
        "Human commit two",
        "Agent commit\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
    ]
    assert detect_pr_origin_from_data(messages, "caroescm") == "agent"
