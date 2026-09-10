"""
AgentTruth-Bench / Watchdoc-Bench eval cases.

Each case represents one PR against benchmark/sample_repo/. `diff` is a list of DiffEntry
objects, the same shape watchdoc.github_api.get_diff() returns.
`target_path` + `target_content_before` is the target file's content as it
exists BEFORE any fix — i.e. the (possibly now-stale) state we're checking.

`expected` describes ground truth:
- drift: bool — is there real drift to catch at all
- stale_line: the exact line that should be flagged (None for clean cases)
- category: doc_semantic | doc_deterministic | instruction_semantic |
            instruction_deterministic | clean
"""

import os

from watchdoc.models import DiffEntry

_HERE = os.path.dirname(os.path.abspath(__file__))


def _read_sample(relative_path):
    with open(os.path.join(_HERE, "sample_repo", relative_path)) as f:
        return f.read()


CASES = [
    # ---------- Instruction-file, semantic staleness ----------
    {
        "id": "instr_semantic_01",
        "category": "instruction_semantic",
        "description": "AGENTS.md mandates axios; code swaps to node-fetch",
        "diff": [DiffEntry(
            filename="lib/http.js",
            patch=(
                "@@ -1,3 +1,3 @@\n"
                "-const axios = require(\"axios\");\n"
                "-module.exports = (url) => axios.get(url);\n"
                "+const fetch = require(\"node-fetch\");\n"
                "+module.exports = (url) => fetch(url);\n"
            ),
        )],
        "target_path": "AGENTS.md",
        "target_content_before": _read_sample("AGENTS.md"),
        "expected": {
            "drift": True,
            "stale_line": "Always use `axios` for HTTP requests in this project. Do not add other HTTP client libraries.",
        },
    },
    {
        "id": "instr_semantic_02",
        "category": "instruction_semantic",
        "description": "AGENTS.md says CLI parsing uses commander; code swaps to yargs",
        "diff": [DiffEntry(
            filename="index.js",
            patch=(
                "@@ -1,2 +1,2 @@\n"
                "-const { Command } = require(\"commander\");\n"
                "+const yargs = require(\"yargs\");\n"
            ),
        )],
        "target_path": "AGENTS.md",
        "target_content_before": _read_sample("AGENTS.md"),
        "expected": {
            "drift": True,
            "stale_line": "CLI argument parsing uses `commander`. Add new commands as subcommands in `index.js`.",
        },
    },
    {
        "id": "instr_semantic_03",
        "category": "instruction_semantic",
        "description": "AGENTS.md says remove always prompts unless --force; code removes the prompt entirely",
        "diff": [DiffEntry(
            filename="lib/store.js",
            patch=(
                "@@ -30,8 +30,5 @@\n"
                " function removeTask(id, force) {\n"
                "-  if (!force) {\n"
                "-    console.log(\"Are you sure? Pass --force to skip this prompt.\");\n"
                "-    return;\n"
                "-  }\n"
                "   const tasks = loadTasks().filter((t) => t.id !== Number(id));\n"
                "   saveTasks(tasks);\n"
                " }\n"
            ),
        )],
        "target_path": "AGENTS.md",
        "target_content_before": _read_sample("AGENTS.md"),
        "expected": {
            "drift": True,
            "stale_line": "The `todo remove <id>` command always prompts for confirmation unless `--force` is passed.",
        },
    },

    # ---------- Instruction-file, deterministic / broken reference ----------
    {
        "id": "instr_deterministic_01",
        "category": "instruction_deterministic",
        "description": "AGENTS.md tells agents to add commands in index.js; file is renamed to cli.js",
        "diff": [DiffEntry(
            filename="index.js -> cli.js",
            patch=(
                "@@ -1,1 +1,1 @@\n"
                "-index.js\n"
                "+cli.js\n"
            ),
        )],
        "target_path": "AGENTS.md",
        "target_content_before": _read_sample("AGENTS.md"),
        "expected": {
            "drift": True,
            "stale_line": "CLI argument parsing uses `commander`. Add new commands as subcommands in `index.js`.",
        },
    },
    {
        "id": "instr_deterministic_02",
        "category": "instruction_deterministic",
        "description": "AGENTS.md references npm test; package.json's test script key is renamed to 'check'",
        "diff": [DiffEntry(
            filename="package.json",
            patch=(
                "@@ -8,3 +8,3 @@\n"
                "   \"scripts\": {\n"
                "-    \"test\": \"node --test test/\"\n"
                "+    \"check\": \"node --test test/\"\n"
                "   }\n"
            ),
        )],
        "target_path": "AGENTS.md",
        "target_content_before": _read_sample("AGENTS.md"),
        "expected": {
            "drift": True,
            "stale_line": "Run tests with `npm test`. All new commands must have a corresponding test in `test/`.",
        },
    },
    {
        "id": "instr_deterministic_03",
        "category": "instruction_deterministic",
        "description": "AGENTS.md references removeTask; function is renamed to deleteTask",
        "diff": [DiffEntry(
            filename="lib/store.js",
            patch=(
                "@@ -30,1 +30,1 @@\n"
                "-function removeTask(id, force) {\n"
                "+function deleteTask(id, force) {\n"
            ),
        )],
        "target_path": "AGENTS.md",
        "target_content_before": _read_sample("AGENTS.md"),
        "expected": {
            "drift": True,
            "stale_line": "Internally this is implemented by the `removeTask` function in `lib/store.js`.",
        },
    },

    # ---------- Doc (README), semantic staleness ----------
    {
        "id": "doc_semantic_01",
        "category": "doc_semantic",
        "description": "README says HTTP sync uses axios; code swaps to node-fetch",
        "diff": [DiffEntry(
            filename="lib/http.js",
            patch=(
                "@@ -1,3 +1,3 @@\n"
                "-const axios = require(\"axios\");\n"
                "+const fetch = require(\"node-fetch\");\n"
            ),
        )],
        "target_path": "README.md",
        "target_content_before": _read_sample("README.md"),
        "expected": {
            "drift": True,
            "stale_line": "HTTP sync (optional, for the `todo sync` command) uses `axios` under the hood.",
        },
    },
    {
        "id": "doc_semantic_02",
        "category": "doc_semantic",
        "description": "README says --verbose works for any command; code restricts it to just 'list'",
        "diff": [DiffEntry(
            filename="index.js",
            patch=(
                "@@ -10,6 +10,7 @@\n"
                " program\n"
                "   .command(\"list\")\n"
                "   .description(\"list all tasks\")\n"
                "+  .option(\"--verbose\", \"only supported here now\")\n"
                "   .action(() => store.listTasks());\n"
            ),
        )],
        "target_path": "README.md",
        "target_content_before": _read_sample("README.md"),
        "expected": {
            "drift": True,
            "stale_line": "`--verbose` prints extra debug output for any command.",
        },
    },
    {
        "id": "doc_semantic_03",
        "category": "doc_semantic",
        "description": "README says tasks stored at ~/.todo-cli/tasks.json; code changes path",
        "diff": [DiffEntry(
            filename="lib/store.js",
            patch=(
                "@@ -4,2 +4,2 @@\n"
                "-const STORE_PATH = path.join(os.homedir(), \".todo-cli\", \"tasks.json\");\n"
                "+const STORE_PATH = path.join(os.homedir(), \".config\", \"todo-cli\", \"tasks.json\");\n"
            ),
        )],
        "target_path": "README.md",
        "target_content_before": _read_sample("README.md"),
        "expected": {
            "drift": True,
            "stale_line": "Tasks are stored as JSON in `~/.todo-cli/tasks.json`.",
        },
    },

    # ---------- Doc (README), deterministic / broken reference ----------
    {
        "id": "doc_deterministic_01",
        "category": "doc_deterministic",
        "description": "README documents --force flag; flag is removed from remove command entirely",
        "diff": [DiffEntry(
            filename="index.js",
            patch=(
                "@@ -18,7 +18,6 @@\n"
                " program\n"
                "   .command(\"remove <id>\")\n"
                "   .description(\"remove a task\")\n"
                "-  .option(\"--force\", \"skip confirmation prompt\")\n"
                "-  .action((id, options) => store.removeTask(id, options.force));\n"
                "+  .action((id) => store.removeTask(id, true));\n"
            ),
        )],
        "target_path": "README.md",
        "target_content_before": _read_sample("README.md"),
        "expected": {
            "drift": True,
            "stale_line": "`--force` skips the confirmation prompt when removing a task.",
        },
    },
    {
        "id": "doc_deterministic_02",
        "category": "doc_deterministic",
        "description": "README's usage example shows `todo done 1`; done command is renamed to `complete`",
        "diff": [DiffEntry(
            filename="index.js",
            patch=(
                "@@ -12,3 +12,3 @@\n"
                " program\n"
                "-  .command(\"done <id>\")\n"
                "+  .command(\"complete <id>\")\n"
            ),
        )],
        "target_path": "README.md",
        "target_content_before": _read_sample("README.md"),
        "expected": {
            "drift": True,
            "stale_line": "todo done 1",
        },
    },
    {
        "id": "doc_deterministic_03",
        "category": "doc_deterministic",
        "description": "README says `npm test` runs tests; script key renamed to 'check'",
        "diff": [DiffEntry(
            filename="package.json",
            patch=(
                "@@ -8,3 +8,3 @@\n"
                "   \"scripts\": {\n"
                "-    \"test\": \"node --test test/\"\n"
                "+    \"check\": \"node --test test/\"\n"
                "   }\n"
            ),
        )],
        "target_path": "README.md",
        "target_content_before": _read_sample("README.md"),
        "expected": {
            "drift": True,
            "stale_line": "npm test",
        },
    },

    # ---------- Clean cases (no real drift) — false-positive control group ----------
    {
        "id": "clean_01",
        "category": "clean",
        "description": "Internal refactor of loadTasks, no behavior/interface change",
        "diff": [DiffEntry(
            filename="lib/store.js",
            patch=(
                "@@ -6,3 +6,4 @@\n"
                " function loadTasks() {\n"
                "-  if (!fs.existsSync(STORE_PATH)) return [];\n"
                "-  return JSON.parse(fs.readFileSync(STORE_PATH, \"utf8\"));\n"
                "+  const exists = fs.existsSync(STORE_PATH);\n"
                "+  if (!exists) return [];\n"
                "+  return JSON.parse(fs.readFileSync(STORE_PATH, \"utf8\"));\n"
            ),
        )],
        "target_path": "AGENTS.md",
        "target_content_before": _read_sample("AGENTS.md"),
        "expected": {"drift": False, "stale_line": None},
    },
    {
        "id": "clean_02",
        "category": "clean",
        "description": "Adds a new test file, doesn't change any documented behavior",
        "diff": [DiffEntry(
            filename="test/store.test.js",
            patch=(
                "@@ -0,0 +1,3 @@\n"
                "+const { test } = require(\"node:test\");\n"
                "+test(\"addTask works\", () => {});\n"
            ),
        )],
        "target_path": "AGENTS.md",
        "target_content_before": _read_sample("AGENTS.md"),
        "expected": {"drift": False, "stale_line": None},
    },
    {
        "id": "clean_03",
        "category": "clean",
        "description": "Formatting-only change (whitespace), no behavior change",
        "diff": [DiffEntry(
            filename="index.js",
            patch=(
                "@@ -1,2 +1,3 @@\n"
                " #!/usr/bin/env node\n"
                "+\n"
                " const { Command } = require(\"commander\");\n"
            ),
        )],
        "target_path": "README.md",
        "target_content_before": _read_sample("README.md"),
        "expected": {"drift": False, "stale_line": None},
    },
    {
        "id": "clean_04",
        "category": "clean",
        "description": "Bumps a dependency's patch version only, no API/behavior change",
        "diff": [DiffEntry(
            filename="package.json",
            patch=(
                "@@ -5,2 +5,2 @@\n"
                "-    \"axios\": \"^1.6.0\"\n"
                "+    \"axios\": \"^1.6.2\"\n"
            ),
        )],
        "target_path": "AGENTS.md",
        "target_content_before": _read_sample("AGENTS.md"),
        "expected": {"drift": False, "stale_line": None},
    },
    {
        "id": "clean_05",
        "category": "clean",
        "description": "Adds an internal helper function that isn't part of any documented interface",
        "diff": [DiffEntry(
            filename="lib/store.js",
            patch=(
                "@@ -40,0 +41,3 @@\n"
                "+function _debugDump() {\n"
                "+  console.log(loadTasks());\n"
                "+}\n"
            ),
        )],
        "target_path": "README.md",
        "target_content_before": _read_sample("README.md"),
        "expected": {"drift": False, "stale_line": None},
    },
    {
        "id": "clean_06",
        "category": "clean",
        "description": "Adds a code comment only, no functional change",
        "diff": [DiffEntry(
            filename="lib/store.js",
            patch=(
                "@@ -1,3 +1,4 @@\n"
                " const fs = require(\"fs\");\n"
                "+// handles reading/writing tasks.json\n"
                " const os = require(\"os\");\n"
            ),
        )],
        "target_path": "AGENTS.md",
        "target_content_before": _read_sample("AGENTS.md"),
        "expected": {"drift": False, "stale_line": None},
    },
    {
        "id": "clean_07",
        "category": "clean",
        "description": "Reorders object keys in package.json, no semantic change",
        "diff": [DiffEntry(
            filename="package.json",
            patch=(
                "@@ -1,4 +1,4 @@\n"
                " {\n"
                "-  \"name\": \"todo-cli\",\n"
                "   \"version\": \"1.0.0\",\n"
                "+  \"name\": \"todo-cli\",\n"
            ),
        )],
        "target_path": "README.md",
        "target_content_before": _read_sample("README.md"),
        "expected": {"drift": False, "stale_line": None},
    },
    {
        "id": "clean_08",
        "category": "clean",
        "description": "Adds error handling around an existing call, no interface/behavior change from the user's perspective",
        "diff": [DiffEntry(
            filename="lib/store.js",
            patch=(
                "@@ -10,2 +10,6 @@\n"
                " function saveTasks(tasks) {\n"
                "-  fs.mkdirSync(path.dirname(STORE_PATH), { recursive: true });\n"
                "-  fs.writeFileSync(STORE_PATH, JSON.stringify(tasks, null, 2));\n"
                "+  try {\n"
                "+    fs.mkdirSync(path.dirname(STORE_PATH), { recursive: true });\n"
                "+    fs.writeFileSync(STORE_PATH, JSON.stringify(tasks, null, 2));\n"
                "+  } catch (e) {\n"
                "+    console.error(\"Failed to save tasks:\", e.message);\n"
                "+  }\n"
            ),
        )],
        "target_path": "AGENTS.md",
        "target_content_before": _read_sample("AGENTS.md"),
        "expected": {"drift": False, "stale_line": None},
    },
]
