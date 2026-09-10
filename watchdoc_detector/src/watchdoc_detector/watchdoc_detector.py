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

The pipeline is synchronous, thread-pooled code. It runs in a worker thread
via ``asyncio.to_thread`` so NAT's event loop is never blocked.
"""
import asyncio
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor

from nat.plugin_api import Builder
from nat.plugin_api import FunctionBaseConfig
from nat.plugin_api import FunctionInfo
from nat.plugin_api import register_function
from pydantic import Field

from watchdoc import nim_client
from watchdoc.claims import check_claims_against_target, extract_claims, parse_claims
from watchdoc.fixes import commit_fixes_to_branch, draft_fix, post_pr_suggestion
from watchdoc.github_api import detect_pr_origin, get_diff, get_pr_context
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
    ensemble_size: int = Field(
        default=3, ge=1,
        description="Independent samples per claim/target check; findings are unioned.",
    )
    max_concurrent_nim_calls: int = Field(
        default=nim_client._MAX_CONCURRENT_REQUESTS, ge=1,
        description="Upper bound on simultaneously open NIM streams.",
    )
    nim_timeout_seconds: int = Field(
        default=nim_client._TIMEOUT_SECONDS, ge=1,
        description="Per-request timeout. Thinking-mode calls have been observed to take ~19 minutes.",
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


def _process_target(target_path, claims, repo, pr, origin, repo_root, ensemble_size):
    """Runs the full check -> fix -> deliver flow for one target file and
    returns the delivered findings (each finding dict gains 'fix' and
    'delivery'). Targets are independent of each other (different files), so
    this is safe to run concurrently across targets rather than one at a time."""
    with open(os.path.join(repo_root, target_path), encoding="utf-8") as f:
        target_content = f.read()

    # Each claim is checked independently (ensemble per claim, all claims in
    # parallel), findings unioned — one call per narrow claim keeps every
    # generation safely under NIM's ~10-minute server-side cap, and a single
    # sample has shown real run-to-run variance (the same true finding
    # sometimes missed), so we don't trust just one.
    findings = check_claims_against_target(claims, target_path, target_content, n=ensemble_size)
    logger.info("Merged findings for %s: %s", target_path, findings)

    for finding in findings:
        finding["fix"] = draft_fix(target_path, finding["line"], finding["reason"])

    if origin == "agent":
        # All fixes for this file go into one commit, applied to one running
        # copy of the content; committing per finding from the original
        # content would make each commit revert the previous one.
        statuses = commit_fixes_to_branch(repo, pr, target_path, target_content, findings)
        for finding, status in zip(findings, statuses):
            finding["delivery"] = status
    else:
        for finding in findings:
            finding["delivery"] = post_pr_suggestion(
                pr, target_path, target_content, finding["line"], finding["fix"],
                finding_type=finding.get("type", "drift"), reason=finding["reason"],
            )

    return findings


def _format_target_report(target_path, findings):
    lines = [f"=== {target_path} ==="]
    for finding in findings:
        lines.append(f"- [{finding.get('type', '?')}] {finding['line']}")
        lines.append(f"  reason: {finding['reason']}")
        lines.append(f"  fix: {finding['fix']}")
        lines.append(f"  delivery: {finding['delivery']}")
    return "\n".join(lines)


def run_pipeline(repo_root, ensemble_size=3, pr_number=None):
    """The whole Watchdoc run, synchronous. Failure policy, consistent with the
    layers below it (a dropped ensemble sample or claim is a missing vote):
    one target's crash is recorded and reported, the other targets still
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

    claims_raw = extract_claims(diff)
    logger.info("Extracted claims:\n%s", claims_raw)

    claims = parse_claims(claims_raw)
    logger.info("Parsed %d individual claim(s)", len(claims))

    if not claims:
        post_run_summary_safely(pr, build_run_summary(origin, targets, [], {}))
        return "No claims in docs/instruction files are affected by this diff."

    def guarded(target_path):
        try:
            return target_path, _process_target(
                target_path, claims, repo, pr, origin, repo_root, ensemble_size), None
        except Exception as e:  # noqa: BLE001 — every failure must be reported, whatever it is
            logger.exception("Checking %s failed", target_path)
            return target_path, [], f"{type(e).__name__}: {e}"

    with ThreadPoolExecutor(max_workers=len(targets)) as executor:
        results = list(executor.map(guarded, targets))

    findings_by_target = {t: findings for t, findings, error in results if error is None}
    failed_targets = {t: error for t, _, error in results if error is not None}

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
            run_pipeline, repo_root, config.ensemble_size, parse_pr_number(task))

    yield FunctionInfo.from_fn(run_watchdoc_check, description=run_watchdoc_check.__doc__)
