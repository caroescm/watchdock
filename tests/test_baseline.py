from baseline import deterministic_check


def test_catches_literal_removal():
    """An identifier that's removed and never re-added should be flagged."""
    diff = [{
        "filename": "lib/http.js",
        "patch": '-const axios = require("axios");\n+const fetch = require("node-fetch");\n',
    }]
    target_content = "Always use `axios` for HTTP requests in this project."

    findings = deterministic_check(diff, target_content)

    assert len(findings) == 1
    assert findings[0].type == "broken reference"


def test_does_not_flag_when_identifier_still_present():
    diff = [{
        "filename": "lib/http.js",
        "patch": '+const axios = require("axios");\n',
    }]
    target_content = "Always use `axios` for HTTP requests in this project."

    assert deterministic_check(diff, target_content) == []


def test_never_false_positives_on_unrelated_diff():
    """This is the property that gives the baseline its perfect precision:
    it should never flag something the diff doesn't actually touch."""
    diff = [{
        "filename": "unrelated.js",
        "patch": '-const foo = 1;\n+const foo = 2;\n',
    }]
    target_content = "Always use `axios` for HTTP requests in this project."

    assert deterministic_check(diff, target_content) == []


def test_cannot_catch_semantic_only_drift():
    """Documents the baseline's known, intentional limitation: if the
    identifier persists in the diff, a purely deterministic checker has
    nothing to catch, even if the described behavior actually changed."""
    diff = [{
        "filename": "index.js",
        "patch": '+.option("--verbose", "only supported here now")\n',
    }]
    target_content = "`--verbose` prints extra debug output for any command."

    # The flag "--verbose" is still present, so the naive checker sees no
    # removal — this is exactly the gap Watchdoc's semantic reasoning fills.
    assert deterministic_check(diff, target_content) == []
