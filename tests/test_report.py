from report import SUMMARY_MARKER, build_run_summary, upsert_run_summary, post_run_summary_safely


def test_build_run_summary_no_claims():
    summary = build_run_summary("human", ["README.md"], [], {})

    assert "No doc-relevant changes" in summary
    assert "README.md" in summary


def test_build_run_summary_clean_run_reports_what_was_checked():
    summary = build_run_summary(
        "human", ["README.md", "AGENTS.md"],
        ["claim one", "claim two", "claim three"],
        {"README.md": [], "AGENTS.md": []},
    )

    assert "No drift detected" in summary
    assert "3 claim(s)" in summary
    assert "2 target file(s)" in summary
    assert "claim two" in summary  # claims listed in the collapsible section


def test_build_run_summary_lists_findings_with_reason_fix_and_delivery():
    findings = {
        "README.md": [{
            "line": "Always use `requests` for HTTP calls.",
            "type": "semantic staleness",
            "reason": "Code now uses httpx.",
            "fix": "Always use `httpx` for HTTP calls.",
            "delivery": "suggestion_posted",
        }],
        "AGENTS.md": [],
    }
    summary = build_run_summary("agent", ["README.md", "AGENTS.md"], ["a claim"], findings)

    assert "1 stale line(s) found" in summary
    assert "semantic staleness" in summary
    assert "Code now uses httpx." in summary
    assert "Always use `httpx` for HTTP calls." in summary
    assert "suggestion posted" in summary
    assert "### `AGENTS.md`" not in summary  # clean targets get no section


class _FakeComment:
    def __init__(self, body):
        self.body = body

    def edit(self, body):
        self.body = body


class _FakePR:
    def __init__(self, existing_comments=None):
        self.comments = list(existing_comments or [])

    def get_issue_comments(self):
        return list(self.comments)

    def create_issue_comment(self, body):
        self.comments.append(_FakeComment(body))


def test_upsert_creates_summary_comment_on_first_run():
    pr = _FakePR()

    result = upsert_run_summary(pr, "the summary")

    assert result == "summary_posted"
    assert len(pr.comments) == 1
    assert SUMMARY_MARKER in pr.comments[0].body


def test_upsert_edits_existing_summary_instead_of_stacking_new_ones():
    existing = _FakeComment(f"{SUMMARY_MARKER}\nold summary")
    pr = _FakePR([_FakeComment("unrelated comment"), existing])

    result = upsert_run_summary(pr, "new summary")

    assert result == "summary_updated"
    assert len(pr.comments) == 2  # no new comment added
    assert "new summary" in existing.body
    assert "old summary" not in existing.body


def test_post_run_summary_safely_swallows_api_errors():
    """A read-only token can detect drift but not comment — reporting failure
    must never fail the drift check itself."""
    class _BrokenPR:
        def get_issue_comments(self):
            raise RuntimeError("403 Forbidden")

    assert post_run_summary_safely(_BrokenPR(), "summary") == "summary_failed"
