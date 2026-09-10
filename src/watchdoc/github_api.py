import json
import os

from github import Github

from watchdoc.models import Origin


def get_pr_context(pr_number=None):
    """Returns (repo, pr) for the PR this run is about.

    Inside a GitHub Action the PR comes from the workflow's event payload.
    For a local run there is no payload, so callers pass pr_number
    explicitly (the NAT entry point takes it from the `--input` text)."""
    token = os.environ["GITHUB_TOKEN"]
    repo_name = os.environ["GITHUB_REPOSITORY"]

    if pr_number is None:
        event_path = os.environ.get("GITHUB_EVENT_PATH")
        if not event_path:
            raise RuntimeError(
                "No PR to check: GITHUB_EVENT_PATH is not set and no PR number was given. "
                "For a local run, pass the PR in the input, e.g. --input 'check PR #12'."
            )
        with open(event_path) as f:
            event = json.load(f)
        pr_number = event["pull_request"]["number"]

    gh = Github(token)
    repo = gh.get_repo(repo_name)
    pr = repo.get_pull(pr_number)
    return repo, pr


def get_diff(pr):
    """Returns the PR's diff as a list of {filename, patch} dicts."""
    diff = []
    for file in pr.get_files():
        diff.append({
            "filename": file.filename,
            "patch": file.patch,
        })
    return diff


AGENT_TRAILER_PATTERNS = [
    "co-authored-by: claude",
    "co-authored-by: cursor",
    "co-authored-by: github copilot",
    "co-authored-by: codex",
    "shipped-by:",
]


def detect_pr_origin_from_data(commit_messages, author_login=""):
    """Pure logic, no API calls: Origin.AGENT if commit messages or the
    author login look like a known AI coding agent, else Origin.HUMAN.
    Defaults to HUMAN whenever ambiguous — never guess AGENT."""
    for message in commit_messages:
        lowered = message.lower()
        for pattern in AGENT_TRAILER_PATTERNS:
            if pattern in lowered:
                return Origin.AGENT

    if (author_login or "").lower().endswith("[bot]"):
        return Origin.AGENT

    return Origin.HUMAN


def detect_pr_origin(pr):
    """Reads a real PR's commits and author, returns an Origin."""
    commit_messages = [c.commit.message for c in pr.get_commits()]
    author_login = pr.user.login if pr.user else ""
    return detect_pr_origin_from_data(commit_messages, author_login)