import pytest

from watchdoc.errors import ConfigError
from watchdoc.github_api import NO_PATCH_PLACEHOLDER, detect_pr_origin_from_data, get_diff, get_pr_context
from watchdoc.models import DiffEntry, Origin


def test_human_pr_no_trailers():
    assert detect_pr_origin_from_data(["Fix typo in README"], "caroescm") == Origin.HUMAN


def test_agent_pr_claude_trailer():
    messages = ["Add feature\n\nCo-Authored-By: Claude <noreply@anthropic.com>"]
    assert detect_pr_origin_from_data(messages, "caroescm") == Origin.AGENT


def test_agent_pr_cursor_trailer():
    messages = ["Fix bug\n\nCo-Authored-By: Cursor <cursor@example.com>"]
    assert detect_pr_origin_from_data(messages, "caroescm") == Origin.AGENT


def test_known_agent_bot_login_is_agent():
    assert detect_pr_origin_from_data(["Add feature"], "devin-ai-integration[bot]") == Origin.AGENT


def test_dependency_and_ci_bots_are_not_agents():
    """Dependabot and Renovate force-push over their branches, so a fix
    committed there is lost. Their PRs get suggestions like a human's."""
    for login in ["dependabot[bot]", "renovate[bot]", "github-actions[bot]", "some-unknown[bot]"]:
        assert detect_pr_origin_from_data(["Bump dependency"], login) == Origin.HUMAN


def test_trailer_must_start_a_line_and_name_a_known_agent():
    assert detect_pr_origin_from_data(["Release notes: shipped-by: the release team"], "x") == Origin.HUMAN
    assert detect_pr_origin_from_data(["Fix\n\nShipped-by: Codex"], "x") == Origin.AGENT
    assert detect_pr_origin_from_data(["Fix\n\nCo-Authored-By: Alice <a@example.com>"], "x") == Origin.HUMAN


def test_ambiguous_defaults_to_human():
    """Never default to auto-commit when origin is unclear."""
    assert detect_pr_origin_from_data([], "") == Origin.HUMAN
    assert detect_pr_origin_from_data([None], "") == Origin.HUMAN


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


class _FakeFile:
    def __init__(self, filename, patch):
        self.filename = filename
        self.patch = patch


class _FakeFilesPR:
    def __init__(self, files):
        self._files = files

    def get_files(self):
        return self._files


def test_get_diff_replaces_missing_patch_with_placeholder():
    """PyGithub returns patch=None for binary, oversized and renamed files;
    the string 'None' must never reach the prompt."""
    pr = _FakeFilesPR([_FakeFile("a.py", "-old\n+new"), _FakeFile("logo.png", None)])

    assert get_diff(pr) == [
        DiffEntry("a.py", "-old\n+new"),
        DiffEntry("logo.png", NO_PATCH_PLACEHOLDER),
    ]


def test_get_pr_context_without_event_payload_or_pr_number_explains_what_to_do(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)

    with pytest.raises(ConfigError, match="PR #12"):
        get_pr_context()


def test_get_pr_context_names_the_missing_variable(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")

    with pytest.raises(ConfigError, match="GITHUB_TOKEN"):
        get_pr_context(pr_number=1)
