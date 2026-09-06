# todo-cli

A tiny command-line todo list manager.

## Installation

```
npm install
```

## Usage

```
todo add "Buy milk"
todo list
todo done 1
todo remove 1 --force
```

Tasks are stored as JSON in `~/.todo-cli/tasks.json`.

## Flags

- `--force` skips the confirmation prompt when removing a task.
- `--verbose` prints extra debug output for any command.

## Development

Run tests with:

```
npm test
```

HTTP sync (optional, for the `todo sync` command) uses `axios` under the hood.
