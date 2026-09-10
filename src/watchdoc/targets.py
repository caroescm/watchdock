import os

import yaml

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

CONFIG_FILENAME = ".still-config.yml"


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
        return None
    with open(config_path) as f:
        return yaml.safe_load(f) or {}


def _auto_detect(repo_root):
    found = []

    for path in DEFAULT_INSTRUCTION_FILES + DEFAULT_DOC_PATHS:
        if os.path.isfile(os.path.join(repo_root, path)):
            found.append(path)

    for doc_dir in DEFAULT_DOC_DIRS:
        dir_path = os.path.join(repo_root, doc_dir)
        if os.path.isdir(dir_path):
            for root, _, files in os.walk(dir_path):
                for name in files:
                    full = os.path.join(root, name)
                    rel = os.path.relpath(full, repo_root)
                    found.append(rel)

    return found