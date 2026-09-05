import os
import json
from github import Github


def get_pr_context():
    """Reads which PR triggered this run and returns (repo, pr) objects."""
    token = os.environ["GITHUB_TOKEN"]
    repo_name = os.environ["GITHUB_REPOSITORY"]
    event_path = os.environ["GITHUB_EVENT_PATH"]

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
    """Pure logic, no API calls: returns 'agent' if commit messages or the
    author login look like a known AI coding agent, else 'human'. Defaults
    to 'human' whenever ambiguous — never guess 'agent'."""
    for message in commit_messages:
        lowered = message.lower()
        for pattern in AGENT_TRAILER_PATTERNS:
            if pattern in lowered:
                return "agent"

    if (author_login or "").lower().endswith("[bot]"):
        return "agent"

    return "human"


def detect_pr_origin(pr):
    """Reads a real PR's commits and author, returns 'agent' or 'human'."""
    commit_messages = [c.commit.message for c in pr.get_commits()]
    author_login = pr.user.login if pr.user else ""
    return detect_pr_origin_from_data(commit_messages, author_login)