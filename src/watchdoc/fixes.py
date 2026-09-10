"""Drafting a fix for a finding and getting it onto the PR.

Both delivery paths locate the stale line the same way, ``_line_index``: a
whole-line match with surrounding whitespace ignored. A quoted fragment is
never matched inside a longer, different line, and a fix identical to the
stale line is refused, so nothing here can commit or suggest an edit that
wasn't asked for.
"""
import logging

from watchdoc import nim_client
from watchdoc.models import Delivery, Finding
from watchdoc.prompts import draft_fix_prompt

logger = logging.getLogger(__name__)


def draft_fix(target_path: str, stale_line: str, reason: str) -> str:
    """Asks the model to rewrite one stale line correctly. A mechanical
    rewrite, not a judgment call: the decision that the line is wrong was
    already made by the check stage, so thinking is off here."""
    prompt = draft_fix_prompt(target_path, stale_line, reason)
    return nim_client.chat_completion(prompt, enable_thinking=False).strip()


def _line_index(lines: list[str], stale_line: str) -> int | None:
    """0-based index of the first line equal to stale_line once both are
    stripped, or None."""
    wanted = stale_line.strip()
    if not wanted:
        return None
    for i, line in enumerate(lines):
        if line.strip() == wanted:
            return i
    return None


def find_line_number(file_content: str, stale_line: str) -> int | None:
    """1-based line number of stale_line inside file_content, or None."""
    index = _line_index(file_content.splitlines(), stale_line)
    return None if index is None else index + 1


def is_noop_fix(stale_line: str, fix_text: str) -> bool:
    """True when the drafted fix would leave the line as it is."""
    return stale_line.strip() == fix_text.strip()


def apply_fix(content: str, stale_line: str, fix_text: str) -> str | None:
    """Replaces the whole first line matching stale_line with fix_text,
    keeping that line's indentation and line ending. Returns the new content,
    or None if no line matches; the caller must refuse to guess."""
    lines = content.splitlines(keepends=True)
    index = _line_index(lines, stale_line)
    if index is None:
        return None
    old = lines[index]
    indent = old[:len(old) - len(old.lstrip())]
    ending = "\n" if old.endswith("\n") else ""
    lines[index] = f"{indent}{fix_text.strip()}{ending}"
    return "".join(lines)


def _explanation(target_path: str, finding: Finding) -> str:
    return f"🔎 **Watchdoc — {finding.kind}** in `{target_path}`: {finding.reason}"


def post_pr_suggestion(pr, target_path: str, target_content: str, finding: Finding) -> Delivery:
    """Posts a GitHub suggestion-block review comment on the exact stale line,
    with the kind of drift and why the line is wrong above the one-click fix.

    ``target_content`` must be the file as it is at the PR head: the comment
    is anchored to pr.head.sha, and a line number taken from the Action's
    checkout (the merge commit) can point at the wrong line when the base
    branch also changed the file.

    Falls back to a plain PR comment carrying the same explanation if the
    line can't be located (the model paraphrased instead of quoting), or if
    GitHub rejects the review comment: review comments can only anchor to
    lines inside the PR's diff, and a stale doc line usually isn't."""
    if finding.fix is None or is_noop_fix(finding.line, finding.fix):
        return Delivery.NOT_APPLIED_NO_CHANGE

    explanation = _explanation(target_path, finding)
    fallback_body = (
        f"{explanation}\n\n**Stale line:**\n> {finding.line}\n\n"
        f"**Suggested replacement:**\n> {finding.fix}"
    )

    line_number = find_line_number(target_content, finding.line)
    if line_number is None:
        pr.create_issue_comment(fallback_body)
        return Delivery.FALLBACK_COMMENT

    try:
        # The suggestion fence is built by hand (instead of as_suggestion=True)
        # so the comment can carry the explanation above the one-click fix.
        pr.create_review_comment(
            body=f"{explanation}\n\n```suggestion\n{finding.fix}\n```",
            commit=pr.head.sha,
            path=target_path,
            line=line_number,
        )
        return Delivery.SUGGESTION_POSTED
    except Exception:  # noqa: BLE001 — degrade to a plain comment, but say why
        logger.warning("Review comment on %s:%s rejected; posting a plain PR comment instead",
                       target_path, line_number, exc_info=True)
        pr.create_issue_comment(fallback_body)
        return Delivery.FALLBACK_COMMENT


def commit_fixes_to_branch(repo, pr, target_path: str, target_content: str, findings: list[Finding]) -> list[Delivery]:
    """Applies every drafted fix for one target file to a single working copy
    and commits the result to the PR's branch in one commit. The reviewer
    still sees the fix as part of the PR before merge.

    ``target_content`` must be the file as it is at the PR head, since that
    is the version the commit replaces. Returns one Delivery per finding, in
    order: COMMITTED, NOT_APPLIED_NO_MATCH (the line wasn't found in the file
    as it stood after the earlier fixes), NOT_APPLIED_NO_CHANGE (the fix is
    the stale line unchanged), or COMMIT_FAILED (GitHub refused the commit
    and the fix went out through the suggestion path instead).

    Applying all fixes to one running copy is what makes several findings in
    the same file safe: committing each from the original content would have
    each commit silently revert the previous one.

    Leaves one PR comment listing exactly what changed and why, so committed
    fixes never land silently."""
    statuses: list[Delivery] = []
    applied: list[tuple[int, Finding]] = []
    content = target_content
    for index, finding in enumerate(findings):
        if finding.fix is None or is_noop_fix(finding.line, finding.fix):
            statuses.append(Delivery.NOT_APPLIED_NO_CHANGE)
            continue
        updated = apply_fix(content, finding.line, finding.fix)
        if updated is None:
            statuses.append(Delivery.NOT_APPLIED_NO_MATCH)
            continue
        content = updated
        applied.append((index, finding))
        statuses.append(Delivery.COMMITTED)

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
    except Exception:  # noqa: BLE001 — fork PR, read-only token, moved branch; re-deliver as suggestions
        logger.warning("Couldn't commit fixes to %s on %s; delivering them as review suggestions",
                       target_path, pr.head.ref, exc_info=True)
        for index, finding in applied:
            post_pr_suggestion(pr, target_path, target_content, finding)
            statuses[index] = Delivery.COMMIT_FAILED
        return statuses

    changes = "\n\n".join(
        f"**{finding.kind}** — {finding.reason}\n\n"
        f"**Old:**\n> {finding.line}\n\n**New:**\n> {finding.fix}"
        for _, finding in applied
    )
    try:
        pr.create_issue_comment(
            f"🔧 **Watchdoc**: committed {len(applied)} fix(es) to `{target_path}` on this branch.\n\n{changes}"
        )
    except Exception:  # noqa: BLE001 — the fixes landed; a failed comment must not fail the run
        logger.warning("Committed fixes to %s but couldn't post the explanatory comment",
                       target_path, exc_info=True)
    return statuses
