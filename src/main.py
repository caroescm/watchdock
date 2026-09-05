import os
import json
from github import Github

def main():
    token = os.environ["GITHUB_TOKEN"]
    repo_name = os.environ["GITHUB_REPOSITORY"]
    event_path = os.environ["GITHUB_EVENT_PATH"]

    with open(event_path) as f:
        event = json.load(f)

    pr_number = event["pull_request"]["number"]

    gh = Github(token)
    repo = gh.get_repo(repo_name)
    pr = repo.get_pull(pr_number)

    print(f"PR #{pr_number}: {pr.title}")
    for file in pr.get_files():
        print(f"--- {file.filename} ---")
        print(file.patch)

if __name__ == "__main__":
    main()
