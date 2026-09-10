"""Watchdock core: detects semantic drift in docs and AI-agent instruction files
against a PR diff, drafts fixes, and delivers them to the PR.

Module map, in pipeline order:

- ``pipeline``    the whole run, host-independent (``run_pipeline``)
- ``github_api``  the PR, its diff and files, and who authored it
- ``targets``     which files in the checkout are doc/instruction targets
- ``diff``        filtering and formatting the diff for the model
- ``claims``      the two detection calls and the bounded ensemble fan-out
- ``prompts``     every prompt string, and nothing else
- ``parsing``     reading the model's replies
- ``fixes``       drafting a fix and committing or suggesting it
- ``report``      the one summary comment per PR
- ``nim_client``  the model client: streaming, retries, concurrency gate
- ``models``      the frozen dataclasses and enums every stage shares
- ``env``         every environment variable, in one place
- ``errors``      the exceptions Watchdock raises on purpose

The NeMo Agent Toolkit entry point that hosts the pipeline lives in the
separate ``watchdock_detector`` package, which depends on this one.
"""
