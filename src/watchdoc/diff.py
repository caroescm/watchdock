"""Turning a PR diff into the text the claim-extraction prompt sees."""
import fnmatch

from watchdoc.models import DiffEntry

# Files that are never informative for doc or instruction drift. They add
# token volume to claim extraction without ever being the kind of change
# that makes a documented claim stale, and a smaller diff means a faster
# generation. Patterns are fnmatch globs matched against the full path from
# the repository root; ``*`` also matches ``/``, so ``*_test.*`` matches at
# any depth while ``tests/*`` needs the ``*/tests/*`` twin for nested trees.
IRRELEVANT_DIFF_PATTERNS = [
    "test/*", "tests/*", "*/test/*", "*/tests/*", "*_test.*", "*.test.*",
    "LICENSE", "LICENSE.*", ".gitignore",
    "*.lock", "package-lock.json",
    "*.egg-info/*",
]


def is_relevant(filename: str) -> bool:
    return not any(fnmatch.fnmatch(filename, pattern) for pattern in IRRELEVANT_DIFF_PATTERNS)


def relevant_entries(diff: list[DiffEntry]) -> list[DiffEntry]:
    return [entry for entry in diff if is_relevant(entry.filename)]


def format_diff(diff: list[DiffEntry]) -> str:
    """One text block, each file's patch under a ``--- path ---`` header."""
    return "\n\n".join(f"--- {entry.filename} ---\n{entry.patch}" for entry in diff)
