import fnmatch
import logging
from concurrent.futures import ThreadPoolExecutor

from nim_client import chat_completion

logger = logging.getLogger(__name__)

# File patterns that are never informative for doc/instruction drift — they
# add token volume to extract_claims without ever being the kind of change
# that makes a doc claim stale. Filtering them out shrinks the diff we send
# to the model, which directly cuts generation latency on real-world PRs
# that touch many files at once.
IRRELEVANT_DIFF_PATTERNS = [
    "test/*", "tests/*", "*_test.*", "*.test.*",
    "LICENSE", "LICENSE.*", ".gitignore",
    "package-lock.json", "yarn.lock", "poetry.lock", "*.lock",
    "*.egg-info/*",
]


def _is_relevant(filename):
    return not any(fnmatch.fnmatch(filename, pattern) for pattern in IRRELEVANT_DIFF_PATTERNS)


def format_diff(diff):
    """Turns the list of {filename, patch} dicts from get_diff() into one text block."""
    parts = []
    for entry in diff:
        parts.append(f"--- {entry['filename']} ---\n{entry['patch']}")
    return "\n\n".join(parts)


def extract_claims(diff):
    """Given a PR diff, ask the model what doc/instruction claims this change could affect."""
    relevant_diff = [entry for entry in diff if _is_relevant(entry["filename"])]
    if not relevant_diff:
        return "NONE"

    diff_text = format_diff(relevant_diff)

    prompt = f"""You are reviewing a code change to figure out what it might make false in project documentation or AI-agent instruction files (like README.md, AGENTS.md, CLAUDE.md).
                Given the diff below, list any specific claims this change could affect: library/dependency choices, CLI commands, config keys, function signatures, file/module locations, described behavior, or stated conventions.
                For each one, briefly describe what changed and what kind of documented claim it might now contradict. If nothing in the diff seems relevant to documentation or agent instructions, respond with exactly: NONE

Diff:
{diff_text}
"""
    return chat_completion(prompt)


def check_claim_against_target(claims, target_path, target_content):
    """One NIM call: does target_content still accurately describe the code, given claims?"""
    prompt = f"""You are checking whether a documentation or agent-instruction file is still accurate, given a set of claims about what a code change affected.

Claims about what changed:
{claims}

Here is the current content of `{target_path}`:
{target_content}

For each line in `{target_path}` that these claims make now inaccurate or contradicted, quote the exact line and explain why it's now wrong. Roughly classify it as one of:
- semantic staleness: the line is still syntactically fine but now describes something false
- broken reference: the line points to something (a file, command, or symbol) that no longer exists at all

If a line could reasonably be either, just pick whichever fits best and move on — do not spend time deliberating between the two, the exact label is a minor detail, not the point. The important thing is catching every line that's actually wrong.

Do NOT include lines that are unaffected — only report lines that are actually now wrong.
If nothing in `{target_path}` is affected by these claims, respond with exactly: NONE

Respond in this format for each finding, with a blank line between findings:
LINE: <exact quoted line, verbatim from the file above>
TYPE: semantic staleness | broken reference
REASON: <why it's now wrong>
"""
    return chat_completion(prompt)


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


def _normalize(text):
    return " ".join(text.lower().split())


def _merge_findings(list_of_finding_lists):
    """Unions findings from multiple independent samples, deduplicating by
    normalized substring match on the line text (same tolerant-matching
    idea used to score against ground truth in the benchmark)."""
    merged = []
    seen_normalized = []
    for findings in list_of_finding_lists:
        for finding in findings:
            norm = _normalize(finding["line"])
            if any(norm in s or s in norm for s in seen_normalized):
                continue
            seen_normalized.append(norm)
            merged.append(finding)
    return merged


def check_claim_against_target_ensemble(claims, target_path, target_content, n=3):
    """Runs check_claim_against_target n times in parallel and unions the
    results, instead of trusting a single sample.

    Empirically, the same case sometimes gets caught and sometimes gets
    missed across independent temperature=0.0 calls to this model/serving
    stack — real nondeterminism, not something a single prompt tweak fixes.
    Every run we tested had zero false positives even when it missed real
    findings, so "union across samples" trades more API calls for higher
    recall without a corresponding precision cost we've observed.

    A capped-wait version (proceed with whichever samples finish within a
    time budget, abandon stragglers) was tried and reverted: on the same
    3 hardest cases, it dropped recall to 67%, failing exactly the case
    that has consistently needed the most deliberation time throughout
    testing (its failure landed at 317.7s, just past the 300s cap tried).
    Waiting for all n samples costs more time but that latency is doing
    real work, not padding — same lesson as the thinking-mode finding.

    A real triggered run hit a connection error on all 3 samples at once
    (the shared client's single retry also failed) and crashed the entire
    check with no result posted at all, for what should have been an
    isolated network blip. A dropped sample is now treated as a missing
    vote, not a fatal error — only raise if every sample fails, since at
    that point there's genuinely no result to report rather than one to
    degrade gracefully from.
    """
    with ThreadPoolExecutor(max_workers=n) as executor:
        futures = [
            executor.submit(check_claim_against_target, claims, target_path, target_content)
            for _ in range(n)
        ]
        raw_results = []
        for future in futures:
            try:
                raw_results.append(future.result())
            except Exception:
                logger.warning("Ensemble sample for %s failed and was dropped", target_path, exc_info=True)

    if not raw_results:
        raise RuntimeError(f"All {n} ensemble samples failed for {target_path}; no result to report")

    all_findings = [parse_findings(r) for r in raw_results]
    return _merge_findings(all_findings)
