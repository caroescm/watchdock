# Security Policy

## Supported versions

Only the latest release on the `v1` tag receives security fixes. Pin
`caroescm/watchdock@v1` to stay on it.

| Version | Supported |
| ------- | --------- |
| `v1` (latest 1.x release) | Yes |
| Older 1.x tags | No |

## Reporting a vulnerability

Please do not open a public issue for security problems.

Use GitHub's private vulnerability reporting instead:
<https://github.com/caroescm/watchdock/security/advisories/new>

Include the affected version, a description of the issue, and steps to
reproduce it. You will get an acknowledgement within 7 days and a fix or a
mitigation plan within 30 days for confirmed reports.

## Scope

Watchdock runs inside your GitHub Actions workflow and handles two secrets:

- `nvidia_api_key`: sent only to the NVIDIA NIM endpoint configured in the
  workflow config. It is never written to logs or to the repository.
- `github_token`: used to read the pull request and post the summary comment,
  review suggestions, or fix commits. Grant only the permissions documented in
  the README; set `commit_fixes: 'false'` to drop `contents: write`.

Reports about dependency vulnerabilities are welcome too. The pinned versions
live in `constraints.txt`, and Dependabot opens PRs for updates.
