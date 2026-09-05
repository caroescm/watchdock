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