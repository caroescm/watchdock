import fnmatch
import logging
from concurrent.futures import ThreadPoolExecutor

from watchdoc.models import Finding
from watchdoc.nim_client import chat_completion

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
    """Given a PR diff, returns the list of doc/instruction claims this change
    could affect, one string per claim. [] means nothing doc-relevant changed.

    Each claim is later checked against every target file in its own
    independent call, so each one has to stand alone."""
    return parse_claims(_request_claims(diff))


def _request_claims(diff):
    """The raw model call behind extract_claims: one CLAIM: block per claim,
    or the literal NONE. Kept separate so the parser can be tested on real
    model output without a network call."""
    relevant_diff = [entry for entry in diff if _is_relevant(entry["filename"])]
    if not relevant_diff:
        return "NONE"

    diff_text = format_diff(relevant_diff)

    prompt = f"""You are reviewing a code change to figure out what it might make false in project documentation or AI-agent instruction files (like README.md, AGENTS.md, CLAUDE.md).
                Given the diff below, list any specific claims this change could affect: library/dependency choices, CLI commands, config keys, function signatures, file/module locations, described behavior, or stated conventions.
                For each one, briefly describe what changed and what kind of documented claim it might now contradict.

Respond with one block per claim, with a blank line between blocks, in exactly this format:
CLAIM: <what changed and what documented claim it could now contradict>

Each CLAIM must be fully self-contained — it will later be checked against documentation on its own, without the other claims or this diff for context — so name the specific files, symbols, commands, or values involved rather than referring to "the change above" or "see previous".

If nothing in the diff seems relevant to documentation or agent instructions, respond with exactly: NONE

Diff:
{diff_text}
"""
    return chat_completion(prompt)


def _is_none_response(text):
    return not text.strip() or text.strip().upper() == "NONE"


def _parse_blocks(text, start_key, keys):
    """The one block parser behind parse_claims and parse_findings.

    The model is asked for blocks of `KEY: value` lines. A block starts at
    every `start_key:` line; any other listed key sets that field of the
    current block. Rules, applied identically to both formats:

    - keys match case-insensitively (`Line:` and `LINE:` are the same);
    - a non-empty line that doesn't start with a listed key is a
      continuation of the most recent field, folded in with a space, so a
      value the model wraps across lines survives intact;
    - blank lines are ignored, they carry no structure;
    - a field line before the first start_key belongs to no block and is
      dropped.

    Returns a list of dicts keyed by the listed key names.
    """
    key_names = {k.lower(): k for k in keys}
    blocks = []
    current = None
    field = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        prefix, colon, rest = line.partition(":")
        name = key_names.get(prefix.strip().lower()) if colon else None
        if name is not None:
            if name == start_key:
                if current is not None:
                    blocks.append(current)
                current = {}
            if current is None:
                continue
            current[name] = rest.strip()
            field = name
        elif current is not None and field is not None:
            current[field] = f"{current[field]} {line}".strip()
    if current is not None:
        blocks.append(current)
    return blocks


def parse_claims(extract_result):
    """Parses the model's CLAIM: blocks into a list of individual claim
    strings. Returns [] for a NONE response.

    If the output is non-NONE but contains no CLAIM: blocks at all (the model
    ignored the format), the whole output is returned as a single claim
    rather than silently dropping everything — the downstream check prompt
    handled free-form claim prose fine for the project's entire history, so
    a format miss degrades to the old behavior instead of a false 'no drift'."""
    if _is_none_response(extract_result):
        return []

    claims = [block["CLAIM"] for block in _parse_blocks(extract_result, "CLAIM", ("CLAIM",))
              if block.get("CLAIM")]
    if not claims:
        logger.warning("claim extraction output had no CLAIM: blocks; falling back to the whole output as one claim")
        return [extract_result.strip()]
    return claims


def check_claim_against_target(claim, target_path, target_content):
    """One NIM call, parsed: the lines of target_content that one claim makes
    inaccurate, as Finding objects. [] means the file is still accurate."""
    return parse_findings(_request_check(claim, target_path, target_content))


def _request_check(claim, target_path, target_content):
    """The raw model call behind check_claim_against_target.

    Takes a single claim, not the whole extracted set: NVIDIA's free-tier NIM
    endpoint enforces a hard ~10-minute server-side generation cap (two real
    triggered runs both died at 10:00 sharp, mid-stream, immune to streaming
    and retries), and holding an entire multi-category claims blob against a
    large target file in one continuous reasoning pass is exactly what pushed
    calls past it. One narrow claim per call is also the granularity the
    benchmark's 100%-recall result was actually measured at — every benchmark
    case was a single, narrowly-scoped change."""
    prompt = f"""You are checking whether a documentation or agent-instruction file is still accurate, given a claim about what a code change affected.

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
    return chat_completion(prompt)


def parse_findings(check_result):
    """Parses the model's LINE:/TYPE:/REASON: blocks into a list of Finding
    objects. Returns [] for a NONE response."""
    if _is_none_response(check_result):
        return []

    blocks = _parse_blocks(check_result, "LINE", ("LINE", "TYPE", "REASON"))
    return [
        Finding(line=b["LINE"], reason=b["REASON"], type=b.get("TYPE", "drift"))
        for b in blocks if _is_real_finding(b)
    ]


def _is_real_finding(block):
    """The single validity rule for a parsed finding block. An incomplete
    block (no LINE or no REASON — the model didn't follow the format) is
    dropped rather than crashing draft_fix downstream. So is a block the
    model itself marked as unaffected ("N/A"), which older responses still
    emit despite the prompt, and a line that can't be a real quote from the
    file (see _is_junk_line)."""
    if not block.get("LINE") or not block.get("REASON"):
        return False
    type_ = block.get("TYPE", "").lower()
    if "n/a" in type_ or "unaffected" in type_:
        return False
    return not _is_junk_line(block["LINE"])


def _is_junk_line(line):
    """True for 'lines' that can't be real quotes from a target file: an
    echoed format placeholder (a real ensemble sample once returned the
    literal `<exact quoted line, verbatim from the file above>`), or content
    with no alphanumeric characters at all (a real sample once returned a
    bare `...`). These would otherwise flow into draft_fix and produce a
    nonsense suggestion on the PR."""
    stripped = line.strip()
    if stripped.startswith("<") and stripped.endswith(">"):
        return True
    return not any(ch.isalnum() for ch in stripped)


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
            norm = _normalize(finding.line)
            if any(norm in s or s in norm for s in seen_normalized):
                continue
            seen_normalized.append(norm)
            merged.append(finding)
    return merged


def check_claim_against_target_ensemble(claim, target_path, target_content, n=3):
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
            executor.submit(check_claim_against_target, claim, target_path, target_content)
            for _ in range(n)
        ]
        samples = []
        for future in futures:
            try:
                samples.append(future.result())
            except Exception:
                logger.warning("Ensemble sample for %s failed and was dropped", target_path, exc_info=True)

    if not samples:
        raise RuntimeError(f"All {n} ensemble samples failed for {target_path}; no result to report")

    return _merge_findings(samples)


def check_claims_against_target(claims_list, target_path, target_content, n=3):
    """Checks each individual claim against the target as its own independent
    ensemble, all claims in parallel, and unions every finding.

    This per-claim decomposition exists because of a measured hard wall:
    NVIDIA's free-tier NIM endpoint kills generation at ~10 minutes
    server-side (two separate real triggered runs failed mid-stream at
    exactly 10:00 — after streaming and whole-call retries were already in
    place, so neither can help). A single call carrying the entire claims
    blob against a large target file is the one task shape that ran long
    enough to hit it. One claim per call keeps each generation short, and
    since claims run concurrently, wall-clock is the slowest single claim
    rather than one monolithic pass. nim_client caps global concurrency so
    (claims x ensemble x targets) fan-out can't stampede the API.

    Failure policy mirrors the sample level one layer up: a claim whose
    entire ensemble failed is logged and dropped (a missing vote, not a
    fatal error); raise only if every claim failed, since then there's
    genuinely no result to report."""
    with ThreadPoolExecutor(max_workers=max(len(claims_list), 1)) as executor:
        futures = {
            executor.submit(check_claim_against_target_ensemble, claim, target_path, target_content, n): claim
            for claim in claims_list
        }
        per_claim_findings = []
        failed = 0
        for future, claim in futures.items():
            try:
                per_claim_findings.append(future.result())
            except Exception:
                failed += 1
                logger.warning("Every ensemble sample failed for claim %r against %s; dropping that claim",
                               claim[:120], target_path, exc_info=True)

    if claims_list and failed == len(claims_list):
        raise RuntimeError(f"All {len(claims_list)} claims failed against {target_path}; no result to report")

    return _merge_findings(per_claim_findings)
