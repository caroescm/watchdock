import fnmatch
import logging
from concurrent.futures import ThreadPoolExecutor

from watchdoc import nim_client
from watchdoc.models import Finding
from watchdoc.nim_client import chat_completion
from watchdoc.prompts import check_claim_prompt, extract_claims_prompt

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

    prompt = extract_claims_prompt(diff_text)
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
    """The raw model call behind check_claim_against_target: one claim, one
    target, one call. See check_claims_against_targets for why not the
    whole claim set at once."""
    prompt = check_claim_prompt(claim, target_path, target_content)
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


def check_claims_against_targets(claims_list, contents_by_target, n=3, max_workers=None):
    """Checks every claim against every target, n independent samples each,
    through ONE bounded thread pool, and unions the findings per target.

    Returns (findings_by_target, errors_by_target). A target appears in
    exactly one of the two: with its merged findings, or with the reason it
    could not be checked at all.

    Why one flat pool: the natural unit of work is a single sample — one
    NIM call for one (target, claim) pair — and there are
    targets x claims x n of them. Nesting a pool per layer, each sized to
    its item count, parked hundreds of idle threads on nim_client's
    concurrency gate for a big docs/ tree. The pool here is capped at that
    gate's size (see nim_client.max_concurrent_requests); more threads
    could only wait.

    Why n samples per claim, all awaited: the model is nondeterministic at
    temperature 0.0 and has shown no false positives, so a union of samples
    buys recall for free; abandoning slow samples cost recall. Why one
    claim per call: NIM's free tier caps a generation at ~10 minutes, and
    the whole-claims-blob shape is what hit it. Both measured; see
    project-docs/BENCHMARK.md, "Operational findings from live runs".

    Failure policy, the same at every level: a failed sample is a missing
    vote for its claim; a claim with no surviving sample is dropped for
    that target; a target with no surviving claim is reported as an error.
    Nothing here raises for a partial failure — the caller decides.
    """
    tasks = [
        (target_path, claim)
        for target_path in contents_by_target
        for claim in claims_list
        for _ in range(n)
    ]
    findings_by_target = {t: [] for t in contents_by_target}
    errors_by_target = {}
    if not tasks:
        return findings_by_target, errors_by_target

    workers = min(len(tasks), max_workers or nim_client.max_concurrent_requests())
    samples = {}  # (target, claim) -> list of successful sample results
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(check_claim_against_target, claim, target_path, contents_by_target[target_path]): (target_path, claim)
            for target_path, claim in tasks
        }
        for future, key in futures.items():
            try:
                result = future.result()
            except Exception:
                logger.warning("Ensemble sample for %s failed and was dropped", key[0], exc_info=True)
                continue
            samples.setdefault(key, []).append(result)

    for target_path in contents_by_target:
        per_claim = []
        dropped = 0
        for claim in claims_list:
            key = (target_path, claim)
            if key in samples:
                per_claim.append(_merge_findings(samples[key]))
            else:
                dropped += 1
                logger.warning("Every ensemble sample failed for claim %r against %s; dropping that claim",
                               claim[:120], target_path)
        if claims_list and dropped == len(claims_list):
            errors_by_target[target_path] = (
                f"All {len(claims_list)} claim(s) failed against {target_path}; no result to report")
            del findings_by_target[target_path]
        else:
            findings_by_target[target_path] = _merge_findings(per_claim)

    return findings_by_target, errors_by_target


def check_claims_against_target(claims_list, target_path, target_content, n=3, max_workers=None):
    """Single-target form of check_claims_against_targets. Raises RuntimeError
    if every claim failed, since then there's genuinely no result to report."""
    findings_by_target, errors = check_claims_against_targets(
        claims_list, {target_path: target_content}, n=n, max_workers=max_workers)
    if target_path in errors:
        raise RuntimeError(errors[target_path])
    return findings_by_target[target_path]


def check_claim_against_target_ensemble(claim, target_path, target_content, n=3):
    """Single-claim form: n samples of one claim against one target, unioned.
    Raises RuntimeError if every sample failed."""
    return check_claims_against_target([claim], target_path, target_content, n=n)
