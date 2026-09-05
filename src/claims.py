from nim_client import get_client, MODEL


def format_diff(diff):
    """Turns the list of {filename, patch} dicts from get_diff() into one text block."""
    parts = []
    for entry in diff:
        parts.append(f"--- {entry['filename']} ---\n{entry['patch']}")
    return "\n\n".join(parts)


def extract_claims(diff):
    """Given a PR diff, ask the model what doc/instruction claims this change could affect."""
    diff_text = format_diff(diff)

    prompt = f"""You are reviewing a code change to figure out what it might make false in project documentation or AI-agent instruction files (like README.md, AGENTS.md, CLAUDE.md).
                Given the diff below, list any specific claims this change could affect: library/dependency choices, CLI commands, config keys, function signatures, file/module locations, described behavior, or stated conventions.
                For each one, briefly describe what changed and what kind of documented claim it might now contradict. If nothing in the diff seems relevant to documentation or agent instructions, respond with exactly: NONE

Diff:
{diff_text}
"""

    client = get_client()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
    )
    return response.choices[0].message.content

def check_claim_against_target(claims, target_path, target_content):
    """One NIM call: does target_content still accurately describe the code, given claims?"""
    prompt = f"""You are checking whether a documentation or agent-instruction file is still accurate, given a set of claims about what a code change affected.

Claims about what changed:
{claims}

Here is the current content of `{target_path}`:
{target_content}

For each line in `{target_path}` that these claims make now inaccurate or contradicted, quote the exact line and explain why it's now wrong. Distinguish between:
- semantic staleness: the line is still syntactically fine but now describes something false
- broken reference: the line points to something (a file, command, or symbol) that no longer exists at all

Do NOT include lines that are unaffected — only report lines that are actually now wrong.
If nothing in `{target_path}` is affected by these claims, respond with exactly: NONE

Respond in this format for each finding, with a blank line between findings:
LINE: <exact quoted line, verbatim from the file above>
TYPE: semantic staleness | broken reference
REASON: <why it's now wrong>
"""
    client = get_client()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
    )
    return response.choices[0].message.content


def parse_findings(check_result):
    """Parses check_claim_against_target's LINE:/TYPE:/REASON: text into a list
    of {line, type, reason} dicts. Returns [] for a NONE response."""
    if check_result.strip().upper() == "NONE":
        return []

    findings = []
    current = {}
    for raw_line in check_result.splitlines():
        line = raw_line.strip()
        if line.startswith("LINE:"):
            if current.get("line") and current.get("reason"):
                findings.append(current)
            current = {"line": line[len("LINE:"):].strip()}
        elif line.startswith("TYPE:"):
            current["type"] = line[len("TYPE:"):].strip()
        elif line.startswith("REASON:"):
            current["reason"] = line[len("REASON:"):].strip()

    # Only keep findings that have both a line and a reason — an incomplete
    # block (e.g. the model didn't follow the format exactly) is dropped
    # rather than crashing draft_fix downstream with a missing key.
    if current.get("line") and current.get("reason"):
        findings.append(current)

    # Defensive filter: even though the prompt says not to, older responses
    # sometimes still mention unaffected lines as "N/A" — drop those.
    return [f for f in findings if "n/a" not in f.get("type", "").lower()
            and "unaffected" not in f.get("type", "").lower()]