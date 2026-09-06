from nim_client import chat_completion


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


def post_pr_suggestion(pr, target_path, target_content, stale_line, fix_text):
    """Posts a GitHub suggestion-block review comment on the exact stale line.
    Falls back to a plain PR comment if the exact line can't be pinpointed
    (e.g. the model paraphrased instead of quoting verbatim)."""
    line_number = _find_line_number(target_content, stale_line)

    if line_number is None:
        pr.create_issue_comment(
            f"⚠️ Still found a stale claim in `{target_path}` but couldn't pinpoint the "
            f"exact line for a suggestion.\n\n**Old:** {stale_line}\n\n**Suggested:** {fix_text}"
        )
        return "fallback_comment"

    pr.create_review_comment(
        body=fix_text,
        commit=pr.head.sha,
        path=target_path,
        line=line_number,
        as_suggestion=True,
    )
    return "suggestion_posted"


def commit_fix_to_branch(repo, pr, target_path, target_content, stale_line, fix_text):
    """Directly commits the corrected file content to the PR's branch. Used only
    for agent-authored PRs (see detect_pr_origin) — the reviewer still sees the
    fix as part of the PR before merge, just without an extra manual step."""
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
    return "committed"
