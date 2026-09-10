import json
import logging
import os
import re

from github import Github

from watchdoc.models import Origin

logger = logging.getLogger(__name__)

NO_PATCH_PLACEHOLDER = "[no textual diff: binary, oversized, or renamed without changes]"


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
    """Returns the PR's diff as a list of {filename, patch} dicts.

    PyGithub gives `patch` as None for binary files, files over GitHub's
    diff size limit, and pure renames. Those still changed, so they stay in
    the list, but with a placeholder rather than the string "None" landing
    in the prompt."""
    diff = []
    for file in pr.get_files():
        diff.append({
            "filename": file.filename,
            "patch": file.patch if file.patch is not None else NO_PATCH_PLACEHOLDER,
        })
    return diff


def get_file_at(repo, path, ref):
    """The text of `path` at `ref` (a sha or branch), or None if it can't be
    fetched or isn't text. Used to anchor suggestions and commits to the PR
    head rather than to the merge commit the Action checks out."""
    try:
        return repo.get_contents(path, ref=ref).decoded_content.decode("utf-8")
    except Exception:  # noqa: BLE001 — caller falls back to the checkout content
        logger.warning("Couldn't fetch %s at %s; falling back to the checkout content",
                       path, ref, exc_info=True)
        return None


# AI coding agents leave one of these git trailers on their commits. A
# trailer is a `Key: value` line at the start of a line in the commit body;
# matching anywhere in the message let unrelated text ("shipped-by: the
# release team") flip a PR to agent.
AGENT_NAMES = ["claude", "cursor", "github copilot", "copilot", "codex", "devin", "aider"]
_AGENT_TRAILER = re.compile(
    r"^(?:co-authored-by|shipped-by):\s*(?:" + "|".join(re.escape(n) for n in AGENT_NAMES) + r")\b",
    re.IGNORECASE | re.MULTILINE,
)

# Bot accounts that are AI coding agents opening PRs under their own login.
# Being on this list means Watchdoc will commit straight onto the PR branch,
# so only agents that keep their branches (no force-push over external
# commits) belong here.
AGENT_LOGINS = {
    "copilot-swe-agent[bot]",
    "devin-ai-integration[bot]",
}


def detect_pr_origin_from_data(commit_messages, author_login=""):
    """Pure logic, no API calls: Origin.AGENT if a commit carries a known
    agent's trailer or the author is a known agent account, else
    Origin.HUMAN. Defaults to HUMAN whenever ambiguous — never guess AGENT.

    Not every `[bot]` is an agent. Dependabot, Renovate and CI bots open
    PRs too, and they force-push over anything committed to their branches,
    so a fix committed there is lost. Those PRs get suggestions like a
    human's."""
    for message in commit_messages:
        if _AGENT_TRAILER.search(message or ""):
            return Origin.AGENT

    if (author_login or "").lower() in AGENT_LOGINS:
        return Origin.AGENT

    return Origin.HUMAN


def detect_pr_origin(pr):
    """Reads a real PR's commits and author, returns an Origin."""
    commit_messages = [c.commit.message for c in pr.get_commits()]
    author_login = pr.user.login if pr.user else ""
    return detect_pr_origin_from_data(commit_messages, author_login)