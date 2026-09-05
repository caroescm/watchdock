from src.github_api import get_pr_context, get_diff

def main():
    repo, pr = get_pr_context()
    diff = get_diff(pr)

    print(f"PR #{pr.number}: {pr.title}")
    for entry in diff:
        print(f"--- {entry['filename']} ---")
        print(entry['patch'])

if __name__ == "__main__":
    main()