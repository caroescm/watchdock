"""Which files in the checked-out repository are doc or instruction targets.

Auto-detection covers the conventional names; a ``.watchdoc.yml`` at the
repository root can replace the list (``targets``) or subtract from it
(``ignore``). Both keys take fnmatch globs, the same syntax as the diff
filter in diff.py.
"""
import fnmatch
import logging
import os

import yaml

from watchdoc.errors import ConfigError

logger = logging.getLogger(__name__)

# Files, or directories of files (.cursor/rules is a directory of .mdc
# rules in current Cursor), that instruct AI coding agents.
DEFAULT_INSTRUCTION_FILES = [
    "AGENTS.md",
    "CLAUDE.md",
    ".cursor/rules",
    ".cursorrules",
    "conventions.md",
]

DEFAULT_DOC_PATHS = [
    "README.md",
    "README.rst",
]

DEFAULT_DOC_DIRS = [
    "docs",
    "documentation",
]

# Only these count as documentation inside a docs directory; a PNG or a
# conf.py under docs/ is not a target.
DOC_EXTENSIONS = {".md", ".mdx", ".markdown", ".rst", ".txt", ".adoc"}

CONFIG_FILENAME = ".watchdoc.yml"


def discover_targets(repo_root: str) -> list[str]:
    """Repository-relative paths of the text files to check, in a stable order."""
    config = _load_config(repo_root)

    candidates = config["targets"] if "targets" in config else _auto_detect(repo_root)
    ignore = config.get("ignore", [])

    return [
        path for path in candidates
        if not any(fnmatch.fnmatch(path, pattern) for pattern in ignore)
        and _is_text_file(os.path.join(repo_root, path))
    ]


def _is_text_file(path: str, sample_bytes: int = 8192) -> bool:
    """True for a regular file whose first bytes decode as UTF-8 text."""
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "rb") as f:
            sample = f.read(sample_bytes)
    except OSError:
        return False
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        # A multi-byte character cut at the sample boundary also fails here;
        # tolerate that by retrying without the last few bytes.
        try:
            sample[:-3].decode("utf-8")
        except UnicodeDecodeError:
            return False
    return True


def _load_config(repo_root: str) -> dict[str, list[str]]:
    """The validated ``.watchdoc.yml`` contents, or {} when there is none.
    Each of ``targets`` and ``ignore``, when present, must be a list of
    strings; anything else is a ConfigError rather than a silent misread
    (a bare string would otherwise iterate as characters)."""
    config_path = os.path.join(repo_root, CONFIG_FILENAME)
    if not os.path.isfile(config_path):
        return {}
    with open(config_path) as f:
        loaded = yaml.safe_load(f) or {}
    if not isinstance(loaded, dict):
        raise ConfigError(f"{config_path} must be a mapping with optional 'targets' and 'ignore' lists")
    for key in ("targets", "ignore"):
        if key in loaded and not (isinstance(loaded[key], list) and all(isinstance(p, str) for p in loaded[key])):
            raise ConfigError(f"{config_path}: '{key}' must be a list of path globs")
    return loaded


def _auto_detect(repo_root: str) -> list[str]:
    found: list[str] = []

    for path in DEFAULT_INSTRUCTION_FILES + DEFAULT_DOC_PATHS:
        full = os.path.join(repo_root, path)
        if os.path.isfile(full):
            found.append(path)
        elif os.path.isdir(full):
            # e.g. .cursor/rules/*.mdc: every text file in it is an instruction file
            found.extend(_walk(repo_root, full))

    for doc_dir in DEFAULT_DOC_DIRS:
        dir_path = os.path.join(repo_root, doc_dir)
        if os.path.isdir(dir_path):
            found.extend(_walk(repo_root, dir_path, extensions=DOC_EXTENSIONS))

    return found


def _walk(repo_root: str, dir_path: str, extensions: set[str] | None = None) -> list[str]:
    """Relative paths of the files under dir_path, sorted for a stable
    order, restricted to ``extensions`` when given."""
    paths = []
    for root, _, files in os.walk(dir_path):
        for name in files:
            if extensions is not None and os.path.splitext(name)[1].lower() not in extensions:
                continue
            paths.append(os.path.relpath(os.path.join(root, name), repo_root))
    return sorted(paths)
