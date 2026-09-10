# Watchdoc

**Keep your docs and your AI agent's instructions true.**

Watchdoc is a GitHub Action that catches semantic drift — in both human-facing docs (`README.md`, `/docs`) and AI-agent instruction files (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules`) — on every pull request, in any language. When a code change makes a stated claim, convention, or instruction wrong, Watchdoc flags the exact line and fixes it: a suggestion comment when a human authored the PR, or a direct commit when an AI coding agent did.

Built on **NVIDIA NeMo Agent Toolkit** and a **NIM-hosted Nemotron model**, for the [NVIDIA GTC Berlin Golden Ticket Developer Contest](https://developer.nvidia.com/gtc-golden-ticket-contest).

## See it in action

A real PR in this repo renamed the config file `.still.yml` to `.still-config.yml` in code. Watchdoc detected that the README's description was now stale, recognized the PR as agent-authored, committed the one-line fix itself, and explained what it did:

> 🔧 **Watchdoc — semantic staleness**: committed a fix to `README.md` on this branch.
>
> **Why:** The CONFIG_FILENAME changed from `.still.yml` to `.still-config.yml`, so Watchdoc now reads the explicit target list from `.still-config.yml` instead. This line still reads syntactically valid text but now describes an outdated config filename, making it semantically stale.
>
> **Old:** … (or reads an explicit list from `.still.yml`)
> **New:** … (or reads an explicit list from `.watchdoc.yml`)

Every run also maintains a single summary comment on the PR — green "no drift detected" or red with the findings — created once and edited in place on re-runs, so it never piles up.

## Quick start (for adopters)

Add `.github/workflows/watchdoc.yml` to your repo:

```yaml
name: Watchdoc
on:
  pull_request:

jobs:
  drift-check:
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write   # post suggestions and the summary comment
      contents: write        # only if commit_fixes stays 'true' (direct commits on agent PRs)
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
      - uses: caroescm/watchdoc@main
        with:
          nvidia_api_key: ${{ secrets.NVIDIA_API_KEY }}
```

Get a free NVIDIA API key at [build.nvidia.com](https://build.nvidia.com) (no credit card, ~1,000 free inference credits) and add it as a repo secret named `NVIDIA_API_KEY`. That's it — no other setup. To try it end to end, open a PR that changes something your `README.md` or `AGENTS.md` describes (rename a command, a flag, a config key) and watch Watchdoc flag and fix the stale line.

### Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `nvidia_api_key` | Yes | — | NVIDIA NIM API key, used to call the NIM-hosted Nemotron model. |
| `github_token` | No | `${{ github.token }}` | GitHub token for reading PRs and posting results. |
| `commit_fixes` | No | `'true'` | Commit fixes directly onto agent-authored PR branches. Set `'false'` to deliver every fix as a review suggestion and drop `contents: write` from the job. |

### Outputs

Watchdoc has no outputs — it delivers results directly as PR comments and commits rather than exposing them to downstream steps.

## The problem

`AGENTS.md`-style files are now the memory layer autonomous coding agents (Claude Code, Cursor, Codex, RALPH-style loops) actually run on — not just documentation for humans. 2026 practitioner consensus is blunt about what happens when they drift: a stale instruction file can be *worse than no file at all*, actively misdirecting an agent and measurably hurting task completion. Notably, no coding agent — including Claude Code itself — auto-updates its own instruction file today; the documented best practice is still "update it manually after every major change."

Existing tools each solve one narrow slice of this — deterministic checkers (Evidoc, config-drift-checker) can tell you a referenced path/command no longer exists, but can't catch a *semantic* contradiction where the text is still syntactically valid but now describes something false. Semantic tools (doc-drift, Mintlify Workflows, CodeRabbit) target human-facing docs only, with no notion of agent-instruction files. Nobody combines semantic reasoning, both target types, any-language support, and origin-aware delivery — see the full comparison in [PRD.md](project-docs/PRD.md#12-competitive-differentiation-verified).

## How it works

On every PR, Watchdoc:

1. **Discovers targets** — auto-detects `AGENTS.md`, `CLAUDE.md`, `.cursor/rules`, `conventions.md`, `README.md`, `/docs/**` (or reads an explicit list from `.watchdoc.yml`)
2. **Extracts claims** from the diff — a reasoning-model pass that lists, one by one, what this change could make wrong in documentation
3. **Checks each claim against each target file independently** — every claim gets its own focused model calls (a 3-sample parallel ensemble per claim, findings unioned), distinguishing *semantic staleness* (still valid-looking text, now wrong) from a *broken reference* (something that flat-out no longer exists)
4. **Drafts a fix** for each real finding
5. **Detects PR origin** — human or AI agent (via commit trailers like `Co-Authored-By: Claude`)
6. **Delivers the fix** — a suggestion-block comment (with the finding type and reason) for a human to accept with one click, or a direct commit into the same branch plus an explanatory comment for an agent-authored PR — still reviewed by a human before merge either way
7. **Maintains one summary comment** — what was checked, every claim extracted, every fix delivered; edited in place on re-runs

If nothing is affected, the summary comment simply reports a green "no drift detected" — one tidy comment per PR, never a pile.

## Architecture

- **NVIDIA NeMo Agent Toolkit** (`nvidia-nat`) — the Watchdoc pipeline is registered as a NAT workflow (`watchdoc_detector/`), invoked via `nat run`, not a raw API wrapper.
- **NVIDIA NIM** (build.nvidia.com) — hosts the reasoning model (`nvidia/nemotron-3.5-lightning-30b-a3b`), free-tier, OpenAI-compatible endpoint.
- **Deterministic pipeline, not a free-planning agent** — the steps (discover → extract claims → check → fix → deliver) always run in the same fixed order, so we built this as a NAT workflow function rather than a `tool_calling_agent` choosing its own order. See [PRD.md §7](project-docs/PRD.md#7-nvidia-technology-usage) for the reasoning.
- **Reliability engineering, all measured against real failures**, not hypothetical ones:
  - *Per-claim decomposition:* NVIDIA's free-tier NIM endpoint enforces a hard ~10-minute server-side generation cap (two real CI runs died at 10:00 sharp). One narrow claim per call keeps every generation far under it — and since claims run in parallel, wall-clock is the slowest claim, not one monolithic pass. On the exact PR that used to fail, a whole-blob control call also *missed* findings the per-claim calls caught.
  - *3x ensembles:* the model is measurably nondeterministic at `temperature=0` — the same call sometimes catches a real finding and sometimes misses it. Three samples unioned hedge that; a failed sample is a missing vote, not a fatal error.
  - *Streaming + whole-call retries:* non-streaming calls got connection-reset at ~4.5 minutes of silence by an intermediate proxy; mid-stream server errors aren't covered by SDK retries. Both observed in real runs, both handled.
  - *Bounded concurrency:* a global cap on simultaneous NIM requests, so (claims × ensemble × targets) fan-out can't stampede the free-tier API.
- **Source layout:** `src/watchdoc/` (the core package: `targets.py`, `github_api.py`, `claims.py`, `fixes.py`, `report.py`, `nim_client.py`), `watchdoc_detector/` (the NAT workflow package, which depends on `watchdoc`), `tests/` (pytest suite, no network needed), `benchmark/` (eval harness — see below).

## Benchmark

Watchdoc-Bench compares three approaches on a self-built eval set: a deterministic baseline (mimicking Evidoc), a single-prompt LLM call, and Watchdoc's full pipeline (claim extraction → per-claim 3x parallel ensemble checks, unioned). Final result on all 9 cases run:

| Approach | Recall | Precision |
|---|---|---|
| Deterministic baseline | 57% | 1.00 |
| Single-prompt LLM | 71% | 1.00 |
| **Watchdoc (full pipeline)** | **100%** | **1.00** |

On the 3 cases specifically designed to be unsolvable by a deterministic checker: baseline 0%, single-prompt 67%, Watchdoc 100%. After the per-claim redesign, the highest-signal subset (those 3 hardest cases, the historical truncation-bug case, and 2 clean controls) was re-run against the live API: same 100% recall and precision, with faster per-case times.

This 100% wasn't the first result — it came from diagnosing and fixing a real bug (the model getting stuck deliberating and running out of its thinking-token budget before emitting an answer) after an earlier optimization attempt for speed dropped recall to 71%. Full methodology, the complete optimization journey, and honest remaining limitations (small sample, demonstrated model nondeterminism at temperature=0, real latency) are in [BENCHMARK.md](project-docs/BENCHMARK.md) — worth reading, since the journey is more informative than the final table alone.

## Local development

```bash
git clone https://github.com/caroescm/watchdoc.git
cd watchdoc
python3 -m venv venv && source venv/bin/activate   # Python 3.11-3.13 (nvidia-nat doesn't support 3.14 yet)
pip install -e ".[dev]" -e watchdoc_detector
```

The single `pip install` resolves both local packages together: `watchdoc` (core logic plus the `dev` extra for pytest) and `watchdoc_detector` (the NAT entry point, which depends on `watchdoc`). The action itself installs with `-c constraints.txt`, which pins every transitive dependency; refresh it with `pip freeze --exclude-editable > constraints.txt` after a deliberate upgrade.

Run the test suite (no API key needed — these test pure logic, not live model calls):

```bash
python3 -m pytest tests/ -v
```

Run the actual pipeline locally against a real PR. It needs `NVIDIA_API_KEY`, `GITHUB_TOKEN` and `GITHUB_REPOSITORY` set, plus the PR number in the input (inside GitHub Actions the PR comes from the event payload instead):

```bash
cd /path/to/the/repo/you/want/to/check       # or set repo_root in config.yml
GITHUB_REPOSITORY=owner/repo nat run \
  --config_file /path/to/watchdoc/watchdoc_detector/src/watchdoc_detector/configs/config.yml \
  --input "check PR #12"
```

Model, ensemble size, concurrency cap, timeout and the scanned checkout are all fields on the workflow in [`config.yml`](watchdoc_detector/src/watchdoc_detector/configs/config.yml); the defaults are listed there.

## Compatibility

Every run of this repo's own CI (`.github/workflows/watchdoc.yml`) exercises the action end-to-end on `ubuntu-latest` — that's the only runner it's actually been tested on. It hasn't been run on `macos-latest` or `windows-latest`. Both `run:` steps in [`action.yml`](action.yml) declare `shell: bash` explicitly (composite steps don't inherit a default shell), so they should work on all three GitHub-hosted runners — Windows runners ship Git Bash — but the Python/`pip`/`nat` toolchain behavior on macOS and Windows hasn't been verified in CI. If you hit a runner-specific issue, please open one.

## Known limitations / future work

- **Live, mid-session auto-update** (updating a file the instant an agent edits it, before any PR exists) is not built — this needs a local git hook or agent-tool hook, a meaningfully separate project. The obvious next step.
- The full 20-case benchmark wasn't entirely run (9/20, chosen to include the highest-signal cases) — see [`BENCHMARK.md`](project-docs/BENCHMARK.md) for what's outstanding.
- No per-language AST parsing — Watchdoc reasons from raw diff/file text, which is what makes it language-agnostic, but is less precise than a formal parser for very large diffs.
- **Latency is real, if much improved.** Measured on real CI runs: a clean PR completes in ~5–6 minutes; a PR with drift (detection + fix drafting + delivery) takes ~9–12. Reasoning-mode calls are doing real inference work — every shortcut tried (disabling thinking, capping the ensemble wait) measurably cost recall and was reverted. See [`BENCHMARK.md`](project-docs/BENCHMARK.md) for the full latency investigation.
- **API cost per PR is higher** than a single-call design: the detection step runs 3 ensemble samples per extracted claim in exchange for reliability.
- **Precision on judgment-call lines isn't perfect**: the model can occasionally flag a line whose truth is arguable (e.g. a competitive claim about other tools). Delivery fails safe — a fix that doesn't match verbatim, or changes nothing, is refused rather than applied — and everything it does is explained on the PR.
- **A bounded time budget with honest "incomplete" reporting** (post confirmed findings, flag remaining analysis as incomplete rather than silently timing out) is a concrete, low-risk next step — see [`BENCHMARK.md`](project-docs/BENCHMARK.md) for this and other latency ideas that were evaluated and not built, with the reasoning why.

## License

[MIT](LICENSE)
