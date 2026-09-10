import pytest

from watchdock.errors import ConfigError
from watchdock.targets import discover_targets


def test_discover_targets_empty_repo(tmp_path):
    """No conventional filenames present -> empty list, not an error."""
    assert discover_targets(str(tmp_path)) == []


def test_discover_targets_auto_detect(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# agents")
    (tmp_path / "README.md").write_text("# readme")
    (tmp_path / "irrelevant.txt").write_text("not a target")

    result = discover_targets(str(tmp_path))

    assert "AGENTS.md" in result
    assert "README.md" in result
    assert "irrelevant.txt" not in result


def test_discover_targets_docs_directory(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "api.md").write_text("# api")

    result = discover_targets(str(tmp_path))

    assert "docs/api.md" in result


def test_watchdock_yml_overrides_auto_detect(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# agents")
    (tmp_path / "README.md").write_text("# readme")
    (tmp_path / "CONTRIBUTING.md").write_text("# contributing")
    (tmp_path / ".watchdock.yml").write_text(
        "targets:\n  - CONTRIBUTING.md\n"
    )

    result = discover_targets(str(tmp_path))

    # Only the explicit list should be considered, not the auto-detected files
    assert result == ["CONTRIBUTING.md"]


def test_watchdock_yml_ignore_subtracts_from_auto_detect(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# agents")
    (tmp_path / "README.md").write_text("# readme")
    (tmp_path / ".watchdock.yml").write_text(
        "ignore:\n  - README.md\n"
    )

    result = discover_targets(str(tmp_path))

    assert "AGENTS.md" in result
    assert "README.md" not in result


def test_watchdock_yml_targets_pointing_at_nonexistent_file_is_dropped(tmp_path):
    (tmp_path / ".watchdock.yml").write_text(
        "targets:\n  - DOES_NOT_EXIST.md\n"
    )

    assert discover_targets(str(tmp_path)) == []


def test_binary_file_under_docs_is_not_a_target(tmp_path):
    """A PNG or PDF under docs/ can't be checked for drift and would raise
    when read as text; it must be dropped at discovery, not crash a target."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "api.md").write_text("# api")
    (docs_dir / "diagram.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(256)))

    result = discover_targets(str(tmp_path))

    assert result == ["docs/api.md"]


def test_explicit_target_that_is_binary_is_dropped(tmp_path):
    (tmp_path / "logo.bin").write_bytes(b"\x00\x01\x02\xff\xfe")
    (tmp_path / ".watchdock.yml").write_text("targets:\n  - logo.bin\n")

    assert discover_targets(str(tmp_path)) == []


def test_docs_directory_only_yields_documentation_extensions(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    for name in ["guide.md", "api.rst", "notes.txt", "conf.py", "Makefile", "requirements.txt"]:
        (docs_dir / name).write_text("text")

    result = discover_targets(str(tmp_path))

    assert result == ["docs/api.rst", "docs/guide.md", "docs/notes.txt", "docs/requirements.txt"]


def test_cursor_rules_directory_yields_its_rule_files(tmp_path):
    """Current Cursor keeps rules as .cursor/rules/*.mdc, a directory, which
    an isfile() check silently skipped."""
    rules = tmp_path / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "python.mdc").write_text("rules")
    (rules / "style.mdc").write_text("rules")

    result = discover_targets(str(tmp_path))

    assert result == [".cursor/rules/python.mdc", ".cursor/rules/style.mdc"]



def test_watchdock_yml_ignore_accepts_globs(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "api.md").write_text("# api")
    (docs / "changelog.md").write_text("# changelog")
    (tmp_path / "README.md").write_text("# readme")
    (tmp_path / ".watchdock.yml").write_text("ignore:\n  - docs/*\n")

    assert discover_targets(str(tmp_path)) == ["README.md"]


def test_watchdock_yml_with_a_scalar_targets_value_is_a_config_error(tmp_path):
    """A bare string would otherwise iterate as single characters."""
    (tmp_path / ".watchdock.yml").write_text("targets: README.md\n")

    with pytest.raises(ConfigError, match="'targets' must be a list"):
        discover_targets(str(tmp_path))


def test_watchdock_yml_that_is_not_a_mapping_is_a_config_error(tmp_path):
    (tmp_path / ".watchdock.yml").write_text("- README.md\n")

    with pytest.raises(ConfigError, match="must be a mapping"):
        discover_targets(str(tmp_path))
