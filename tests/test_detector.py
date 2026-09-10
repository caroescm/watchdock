"""Tests for the NAT entry point's orchestration: where it looks for targets,
how several fixes to one file are delivered, and what happens when one
target's check crashes. All model and GitHub calls are stubbed."""
import os
from unittest.mock import patch

import pytest

pytest.importorskip("nat", reason="watchdoc_detector requires nvidia-nat")

from watchdoc.models import Delivery, Finding, Origin  # noqa: E402
from watchdoc_detector import watchdoc_detector as det  # noqa: E402


class _FakeContentFile:
    sha = "sha"


class _FakeRepo:
    def __init__(self):
        self.updates = []

    def get_contents(self, path, ref):
        return _FakeContentFile()

    def update_file(self, path, message, content, sha, branch):
        self.updates.append({"path": path, "content": content})


class _FakePR:
    number = 7
    title = "A PR"
    head = type("Head", (), {"ref": "feature", "sha": "abc"})()

    def __init__(self):
        self.issue_comments = []
        self.review_comments = []

    def create_issue_comment(self, body):
        self.issue_comments.append(body)

    def create_review_comment(self, body, commit, path, line):
        self.review_comments.append(body)


def test_resolve_repo_root_prefers_config_then_workspace_then_cwd(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path / "ws"))
    assert det.resolve_repo_root(str(tmp_path / "cfg")) == str(tmp_path / "cfg")
    assert det.resolve_repo_root() == str(tmp_path / "ws")

    monkeypatch.delenv("GITHUB_WORKSPACE")
    assert det.resolve_repo_root() == os.path.abspath(os.getcwd())


def test_repo_root_is_never_derived_from_the_package_location(monkeypatch, tmp_path):
    """The bug this guards against: scanning the action's own checkout (this
    repo's README/AGENTS.md) instead of the calling repository's."""
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    package_dir = os.path.dirname(det.__file__)
    assert not det.resolve_repo_root().startswith(package_dir)


def test_parse_pr_number():
    assert det.parse_pr_number("check PR #12") == 12
    assert det.parse_pr_number("check this PR") is None
    assert det.parse_pr_number(None) is None


def test_process_target_commits_all_fixes_for_agent_pr_in_one_commit(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Use `requests`.\nRun `make test`.\n")
    repo, pr = _FakeRepo(), _FakePR()
    findings = [
        Finding(line="Use `requests`.", type="semantic staleness", reason="httpx now"),
        Finding(line="Run `make test`.", type="broken reference", reason="pytest now"),
    ]

    with patch.object(det, "check_claims_against_target", return_value=findings), \
         patch.object(det, "draft_fix", side_effect=["Use `httpx`.", "Run `pytest`."]):
        result = det._process_target("AGENTS.md", ["c"], repo, pr, Origin.AGENT, str(tmp_path), 3)

    assert [f.delivery for f in result] == [Delivery.COMMITTED, Delivery.COMMITTED]
    assert [f.fix for f in result] == ["Use `httpx`.", "Run `pytest`."]
    assert len(repo.updates) == 1
    assert repo.updates[0]["content"] == "Use `httpx`.\nRun `pytest`.\n"


def test_process_target_reads_from_the_given_repo_root_and_uses_ensemble_size(tmp_path):
    (tmp_path / "README.md").write_text("hello\n")
    repo, pr = _FakeRepo(), _FakePR()

    with patch.object(det, "check_claims_against_target", return_value=[]) as check:
        det._process_target("README.md", ["c"], repo, pr, Origin.HUMAN, str(tmp_path), 5)

    check.assert_called_once_with(["c"], "README.md", "hello\n", n=5)


def _run_pipeline_with(monkeypatch, targets, process_side_effect):
    pr = _FakePR()
    posted = []
    monkeypatch.setattr(det, "get_pr_context", lambda pr_number=None: (_FakeRepo(), pr))
    monkeypatch.setattr(det, "get_diff", lambda pr: [{"filename": "a.py", "patch": "+x"}])
    monkeypatch.setattr(det, "discover_targets", lambda root: targets)
    monkeypatch.setattr(det, "detect_pr_origin", lambda pr: Origin.HUMAN)
    monkeypatch.setattr(det, "extract_claims", lambda diff: ["something changed"])
    monkeypatch.setattr(det, "_process_target", process_side_effect)
    monkeypatch.setattr(det, "post_run_summary_safely", lambda pr, summary: posted.append(summary))
    return pr, posted


def test_run_pipeline_reports_a_failed_target_and_still_posts_summary_then_fails(monkeypatch, tmp_path):
    """One target crashing must not take the others down or skip the summary.
    The run fails only after everything checkable was checked and reported."""
    def process(target_path, *args):
        if target_path == "docs/broken.md":
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        return [Finding(line="l", type="t", reason="r", fix="f", delivery=Delivery.SUGGESTION_POSTED)]

    _, posted = _run_pipeline_with(monkeypatch, ["README.md", "docs/broken.md"], process)

    with pytest.raises(RuntimeError, match="docs/broken.md"):
        det.run_pipeline(str(tmp_path))

    assert len(posted) == 1
    assert "1 target(s) could not be checked" in posted[0]
    assert "`docs/broken.md`: UnicodeDecodeError" in posted[0]
    assert "1 stale line(s) found" in posted[0]  # the healthy target's finding is still reported


def test_run_pipeline_succeeds_when_every_target_completes(monkeypatch, tmp_path):
    _, posted = _run_pipeline_with(monkeypatch, ["README.md"], lambda *a: [])

    assert det.run_pipeline(str(tmp_path)) == "No drift detected in any target file."
    assert "No drift detected" in posted[0]
