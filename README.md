# Still

**Keep your docs and your AI agent's instructions true.**

Still is a GitHub Action that catches semantic drift — in both human-facing docs (`README.md`, `/docs`) and AI-agent instruction files (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules`) — on every pull request, in any language. When a code change makes a stated claim, convention, or instruction wrong, Still flags the exact line and fixes it: a suggestion comment when a human authored the PR, or a direct commit when an AI coding agent did.

Built on **NVIDIA NeMo Agent Toolkit** and a **NIM-hosted Nemotron model**, for the [NVIDIA GTC Berlin Golden Ticket Developer Contest](https://developer.nvidia.com/gtc-golden-ticket-contest).

## The problem

`AGENTS.md`-style files are now the memory layer autonomous coding agents (Claude Code, Cursor, Codex, RALPH-style loops) actually run on — not just documentation for humans. 2026 practitioner consensus is blunt about what happens when they drift: a stale instruction file can be *worse than no file at all*, actively misdirecting an agent and measurably hurting task completion. Notably, no coding agent — including Claude Code itself — auto-updates its own instruction file today; the documented best practice is still "update it manually after every major change."

Existing tools each solve one narrow slice of this — deterministic checkers (Evidoc, config-drift-checker) can tell you a referenced path/command no longer exists, but can't catch a *semantic* contradiction where the text is still syntactically valid but now describes something false. Semantic tools (doc-drift, Mintlify Workflows, CodeRabbit) target human-facing docs only, with no notion of agent-instruction files. Nobody combines semantic reasoning, both target types, any-language support, and origin-aware delivery — see the full comparison in [PRD.md](PRD.md#12-competitive-differentiation-verified).

## How it works

1. A maintainer adds one workflow file to their repo, referencing this Action, and sets a free `NVIDIA_API_KEY` secret.
2. On every PR, Still:
   - **Discovers targets** — auto-detects `AGENTS.md`, `CLAUDE.md`, `.cursor/rules`, `conventions.md`, `README.md`, `/docs/**` (or reads an explicit list from `.still.yml`)
   - **Extracts claims** from the diff — what could this change make wrong?
   - **Checks each target file** for lines those claims now contradict, distinguishing *semantic staleness* (still valid-looking text, now wrong) from a *broken reference* (something that flat-out no longer exists)
   - **Drafts a fix** for each real finding
   - **Detects PR origin** — human or AI agent (via commit trailers like `Co-Authored-By: Claude`)
   - **Delivers the fix**: a suggestion-block comment for a human to accept with one click, or a direct commit into the same branch for an agent-authored PR — still reviewed by a human before merge either way

If nothing is affected, Still posts nothing — no noise on every PR.

## Quick start (for adopters)

Add `.github/workflows/still.yml` to your repo:

```yaml
name: Still
on:
  pull_request:

jobs:
  drift-check:
    runs-on: ubuntu-latest
    permissions:
      pull-requests: read
      contents: write
    steps:
      - uses: actions/checkout@v4
      - uses: <this-repo>@v1
        with:
          nvidia_api_key: ${{ secrets.NVIDIA_API_KEY }}
```

Get a free NVIDIA API key at [build.nvidia.com](https://build.nvidia.com) (no credit card, ~1,000 free inference credits) and add it as a repo secret named `NVIDIA_API_KEY`. That's it — no other setup.

## Local development

```bash
git clone <this-repo>
cd still
python3 -m venv venv && source venv/bin/activate   # Python 3.11-3.13 (nvidia-nat doesn't support 3.14 yet)
pip install -r requirements.txt
pip install -e still_detector
```

Run the test suite (no API key needed — these test pure logic, not live model calls):

```bash
python3 -m pytest tests/ -v
```

Run the actual pipeline locally against a real PR (needs `NVIDIA_API_KEY` and `GITHUB_TOKEN` env vars set):

```bash
nat run --config_file still_detector/src/still_detector/configs/config.yml --input "check this PR"
```

## Architecture

- **NVIDIA NeMo Agent Toolkit** (`nvidia-nat`) — the Still pipeline is registered as a NAT workflow (`still_detector/`), invoked via `nat run`, not a raw API wrapper.
- **NVIDIA NIM** (build.nvidia.com) — hosts the reasoning model (`nvidia/nemotron-3.5-lightning-30b-a3b`), free-tier, OpenAI-compatible endpoint.
- **Deterministic pipeline, not a free-planning agent** — the four steps (discover → extract claims → check → fix) always run in the same fixed order, so we built this as a NAT workflow function rather than a `tool_calling_agent` choosing its own order. See [PRD.md §7](PRD.md#7-nvidia-technology-usage) for the reasoning.
- Source layout: `src/` (core logic: `targets.py`, `github_api.py`, `claims.py`, `fixes.py`, `nim_client.py`), `still_detector/` (the NAT workflow package wrapping it), `tests/` (pytest suite), `benchmark/` (eval harness — see below).

## Benchmark

Still-Bench compares three approaches on a self-built eval set: a deterministic baseline (mimicking Evidoc), a single-prompt LLM call, and Still's full pipeline. On the 3 cases specifically designed to be unsolvable by a deterministic checker:

| Approach | Recall on baseline-proof cases |
|---|---|
| Deterministic baseline | 0% |
| Single-prompt LLM | 67% |
| **Still (full pipeline)** | **100%** |

Full methodology, all results, and honest limitations (small sample, observed run-to-run variance, one latency outlier) are in [BENCHMARK.md](BENCHMARK.md).

## Known limitations / future work

- **Live, mid-session auto-update** (updating a file the instant an agent edits it, before any PR exists) is not built — this needs a local git hook or agent-tool hook, a meaningfully separate project. The obvious next step.
- The full 20-case benchmark wasn't entirely run (9/20, chosen to include the highest-signal cases) — see `BENCHMARK.md` for what's outstanding.
- No per-language AST parsing — Still reasons from raw diff/file text, which is what makes it language-agnostic, but is less precise than a formal parser for very large diffs.

## Testing this repo yourself

Clone it, run `pytest tests/` (no API key needed), then try the Quick Start steps against a scratch repo with your own `AGENTS.md`/`README.md` to see it flag a real drift case end to end.

## License

[MIT](LICENSE)
