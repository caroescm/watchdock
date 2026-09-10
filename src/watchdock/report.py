"""Builds and posts Watchdock's per-run summary comment on the PR.

Every run maintains exactly one summary comment: created on the first run,
edited in place on every run after that (found again via an invisible HTML
marker), so re-pushing to a PR never piles up stale bot comments. The summary
is posted on every run, including clean ones and ones that found nothing to
check: a green "checked N claims, nothing drifted" is information, and
silence is indistinguishable from "didn't run".

This is the one renderer for a run's results. The NAT entry point returns
the same markdown as the workflow's output.
"""
import logging

from watchdock.models import Delivery, Finding, Origin, SummaryOutcome

logger = logging.getLogger(__name__)

SUMMARY_MARKER = "<!-- watchdock-run-summary -->"

DELIVERY_LABELS: dict[Delivery, str] = {
    Delivery.COMMITTED: "🔧 fix committed to this branch",
    Delivery.SUGGESTION_POSTED: "💡 one-click suggestion posted",
    Delivery.IN_SUMMARY: "📝 fix listed here only: the stale line is outside this PR's diff, "
                         "so GitHub can't attach a one-click suggestion to it",
    Delivery.NOT_APPLIED_NO_MATCH: "⚠️ not applied: the stale line wasn't found verbatim in the file",
    Delivery.NOT_APPLIED_NO_CHANGE: "⚠️ not applied: the drafted fix was identical to the stale line",
    Delivery.COMMIT_FAILED: "⚠️ couldn't commit to this branch; delivered as a review suggestion "
                            "or listed here instead (the Action log says why the commit was refused)",
}
UNDELIVERED_LABEL = "❔ not delivered (delivery for this file failed; see above)"


def build_run_summary(
    origin: Origin | str,
    targets: list[str],
    claims: list[str],
    findings_by_target: dict[str, list[Finding]],
    failed_targets: dict[str, str] | None = None,
    extraction_error: str | None = None,
) -> str:
    """Pure markdown builder, no API calls.

    ``findings_by_target``: {target_path: [Finding, ...]}. Targets with no
    findings map to an empty list. A finding whose ``delivery`` is None was
    detected but never delivered; it is listed as such rather than dropped.

    ``failed_targets``: {target_path: error message} for targets that could
    not be read, checked or delivered. They are reported rather than hidden,
    so a run that couldn't check a file never reads as "this file is fine".

    ``extraction_error``: set when claim extraction itself failed, so no
    target was checked at all. Without it an empty ``claims`` would read as
    "no doc-relevant changes", which is the opposite of what happened.
    """
    failed_targets = failed_targets or {}
    total = sum(len(f) for f in findings_by_target.values())
    affected = sum(1 for f in findings_by_target.values() if f)

    if not targets:
        headline = ("ℹ️ **No target files found** — this repository has no README, docs "
                    "directory or agent-instruction file to check, and no `.watchdock.yml` naming any.")
    elif extraction_error:
        headline = ("⚠️ **Drift check could not run** — extracting claims from this diff failed "
                    f"({extraction_error}), so no target was checked. See the Action log for the traceback; "
                    "re-running the job usually succeeds.")
    elif not claims:
        headline = ("✅ **No doc-relevant changes** — nothing in this diff could "
                    "affect docs or agent-instruction files.")
    elif total == 0:
        headline = (f"✅ **No drift detected** — {len(claims)} claim(s) from this diff "
                    f"checked against {len(targets)} target file(s); everything is "
                    f"still accurate.")
    else:
        headline = (f"🔴 **{total} stale line(s) found** in {affected} file(s) — "
                    f"fixes delivered below.")

    lines = [
        "## 🛰️ Watchdock — docs & agent-instruction drift check",
        "",
        headline,
        "",
        f"**PR origin:** {origin} · **Targets checked:** "
        + (", ".join(f"`{t}`" for t in targets) if targets else "none found"),
    ]

    if failed_targets:
        lines += ["", f"⚠️ **{len(failed_targets)} target(s) could not be fully processed** — "
                      "see the Action log for the full traceback:"]
        lines += [f"- `{path}`: {error}" for path, error in failed_targets.items()]

    if claims:
        lines += ["", f"<details><summary>🔍 Claims extracted from this diff ({len(claims)})</summary>", ""]
        lines += [f"{i}. {c}" for i, c in enumerate(claims, 1)]
        lines += ["", "</details>"]

    for target_path, findings in findings_by_target.items():
        if not findings:
            continue
        lines += ["", f"### `{target_path}`"]
        for f in findings:
            delivery = DELIVERY_LABELS[f.delivery] if f.delivery is not None else UNDELIVERED_LABEL
            lines += [
                f"- **{f.kind}** — {f.reason}",
                f"  - Stale: {f.line}",
                f"  - Fixed: {f.fix if f.fix is not None else '(no fix drafted)'}",
                f"  - {delivery}",
            ]

    lines += ["", "---",
              "_Powered by NVIDIA NeMo Agent Toolkit + a NIM-hosted Nemotron model. "
              "This comment is updated in place on every run._"]
    return "\n".join(lines)


def upsert_run_summary(pr, summary_markdown: str) -> SummaryOutcome:
    """Creates the summary comment on first run, edits it in place after."""
    body = f"{SUMMARY_MARKER}\n{summary_markdown}"
    for comment in pr.get_issue_comments():
        if SUMMARY_MARKER in (comment.body or ""):
            comment.edit(body)
            return SummaryOutcome.UPDATED
    pr.create_issue_comment(body)
    return SummaryOutcome.POSTED


def post_run_summary_safely(pr, summary_markdown: str) -> SummaryOutcome:
    """The summary is reporting, not detection: a failure to post it (e.g. a
    token without pull-requests: write) must never fail the drift check
    itself. Logged loudly instead."""
    try:
        return upsert_run_summary(pr, summary_markdown)
    except Exception:  # noqa: BLE001 — reporting must never fail the run; logged with traceback
        logger.warning("Couldn't post the run summary comment (does the workflow "
                       "grant pull-requests: write?)", exc_info=True)
        return SummaryOutcome.FAILED
