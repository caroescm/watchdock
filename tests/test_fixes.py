from fakes import FakePR, FakeRepo
from watchdock.fixes import apply_fix, commit_fixes_to_branch, find_line_number, is_noop_fix, post_pr_suggestion
from watchdock.models import Delivery, Finding, FindingType


def _finding(line, fix, kind=FindingType.SEMANTIC_STALENESS, reason="Code now uses httpx."):
    return Finding(line=line, fix=fix, kind=kind, reason=reason)


def test_find_line_number_exact_match():
    assert find_line_number("line one\nline two\nline three\n", "line two") == 2


def test_find_line_number_tolerates_surrounding_whitespace():
    assert find_line_number("line one\n  line two  \nline three\n", "line two") == 2


def test_find_line_number_no_match_returns_none():
    assert find_line_number("line one\nline two\n", "not in here") is None
    assert find_line_number("line one\n", "") is None


def test_apply_fix_replaces_the_first_matching_line_only():
    content = "use `requests`\nuse `requests`\n"
    assert apply_fix(content, "use `requests`", "use `httpx`") == "use `httpx`\nuse `requests`\n"


def test_apply_fix_never_matches_a_fragment_inside_a_longer_line():
    """A quoted fragment must not edit a different line that happens to
    contain it; that is the path that commits."""
    content = "Do not use `requests` here.\nuse `requests`\n"
    assert apply_fix(content, "use `requests`", "use `httpx`") == "Do not use `requests` here.\nuse `httpx`\n"
    assert apply_fix("Do not use `requests` here.\n", "use `requests`", "use `httpx`") is None


def test_apply_fix_keeps_indentation_and_line_ending():
    indented = "- a\n    - use `requests`\n- c"
    assert apply_fix(indented, "- use `requests`", "- use `httpx`") == "- a\n    - use `httpx`\n- c"
    assert apply_fix("use `requests`", "use `requests`", "use `httpx`") == "use `httpx`"


def test_apply_fix_returns_none_when_line_absent():
    assert apply_fix("some content\n", "not in here", "fix") is None


def test_is_noop_fix_ignores_surrounding_whitespace():
    assert is_noop_fix("use `requests`", "  use `requests`  ")
    assert not is_noop_fix("use `requests`", "use `httpx`")


def test_commit_fixes_to_branch_applies_replacement_when_line_matches():
    repo, pr = FakeRepo(), FakePR()
    original = "Always use `requests` for HTTP calls.\nOther content.\n"

    statuses = commit_fixes_to_branch(repo, pr, "AGENTS.md", original, [
        _finding("Always use `requests` for HTTP calls.", "Always use `httpx` for HTTP calls."),
    ])

    assert statuses == [Delivery.COMMITTED]
    assert repo.updated["content"] == "Always use `httpx` for HTTP calls.\nOther content.\n"


def test_commit_fixes_to_branch_applies_all_fixes_to_one_file_in_one_commit():
    """Committing each finding from the original content would make the
    second commit silently revert the first fix. Every fix is applied to the
    same running copy and they land together."""
    repo, pr = FakeRepo(), FakePR()
    original = "Always use `requests` for HTTP calls.\nRun `make test` before pushing.\n"

    statuses = commit_fixes_to_branch(repo, pr, "AGENTS.md", original, [
        _finding("Always use `requests` for HTTP calls.", "Always use `httpx` for HTTP calls."),
        _finding("Run `make test` before pushing.", "Run `pytest` before pushing.",
                 FindingType.BROKEN_REFERENCE, "make target removed"),
    ])

    assert statuses == [Delivery.COMMITTED, Delivery.COMMITTED]
    assert repo.update_calls == 1
    assert repo.updated["content"] == "Always use `httpx` for HTTP calls.\nRun `pytest` before pushing.\n"
    assert "2 stale claim(s)" in repo.updated["message"]


def test_commit_fixes_to_branch_leaves_explanatory_comment():
    """A directly-committed fix must never land silently."""
    repo, pr = FakeRepo(), FakePR()

    commit_fixes_to_branch(repo, pr, "AGENTS.md", "Always use `requests` for HTTP calls.\n", [
        _finding("Always use `requests` for HTTP calls.", "Always use `httpx` for HTTP calls."),
    ])

    assert len(pr.issue_comments) == 1
    comment = pr.issue_comments[0]
    assert "semantic staleness" in comment
    assert "AGENTS.md" in comment
    assert "Code now uses httpx." in comment
    assert "`requests`" in comment and "`httpx`" in comment


def test_commit_fixes_to_branch_refuses_when_no_exact_match():
    """If the stale line doesn't match verbatim, refuse rather than guess."""
    repo, pr = FakeRepo(), FakePR()

    statuses = commit_fixes_to_branch(repo, pr, "AGENTS.md", "Always use `requests` for HTTP calls.\n", [
        _finding("This text does not appear anywhere in the file", "Some fix"),
    ])

    assert statuses == [Delivery.NOT_APPLIED_NO_MATCH]
    assert repo.update_calls == 0
    assert pr.issue_comments == []


def test_commit_fixes_to_branch_refuses_a_fix_that_changes_nothing():
    repo, pr = FakeRepo(), FakePR()
    line = "Always use `requests` for HTTP calls."

    statuses = commit_fixes_to_branch(repo, pr, "AGENTS.md", f"{line}\n",
                                      [_finding(line, f"  {line}  "), _finding(line, None)])

    assert statuses == [Delivery.NOT_APPLIED_NO_CHANGE, Delivery.NOT_APPLIED_NO_CHANGE]
    assert repo.update_calls == 0


def test_commit_fixes_to_branch_skips_unmatched_and_commits_the_rest():
    repo, pr = FakeRepo(), FakePR()

    statuses = commit_fixes_to_branch(repo, pr, "AGENTS.md", "Always use `requests` for HTTP calls.\n", [
        _finding("Not in the file", "x"),
        _finding("Always use `requests` for HTTP calls.", "Always use `httpx` for HTTP calls."),
    ])

    assert statuses == [Delivery.NOT_APPLIED_NO_MATCH, Delivery.COMMITTED]
    assert repo.update_calls == 1


def test_commit_fixes_to_branch_falls_back_to_suggestion_when_github_rejects_commit():
    """A fork PR (head branch isn't in this repo) or a read-only token makes
    update_file fail. The fix is delivered as a review suggestion instead and
    the status says the commit failed."""
    repo, pr = FakeRepo(update_error=RuntimeError("404 branch not found")), FakePR()

    statuses = commit_fixes_to_branch(repo, pr, "AGENTS.md", "Always use `requests` for HTTP calls.\n", [
        _finding("Always use `requests` for HTTP calls.", "Always use `httpx` for HTTP calls."),
    ])

    assert statuses == [Delivery.COMMIT_FAILED]
    assert len(pr.review_comments) == 1
    assert "```suggestion\nAlways use `httpx` for HTTP calls.\n```" in pr.review_comments[0]["body"]


def test_post_pr_suggestion_includes_kind_and_reason_with_suggestion_fence():
    pr = FakePR()
    finding = _finding("Always use `requests` for HTTP calls.", "Always use `httpx` for HTTP calls.")

    result = post_pr_suggestion(pr, "README.md", "Always use `requests` for HTTP calls.\n", finding)

    assert result == Delivery.SUGGESTION_POSTED
    posted = pr.review_comments[0]
    assert posted["path"] == "README.md" and posted["line"] == 1 and posted["commit"] == pr.head.sha
    assert "semantic staleness" in posted["body"]
    assert "Code now uses httpx." in posted["body"]
    assert "```suggestion\nAlways use `httpx` for HTTP calls.\n```" in posted["body"]


def test_post_pr_suggestion_falls_back_to_comment_when_line_is_not_found():
    pr = FakePR()

    result = post_pr_suggestion(pr, "README.md", "something else\n", _finding("paraphrased line", "fix"))

    assert result == Delivery.FALLBACK_COMMENT
    assert pr.review_comments == []
    assert "paraphrased line" in pr.issue_comments[0] and "fix" in pr.issue_comments[0]


def test_post_pr_suggestion_falls_back_to_comment_when_github_rejects_line():
    """GitHub only allows review comments on lines inside the PR's diff; a
    stale doc line usually isn't. A rejected review comment degrades to a
    plain PR comment, not a crash."""
    pr = FakePR(review_comment_error=RuntimeError("422 line not in diff"))
    finding = _finding("Always use `requests` for HTTP calls.", "Always use `httpx` for HTTP calls.")

    result = post_pr_suggestion(pr, "README.md", "Always use `requests` for HTTP calls.\n", finding)

    assert result == Delivery.FALLBACK_COMMENT
    assert len(pr.issue_comments) == 1
    assert "Code now uses httpx." in pr.issue_comments[0]


def test_post_pr_suggestion_refuses_a_fix_that_changes_nothing():
    pr = FakePR()

    assert post_pr_suggestion(pr, "README.md", "stale\n", _finding("stale", "stale")) == Delivery.NOT_APPLIED_NO_CHANGE
    assert post_pr_suggestion(pr, "README.md", "stale\n", _finding("stale", None)) == Delivery.NOT_APPLIED_NO_CHANGE
    assert pr.review_comments == [] and pr.issue_comments == []


def test_post_pr_suggestion_logs_why_it_fell_back(caplog):
    pr = FakePR(review_comment_error=RuntimeError("422 line not in diff"))

    with caplog.at_level("WARNING"):
        post_pr_suggestion(pr, "README.md", "stale\n", _finding("stale", "fresh"))

    assert "README.md:1" in caplog.text and "422 line not in diff" in caplog.text
