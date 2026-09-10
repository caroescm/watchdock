import logging
import os

import yaml

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

# Only these count as documentation inside a docs directory. The walk
# used to take every file, so a PNG or a conf.py became a target.
DOC_EXTENSIONS = {".md", ".mdx", ".markdown", ".rst", ".txt", ".adoc"}

CONFIG_FILENAME = ".watchdoc.yml"
# The project's former name. Still read, with a warning, so existing
# adopters don't silently lose their explicit target list.
LEGACY_CONFIG_FILENAME = ".still-config.yml"


def discover_targets(repo_root):
    config = _load_config(repo_root)

    if config and "targets" in config:
        candidates = config["targets"]
    else:
        candidates = _auto_detect(repo_root)

    ignore = set(config.get("ignore", [])) if config else set()

    existing = [
        path for path in candidates
        if path not in ignore and _is_text_file(os.path.join(repo_root, path))
    ]
    return existing


def _is_text_file(path, sample_bytes=8192):
    """True for a regular file whose first bytes decode as UTF-8 text. A PNG
    or PDF under docs/ is not a target: reading it as text would raise and
    sending it to the model would be meaningless."""
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


def _load_config(repo_root):
    config_path = os.path.join(repo_root, CONFIG_FILENAME)
    if not os.path.isfile(config_path):
        legacy_path = os.path.join(repo_root, LEGACY_CONFIG_FILENAME)
        if not os.path.isfile(legacy_path):
            return None
        logger.warning("%s is the old config name; rename it to %s", LEGACY_CONFIG_FILENAME, CONFIG_FILENAME)
        config_path = legacy_path
    with open(config_path) as f:
        return yaml.safe_load(f) or {}


def _auto_detect(repo_root):
    found = []

    for path in DEFAULT_INSTRUCTION_FILES + DEFAULT_DOC_PATHS:
        full = os.path.join(repo_root, path)
        if os.path.isfile(full):
            found.append(path)
        elif os.path.isdir(full):
            # e.g. .cursor/rules/*.mdc — every text file in it is an instruction file
            found.extend(_walk(repo_root, full))

    for doc_dir in DEFAULT_DOC_DIRS:
        dir_path = os.path.join(repo_root, doc_dir)
        if os.path.isdir(dir_path):
            found.extend(_walk(repo_root, dir_path, extensions=DOC_EXTENSIONS))

    return found


def _walk(repo_root, dir_path, extensions=None):
    """Relative paths of the files under dir_path, sorted for a stable
    order, restricted to `extensions` when given."""
    paths = []
    for root, _, files in os.walk(dir_path):
        for name in files:
            if extensions is not None and os.path.splitext(name)[1].lower() not in extensions:
                continue
            paths.append(os.path.relpath(os.path.join(root, name), repo_root))
    return sorted(paths)
