"""The whole Watchdoc run, as plain synchronous code with no host dependency.

Phases: locate the PR and its targets, extract claims from the diff, check
every (target x claim x sample) through one bounded pool, then draft and
deliver fixes per target, and finally post the one summary comment.

Failure policy, consistent with the layers below it (a dropped ensemble
sample or claim is a missing vote): a target that can't be read, checked
or delivered is recorded and reported, the other targets still complete,
the summary is still posted, and only then does the run fail, so the
Action goes red without hiding what was checked.

The NAT entry point in the ``watchdoc_detector`` package is a thin adapter
over ``run_pipeline``; everything testable lives here.
"""
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

from watchdoc import env
from watchdoc.claims import DEFAULT_ENSEMBLE_SIZE, check_claims_against_targets, extract_claims, pool_size
from watchdoc.errors import PipelineError
from watchdoc.fixes import commit_fixes_to_branch, draft_fix, post_pr_suggestion
from watchdoc.github_api import detect_pr_origin, get_diff, get_file_at, get_pr_context
from watchdoc.models import DeliveryMode, Finding, Origin
from watchdoc.report import build_run_summary, post_run_summary_safely
from watchdoc.targets import discover_targets

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunOptions:
    """Per-run knobs, passed as one object so call sites can't misorder them."""
    ensemble_size: int = DEFAULT_ENSEMBLE_SIZE
    max_workers: int | None = None      # None: size pools to the NIM concurrency cap
    commit_fixes: bool = True           # False: never commit, even on agent PRs
    pr_number: int | None = None        # None: take the PR from the event payload


DEFAULT_OPTIONS = RunOptions()

_PR_NUMBER = re.compile(r"#(\d+)")


def resolve_repo_root(configured: str | None = None) -> str:
    """The repository to scan is the *calling* repository, never this action's
    own checkout. Inside GitHub Actions that is $GITHUB_WORKSPACE; for a local
    run it is the current directory unless the config says otherwise."""
    root = configured or env.optional("GITHUB_WORKSPACE") or os.getcwd()
    return os.path.abspath(root)


def parse_pr_number(task: str | None) -> int | None:
    """Pulls an explicit PR number out of the run input ("check PR #12"), so a
    local run can target a PR without a GitHub event payload. None means
    'use the event payload'."""
    match = _PR_NUMBER.search(task or "")
    return int(match.group(1)) if match else None


def read_targets(repo_root: str, targets: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    """Reads every target once, up front. Returns (contents, errors), each
    keyed by target path; a file that can't be read as text is an error for
    that target, not a crash for the run."""
    contents: dict[str, str] = {}
    errors: dict[str, str] = {}
    for target_path in targets:
        try:
            with open(os.path.join(repo_root, target_path), encoding="utf-8") as f:
                contents[target_path] = f.read()
        except (OSError, UnicodeDecodeError) as e:
            logger.exception("Reading %s failed", target_path)
            errors[target_path] = f"{type(e).__name__}: {e}"
    return contents, errors


def resolve_delivery_mode(origin: Origin, commit_fixes: bool) -> DeliveryMode:
    """Agent-authored PRs get direct commits, unless the operator turned that
    off; everyone else gets review suggestions."""
    if origin == Origin.AGENT and commit_fixes:
        return DeliveryMode.COMMIT
    return DeliveryMode.SUGGEST


def draft_fixes(target_path: str, findings: list[Finding], max_workers: int | None = None) -> list[Finding]:
    """One drafted fix per finding, through a pool bounded like the checks."""
    with ThreadPoolExecutor(max_workers=pool_size(len(findings), max_workers)) as executor:
        fixes = list(executor.map(lambda f: draft_fix(target_path, f.line, f.reason), findings))
    return [replace(finding, fix=fix) for finding, fix in zip(findings, fixes, strict=True)]


def deliver_target(
    target_path: str,
    target_content: str,
    findings: list[Finding],
    repo,
    pr,
    mode: DeliveryMode,
    max_workers: int | None = None,
) -> list[Finding]:
    """Drafts a fix for every finding in one target and delivers them: one
    commit for the whole file in COMMIT mode, one suggestion per finding in
    SUGGEST mode. Returns new Finding objects carrying ``fix`` and ``delivery``.

    Detection ran on ``target_content`` from the checkout, which on a
    pull_request event is the merge commit. Delivery anchors to the PR head
    (suggestions cite pr.head.sha; commits replace the head file), so the
    head version is fetched here and used for both. If it can't be fetched
    the checkout content is used, which is right whenever the base branch
    didn't touch the file."""
    drafted = draft_fixes(target_path, findings, max_workers)

    head_content = get_file_at(repo, target_path, pr.head.sha)
    content = head_content if head_content is not None else target_content

    if mode == DeliveryMode.COMMIT:
        statuses = commit_fixes_to_branch(repo, pr, target_path, content, drafted)
    else:
        statuses = [post_pr_suggestion(pr, target_path, content, finding) for finding in drafted]
    return [replace(finding, delivery=status) for finding, status in zip(drafted, statuses, strict=True)]


def run_pipeline(repo_root: str, options: RunOptions = DEFAULT_OPTIONS) -> str:
    """Runs everything and returns the summary markdown that was posted to
    the PR. Raises PipelineError, after posting, if any target could not be
    fully processed."""
    repo, pr = get_pr_context(options.pr_number)
    targets = discover_targets(repo_root)
    origin = detect_pr_origin(pr)
    logger.info("Repo root: %s", repo_root)
    logger.info("Discovered targets: %s", targets)
    logger.info("PR origin: %s", origin)

    if not targets:
        return _post_summary(pr, build_run_summary(origin, [], [], {}))

    claims = extract_claims(get_diff(pr))
    logger.info("Extracted %d claim(s): %s", len(claims), claims)
    if not claims:
        return _post_summary(pr, build_run_summary(origin, targets, [], {}))

    contents, failed_targets = read_targets(repo_root, targets)
    findings_by_target, check_errors = check_claims_against_targets(
        claims, contents, n=options.ensemble_size, max_workers=options.max_workers)
    failed_targets.update(check_errors)

    mode = resolve_delivery_mode(origin, options.commit_fixes)
    for target_path, findings in findings_by_target.items():
        if not findings:
            continue
        try:
            findings_by_target[target_path] = deliver_target(
                target_path, contents[target_path], findings, repo, pr, mode, options.max_workers)
        except Exception as e:  # noqa: BLE001 — every failure must be reported, whatever it is
            # The findings stay in the summary, marked undelivered; only the
            # delivery failed, not the detection.
            logger.exception("Delivering fixes for %s failed", target_path)
            failed_targets[target_path] = f"{type(e).__name__}: {e}"

    summary = _post_summary(
        pr, build_run_summary(origin, targets, claims, findings_by_target, failed_targets))

    if failed_targets:
        raise PipelineError(
            f"Watchdoc could not fully process {len(failed_targets)} target(s): "
            + ", ".join(failed_targets))
    return summary


def _post_summary(pr, summary: str) -> str:
    outcome = post_run_summary_safely(pr, summary)
    logger.info("Run summary: %s", outcome)
    return summary
