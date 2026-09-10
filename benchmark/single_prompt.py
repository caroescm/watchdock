"""
Single-prompt variant — one NIM call does the whole job (find + explain drift)
at once, no separate extract_claims/check_claim_against_target steps and no
agent framework. Isolates whether the two-step pipeline actually matters.
"""
from watchdoc.claims import parse_findings
from watchdoc.nim_client import chat_completion


def single_prompt_check(diff, target_path, target_content):
    """Returns a list of Finding objects, same contract as the full pipeline."""
    diff_text = "\n".join(f"--- {e['filename']} ---\n{e['patch']}" for e in diff)

    prompt = f"""Given this code diff and this file's current content, find any line
in the file that the diff makes inaccurate or contradicted.

Diff:
{diff_text}

File `{target_path}`:
{target_content}

If nothing is affected, respond with exactly: NONE
Otherwise respond in this format, one block per finding, blank line between blocks:
LINE: <exact quoted line>
TYPE: semantic staleness | broken reference
REASON: <why it's wrong>
"""
    return parse_findings(chat_completion(prompt))
