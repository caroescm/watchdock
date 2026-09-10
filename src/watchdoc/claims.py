"""The detection stage: what a diff could make wrong, and which lines of each
target it actually makes wrong.

Two model calls live here. ``extract_claims`` turns the diff into
self-contained claims; ``check_claim_against_target`` checks one claim
against one file. ``check_claims_against_targets`` fans the second call out
over targets x claims x ensemble samples through one bounded pool.
"""
import logging
from concurrent.futures import ThreadPoolExecutor

from watchdoc import nim_client
from watchdoc.diff import format_diff, relevant_entries
from watchdoc.models import DiffEntry, Finding
from watchdoc.parsing import lines_overlap, normalize_line, parse_claims, parse_findings
from watchdoc.prompts import check_claim_prompt, extract_claims_prompt

logger = logging.getLogger(__name__)

# Independent samples per (claim, target) check, unioned. The model is
# nondeterministic at temperature 0.0 and has shown no false positives, so a
# union of samples buys recall for free. Measured; see BENCHMARK.md.
DEFAULT_ENSEMBLE_SIZE = 3

FindingsByTarget = dict[str, list[Finding]]
ErrorsByTarget = dict[str, str]


def extract_claims(diff: list[DiffEntry]) -> list[str]:
    """The doc/instruction claims this diff could affect, one string per
    claim. [] means nothing doc-relevant changed. Each claim is later checked
    on its own, so each one has to stand alone."""
    return parse_claims(_request_claims(diff))


def _request_claims(diff: list[DiffEntry]) -> str:
    """The raw model call behind extract_claims: CLAIM: blocks or NONE. No
    call at all when nothing relevant changed."""
    relevant = relevant_entries(diff)
    if not relevant:
        return "NONE"
    return nim_client.chat_completion(extract_claims_prompt(format_diff(relevant)))


def check_claim_against_target(claim: str, target_path: str, target_content: str) -> list[Finding]:
    """One model call, parsed: the lines of target_content that one claim
    makes inaccurate. [] means the file is still accurate.

    One claim per call is deliberate: NIM's free tier caps a generation at
    about ten minutes and a whole-claim-set prompt is what hit it."""
    prompt = check_claim_prompt(claim, target_path, target_content)
    return parse_findings(nim_client.chat_completion(prompt))


def merge_findings(samples: list[list[Finding]]) -> list[Finding]:
    """Unions findings from independent samples, deduplicating with the same
    tolerant line match the benchmark scores with (parsing.lines_overlap).

    Deterministic regardless of sample order: when two quotes overlap, the
    longer one survives, since it is the fuller quote of the line and the
    one most likely to match the file verbatim at delivery. Survivors are
    returned in first-seen order."""
    all_findings = [finding for sample in samples for finding in sample]
    by_length = sorted(range(len(all_findings)),
                       key=lambda i: len(normalize_line(all_findings[i].line)), reverse=True)
    kept: list[int] = []
    for i in by_length:
        if not any(lines_overlap(all_findings[i].line, all_findings[k].line) for k in kept):
            kept.append(i)
    return [all_findings[i] for i in sorted(kept)]


def pool_size(task_count: int, max_workers: int | None = None) -> int:
    """Threads for a fan-out of task_count model calls: never more than the
    tasks, never more than the NIM concurrency cap (extra threads would only
    queue on the gate), never fewer than one."""
    limit = max_workers if max_workers is not None else nim_client.max_concurrent_requests()
    return max(1, min(task_count, limit))


def check_claims_against_targets(
    claims_list: list[str],
    contents_by_target: dict[str, str],
    n: int = DEFAULT_ENSEMBLE_SIZE,
    max_workers: int | None = None,
) -> tuple[FindingsByTarget, ErrorsByTarget]:
    """Checks every claim against every target, n independent samples each,
    through one bounded thread pool, and unions the findings per target.

    Returns (findings_by_target, errors_by_target). A target appears in
    exactly one of the two: with its merged findings, or with the reason it
    could not be checked at all.

    Failure policy, the same at every level: a failed sample is a missing
    vote for its claim; a claim with no surviving sample is dropped for that
    target; a target with no surviving claim is an error. Nothing here raises
    for a partial failure; the caller decides.
    """
    findings_by_target: FindingsByTarget = {t: [] for t in contents_by_target}
    if not claims_list or not contents_by_target:
        return findings_by_target, {}

    samples = _run_samples(claims_list, contents_by_target, n, max_workers)

    errors_by_target: ErrorsByTarget = {}
    for target_path in contents_by_target:
        per_claim = []
        for claim in claims_list:
            if (target_path, claim) in samples:
                per_claim.append(merge_findings(samples[(target_path, claim)]))
            else:
                logger.warning("Every ensemble sample failed for claim %r against %s; dropping that claim",
                               claim[:120], target_path)
        if not per_claim:
            errors_by_target[target_path] = (
                f"All {len(claims_list)} claim(s) failed against {target_path}; no result to report")
            del findings_by_target[target_path]
        else:
            findings_by_target[target_path] = merge_findings(per_claim)

    return findings_by_target, errors_by_target


def _run_samples(
    claims_list: list[str],
    contents_by_target: dict[str, str],
    n: int,
    max_workers: int | None,
) -> dict[tuple[str, str], list[list[Finding]]]:
    """Every (target, claim) sample through one pool. Returns the successful
    sample results keyed by (target, claim); a key with no successful sample
    is absent."""
    tasks = [(t, c) for t in contents_by_target for c in claims_list for _ in range(n)]
    samples: dict[tuple[str, str], list[list[Finding]]] = {}
    with ThreadPoolExecutor(max_workers=pool_size(len(tasks), max_workers)) as executor:
        futures = [
            (executor.submit(check_claim_against_target, claim, target_path, contents_by_target[target_path]),
             (target_path, claim))
            for target_path, claim in tasks
        ]
        for future, (target_path, claim) in futures:
            try:
                result = future.result()
            except Exception:  # noqa: BLE001 — a failed sample is a missing vote, logged with its traceback
                logger.warning("Ensemble sample for %s failed and was dropped", target_path, exc_info=True)
                continue
            samples.setdefault((target_path, claim), []).append(result)
    return samples
