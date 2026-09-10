"""Tests for the NAT entry point's orchestration: where it looks for targets,
how several fixes to one file are delivered, and what happens when one
target's check crashes. All model and GitHub calls are stubbed."""
import os
from unittest.mock import patch

import pytest

pytest.importorskip("nat", reason="watchdoc_detector requires nvidia-nat")

from conftest import FakePR as _FakePR, FakeRepo as _FakeRepo  # noqa: E402
from watchdoc.models import Delivery, Finding, Origin  # noqa: E402
from watchdoc_detector import watchdoc_detector as det  # noqa: E402


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


@pytest.fixture(autouse=True)
def head_content_unavailable(monkeypatch):
    """Delivery fetches the PR-head version of the file; by default these
    tests have no API, so it falls back to the checkout content."""
    monkeypatch.setattr(det, "get_file_at", lambda repo, path, ref: None)


def test_deliver_target_anchors_to_the_pr_head_version_of_the_file(monkeypatch):
    """The checkout is the merge commit; if the base branch also changed the
    file, line numbers and content there differ from the PR head that
    suggestions cite and commits replace."""
    repo, pr = _FakeRepo(), _FakePR()
    checkout = "Intro added on base\nUse `requests`.\n"   # merge commit: one extra line on top
    head = "Use `requests`.\n"
    fetched = []
    monkeypatch.setattr(det, "get_file_at", lambda r, path, ref: fetched.append((path, ref)) or head)
    findings = [Finding(line="Use `requests`.", type="semantic staleness", reason="httpx now")]

    with patch.object(det, "draft_fix", return_value="Use `httpx`."):
        det.deliver_target("AGENTS.md", checkout, findings, repo, pr, Origin.AGENT)

    assert fetched == [("AGENTS.md", pr.head.sha)]
    assert repo.updates[0]["content"] == "Use `httpx`.\n"  # head content, not the merge commit's


def test_deliver_target_commits_all_fixes_for_agent_pr_in_one_commit():
    repo, pr = _FakeRepo(), _FakePR()
    content = "Use `requests`.\nRun `make test`.\n"
    findings = [
        Finding(line="Use `requests`.", type="semantic staleness", reason="httpx now"),
        Finding(line="Run `make test`.", type="broken reference", reason="pytest now"),
    ]

    with patch.object(det, "draft_fix", side_effect=["Use `httpx`.", "Run `pytest`."]):
        result = det.deliver_target("AGENTS.md", content, findings, repo, pr, Origin.AGENT)

    assert [f.delivery for f in result] == [Delivery.COMMITTED, Delivery.COMMITTED]
    assert [f.fix for f in result] == ["Use `httpx`.", "Run `pytest`."]
    assert len(repo.updates) == 1
    assert repo.updates[0]["content"] == "Use `httpx`.\nRun `pytest`.\n"


def test_deliver_target_posts_one_suggestion_per_finding_for_human_pr():
    repo, pr = _FakeRepo(), _FakePR()
    findings = [Finding(line="Use `requests`.", type="semantic staleness", reason="httpx now")]

    with patch.object(det, "draft_fix", return_value="Use `httpx`."):
        det.deliver_target("AGENTS.md", "Use `requests`.\n", findings, repo, pr, Origin.HUMAN)

    assert findings[0].delivery == Delivery.SUGGESTION_POSTED
    assert repo.updates == []


def test_read_targets_reports_unreadable_files_instead_of_raising(tmp_path):
    (tmp_path / "README.md").write_text("hello\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "broken.md").write_bytes(b"\xff\xfe not utf-8")

    contents, errors = det.read_targets(str(tmp_path), ["README.md", "docs/broken.md"])

    assert contents == {"README.md": "hello\n"}
    assert list(errors) == ["docs/broken.md"]
    assert errors["docs/broken.md"].startswith("UnicodeDecodeError")


def _run_pipeline_with(monkeypatch, tmp_path, files, check_result):
    """Wires run_pipeline to fakes. `files` are written into tmp_path (bytes
    or str); `check_result` is what check_claims_against_targets returns, or
    a callable receiving its kwargs."""
    for name, body in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body) if isinstance(body, bytes) else path.write_text(body)
    pr = _FakePR()
    posted = []
    check_calls = []

    def fake_check(claims, contents, n=3, max_workers=None):
        check_calls.append({"claims": claims, "contents": contents, "n": n, "max_workers": max_workers})
        return check_result(contents) if callable(check_result) else check_result

    monkeypatch.setattr(det, "get_pr_context", lambda pr_number=None: (_FakeRepo(), pr))
    monkeypatch.setattr(det, "get_diff", lambda pr: [{"filename": "a.py", "patch": "+x"}])
    monkeypatch.setattr(det, "discover_targets", lambda root: list(files))
    monkeypatch.setattr(det, "detect_pr_origin", lambda pr: Origin.HUMAN)
    monkeypatch.setattr(det, "extract_claims", lambda diff: ["something changed"])
    monkeypatch.setattr(det, "check_claims_against_targets", fake_check)
    monkeypatch.setattr(det, "draft_fix", lambda *a: "fixed")
    monkeypatch.setattr(det, "post_run_summary_safely", lambda pr, summary: posted.append(summary))
    return pr, posted, check_calls


def test_run_pipeline_checks_all_readable_targets_in_one_call_with_configured_sizes(monkeypatch, tmp_path):
    """No per-target fan-out in the orchestrator: every readable target goes
    to the claims layer in one call, which owns the single bounded pool."""
    _, _, calls = _run_pipeline_with(
        monkeypatch, tmp_path, {"README.md": "a\n", "AGENTS.md": "b\n"},
        lambda contents: ({t: [] for t in contents}, {}))

    det.run_pipeline(str(tmp_path), ensemble_size=5, max_workers=4)

    assert len(calls) == 1
    assert calls[0]["contents"] == {"README.md": "a\n", "AGENTS.md": "b\n"}
    assert calls[0]["n"] == 5 and calls[0]["max_workers"] == 4


def test_run_pipeline_reports_an_unreadable_target_and_still_posts_summary_then_fails(monkeypatch, tmp_path):
    """One target failing must not take the others down or skip the summary.
    The run fails only after everything checkable was checked and reported."""
    finding = Finding(line="l", type="t", reason="r")
    _, posted, calls = _run_pipeline_with(
        monkeypatch, tmp_path, {"README.md": "l\n", "docs/broken.md": b"\xff\xfe"},
        lambda contents: ({"README.md": [finding]}, {}))

    with pytest.raises(RuntimeError, match="docs/broken.md"):
        det.run_pipeline(str(tmp_path))

    assert list(calls[0]["contents"]) == ["README.md"]  # the unreadable file never reaches the model
    assert len(posted) == 1
    assert "1 target(s) could not be checked" in posted[0]
    assert "`docs/broken.md`: UnicodeDecodeError" in posted[0]
    assert "1 stale line(s) found" in posted[0]  # the healthy target's finding is still reported
    assert finding.delivery == Delivery.SUGGESTION_POSTED


def test_run_pipeline_reports_a_target_whose_every_check_failed(monkeypatch, tmp_path):
    _, posted, _ = _run_pipeline_with(
        monkeypatch, tmp_path, {"README.md": "l\n", "AGENTS.md": "m\n"},
        lambda contents: ({"README.md": []}, {"AGENTS.md": "All 1 claim(s) failed against AGENTS.md"}))

    with pytest.raises(RuntimeError, match="AGENTS.md"):
        det.run_pipeline(str(tmp_path))

    assert "`AGENTS.md`: All 1 claim(s) failed" in posted[0]


def test_run_pipeline_reports_a_delivery_failure_per_target(monkeypatch, tmp_path):
    _, posted, _ = _run_pipeline_with(
        monkeypatch, tmp_path, {"README.md": "l\n"},
        lambda contents: ({"README.md": [Finding(line="l", type="t", reason="r")]}, {}))
    monkeypatch.setattr(det, "draft_fix", lambda *a: (_ for _ in ()).throw(ConnectionError("nim down")))

    with pytest.raises(RuntimeError, match="README.md"):
        det.run_pipeline(str(tmp_path))

    assert "`README.md`: ConnectionError: nim down" in posted[0]


def test_run_pipeline_succeeds_when_every_target_completes(monkeypatch, tmp_path):
    _, posted, _ = _run_pipeline_with(monkeypatch, tmp_path, {"README.md": "l\n"},
                                      lambda contents: ({"README.md": []}, {}))

    assert det.run_pipeline(str(tmp_path)) == "No drift detected in any target file."
    assert "No drift detected" in posted[0]
