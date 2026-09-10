"""NeMo Agent Toolkit entry point for Watchdoc.

NAT's role here is deliberate and limited: it is the host. It registers this
function, parses ``config.yml`` into :class:`WatchdocDetectorFunctionConfig`,
and runs it via ``nat run``. The model calls themselves go through
``watchdoc.nim_client`` (an OpenAI-compatible client against NVIDIA NIM), not
NAT's ``llms:`` abstraction, because the pipeline depends on two things that
abstraction does not expose: the per-call Nemotron ``enable_thinking`` switch
sent as ``chat_template_kwargs``, and whole-call retries around a streamed
response. Everything an operator would want to tune is therefore a field on
the function config below, applied to ``nim_client`` at registration time.

The pipeline is synchronous code fanning out over one bounded thread pool
(sized to the NIM concurrency cap). It runs in a worker thread via
``asyncio.to_thread`` so NAT's event loop is never blocked.
"""
import asyncio
import logging
import os
import re

from nat.plugin_api import Builder
from nat.plugin_api import FunctionBaseConfig
from nat.plugin_api import FunctionInfo
from nat.plugin_api import register_function
from pydantic import Field

from watchdoc import nim_client
from watchdoc.claims import check_claims_against_targets, extract_claims
from watchdoc.fixes import commit_fixes_to_branch, draft_fix, post_pr_suggestion
from watchdoc.github_api import detect_pr_origin, get_diff, get_file_at, get_pr_context
from watchdoc.models import Origin
from watchdoc.report import build_run_summary, post_run_summary_safely
from watchdoc.targets import discover_targets

logger = logging.getLogger(__name__)


class WatchdocDetectorFunctionConfig(FunctionBaseConfig, name="watchdoc_detector"):
    """
    Watchdoc drift detector: checks docs and AI-agent instruction files for semantic drift against a PR diff.
    """

    repo_root: str | None = Field(
        default=None,
        description="Checkout to scan for target files. Defaults to $GITHUB_WORKSPACE (the "
                    "calling repository inside a GitHub Action), then the current directory.",
    )
    model: str = Field(
        default=nim_client.MODEL,
        description="NIM model id used for every call.",
    )
    temperature: float = Field(
        default=nim_client.TEMPERATURE, ge=0.0, le=2.0,
        description="Sampling temperature. 0.0 is the setting the benchmark was measured at.",
    )
    ensemble_size: int = Field(
        default=3, ge=1,
        description="Independent samples per claim/target check; findings are unioned.",
    )
    max_concurrent_nim_calls: int = Field(
        default=nim_client.MAX_CONCURRENT_REQUESTS, ge=1,
        description="Upper bound on simultaneously open NIM streams.",
    )
    nim_timeout_seconds: int = Field(
        default=nim_client._TIMEOUT_SECONDS, ge=1,
        description="Per-request timeout. Thinking-mode calls have been observed to take ~19 minutes.",
    )
    commit_fixes: bool = Field(
        default=True,
        description="Commit fixes directly onto agent-authored PR branches. Needs `contents: write`; "
                    "set false to deliver every fix as a review suggestion and drop that permission.",
    )


_PR_NUMBER = re.compile(r"#(\d+)")


def resolve_repo_root(configured=None):
    """The repository to scan is the *calling* repository, never this action's
    own checkout. Inside GitHub Actions that is $GITHUB_WORKSPACE; for a local
    run it is the current directory unless the config says otherwise."""
    root = configured or os.environ.get("GITHUB_WORKSPACE") or os.getcwd()
    return os.path.abspath(root)


def parse_pr_number(task):
    """Pulls an explicit PR number out of the run input ("check PR #12"), so a
    local run can target a PR without a GitHub event payload. None means
    'use the event payload'."""
    match = _PR_NUMBER.search(task or "")
    return int(match.group(1)) if match else None


def read_targets(repo_root, targets):
    """Reads every target once, up front. Returns (contents, errors), each
    keyed by target path; a file that can't be read as text is an error for
    that target, not a crash for the run."""
    contents, errors = {}, {}
    for target_path in targets:
        try:
            with open(os.path.join(repo_root, target_path), encoding="utf-8") as f:
                contents[target_path] = f.read()
        except Exception as e:  # noqa: BLE001 — reported per target
            logger.exception("Reading %s failed", target_path)
            errors[target_path] = f"{type(e).__name__}: {e}"
    return contents, errors


def deliver_target(target_path, target_content, findings, repo, pr, origin, commit_fixes=True):
    """Drafts a fix for every finding in one target and delivers them — one
    commit for agent PRs, one suggestion per finding for human PRs. Each
    Finding gains `fix` and `delivery`.

    Detection ran on `target_content` from the checkout, which on a
    pull_request event is the merge commit. Delivery anchors to the PR head
    (suggestions cite pr.head.sha; commits replace the head file), so the
    head version is fetched here and used for both. If it can't be fetched
    the checkout content is used, which is right whenever the base branch
    didn't touch the file."""
    for finding in findings:
        finding.fix = draft_fix(target_path, finding.line, finding.reason)

    head_content = get_file_at(repo, target_path, pr.head.sha)
    if head_content is not None:
        target_content = head_content

    if origin == Origin.AGENT and commit_fixes:
        # All fixes for this file go into one commit, applied to one running
        # copy of the content; committing per finding from the original
        # content would make each commit revert the previous one.
        statuses = commit_fixes_to_branch(repo, pr, target_path, target_content, findings)
        for finding, status in zip(findings, statuses):
            finding.delivery = status
    else:
        for finding in findings:
            finding.delivery = post_pr_suggestion(
                pr, target_path, target_content, finding.line, finding.fix,
                finding_type=finding.type, reason=finding.reason,
            )
    return findings


def _format_target_report(target_path, findings):
    lines = [f"=== {target_path} ==="]
    for finding in findings:
        lines.append(f"- [{finding.type}] {finding.line}")
        lines.append(f"  reason: {finding.reason}")
        lines.append(f"  fix: {finding.fix}")
        lines.append(f"  delivery: {finding.delivery}")
    return "\n".join(lines)


def run_pipeline(repo_root, ensemble_size=3, pr_number=None, max_workers=None, commit_fixes=True):
    """The whole Watchdoc run, synchronous, in three phases: read every
    target, check all (target x claim x sample) combinations through one
    bounded pool, then draft and deliver fixes per target.

    Failure policy, consistent with the layers below it (a dropped ensemble
    sample or claim is a missing vote): a target that can't be read,
    checked, or delivered is recorded and reported, the other targets still
    complete, the summary is still posted, and only then does the run fail
    so the Action goes red without hiding what was checked."""
    repo, pr = get_pr_context(pr_number)
    diff = get_diff(pr)
    targets = discover_targets(repo_root)

    logger.info("Repo root: %s", repo_root)
    logger.info("Discovered targets: %s", targets)

    if not targets:
        return "No doc or instruction-file targets found in this repo."

    origin = detect_pr_origin(pr)
    logger.info("PR origin: %s", origin)

    claims = extract_claims(diff)
    logger.info("Extracted %d claim(s): %s", len(claims), claims)

    if not claims:
        post_run_summary_safely(pr, build_run_summary(origin, targets, [], {}))
        return "No claims in docs/instruction files are affected by this diff."

    contents, failed_targets = read_targets(repo_root, targets)
    findings_by_target, check_errors = check_claims_against_targets(
        claims, contents, n=ensemble_size, max_workers=max_workers)
    failed_targets.update(check_errors)

    for target_path, findings in findings_by_target.items():
        if not findings:
            continue
        try:
            deliver_target(target_path, contents[target_path], findings, repo, pr, origin, commit_fixes)
        except Exception as e:  # noqa: BLE001 — every failure must be reported, whatever it is
            logger.exception("Delivering fixes for %s failed", target_path)
            failed_targets[target_path] = f"{type(e).__name__}: {e}"
    for target_path in failed_targets:
        findings_by_target.pop(target_path, None)

    # One persistent summary comment per PR — posted on clean runs too
    # (a green "checked, nothing drifted" is information; silence is
    # indistinguishable from "didn't run"), edited in place on re-runs.
    post_run_summary_safely(
        pr, build_run_summary(origin, targets, claims, findings_by_target, failed_targets))

    if failed_targets:
        raise RuntimeError(
            f"Watchdoc could not check {len(failed_targets)} target(s): "
            + ", ".join(failed_targets))

    reports = [_format_target_report(t, f) for t, f in findings_by_target.items() if f]

    if not reports:
        return "No drift detected in any target file."

    header = f"PR #{pr.number}: {pr.title} (origin: {origin})"
    return "\n\n".join([header] + reports)


@register_function(config_type=WatchdocDetectorFunctionConfig)
async def watchdoc_detector_function(config: WatchdocDetectorFunctionConfig, builder: Builder):
    """
    Registers the Watchdoc drift-detection workflow (addressable via `watchdoc_detector` in configuration).
    """
    nim_client.configure(
        model=config.model,
        temperature=config.temperature,
        timeout_seconds=config.nim_timeout_seconds,
        max_concurrent_requests=config.max_concurrent_nim_calls,
    )
    repo_root = resolve_repo_root(config.repo_root)

    async def run_watchdoc_check(task: str) -> str:
        """
        Runs the full Watchdoc pipeline against a PR: discovers target files,
        fetches the diff, extracts claims, checks each target for drift, and for
        every real finding drafts a fix and delivers it — as a suggestion comment
        for human-authored PRs, or a direct commit for agent-authored PRs. The PR
        comes from the GitHub event payload, or from a "#<number>" in the input.
        """
        return await asyncio.to_thread(
            run_pipeline, repo_root, config.ensemble_size, parse_pr_number(task),
            config.max_concurrent_nim_calls, config.commit_fixes)

    yield FunctionInfo.from_fn(run_watchdoc_check, description=run_watchdoc_check.__doc__)
