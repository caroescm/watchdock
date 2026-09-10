import logging

from watchdoc.nim_client import chat_completion

logger = logging.getLogger(__name__)


def draft_fix(target_path, stale_line, reason):
    """Given a stale line and why it's wrong, ask the model to rewrite it correctly."""
    prompt = f"""You are fixing one line in `{target_path}` that is now inaccurate.

Stale line:
{stale_line}

Why it's wrong:
{reason}

Rewrite this line (or short paragraph, if the stale line is part of one) so it accurately
reflects the current code. Keep the same style, tone, and formatting conventions as the
original. Respond with ONLY the corrected text — no explanation, no quotes, no markdown
fences around it.
"""
    # Mechanical rewrite, not a judgment call — the hard decision (is this
    # line actually wrong, and why) was already made by check_claim_against_
    # target. Safe to run fast/thinking-off here.
    return chat_completion(prompt, enable_thinking=False).strip()


def _find_line_number(file_content, stale_line):
    """Best-effort: find the 1-indexed line number of stale_line inside file_content."""
    stale_stripped = stale_line.strip()
    for i, line in enumerate(file_content.splitlines(), start=1):
        if line.strip() == stale_stripped:
            return i
    return None


def post_pr_suggestion(pr, target_path, target_content, stale_line, fix_text,
                       finding_type="drift", reason=""):
    """Posts a GitHub suggestion-block review comment on the exact stale line,
    with an explanation of what kind of drift was found and why the line is
    wrong — not just the bare replacement text.

    Falls back to a plain PR comment carrying the same explanation if the
    exact line can't be pinpointed (e.g. the model paraphrased instead of
    quoting verbatim), or if GitHub rejects the review comment — review
    comments can only anchor to lines that are part of the PR's diff, and a
    stale doc line usually isn't (the PR changed code, not the doc)."""
    explanation = f"🔎 **Watchdoc — {finding_type}** in `{target_path}`: {reason}"
    line_number = _find_line_number(target_content, stale_line)

    fallback_body = (
        f"{explanation}\n\n**Stale line:**\n> {stale_line}\n\n"
        f"**Suggested replacement:**\n> {fix_text}"
    )

    if line_number is None:
        pr.create_issue_comment(fallback_body)
        return "fallback_comment"

    try:
        # Build the suggestion fence by hand (instead of as_suggestion=True)
        # so the comment can carry the explanation above the one-click fix.
        pr.create_review_comment(
            body=f"{explanation}\n\n```suggestion\n{fix_text}\n```",
            commit=pr.head.sha,
            path=target_path,
            line=line_number,
        )
        return "suggestion_posted"
    except Exception:
        pr.create_issue_comment(fallback_body)
        return "fallback_comment"


def apply_fix(content, stale_line, fix_text):
    """Replaces the first verbatim occurrence of stale_line in content.
    Returns the new content, or None if the line doesn't appear verbatim —
    the caller must refuse to guess rather than commit a wrong edit."""
    if not stale_line or stale_line not in content:
        return None
    return content.replace(stale_line, fix_text, 1)


def commit_fixes_to_branch(repo, pr, target_path, target_content, fixes):
    """Applies every fix for one target file to a single working copy and
    commits the result to the PR's branch in one commit. Used only for
    agent-authored PRs (see detect_pr_origin) — the reviewer still sees the
    fix as part of the PR before merge, just without an extra manual step.

    `fixes` is a list of finding dicts carrying 'line', 'fix', and optionally
    'type' and 'reason'. Returns one delivery status per fix, in order:

    - "committed": the replacement is in the commit.
    - "not_applied_no_match": the stale line wasn't found verbatim in the
      file as it stood after the earlier fixes, so it was skipped.
    - "commit_failed": the line matched but GitHub refused the commit (a fork
      PR whose branch isn't in this repo, a read-only token, a branch that
      moved). The fix is delivered as a review suggestion instead so it still
      reaches the reviewer.

    Applying all fixes to one running copy is what makes several findings in
    the same file safe: committing each finding from the original content
    would have each commit silently revert the previous one.

    Also leaves one PR comment listing exactly what changed and why, so the
    committed fixes never land silently — without it the only trace would be
    an extra commit in the branch history."""
    statuses = []
    applied = []  # (index, finding) for fixes that matched
    content = target_content
    for index, finding in enumerate(fixes):
        updated = apply_fix(content, finding["line"], finding["fix"])
        if updated is None:
            statuses.append("not_applied_no_match")
            continue
        content = updated
        applied.append((index, finding))
        statuses.append("committed")

    if not applied:
        return statuses

    try:
        current_file = repo.get_contents(target_path, ref=pr.head.ref)
        repo.update_file(
            path=target_path,
            message=f"Watchdoc: fix {len(applied)} stale claim(s) in {target_path}",
            content=content,
            sha=current_file.sha,
            branch=pr.head.ref,
        )
    except Exception:
        logger.warning("Couldn't commit fixes to %s on %s; falling back to review suggestions",
                       target_path, pr.head.ref, exc_info=True)
        for index, finding in applied:
            post_pr_suggestion(
                pr, target_path, target_content, finding["line"], finding["fix"],
                finding_type=finding.get("type", "drift"), reason=finding.get("reason", ""),
            )
            statuses[index] = "commit_failed"
        return statuses

    changes = "\n\n".join(
        f"**{finding.get('type', 'drift')}** — {finding.get('reason', '')}\n\n"
        f"**Old:**\n> {finding['line']}\n\n**New:**\n> {finding['fix']}"
        for _, finding in applied
    )
    try:
        pr.create_issue_comment(
            f"🔧 **Watchdoc**: committed {len(applied)} fix(es) to `{target_path}` on this branch.\n\n{changes}"
        )
    except Exception:
        # The fixes themselves landed; a failed comment shouldn't fail the run.
        logger.warning("Committed fixes to %s but couldn't post the explanatory comment",
                       target_path, exc_info=True)
    return statuses
