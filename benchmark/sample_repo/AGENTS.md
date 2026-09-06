# Agent Instructions

## HTTP Calls
Always use `axios` for HTTP requests in this project. Do not add other HTTP client libraries.

## CLI Parsing
CLI argument parsing uses `commander`. Add new commands as subcommands in `index.js`.

## Storage
Task data is stored at `~/.todo-cli/tasks.json`. Do not change this path without updating both `README.md` and `lib/store.js`.

## Testing
Run tests with `npm test`. All new commands must have a corresponding test in `test/`.

## Removing tasks
The `todo remove <id>` command always prompts for confirmation unless `--force` is passed.
Internally this is implemented by the `removeTask` function in `lib/store.js`.
