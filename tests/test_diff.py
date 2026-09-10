from watchdoc.diff import format_diff, is_relevant, relevant_entries
from watchdoc.models import DiffEntry


def test_format_diff_joins_multiple_files():
    result = format_diff([
        DiffEntry("a.py", "-old\n+new"),
        DiffEntry("b.py", "-foo\n+bar"),
    ])

    assert result == "--- a.py ---\n-old\n+new\n\n--- b.py ---\n-foo\n+bar"


def test_is_relevant_filters_test_and_boilerplate_files_at_any_depth():
    for path in [
        "tests/foo_test.py", "test/bar.js", "src/tests/foo.py", "pkg/test/bar.js", "foo.test.js",
        "LICENSE", "LICENSE.md", ".gitignore",
        "package-lock.json", "yarn.lock", "poetry.lock", "Cargo.lock",
        "some_dir.egg-info/PKG-INFO",
    ]:
        assert is_relevant(path) is False, path


def test_is_relevant_keeps_real_source_files():
    for path in ["src/main.py", "index.js", "README.md", "testing_utils.py", "contest/rules.md"]:
        assert is_relevant(path) is True, path


def test_relevant_entries_keeps_order():
    entries = [DiffEntry("LICENSE", "+x"), DiffEntry("a.py", "+y"), DiffEntry("b.py", "+z")]

    assert relevant_entries(entries) == entries[1:]
