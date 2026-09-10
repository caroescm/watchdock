from watchdoc.fixes import _find_line_number, apply_fix, commit_fixes_to_branch, post_pr_suggestion


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

    def __init__(self, update_error=None):
        self.updated = None
        self.update_calls = 0
        self._update_error = update_error

    def get_contents(self, path, ref):
        return _FakeContentFile(sha="fake-sha-123")

    def update_file(self, path, message, content, sha, branch):
        self.update_calls += 1
        if self._update_error:
            raise self._update_error
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


def _fix(line, fix, type_="semantic staleness", reason="Code now uses httpx."):
    return {"line": line, "fix": fix, "type": type_, "reason": reason}


def test_apply_fix_replaces_first_verbatim_occurrence_only():
    content = "use `requests`\nuse `requests`\n"
    assert apply_fix(content, "use `requests`", "use `httpx`") == "use `httpx`\nuse `requests`\n"


def test_apply_fix_returns_none_when_line_absent():
    assert apply_fix("some content\n", "not in here", "fix") is None


def test_commit_fixes_to_branch_applies_replacement_when_line_matches():
    repo = _FakeRepo()
    pr = _FakePR()
    original = "Always use `requests` for HTTP calls.\nOther content.\n"

    statuses = commit_fixes_to_branch(repo, pr, "AGENTS.md", original, [
        _fix("Always use `requests` for HTTP calls.", "Always use `httpx` for HTTP calls."),
    ])

    assert statuses == ["committed"]
    assert "httpx" in repo.updated["content"]
    assert "requests" not in repo.updated["content"]


def test_commit_fixes_to_branch_applies_all_fixes_to_one_file_in_one_commit():
    """Regression test for the overwrite bug: committing each finding from the
    original content made the second commit silently revert the first fix.
    Every fix must be applied to the same running copy and land together."""
    repo = _FakeRepo()
    pr = _FakePR()
    original = "Always use `requests` for HTTP calls.\nRun `make test` before pushing.\n"

    statuses = commit_fixes_to_branch(repo, pr, "AGENTS.md", original, [
        _fix("Always use `requests` for HTTP calls.", "Always use `httpx` for HTTP calls."),
        _fix("Run `make test` before pushing.", "Run `pytest` before pushing.", "broken reference", "make target removed"),
    ])

    assert statuses == ["committed", "committed"]
    assert repo.update_calls == 1
    assert repo.updated["content"] == "Always use `httpx` for HTTP calls.\nRun `pytest` before pushing.\n"
    assert "2 stale claim(s)" in repo.updated["message"]


def test_commit_fixes_to_branch_leaves_explanatory_comment():
    """A directly-committed fix must never land silently — the PR gets a
    comment saying what changed, in which file, and why."""
    repo = _FakeRepo()
    pr = _FakePR()
    original = "Always use `requests` for HTTP calls.\n"

    commit_fixes_to_branch(repo, pr, "AGENTS.md", original, [
        _fix("Always use `requests` for HTTP calls.", "Always use `httpx` for HTTP calls."),
    ])

    assert len(pr.issue_comments) == 1
    comment = pr.issue_comments[0]
    assert "semantic staleness" in comment
    assert "AGENTS.md" in comment
    assert "Code now uses httpx." in comment
    assert "`requests`" in comment and "`httpx`" in comment


def test_commit_fixes_to_branch_refuses_when_no_exact_match():
    """If the stale line doesn't match verbatim, refuse rather than guess —
    this is a deliberate safety property, not just an edge case."""
    repo = _FakeRepo()
    pr = _FakePR()
    original = "Always use `requests` for HTTP calls.\n"

    statuses = commit_fixes_to_branch(repo, pr, "AGENTS.md", original, [
        _fix("This text does not appear anywhere in the file", "Some fix"),
    ])

    assert statuses == ["not_applied_no_match"]
    assert repo.update_calls == 0
    assert pr.issue_comments == []


def test_commit_fixes_to_branch_skips_unmatched_and_commits_the_rest():
    repo = _FakeRepo()
    pr = _FakePR()
    original = "Always use `requests` for HTTP calls.\n"

    statuses = commit_fixes_to_branch(repo, pr, "AGENTS.md", original, [
        _fix("Not in the file", "x"),
        _fix("Always use `requests` for HTTP calls.", "Always use `httpx` for HTTP calls."),
    ])

    assert statuses == ["not_applied_no_match", "committed"]
    assert repo.update_calls == 1


def test_commit_fixes_to_branch_falls_back_to_suggestion_when_github_rejects_commit():
    """A fork PR (head branch isn't in this repo) or a read-only token makes
    update_file fail. That must not crash the target; the fix is delivered as
    a review suggestion instead and the summary says the commit failed."""
    repo = _FakeRepo(update_error=RuntimeError("404 branch not found"))
    pr = _FakePR()
    original = "Always use `requests` for HTTP calls.\n"

    statuses = commit_fixes_to_branch(repo, pr, "AGENTS.md", original, [
        _fix("Always use `requests` for HTTP calls.", "Always use `httpx` for HTTP calls."),
    ])

    assert statuses == ["commit_failed"]
    assert len(pr.review_comments) == 1
    assert "```suggestion\nAlways use `httpx` for HTTP calls.\n```" in pr.review_comments[0]["body"]


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
