"""Drafting a fix for a finding and getting it onto the PR.

Both delivery paths locate the stale line the same way, ``locate``: the
quoted line as a whole; failing that, the same text once the markdown
decoration the model routinely drops from a quote (a list marker, **bold**)
is ignored; failing that, one whole sentence of a line, if exactly one line
in the file contains it. A fragment that starts mid-sentence is never
matched ("use requests" must not edit "Do not use requests"), and a fix
identical to the stale line is refused, so nothing here can commit or
suggest an edit that wasn't asked for.
"""
import logging
import re
from dataclasses import dataclass

from watchdock import nim_client
from watchdock.models import Delivery, Finding
from watchdock.prompts import draft_fix_prompt

logger = logging.getLogger(__name__)

# Leading markdown decoration the model tends to leave out when it quotes a
# line: indentation, a list marker ("- ", "* ", "1. ", "1) "), a blockquote
# ">" or a heading "#". Used only to find where a line's own text starts.
_LEADING_DECORATION = re.compile(r"^\s*(?:(?:[-*+]|\d+[.)])\s+|>\s*|#{1,6}\s+)?")
# Emphasis markers the model also drops (**bold**, _italic_). Stripped from
# both sides for comparison only; the file's own text is never altered.
_EMPHASIS = re.compile(r"[*_]+")
# Where one sentence ends and the next begins inside a line.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Located:
    """Where a quoted stale line sits: the line's index and the span of that
    line the quote covers (the whole line, its text without decoration, or
    one or more whole sentences)."""
    index: int
    start: int
    end: int


def draft_fix(target_path: str, stale_line: str, reason: str) -> str:
    """Asks the model to rewrite one stale line correctly. A mechanical
    rewrite, not a judgment call: the decision that the line is wrong was
    already made by the check stage, so thinking is off here."""
    prompt = draft_fix_prompt(target_path, stale_line, reason)
    return nim_client.chat_completion(prompt, enable_thinking=False).strip()


def _comparable(text: str) -> str:
    return " ".join(_EMPHASIS.sub("", text).lower().split())


def _text_span(line: str) -> tuple[int, int]:
    """Start/end of a line's own text: after indentation and any list, quote
    or heading marker; before trailing whitespace and the line ending."""
    start = _LEADING_DECORATION.match(line).end()
    return start, max(start, len(line.rstrip()))


def _sentence_spans(line: str, start: int, end: int) -> list[tuple[int, int]]:
    """Every span of one or more consecutive whole sentences in
    line[start:end], excluding the full span itself."""
    breaks = list(_SENTENCE_BREAK.finditer(line, start, end))
    starts = [start] + [m.end() for m in breaks]
    ends = [m.start() for m in breaks] + [end]
    return [(b, e) for b in starts for e in ends if b < e and (b, e) != (start, end)]


def locate(lines: list[str], stale_line: str) -> Located | None:
    """Where ``stale_line`` is in ``lines``, or None.

    In order: the first line equal to the quote once both are stripped; the
    first line whose text equals the quote ignoring markdown decoration; a
    whole-sentence fragment of a line, only if exactly one line in the file
    has it. Anything else is a refusal to guess."""
    wanted = stale_line.strip()
    if not wanted:
        return None
    for i, line in enumerate(lines):
        if line.strip() == wanted:
            return Located(i, len(line) - len(line.lstrip()), len(line.rstrip()))

    target = _comparable(_LEADING_DECORATION.sub("", wanted, count=1))
    if not target:
        return None
    fragments: list[Located] = []
    for i, line in enumerate(lines):
        start, end = _text_span(line)
        if _comparable(line[start:end]) == target:
            return Located(i, start, end)
        fragments += [Located(i, b, e) for b, e in _sentence_spans(line, start, end)
                      if _comparable(line[b:e]) == target]
    return fragments[0] if len(fragments) == 1 else None


def _common_edges(a: str, b: str) -> tuple[int, int]:
    """Lengths of the common prefix and common suffix of a and b, the two
    never overlapping."""
    limit = min(len(a), len(b))
    prefix = 0
    while prefix < limit and a[prefix] == b[prefix]:
        prefix += 1
    suffix = 0
    while suffix < limit - prefix and a[-1 - suffix] == b[-1 - suffix]:
        suffix += 1
    return prefix, suffix


def _rewrite(line: str, at: Located, stale_line: str, fix_text: str) -> str:
    """``line`` with the located span corrected. The smallest edit that turns
    the quote into the fix is applied where it occurs inside the span, so
    decoration the model dropped from its quote survives in the file; when
    that edit can't be placed unambiguously, the whole span becomes the fix."""
    quote, fix = stale_line.strip(), fix_text.strip()
    old = line[at.start:at.end]
    prefix, suffix = _common_edges(quote, fix)
    if quote[prefix:len(quote) - suffix]:
        for context in (24, 12, 6, 0):
            lo, hi = max(0, prefix - context), suffix - context
            needle = quote[lo:len(quote) - hi] if hi > 0 else quote[lo:]
            replacement = fix[lo:len(fix) - hi] if hi > 0 else fix[lo:]
            if needle and old.count(needle) == 1:
                return line[:at.start] + old.replace(needle, replacement) + line[at.end:]
    return line[:at.start] + fix + line[at.end:]


def find_line_number(file_content: str, stale_line: str) -> int | None:
    """1-based line number of stale_line inside file_content, or None."""
    at = locate(file_content.splitlines(), stale_line)
    return None if at is None else at.index + 1


def is_noop_fix(stale_line: str, fix_text: str) -> bool:
    """True when the drafted fix would leave the line as it is."""
    return stale_line.strip() == fix_text.strip()


def apply_fix(content: str, stale_line: str, fix_text: str) -> str | None:
    """Corrects the located line in ``content`` (see ``locate``), keeping its
    indentation, decoration and line ending. Returns the new content, or None
    if the line can't be located; the caller must refuse to guess."""
    lines = content.splitlines(keepends=True)
    at = locate(lines, stale_line)
    if at is None:
        return None
    lines[at.index] = _rewrite(lines[at.index], at, stale_line, fix_text)
    return "".join(lines)


def _explanation(target_path: str, finding: Finding) -> str:
    return f"🔎 **Watchdock — {finding.kind}** in `{target_path}`: {finding.reason}"


def post_pr_suggestion(pr, target_path: str, target_content: str, finding: Finding) -> Delivery:
    """Posts a GitHub suggestion-block review comment on the exact stale line,
    with the kind of drift and why the line is wrong above the one-click fix.

    ``target_content`` must be the file as it is at the PR head: the comment
    is anchored to pr.head.sha, and a line number taken from the Action's
    checkout (the merge commit) can point at the wrong line when the base
    branch also changed the file.

    If the line can't be located (the model paraphrased instead of quoting),
    or GitHub rejects the review comment (review comments can only anchor to
    lines inside the PR's diff, and a stale doc line usually isn't), nothing
    extra is posted: the run summary already lists the stale line, the fix
    and the reason, and it is edited in place on every run. A separate
    comment per finding used to be posted here and piled up one copy per
    push, since the drift persists until a human edits the file."""
    if finding.fix is None or is_noop_fix(finding.line, finding.fix):
        return Delivery.NOT_APPLIED_NO_CHANGE

    lines = target_content.splitlines()
    at = locate(lines, finding.line)
    if at is None:
        return Delivery.IN_SUMMARY
    line_number = at.index + 1
    # A suggestion replaces the whole line, so it carries the corrected line
    # (decoration and all), not just the fix for the part the model quoted.
    corrected = _rewrite(lines[at.index], at, finding.line, finding.fix)

    try:
        # The suggestion fence is built by hand (instead of as_suggestion=True)
        # so the comment can carry the explanation above the one-click fix.
        pr.create_review_comment(
            body=f"{_explanation(target_path, finding)}\n\n```suggestion\n{corrected}\n```",
            commit=pr.head.sha,
            path=target_path,
            line=line_number,
        )
        return Delivery.SUGGESTION_POSTED
    except Exception:  # noqa: BLE001 — the summary carries the fix; say why the suggestion didn't
        logger.warning("Review comment on %s:%s rejected; the fix is reported in the run summary only",
                       target_path, line_number, exc_info=True)
        return Delivery.IN_SUMMARY


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
            message=f"Watchdock: fix {len(applied)} stale claim(s) in {target_path}",
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
            f"🔧 **Watchdock**: committed {len(applied)} fix(es) to `{target_path}` on this branch.\n\n{changes}"
        )
    except Exception:  # noqa: BLE001 — the fixes landed; a failed comment must not fail the run
        logger.warning("Committed fixes to %s but couldn't post the explanatory comment",
                       target_path, exc_info=True)
    return statuses
