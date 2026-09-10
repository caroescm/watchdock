from fakes import FakeComment, FakePR
from watchdoc.models import Delivery, Finding, FindingType, Origin, SummaryOutcome
from watchdoc.report import (
    DELIVERY_LABELS,
    SUMMARY_MARKER,
    build_run_summary,
    post_run_summary_safely,
    upsert_run_summary,
)


def test_every_delivery_status_has_a_label():
    assert set(DELIVERY_LABELS) == set(Delivery)


def test_build_run_summary_no_targets():
    summary = build_run_summary(Origin.HUMAN, [], [], {})

    assert "No target files found" in summary
    assert "none found" in summary


def test_build_run_summary_no_claims():
    summary = build_run_summary(Origin.HUMAN, ["README.md"], [], {})

    assert "No doc-relevant changes" in summary
    assert "README.md" in summary


def test_build_run_summary_clean_run_reports_what_was_checked():
    summary = build_run_summary(
        Origin.HUMAN, ["README.md", "AGENTS.md"],
        ["claim one", "claim two", "claim three"],
        {"README.md": [], "AGENTS.md": []},
    )

    assert "No drift detected" in summary
    assert "3 claim(s)" in summary
    assert "2 target file(s)" in summary
    assert "claim two" in summary  # claims listed in the collapsible section


def test_build_run_summary_lists_findings_with_reason_fix_and_delivery():
    findings = {
        "README.md": [Finding(
            line="Always use `requests` for HTTP calls.",
            kind=FindingType.SEMANTIC_STALENESS,
            reason="Code now uses httpx.",
            fix="Always use `httpx` for HTTP calls.",
            delivery=Delivery.SUGGESTION_POSTED,
        )],
        "AGENTS.md": [],
    }
    summary = build_run_summary(Origin.AGENT, ["README.md", "AGENTS.md"], ["a claim"], findings)

    assert "**PR origin:** agent" in summary  # the enum renders as its value
    assert "1 stale line(s) found" in summary
    assert "semantic staleness" in summary
    assert "Code now uses httpx." in summary
    assert "Always use `httpx` for HTTP calls." in summary
    assert "suggestion posted" in summary
    assert "### `AGENTS.md`" not in summary  # clean targets get no section


def test_build_run_summary_reports_targets_that_could_not_be_checked():
    """A target whose check crashed must be listed, never read as 'clean' by omission."""
    summary = build_run_summary(
        Origin.HUMAN, ["README.md", "AGENTS.md"], ["a claim"],
        {"README.md": []},
        failed_targets={"AGENTS.md": "UnicodeDecodeError: bad byte"},
    )

    assert "1 target(s) could not be fully processed" in summary
    assert "`AGENTS.md`: UnicodeDecodeError: bad byte" in summary


def test_build_run_summary_marks_undelivered_findings_instead_of_dropping_them():
    findings = {"AGENTS.md": [Finding(line="old", reason="r")]}

    summary = build_run_summary(Origin.HUMAN, ["AGENTS.md"], ["a claim"], findings,
                                failed_targets={"AGENTS.md": "ConnectionError: nim down"})

    assert "Stale: old" in summary
    assert "(no fix drafted)" in summary
    assert "not delivered" in summary


def test_build_run_summary_labels_commit_failed_delivery_without_guessing_a_cause():
    findings = {"AGENTS.md": [Finding(line="old", reason="r", fix="new", delivery=Delivery.COMMIT_FAILED)]}

    summary = build_run_summary(Origin.AGENT, ["AGENTS.md"], ["a claim"], findings)

    assert "couldn't commit to this branch" in summary
    assert "fork" not in summary


def test_upsert_creates_summary_comment_on_first_run():
    pr = FakePR()

    assert upsert_run_summary(pr, "the summary") == SummaryOutcome.POSTED
    assert len(pr.comments) == 1
    assert SUMMARY_MARKER in pr.comments[0].body


def test_upsert_edits_existing_summary_instead_of_stacking_new_ones():
    existing = FakeComment(f"{SUMMARY_MARKER}\nold summary")
    pr = FakePR(existing_comments=[FakeComment("unrelated comment"), existing])

    assert upsert_run_summary(pr, "new summary") == SummaryOutcome.UPDATED
    assert len(pr.comments) == 2  # no new comment added
    assert "new summary" in existing.body
    assert "old summary" not in existing.body


def test_post_run_summary_safely_swallows_api_errors():
    """A read-only token can detect drift but not comment; reporting failure
    must never fail the drift check itself."""
    class _BrokenPR:
        def get_issue_comments(self):
            raise RuntimeError("403 Forbidden")

    assert post_run_summary_safely(_BrokenPR(), "summary") == SummaryOutcome.FAILED
