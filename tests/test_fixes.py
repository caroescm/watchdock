from fixes import _find_line_number, commit_fix_to_branch


def test_find_line_number_exact_match():
    content = "line one\nline two\nline three\n"
    assert _find_line_number(content, "line two") == 2


def test_find_line_number_tolerates_surrounding_whitespace():
    content = "line one\n  line two  \nline three\n"
    assert _find_line_number(content, "line two") == 2


def test_find_line_number_no_match_returns_none():
    content = "line one\nline two\n"
    assert _find_line_number(content, "not in here") is None


class _FakeContentFile:
    def __init__(self, sha):
        self.sha = sha


class _FakeRepo:
    """Duck-typed stand-in for a PyGithub Repository — no real API calls."""

    def __init__(self):
        self.updated = None

    def get_contents(self, path, ref):
        return _FakeContentFile(sha="fake-sha-123")

    def update_file(self, path, message, content, sha, branch):
        self.updated = {"path": path, "message": message, "content": content, "branch": branch}


class _FakePR:
    head = type("Head", (), {"ref": "some-branch", "sha": "fake-head-sha"})()


def test_commit_fix_to_branch_applies_replacement_when_line_matches():
    repo = _FakeRepo()
    pr = _FakePR()
    original = "Always use `requests` for HTTP calls.\nOther content.\n"

    result = commit_fix_to_branch(
        repo, pr, "AGENTS.md", original,
        stale_line="Always use `requests` for HTTP calls.",
        fix_text="Always use `httpx` for HTTP calls.",
    )

    assert result == "committed"
    assert "httpx" in repo.updated["content"]
    assert "requests" not in repo.updated["content"]


def test_commit_fix_to_branch_refuses_when_no_exact_match():
    """If the stale_line doesn't match verbatim, refuse rather than guess —
    this is a deliberate safety property, not just an edge case."""
    repo = _FakeRepo()
    pr = _FakePR()
    original = "Always use `requests` for HTTP calls.\n"

    result = commit_fix_to_branch(
        repo, pr, "AGENTS.md", original,
        stale_line="This text does not appear anywhere in the file",
        fix_text="Some fix",
    )

    assert result == "not_applied_no_match"
    assert repo.updated is None
