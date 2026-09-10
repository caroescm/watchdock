"""Builds and posts Watchdoc's per-run summary comment on the PR.

Every run maintains exactly one summary comment — created on the first run,
edited in place on every run after that (found again via an invisible HTML
marker), so re-pushing to a PR never piles up stale bot comments. The summary
is posted on clean runs too: a green "checked N claims, nothing drifted" is
information, and silence is indistinguishable from "didn't run".
"""
import logging

from watchdoc.models import Delivery

logger = logging.getLogger(__name__)

SUMMARY_MARKER = "<!-- watchdoc-run-summary -->"

_DELIVERY_LABELS = {
    Delivery.COMMITTED: "🔧 fix committed to this branch",
    Delivery.SUGGESTION_POSTED: "💡 one-click suggestion posted",
    Delivery.FALLBACK_COMMENT: "💬 explanation posted as a PR comment",
    Delivery.NOT_APPLIED_NO_MATCH: "⚠️ couldn't auto-apply (line not matched verbatim)",
    Delivery.COMMIT_FAILED: "⚠️ couldn't commit to this branch (fork PR or read-only token?) — "
                            "posted as a suggestion instead",
}
assert set(_DELIVERY_LABELS) == set(Delivery), "every Delivery needs a label"


def build_run_summary(origin, targets, claims, findings_by_target, failed_targets=None):
    """Pure markdown builder, no API calls.

    findings_by_target: {target_path: [Finding, ...]} with `fix` and
    `delivery` already filled in. Targets with no findings map to an empty
    list.

    failed_targets: {target_path: error message} for targets whose check
    crashed. They are reported rather than hidden, so a run that couldn't
    check a file never reads as "this file is fine".
    """
    failed_targets = failed_targets or {}
    total = sum(len(f) for f in findings_by_target.values())
    affected = sum(1 for f in findings_by_target.values() if f)

    if not claims:
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
        "## 🛰️ Watchdoc — docs & agent-instruction drift check",
        "",
        headline,
        "",
        f"**PR origin:** {origin} · **Targets checked:** "
        + (", ".join(f"`{t}`" for t in targets) if targets else "none found"),
    ]

    if failed_targets:
        lines += ["", f"⚠️ **{len(failed_targets)} target(s) could not be checked** — "
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
            delivery = _DELIVERY_LABELS.get(f.delivery, "❔ not delivered")
            lines += [
                f"- **{f.type}** — {f.reason}",
                f"  - Stale: {f.line}",
                f"  - Fixed: {f.fix or ''}",
                f"  - {delivery}",
            ]

    lines += ["", "---",
              "_Powered by NVIDIA NeMo Agent Toolkit + a NIM-hosted Nemotron model. "
              "This comment is updated in place on every run._"]
    return "\n".join(lines)


def upsert_run_summary(pr, summary_markdown):
    """Creates the summary comment on first run, edits it in place after."""
    body = f"{SUMMARY_MARKER}\n{summary_markdown}"
    for comment in pr.get_issue_comments():
        if SUMMARY_MARKER in (comment.body or ""):
            comment.edit(body)
            return "summary_updated"
    pr.create_issue_comment(body)
    return "summary_posted"


def post_run_summary_safely(pr, summary_markdown):
    """The summary is reporting, not detection — a failure to post it (e.g. a
    token without pull-requests: write) must never fail the drift check
    itself. Logged loudly instead."""
    try:
        return upsert_run_summary(pr, summary_markdown)
    except Exception:
        logger.warning("Couldn't post the run summary comment (does the workflow "
                       "grant pull-requests: write?)", exc_info=True)
        return "summary_failed"
