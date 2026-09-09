import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor

from nat.plugin_api import Builder
from nat.plugin_api import FunctionBaseConfig
from nat.plugin_api import FunctionInfo
from nat.plugin_api import register_function

logger = logging.getLogger(__name__)

# This package lives at <repo_root>/watchdoc_detector/src/watchdoc_detector/watchdoc_detector.py
# Our actual detection logic lives at <repo_root>/src/*.py — add both the repo root
# and its src/ dir to sys.path so we can import them with their existing flat names.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_SRC_DIR = os.path.join(_REPO_ROOT, "src")
for _path in (_REPO_ROOT, _SRC_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from github_api import get_pr_context, get_diff, detect_pr_origin  # noqa: E402
from targets import discover_targets  # noqa: E402
from claims import extract_claims, parse_claims, check_claims_against_target  # noqa: E402
from fixes import draft_fix, post_pr_suggestion, commit_fix_to_branch  # noqa: E402
from report import build_run_summary, post_run_summary_safely  # noqa: E402


class WatchdocDetectorFunctionConfig(FunctionBaseConfig, name="watchdoc_detector"):
    """
    Watchdoc drift detector: checks docs and AI-agent instruction files for semantic drift against a PR diff.
    """


def _process_target(target_path, claims, repo, pr, origin):
    """Runs the full check -> fix -> deliver flow for one target file and
    returns the delivered findings (each finding dict gains 'fix' and
    'delivery'). Targets are independent of each other (different files), so
    this is safe to run concurrently across targets rather than one at a time."""
    full_path = os.path.join(_REPO_ROOT, target_path)
    with open(full_path) as f:
        target_content = f.read()

    # Each claim is checked independently (3-sample ensemble per claim, all
    # claims in parallel), findings unioned — one call per narrow claim keeps
    # every generation safely under NIM's ~10-minute server-side cap, and a
    # single sample has shown real run-to-run variance (the same true finding
    # sometimes missed), so we don't trust just one.
    findings = check_claims_against_target(claims, target_path, target_content)
    logger.info("Merged findings for %s: %s", target_path, findings)

    for finding in findings:
        fix_text = draft_fix(target_path, finding["line"], finding["reason"])

        if origin == "agent":
            delivery = commit_fix_to_branch(
                repo, pr, target_path, target_content, finding["line"], fix_text,
                finding_type=finding.get("type", "drift"), reason=finding["reason"],
            )
        else:
            delivery = post_pr_suggestion(
                pr, target_path, target_content, finding["line"], fix_text,
                finding_type=finding.get("type", "drift"), reason=finding["reason"],
            )

        finding["fix"] = fix_text
        finding["delivery"] = delivery

    return findings


def _format_target_report(target_path, findings):
    lines = [f"=== {target_path} ==="]
    for finding in findings:
        lines.append(f"- [{finding.get('type', '?')}] {finding['line']}")
        lines.append(f"  reason: {finding['reason']}")
        lines.append(f"  fix: {finding['fix']}")
        lines.append(f"  delivery: {finding['delivery']}")
    return "\n".join(lines)


@register_function(config_type=WatchdocDetectorFunctionConfig)
async def watchdoc_detector_function(config: WatchdocDetectorFunctionConfig, builder: Builder):
    """
    Registers the Watchdoc drift-detection workflow (addressable via `watchdoc_detector` in configuration).
    """

    async def run_watchdoc_check(task: str) -> str:
        """
        Runs the full Watchdoc pipeline against the current PR: discovers target files,
        fetches the diff, extracts claims, checks each target for drift, and for
        every real finding drafts a fix and delivers it — as a suggestion comment
        for human-authored PRs, or a direct commit for agent-authored PRs. Target
        files are checked concurrently, since they're independent of each other.
        """
        repo, pr = get_pr_context()
        diff = get_diff(pr)
        targets = discover_targets(_REPO_ROOT)

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

        with ThreadPoolExecutor(max_workers=len(targets)) as executor:
            per_target_findings = list(executor.map(
                lambda t: _process_target(t, claims, repo, pr, origin), targets
            ))

        findings_by_target = dict(zip(targets, per_target_findings))

        # One persistent summary comment per PR — posted on clean runs too
        # (a green "checked, nothing drifted" is information; silence is
        # indistinguishable from "didn't run"), edited in place on re-runs.
        post_run_summary_safely(pr, build_run_summary(origin, targets, claims, findings_by_target))

        reports = [_format_target_report(t, f) for t, f in findings_by_target.items() if f]

        if not reports:
            return "No drift detected in any target file."

        header = f"PR #{pr.number}: {pr.title} (origin: {origin})"
        return "\n\n".join([header] + reports)

    yield FunctionInfo.from_fn(run_watchdoc_check, description=run_watchdoc_check.__doc__)
