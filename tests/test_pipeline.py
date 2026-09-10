"""Tests for the host-independent pipeline: where it looks for targets, how
several fixes to one file are delivered, and what happens when one target's
read, check or delivery fails. All model and GitHub calls are stubbed."""
import os
from unittest.mock import patch

import pytest

from fakes import FakePR, FakeRepo
from watchdoc import pipeline
from watchdoc.errors import PipelineError
from watchdoc.models import Delivery, DeliveryMode, Finding, FindingType, Origin
from watchdoc.pipeline import (
    RunOptions,
    deliver_target,
    parse_pr_number,
    read_targets,
    resolve_delivery_mode,
    resolve_repo_root,
    run_pipeline,
)


def test_resolve_repo_root_prefers_config_then_workspace_then_cwd(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path / "ws"))
    assert resolve_repo_root(str(tmp_path / "cfg")) == str(tmp_path / "cfg")
    assert resolve_repo_root() == str(tmp_path / "ws")

    monkeypatch.delenv("GITHUB_WORKSPACE")
    assert resolve_repo_root() == os.path.abspath(os.getcwd())


def test_repo_root_is_never_derived_from_the_package_location(monkeypatch, tmp_path):
    """The bug this guards against: scanning the action's own checkout (this
    repo's README/AGENTS.md) instead of the calling repository's."""
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    assert not resolve_repo_root().startswith(os.path.dirname(pipeline.__file__))


def test_parse_pr_number():
    assert parse_pr_number("check PR #12") == 12
    assert parse_pr_number("check this PR") is None
    assert parse_pr_number(None) is None


def test_resolve_delivery_mode():
    assert resolve_delivery_mode(Origin.AGENT, commit_fixes=True) == DeliveryMode.COMMIT
    assert resolve_delivery_mode(Origin.AGENT, commit_fixes=False) == DeliveryMode.SUGGEST
    assert resolve_delivery_mode(Origin.HUMAN, commit_fixes=True) == DeliveryMode.SUGGEST


def test_read_targets_reports_unreadable_files_instead_of_raising(tmp_path):
    (tmp_path / "README.md").write_text("hello\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "broken.md").write_bytes(b"\xff\xfe not utf-8")

    contents, errors = read_targets(str(tmp_path), ["README.md", "docs/broken.md"])

    assert contents == {"README.md": "hello\n"}
    assert list(errors) == ["docs/broken.md"]
    assert errors["docs/broken.md"].startswith("UnicodeDecodeError")


@pytest.fixture(autouse=True)
def head_content_unavailable(monkeypatch):
    """Delivery fetches the PR-head version of the file; by default these
    tests have no API, so it falls back to the checkout content."""
    monkeypatch.setattr(pipeline, "get_file_at", lambda repo, path, ref: None)


@pytest.fixture
def fake_drafts(monkeypatch):
    """draft_fix keyed on the stale line, so parallel drafting stays deterministic."""
    drafts = {}
    monkeypatch.setattr(pipeline, "draft_fix", lambda path, line, reason: drafts[line])
    return drafts


def test_deliver_target_anchors_to_the_pr_head_version_of_the_file(monkeypatch, fake_drafts):
    """The checkout is the merge commit; if the base branch also changed the
    file, line numbers and content there differ from the PR head that
    suggestions cite and commits replace."""
    repo, pr = FakeRepo(), FakePR()
    checkout = "Intro added on base\nUse `requests`.\n"   # merge commit: one extra line on top
    head = "Use `requests`.\n"
    fetched = []
    monkeypatch.setattr(pipeline, "get_file_at", lambda r, path, ref: fetched.append((path, ref)) or head)
    fake_drafts["Use `requests`."] = "Use `httpx`."
    findings = [Finding(line="Use `requests`.", reason="httpx now")]

    deliver_target("AGENTS.md", checkout, findings, repo, pr, DeliveryMode.COMMIT)

    assert fetched == [("AGENTS.md", pr.head.sha)]
    assert repo.updates[0]["content"] == "Use `httpx`.\n"  # head content, not the merge commit's


def test_deliver_target_commits_all_fixes_for_agent_pr_in_one_commit_and_returns_new_findings(fake_drafts):
    repo, pr = FakeRepo(), FakePR()
    content = "Use `requests`.\nRun `make test`.\n"
    fake_drafts.update({"Use `requests`.": "Use `httpx`.", "Run `make test`.": "Run `pytest`."})
    findings = [
        Finding(line="Use `requests`.", reason="httpx now"),
        Finding(line="Run `make test`.", reason="pytest now", kind=FindingType.BROKEN_REFERENCE),
    ]

    result = deliver_target("AGENTS.md", content, findings, repo, pr, DeliveryMode.COMMIT)

    assert [f.delivery for f in result] == [Delivery.COMMITTED, Delivery.COMMITTED]
    assert [f.fix for f in result] == ["Use `httpx`.", "Run `pytest`."]
    assert [f.fix for f in findings] == [None, None]  # the inputs are untouched
    assert len(repo.updates) == 1
    assert repo.updates[0]["content"] == "Use `httpx`.\nRun `pytest`.\n"


def test_deliver_target_posts_one_suggestion_per_finding_in_suggest_mode(fake_drafts):
    repo, pr = FakeRepo(), FakePR()
    fake_drafts["Use `requests`."] = "Use `httpx`."
    findings = [Finding(line="Use `requests`.", reason="httpx now")]

    result = deliver_target("AGENTS.md", "Use `requests`.\n", findings, repo, pr, DeliveryMode.SUGGEST)

    assert result[0].delivery == Delivery.SUGGESTION_POSTED
    assert repo.updates == []


def _run_pipeline_with(monkeypatch, tmp_path, files, check_result, origin=Origin.HUMAN):
    """Wires run_pipeline to fakes. `files` are written into tmp_path (bytes
    or str); `check_result` is what check_claims_against_targets returns, or
    a callable receiving the contents dict."""
    for name, body in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body) if isinstance(body, bytes) else path.write_text(body)
    pr = FakePR()
    posted = []
    check_calls = []

    def fake_check(claims, contents, n=3, max_workers=None):
        check_calls.append({"claims": claims, "contents": contents, "n": n, "max_workers": max_workers})
        return check_result(contents) if callable(check_result) else check_result

    monkeypatch.setattr(pipeline, "get_pr_context", lambda pr_number=None: (FakeRepo(), pr))
    monkeypatch.setattr(pipeline, "get_diff", lambda pr: [])
    monkeypatch.setattr(pipeline, "discover_targets", lambda root: list(files))
    monkeypatch.setattr(pipeline, "detect_pr_origin", lambda pr: origin)
    monkeypatch.setattr(pipeline, "extract_claims", lambda diff: ["something changed"])
    monkeypatch.setattr(pipeline, "check_claims_against_targets", fake_check)
    monkeypatch.setattr(pipeline, "draft_fix", lambda *a: "fixed")
    monkeypatch.setattr(pipeline, "post_run_summary_safely", lambda pr, summary: posted.append(summary))
    return pr, posted, check_calls


def test_run_pipeline_checks_all_readable_targets_in_one_call_with_configured_sizes(monkeypatch, tmp_path):
    """No per-target fan-out in the orchestrator: every readable target goes
    to the claims layer in one call, which owns the single bounded pool."""
    _, _, calls = _run_pipeline_with(
        monkeypatch, tmp_path, {"README.md": "a\n", "AGENTS.md": "b\n"},
        lambda contents: ({t: [] for t in contents}, {}))

    run_pipeline(str(tmp_path), RunOptions(ensemble_size=5, max_workers=4))

    assert len(calls) == 1
    assert calls[0]["contents"] == {"README.md": "a\n", "AGENTS.md": "b\n"}
    assert calls[0]["n"] == 5 and calls[0]["max_workers"] == 4


def test_run_pipeline_posts_a_summary_even_when_there_are_no_targets(monkeypatch, tmp_path):
    """Silence is indistinguishable from 'didn't run'."""
    _, posted, calls = _run_pipeline_with(monkeypatch, tmp_path, {}, ({}, {}))

    result = run_pipeline(str(tmp_path))

    assert calls == []
    assert len(posted) == 1 and posted[0] == result
    assert "No target files found" in result


def test_run_pipeline_reports_an_unreadable_target_and_still_posts_summary_then_fails(monkeypatch, tmp_path):
    """One target failing must not take the others down or skip the summary.
    The run fails only after everything checkable was checked and reported."""
    finding = Finding(line="l", reason="r")
    _, posted, calls = _run_pipeline_with(
        monkeypatch, tmp_path, {"README.md": "l\n", "docs/broken.md": b"\xff\xfe"},
        lambda contents: ({"README.md": [finding]}, {}))

    with pytest.raises(PipelineError, match="docs/broken.md"):
        run_pipeline(str(tmp_path))

    assert list(calls[0]["contents"]) == ["README.md"]  # the unreadable file never reaches the model
    assert len(posted) == 1
    assert "1 target(s) could not be fully processed" in posted[0]
    assert "`docs/broken.md`: UnicodeDecodeError" in posted[0]
    assert "1 stale line(s) found" in posted[0]        # the healthy target's finding is still reported
    assert "one-click suggestion posted" in posted[0]


def test_run_pipeline_reports_a_target_whose_every_check_failed(monkeypatch, tmp_path):
    _, posted, _ = _run_pipeline_with(
        monkeypatch, tmp_path, {"README.md": "l\n", "AGENTS.md": "m\n"},
        lambda contents: ({"README.md": []}, {"AGENTS.md": "All 1 claim(s) failed against AGENTS.md"}))

    with pytest.raises(PipelineError, match="AGENTS.md"):
        run_pipeline(str(tmp_path))

    assert "`AGENTS.md`: All 1 claim(s) failed" in posted[0]


def test_run_pipeline_keeps_detected_findings_when_their_delivery_fails(monkeypatch, tmp_path):
    """Detection succeeded; only delivery failed. The summary must still show
    the stale line, marked undelivered, next to the error."""
    _, posted, _ = _run_pipeline_with(
        monkeypatch, tmp_path, {"README.md": "l\n"},
        lambda contents: ({"README.md": [Finding(line="l", reason="the reason")]}, {}))
    monkeypatch.setattr(pipeline, "draft_fix", lambda *a: (_ for _ in ()).throw(ConnectionError("nim down")))

    with pytest.raises(PipelineError, match="README.md"):
        run_pipeline(str(tmp_path))

    assert "`README.md`: ConnectionError: nim down" in posted[0]
    assert "the reason" in posted[0]
    assert "not delivered" in posted[0]


def test_run_pipeline_returns_the_posted_summary_when_every_target_completes(monkeypatch, tmp_path):
    _, posted, _ = _run_pipeline_with(monkeypatch, tmp_path, {"README.md": "l\n"},
                                      lambda contents: ({"README.md": []}, {}))

    result = run_pipeline(str(tmp_path))

    assert result == posted[0]
    assert "No drift detected" in result


def test_run_pipeline_commits_on_agent_prs_only_when_commit_fixes_is_on(monkeypatch, tmp_path):
    finding = Finding(line="l", reason="r")
    pr, posted, _ = _run_pipeline_with(
        monkeypatch, tmp_path, {"README.md": "l\n"},
        lambda contents: ({"README.md": [finding]}, {}), origin=Origin.AGENT)
    with patch.object(pipeline, "commit_fixes_to_branch", return_value=[Delivery.COMMITTED]) as commit:
        run_pipeline(str(tmp_path), RunOptions(commit_fixes=True))
        run_pipeline(str(tmp_path), RunOptions(commit_fixes=False))

    assert commit.call_count == 1
    assert "fix committed to this branch" in posted[0]
    assert "one-click suggestion posted" in posted[1]
