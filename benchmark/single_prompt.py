"""
Single-prompt variant: one NIM call does the whole job (find + explain drift)
at once, with no separate extract_claims / check_claim_against_target steps.
Isolates whether the two-step pipeline actually matters.
"""
from watchdock.diff import format_diff
from watchdock.models import DiffEntry, Finding
from watchdock.nim_client import chat_completion
from watchdock.parsing import parse_findings


def single_prompt_check(diff: list[DiffEntry], target_path: str, target_content: str) -> list[Finding]:
    """Returns a list of Finding objects, same contract as the full pipeline."""
    prompt = f"""Given this code diff and this file's current content, find any line
in the file that the diff makes inaccurate or contradicted.

Diff:
{format_diff(diff)}

File `{target_path}`:
{target_content}

If nothing is affected, respond with exactly: NONE
Otherwise respond in this format, one block per finding, blank line between blocks:
LINE: <exact quoted line>
TYPE: semantic staleness | broken reference
REASON: <why it's wrong>
"""
    return parse_findings(chat_completion(prompt))
