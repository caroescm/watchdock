# Watchdock — PRD
*(working name — a GitHub Action that keeps docs and AI-agent instruction files honest)*

**Contest:** NVIDIA GTC Berlin Golden Ticket Developer Contest
**Author:** Carolina Escudero

---

## 1. One-liner

A GitHub Action that catches **semantic drift** — in both general documentation (README, `/docs`, docstrings) and AI-agent instruction files (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules`, `conventions.md`) — on every pull request, in any language. When a code change makes a stated claim, convention, or instruction wrong, it flags the exact line and fixes it: as a suggestion comment when a human authored the PR, or as a direct commit into the same branch when an AI coding agent did.

## 2. Problem

Two related but distinct things rot when code changes and nobody remembers to update them in the same PR:

1. **Human-facing docs** (README, `/docs`, docstrings) — the classic "docs lie" problem.
2. **Agent-facing instruction files** (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules`, `conventions.md`) — now the memory layer autonomous coding agents (Claude Code, Cursor, Codex, RALPH-style loops) actually run on, not just human reference material. 2026 practitioner consensus is blunt about what happens when these drift: a stale instruction file can be *worse than no file at all*, actively misdirecting an agent and measurably dropping task completion (a commonly cited figure: ~30% degradation). RALPH-style loops make this worse by design — they deliberately avoid conversation memory and rely entirely on `AGENTS.md → conventions.md → checklist` files to hand off state between agent iterations, so a stale file doesn't just confuse a human, it corrupts the loop's actual working memory. Notably, no coding agent — including Claude Code itself — auto-updates its own instruction file today; the documented best practice is still "update it manually after every major change."

Existing tools each solve one narrow slice of this:
- **Evidoc / config-drift-checker** touch agent-instruction files, but only *deterministically* (does a referenced path/command/symbol still exist, or does a rule have eval-case coverage) — neither catches a semantic convention violation, e.g. `AGENTS.md` says "use `requests`" but the PR just switched to `httpx`. Nothing is "broken"; the instruction is just now wrong.
- **CLAUDE.md Auto-Updater** (a Claude Code Skill) does semantic reasoning with confidence scores — the closest thing to our core mechanism — but it only runs *inside a Claude Code session* (manually invoked or periodic), not as a CI check visible to every reviewer on every PR regardless of which tool wrote the code. It also only targets CLAUDE.md, not general docs.
- **jbrockSTL/doc-drift, docsync, CodeRabbit** target human-facing README/docs only, with no notion of agent-instruction files at all.
- **Cursor Bugbot / autogit** auto-commit fixes to a branch, but for code bugs, not doc/instruction drift — though they establish that origin-aware auto-commit (with attribution trailers like `Co-Authored-By: Claude` or `Shipped-by: autogit`) is already normal, checkable practice.

Nobody combines: **semantic reasoning**, **both target types (docs + agent files) in one tool**, **any language**, **direct-commit delivery** (the fix lands on the branch, not just a suggestion someone has to notice and apply), and **NVIDIA-native tooling**, running as a **CI-level check on every PR** regardless of which coding agent (if any) was used. That combination is the wedge — not any single piece of it in isolation.

## 3. Goals (MVP, 5-day build)

1. Works on **any repo structure and any language** — no per-language parser; the model reasons directly from raw diff text + raw doc/instruction-file text, so it isn't tied to Python's `ast` module or any single ecosystem.
2. Checks **both** general docs (README, `/docs`, docstrings/comments) **and** agent-instruction files (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules`, `conventions.md`) with the same underlying mechanism.
3. On every PR: flags stale/contradicted claims and either (a) posts a GitHub suggestion comment for a human to accept with one click, or (b) commits the fix directly into the same PR branch when the PR was authored by a detected AI agent (via commit trailers like `Co-Authored-By: Claude` or known bot identities) — still reviewed by a human before merge either way, just without an extra manual step for agent-authored work.
4. Runs on NVIDIA's free-tier hosted inference (build.nvidia.com / NIM) via the NVIDIA NeMo Agent Toolkit — no local GPU, no paid API key required for judges or adopters to try it.
5. Ships as an installable GitHub Action (one YAML file) with a real open-source repo, clear README, and a demo video showing it catching real drift on a real PR — ideally against a RALPH-style repo to make the "this breaks your agent loop" stakes concrete.

## 4. Non-goals (explicitly out of scope for the 5-day MVP)

- **Live, mid-session auto-update** (updating a file the instant an agent edits it, before any commit/PR exists) — this needs a local git hook or agent-tool hook, not a GitHub Action, and is a meaningfully separate build. Named explicitly in the README as the obvious next step, not attempted now.
- Auto-merging a PR, or committing fixes without any human ever reviewing the final PR — the auto-commit mode still lands inside a PR a human approves before merge.
- Full-repo semantic search / long-context whole-codebase understanding — only the diff + directly touched doc/instruction sections.
- A formal per-language AST-based extraction layer — deliberately skipped in favor of raw-text reasoning (see Goal 1); revisit only if benchmark results show raw-text reasoning missing too much.

## 5. How it works (user flow)

1. Maintainer adds `.github/workflows/still.yml` (one file) to their repo, referencing this Action, and sets a `NIM_API_KEY` secret (free from build.nvidia.com).
2. On every PR, the Action runs:
   a. **Discover targets** — auto-detects instruction files (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules`, `conventions.md`, RALPH-style checklists) and doc files (`README.md`, `/docs/**`). If a `.still.yml` config exists, its explicit path list overrides auto-detection.
   b. **Extract relevant claims from the diff** — one NIM call: "given this code diff, list any statement in these files that this change could make wrong (library choice, command to run, architecture description, file/module locations, conventions, documented behavior)."
   c. **Check claims against current file content** — for each detected file, one NIM call: "does this text still accurately describe the current code, given this change? If not, quote the stale line and explain why, distinguishing semantic staleness (still 'valid'-looking text, now wrong) from an already-broken reference."
   d. **Generate fix suggestions** — for each confirmed stale line, one NIM call: "rewrite this line/paragraph to match the new code behavior/convention."
   e. **Detect PR origin** — check commit trailers/authorship for known AI-agent signatures (`Co-Authored-By: Claude`, Cursor's attribution, known bot accounts).
   f. **Deliver the fix** — human-authored PR: post a comment listing each finding with a GitHub suggestion block per fix (click "Commit suggestion" to apply). Agent-authored PR: commit the fix directly into the same branch, so the reviewer sees code + corrected docs/instructions as one diff.
3. If no drift is found, the Action posts nothing (or a minimal "✅ docs and instructions still accurate" comment) — avoid noisy no-op comments on every PR.

## 6. Auto-detection + config override spec

**Default auto-detection (no config needed):**
- Instruction files: `AGENTS.md`, `CLAUDE.md`, `.cursor/rules`, `.cursorrules`, `conventions.md`, checklist-style files referenced from `AGENTS.md`
- Docs: `README.md` / `README.rst`, any file under `/docs/`, `/documentation/`
- No per-language parsing — the diff's raw text and the target file's raw text both go directly to the model, so any language works without extra code

**Optional override — `.still.yml`:**
```yaml
targets:
  - AGENTS.md
  - CLAUDE.md
  - README.md
  - docs/api.md
ignore:
  - docs/CHANGELOG.md
```
If present, this file's `targets` list replaces auto-detection entirely; `ignore` subtracts from either mode. This satisfies "any org's structure" without building a heavier classifier — auto-detect covers the common conventional filenames, config covers the edge case.

## 7. NVIDIA technology usage

- **NVIDIA NeMo Agent Toolkit** (`nvidia-nat`) is the agent framework the tool is built on — not a raw LLM API wrapper. The pipeline is two cooperating agents defined as YAML-configured `tool_calling_agent` workflows:
  - **Drift-Detector agent**: tools = `get_diff`, `discover_targets`, `extract_claims`, `check_claim_against_target`, `detect_pr_origin`. Decides *whether* drift exists, *where*, and *who authored the PR*.
  - **Fix-Writer agent**: tools = `read_target_section`, `draft_fix`, `post_pr_suggestion`, `commit_fix_to_branch`. Runs only when the Detector confirms drift; picks the delivery mode based on PR origin.
  - Chaining two purpose-built agents through the toolkit's native tool-calling (rather than one long hand-rolled prompt) is itself the "technical innovation" story.
- **build.nvidia.com / NIM**: free API key, ~1,000 inference credits, OpenAI-compatible endpoint, backs the `llm_name` each agent uses. Because both the toolkit and NIM's endpoint are OpenAI-compatible, swapping the underlying model is a one-line YAML change — this is what makes the model-comparison benchmark (Section 11) cheap to run.
- Judging criterion "(b) effective use of NVIDIA/partner technology" is satisfied at two levels: the *framework* (NeMo Agent Toolkit) and the *model* (NIM-hosted Nemotron/Llama/Qwen), not just an API call bolted onto custom Python.

## 8. Architecture / tech stack

- **Language:** Python (the Action's own implementation) — but analyzes repos in **any** language, since it works on raw diff/file text, not a parsed syntax tree
- **Agent framework:** NVIDIA NeMo Agent Toolkit (`nvidia-nat[langchain]`) — two `tool_calling_agent` workflows (Detector, Fix-Writer) defined in YAML, each pointing at a NIM-hosted `llm_name`
- **Trigger:** GitHub Actions `pull_request` event, which invokes the NeMo Agent Toolkit workflow as a CLI step
- **Diff access:** GitHub API (`git diff` against PR base) via `PyGithub` or raw `gh` CLI calls, exposed to the Detector agent as a `get_diff` tool
- **Target discovery:** filesystem walk + optional `.still.yml` parse, exposed as a `discover_targets` tool
- **PR origin detection:** parse commit trailers (`Co-Authored-By:`, `Shipped-by:`) and known bot account names via the GitHub API, exposed as a `detect_pr_origin` tool
- **LLM backend:** NIM API (OpenAI-compatible), configured per-agent in the toolkit's workflow YAML — swappable per model without code changes
- **Delivery:** GitHub REST API — `POST .../pulls/{pr}/comments` for suggestion blocks (human PRs), or a direct `git commit` + push to the PR branch (agent PRs)
- **Distribution:** public GitHub repo containing the Action's `action.yml`, the toolkit's workflow YAML configs, and Python tool implementations, so any repo can reference it as `uses: <you>/still-action@v1`

## 9. Demo plan

1. Pick (or create) a small real open-source-style repo with both a README and an `AGENTS.md`/`CLAUDE.md` describing conventions (library choices, commands, architecture notes) — ideally not Python, to visibly prove the any-language claim.
2. Show two PRs side by side: one opened normally (comment + suggestion flow), one with a `Co-Authored-By: Claude` trailer (auto-commit flow) — same underlying detection, two different delivery paths.
3. Screen-record: Action runs on each → human PR gets a comment with a click-to-accept suggestion; agent PR gets the fix already committed into the diff → explain *why* each finding is semantically wrong, not just broken.
4. Close by briefly showing the failure mode this prevents — an agent given the stale instruction confidently doing the wrong thing — then name-drop the RALPH-loop dependency for anyone in that audience.
5. 30-60 second video, voiceover explaining the problem/solution in the first 10 seconds (judges skim fast).

## 10. Judging criteria alignment

| Criterion | How this entry addresses it |
|---|---|
| Technical innovation | Semantic (not deterministic) drift detection across both docs and agent-instruction files, delivered as a direct commit onto the PR branch by default (not just a suggestion someone has to notice and apply) — a combination no existing tool provides — via diff-aware claim extraction chained through cooperating agents rather than one giant prompt |
| Effective use of NVIDIA/partner tech | NeMo Agent Toolkit + NIM-hosted model is the core reasoning engine for every step, not decorative |
| Impact/usefulness | Targets a documented, active failure mode (stale agent instructions, plus classic doc rot) relevant to anyone running agentic coding workflows — including the contest's own audience |
| Documentation quality | The submission's own README/demo must be exemplary — dogfooding the tool's purpose |

## 11. Benchmark — "Watchdock-Bench"

Winning past entries (e.g. Project Chimera's RALPH loop benchmark, comparing Nemotron-3-Nano-30B-A3B against Claude Sonnet 4.5 on iterations/tokens/test-pass-rate) show that judges respond to a real number, not a claim. This is the equivalent here, scoped to be buildable in about a day since it needs no training — only a fixed eval set and a script that scores pipeline output against ground truth.

**The eval set:** hand-built, ~20-25 synthetic PRs against a small sample repo with both a README and an `AGENTS.md`/`CLAUDE.md`, each with a known ground-truth label:
- ~12-15 **drift cases**, split across both target types (docs and instruction files) and across two difficulty tiers: (a) cases a deterministic checker like Evidoc *could* catch (broken path/command — included to show we're at least as good as that baseline) and (b) cases only semantic reasoning can catch (still-valid-looking text that's now wrong) — this split is the core evidence for the "we do something deterministic tools can't" claim. Ground truth records exactly which line is stale and what the correct fix text is.
- ~8-10 **clean cases** (control group): code changes that do *not* affect any documented/instructed behavior (internal refactor, added a test, formatting change). These measure false-positive rate — a tool that "finds" drift everywhere is useless.
- A handful of cases include a `Co-Authored-By: Claude`-style trailer, specifically to verify the auto-commit path fires correctly and only when it should.

**What gets measured, run three ways for comparison:**
1. **Deterministic baseline (Evidoc-style)** — flags drift only if a referenced path/command/symbol no longer exists. This is the ceiling of what the closest existing agent-file tool can do, and should score ~0 recall on the semantic-only cases by design — that gap *is* the headline benchmark result.
2. **Single-prompt LLM** — one NIM call, no agent framework, asked to do the whole job at once. Isolates whether the agent architecture matters versus one prompt doing the same job.
3. **Full NeMo Agent Toolkit pipeline** (Detector + Fix-Writer) — the actual product.

**Metrics reported per approach:**
- **Precision / Recall / F1** on drift detection, reported separately for docs vs. instruction-file targets (to show the tool isn't only good at one)
- **Origin-detection accuracy**: did it correctly route human PRs to comment mode and agent PRs to auto-commit mode
- **Fix-quality rate**: of correctly-detected drift cases, what % of the suggested fix text is judged (manual read) to correctly restore accuracy
- **Cost per PR**: NIM credits/tokens consumed, so the README can honestly state "costs ~X of your free 1,000 NIM credits per PR"

**Bonus comparison** (cheap — a one-line YAML change per Section 7): run the full pipeline with two different NIM-catalog models (e.g. Nemotron vs. Llama) on the same eval set, report which performs better on this specific task.

**Why this is honest, not gamed:** the eval set is small and self-authored, so it should be reported as exactly that — "a ~20-case eval set I built to sanity-check the approach," not an industry benchmark. That framing is still far stronger than an unverified claim, and it's what the README and demo video should say explicitly.

## 12. Competitive differentiation (verified)

| Tool | Scope | Detection | Fix mechanism | Trigger point | NVIDIA tech | Both targets? |
|---|---|---|---|---|---|---|
| CLAUDE.md Auto-Updater (Skill) | CLAUDE.md only | Semantic, with confidence scores | Proposes diffs | Manual/periodic, inside a Claude Code session | No | No |
| Evidoc | Agent-instruction files | Deterministic only | Repair prompts (human hands to an agent) | GitHub, PR-based | No | No |
| jameskomo/config-drift-checker | CLAUDE.md rules | Eval-case coverage testing | No | CI | No | No |
| jbrockSTL/doc-drift | README/docs | LLM (OpenAI `gpt-4o-mini`) | Suggestion, manual doc-source config | GitHub, PR-based | No | No |
| suhteevah/docsync | General docs, 40+ languages | Deterministic (tree-sitter) | Auto-fix (paid tier) | Local/CI | No | No |
| CodeRabbit | Whole PR review | LLM, doc-check is one minor feature | Minor feature among many | GitHub, PR-based | No | No |
| Cursor Bugbot / autogit | Code bugs (not docs) | LLM / N/A | Auto-commit to branch, attribution trailers | GitHub / local | No | N/A |
| **Watchdock (ours)** | Docs + agent-instruction files | LLM semantic reasoning via NeMo Agent Toolkit | Auto-commit to the PR branch by default (any origin); review-suggestion mode available via `commit_fixes: false` | GitHub, every PR | **Yes** | **Yes** |

**The gap this closes:** every agent-instruction-file tool is either deterministic (Evidoc, config-drift-checker) or locked inside a single client's session rather than a CI gate everyone sees (CLAUDE.md Auto-Updater). Every semantic/LLM doc tool targets human docs only. Nobody covers both target types with one mechanism, nobody delivers a direct commit for this specific failure mode (a doc gone stale from a change elsewhere, which is exactly the case a native GitHub suggestion can't attach to), nobody uses NVIDIA's stack, and nobody publishes a benchmark. The honest claim is the *combination*, not any single piece in isolation.

## 13. 5-day build plan

| Day | Work |
|---|---|
| Day 1 | NeMo Agent Toolkit installed, NIM API key working, minimal `tool_calling_agent` YAML workflow makes one successful round-trip call. GitHub Action skeleton triggers on PR and can read the diff. |
| Day 2 | Target discovery (auto-detect instruction files + docs + `.still.yml` override), raw-text claim extraction wired as a toolkit tool. Detector agent runs end-to-end on one hand-crafted semantic-drift case per target type. |
| Day 3 | Fix-Writer agent, PR-origin detection (commit trailer parsing), and both delivery paths (suggestion comment / direct commit) via GitHub API. Test against 2-3 real sample PRs of each origin type. |
| Day 4 | Build the Watchdock-Bench eval set (~20-25 cases, split per Section 11), implement the deterministic baseline + single-prompt variant, run all three approaches, compute metrics. Use results to tune prompts if false-positive rate is high. |
| Day 5 | Run the bonus model-comparison, finalize README with the benchmark table and competitive matrix, record demo video, publish the open-source repo, submit with #NVIDIAGTC + judge tag. |

## 14. Risks / open questions

- **False positives**: the Day 4 benchmark exists specifically to catch and quantify this before submission, not just hope it's fine.
- **Origin-detection reliability**: commit-trailer parsing is well-established (Claude Code, Cursor, autogit all use it) but must handle the case where trailers are missing/stripped — default to comment mode (safer) whenever origin is ambiguous, never default to auto-commit.
- **Which judge to tag**: pending seeing each judge's specific posted requirements (not yet found as of this PRD). Leading candidates: Johnny Nunez (NVIDIA AI Dev Advocate, general dev tooling) or Chorouk Malmoum (AgentX Academy, since the pipeline is a small autonomous multi-agent system).
- **NIM rate limits**: free tier is ~40 requests/min — fine for demo/PR-scale usage and for a ~20-case benchmark run.
- **NeMo Agent Toolkit learning curve**: unfamiliar framework adds Day 1 risk — if setup takes longer than expected, fall back to raw API calls rather than losing a full day; the benchmark and PRD narrative both still work either way, just with a smaller "effective use of NVIDIA tech" claim.
- **Scope creep risk**: two target types + origin-aware delivery + auto-detection + a benchmark is already a full 5 days — resist adding live/mid-session updates or a formal AST layer before the deadline.
