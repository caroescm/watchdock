from fixes import _find_line_number, commit_fix_to_branch, post_pr_suggestion


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

    def __init__(self, review_comment_error=None):
        self.issue_comments = []
        self.review_comments = []
        self._review_comment_error = review_comment_error

    def create_issue_comment(self, body):
        self.issue_comments.append(body)

    def create_review_comment(self, body, commit, path, line):
        if self._review_comment_error:
            raise self._review_comment_error
        self.review_comments.append({"body": body, "path": path, "line": line})


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


def test_commit_fix_to_branch_leaves_explanatory_comment():
    """A directly-committed fix must never land silently — the PR gets a
    comment saying what changed, in which file, and why."""
    repo = _FakeRepo()
    pr = _FakePR()
    original = "Always use `requests` for HTTP calls.\n"

    commit_fix_to_branch(
        repo, pr, "AGENTS.md", original,
        stale_line="Always use `requests` for HTTP calls.",
        fix_text="Always use `httpx` for HTTP calls.",
        finding_type="semantic staleness",
        reason="Code now uses httpx.",
    )

    assert len(pr.issue_comments) == 1
    comment = pr.issue_comments[0]
    assert "semantic staleness" in comment
    assert "AGENTS.md" in comment
    assert "Code now uses httpx." in comment
    assert "`requests`" in comment and "`httpx`" in comment


def test_post_pr_suggestion_includes_type_and_reason_with_suggestion_fence():
    pr = _FakePR()
    content = "Always use `requests` for HTTP calls.\n"

    result = post_pr_suggestion(
        pr, "README.md", content,
        stale_line="Always use `requests` for HTTP calls.",
        fix_text="Always use `httpx` for HTTP calls.",
        finding_type="semantic staleness",
        reason="Code now uses httpx.",
    )

    assert result == "suggestion_posted"
    body = pr.review_comments[0]["body"]
    assert "semantic staleness" in body
    assert "Code now uses httpx." in body
    assert "```suggestion\nAlways use `httpx` for HTTP calls.\n```" in body


def test_post_pr_suggestion_falls_back_to_comment_when_github_rejects_line():
    """GitHub only allows review comments on lines inside the PR's diff; a
    stale doc line usually isn't (the PR changed code, not the doc). A
    rejected review comment must degrade to a plain PR comment, not crash."""
    pr = _FakePR(review_comment_error=RuntimeError("422 line not in diff"))
    content = "Always use `requests` for HTTP calls.\n"

    result = post_pr_suggestion(
        pr, "README.md", content,
        stale_line="Always use `requests` for HTTP calls.",
        fix_text="Always use `httpx` for HTTP calls.",
        finding_type="semantic staleness",
        reason="Code now uses httpx.",
    )

    assert result == "fallback_comment"
    assert len(pr.issue_comments) == 1
    assert "Code now uses httpx." in pr.issue_comments[0]


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
