"""
Deterministic baseline — mimics Evidoc's approach: flag drift only if a
referenced path/command/symbol literally no longer exists. No LLM, no
semantic reasoning. This is the ceiling of what the closest existing
agent-instruction-file tool can do.
"""
import re

from watchdoc.models import Finding


def deterministic_check(diff, target_content):
    """Returns a list of Finding objects, or [] if nothing's flagged.

    Approach: extract quoted `identifiers` (backtick-wrapped tokens) from each
    line of target_content, then check whether that identifier still appears
    anywhere in the diff's patch text. This can only ever catch a reference
    that's been literally removed/renamed — never a semantic contradiction
    like "always use X" when the code silently switched to Y.
    """
    diff_text = "\n".join(entry["patch"] for entry in diff)

    findings = []
    for line in target_content.splitlines():
        identifiers = re.findall(r"`([^`]+)`", line)
        for identifier in identifiers:
            # Only check identifiers that look like commands/paths/symbols,
            # not prose fragments — a crude but representative heuristic.
            if not re.search(r"[a-zA-Z_./-]", identifier):
                continue
            if _identifier_removed_in_diff(identifier, diff_text):
                findings.append(Finding(
                    line=line.strip(),
                    type="broken reference",
                    reason=f"`{identifier}` no longer appears in the diff's changed content",
                ))
                break  # one finding per line is enough
    return findings


def _identifier_removed_in_diff(identifier, diff_text):
    """True if the identifier appears on a removed (-) line but not on any
    added (+) line — i.e. it looks like it was actually deleted, not just
    untouched by this particular diff."""
    removed = any(
        identifier in line for line in diff_text.splitlines() if line.startswith("-")
    )
    added = any(
        identifier in line for line in diff_text.splitlines() if line.startswith("+")
    )
    return removed and not added
