"""Reading the model's replies: the block format both prompts use, the two
parsers built on it, and the tolerant line matching shared by the ensemble
merge and the benchmark scorer.

The formats themselves are defined in prompts.py; a change there needs the
matching change here.
"""
import logging

from watchdoc.models import Finding, FindingType

logger = logging.getLogger(__name__)


def is_none_response(text: str) -> bool:
    """True for the literal NONE the prompts ask for, or an empty reply."""
    stripped = text.strip()
    return not stripped or stripped.upper() == "NONE"


def parse_blocks(text: str, start_key: str, keys: tuple[str, ...]) -> list[dict[str, str]]:
    """The one block parser behind parse_claims and parse_findings.

    The model is asked for blocks of ``KEY: value`` lines. A block starts at
    every ``start_key:`` line; any other listed key sets that field of the
    current block. Rules, applied identically to both formats:

    - keys match case-insensitively (``Line:`` and ``LINE:`` are the same);
    - a non-empty line that doesn't start with a listed key is a
      continuation of the most recent field, folded in with a space, so a
      value the model wraps across lines survives intact;
    - blank lines are ignored, they carry no structure;
    - a field line before the first start_key belongs to no block and is
      dropped.

    Returns a list of dicts keyed by the listed key names.
    """
    key_names = {k.lower(): k for k in keys}
    blocks: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    field: str | None = None
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


def parse_claims(extract_result: str) -> list[str]:
    """Parses the model's CLAIM: blocks into individual claim strings.
    Returns [] for a NONE response.

    If the output is non-NONE but contains no CLAIM: blocks at all (the model
    ignored the format), the whole output is returned as a single claim
    rather than silently dropping everything: the check prompt handles
    free-form claim prose, so a format miss degrades to one broad claim
    instead of a false 'no drift'."""
    if is_none_response(extract_result):
        return []

    claims = [block["CLAIM"] for block in parse_blocks(extract_result, "CLAIM", ("CLAIM",))
              if block.get("CLAIM")]
    if not claims:
        logger.warning("claim extraction output had no CLAIM: blocks; using the whole output as one claim")
        return [extract_result.strip()]
    return claims


def parse_findings(check_result: str) -> list[Finding]:
    """Parses the model's LINE:/TYPE:/REASON: blocks into Finding objects.
    Returns [] for a NONE response."""
    if is_none_response(check_result):
        return []

    blocks = parse_blocks(check_result, "LINE", ("LINE", "TYPE", "REASON"))
    return [
        Finding(line=b["LINE"], reason=b["REASON"], kind=FindingType.from_model_output(b.get("TYPE")))
        for b in blocks if _is_real_finding(b)
    ]


def _is_real_finding(block: dict[str, str]) -> bool:
    """The single validity rule for a parsed finding block. An incomplete
    block (no LINE or no REASON) is dropped rather than crashing the drafter
    downstream. So is a block the model itself marked as unaffected ("N/A"),
    and a line that can't be a real quote from the file (see _is_junk_line)."""
    if not block.get("LINE") or not block.get("REASON"):
        return False
    type_ = block.get("TYPE", "").lower()
    if "n/a" in type_ or "unaffected" in type_:
        return False
    return not _is_junk_line(block["LINE"])


def _is_junk_line(line: str) -> bool:
    """True for 'lines' that can't be real quotes from a target file: an
    echoed format placeholder such as ``<exact quoted line, ...>``, or
    content with no alphanumeric characters at all such as a bare ``...``.
    Both have been seen in real replies; both would otherwise reach the
    drafter and produce a nonsense suggestion on the PR."""
    stripped = line.strip()
    if stripped.startswith("<") and stripped.endswith(">"):
        return True
    return not any(ch.isalnum() for ch in stripped)


def normalize_line(text: str) -> str:
    """Lower-cased, whitespace-collapsed form used for tolerant comparison."""
    return " ".join(text.lower().split())


def lines_overlap(a: str, b: str) -> bool:
    """True when either normalized line contains the other. Two quotes of the
    same line that differ only in trailing punctuation or a dropped word
    still count as the same line; the ensemble merge and the benchmark
    scorer both rely on this."""
    na, nb = normalize_line(a), normalize_line(b)
    return na in nb or nb in na
