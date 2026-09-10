"""Every prompt Watchdock sends to the model, and nothing else.

Keeping them here means a change to what the model is asked is a diff in
this file alone, reviewable apart from the logic that sends it. Each
function returns the finished prompt string; the parsers that read the
replies live in parsing.py.

The output formats are load-bearing: parse_claims reads the CLAIM: blocks
and parse_findings reads the LINE:/TYPE:/REASON: blocks, so a format change
here needs the matching parser change there.
"""


def extract_claims_prompt(diff_text: str) -> str:
    """What in this diff could make a documented claim false? One CLAIM:
    block per claim, or NONE."""
    return f"""You are reviewing a code change to figure out what it might make false in project documentation or AI-agent instruction files (like README.md, AGENTS.md, CLAUDE.md).
Given the diff below, list any specific claims this change could affect: library/dependency choices, CLI commands, config keys, function signatures, file/module locations, described behavior, or stated conventions.
For each one, briefly describe what changed and what kind of documented claim it might now contradict.

You do not have the documentation or instruction files in front of you at this step, and you do not need them — you are only listing what the diff could affect, not confirming that a target file actually says so. That confirmation happens in a later, separate step. Do not mention that you lack the files, do not ask for them, and do not explain your reasoning process.

Respond with ONLY one block per claim, with a blank line between blocks, in exactly this format and nothing else — no preamble, no reasoning, no closing remarks:
CLAIM: <what changed and what documented claim it could now contradict>

Each CLAIM must be fully self-contained — it will later be checked against documentation on its own, without the other claims or this diff for context — so name the specific files, symbols, commands, or values involved rather than referring to "the change above" or "see previous". A changed numeric or config value (a timeout, an interval, a default) is exactly as reportable as a renamed function or a changed CLI flag.

If nothing in the diff seems relevant to documentation or agent instructions, respond with exactly: NONE

Diff:
{diff_text}
"""


def check_claim_prompt(claim: str, target_path: str, target_content: str) -> str:
    """Which lines of this file does one claim make wrong? One
    LINE:/TYPE:/REASON: block per finding, or NONE."""
    return f"""You are checking whether a documentation or agent-instruction file is still accurate, given a claim about what a code change affected.

Claim about what changed:
{claim}

Here is the current content of `{target_path}`:
{target_content}

For each line in `{target_path}` that this claim makes now inaccurate or contradicted, quote the exact line and explain why it's now wrong. Roughly classify it as one of:
- semantic staleness: the line is still syntactically fine but now describes something false
- broken reference: the line points to something (a file, command, or symbol) that no longer exists at all

If a line could reasonably be either, just pick whichever fits best and move on — do not spend time deliberating between the two, the exact label is a minor detail, not the point. The important thing is catching every line that's actually wrong.

Do NOT include lines that are unaffected — only report lines that are actually now wrong.
If nothing in `{target_path}` is affected by this claim, respond with exactly: NONE

Respond in this format for each finding, with a blank line between findings:
LINE: <exact quoted line, verbatim from the file above>
TYPE: semantic staleness | broken reference
REASON: <why it's now wrong>
"""


def draft_fix_prompt(target_path: str, stale_line: str, reason: str) -> str:
    """Rewrite one stale line. Reply is the corrected text only."""
    return f"""You are fixing one line in `{target_path}` that is now inaccurate.

Stale line:
{stale_line}

Why it's wrong:
{reason}

Rewrite this line (or short paragraph, if the stale line is part of one) so it accurately
reflects the current code. Keep the same style, tone, and formatting conventions as the
original. Respond with ONLY the corrected text — no explanation, no quotes, no markdown
fences around it.
"""
