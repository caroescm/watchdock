import logging

from nim_client import chat_completion

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
    explanation = f"🔎 **Still — {finding_type}** in `{target_path}`: {reason}"
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


def commit_fix_to_branch(repo, pr, target_path, target_content, stale_line, fix_text,
                         finding_type="drift", reason=""):
    """Directly commits the corrected file content to the PR's branch. Used only
    for agent-authored PRs (see detect_pr_origin) — the reviewer still sees the
    fix as part of the PR before merge, just without an extra manual step.

    Also leaves a PR comment saying exactly what was changed and why, so the
    committed fix never lands silently — without it the only trace would be
    an extra commit in the branch history."""
    updated_content = target_content.replace(stale_line, fix_text)

    if updated_content == target_content:
        # stale_line didn't match anything verbatim — refuse to guess, don't commit
        return "not_applied_no_match"

    current_file = repo.get_contents(target_path, ref=pr.head.ref)
    repo.update_file(
        path=target_path,
        message=f"Still: fix stale claim in {target_path}",
        content=updated_content,
        sha=current_file.sha,
        branch=pr.head.ref,
    )

    try:
        pr.create_issue_comment(
            f"🔧 **Still — {finding_type}**: committed a fix to `{target_path}` on this branch.\n\n"
            f"**Why:** {reason}\n\n**Old:**\n> {stale_line}\n\n**New:**\n> {fix_text}"
        )
    except Exception:
        # The fix itself landed; a failed comment shouldn't fail the run.
        logger.warning("Committed fix to %s but couldn't post the explanatory comment",
                       target_path, exc_info=True)
    return "committed"
