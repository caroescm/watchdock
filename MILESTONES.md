# Still — Milestones & Issue Tracker

Mirrors the 5-day build plan in [PRD.md](PRD.md) Section 13, broken into checkable issues. Check items off as you go; each issue is scoped to be a single sitting, not a multi-day task.

---

## M1 — Foundation (Day 1)
**Done when:** a GitHub Action triggers on a PR, reads the diff, and successfully round-trips one call through NeMo Agent Toolkit to NIM.

- [ ] Create public GitHub repo (`still` or final name), add MIT/Apache license, minimal README stub
- [ ] Sign up at build.nvidia.com, get free NIM API key, confirm a raw test call works (curl or Python `openai` client pointed at NIM's base URL)
- [ ] Install `nvidia-nat[langchain]` locally, work through the minimal "hello world" `tool_calling_agent` example from NeMo Agent Toolkit docs
- [ ] Get one YAML-configured agent workflow calling a NIM-hosted model end-to-end (no tools yet, just prompt → response)
- [ ] Scaffold `action.yml` for the GitHub Action (inputs: `NIM_API_KEY`; trigger: `pull_request`)
- [ ] Action successfully checks out the PR and fetches the diff (`git diff` against base, or GitHub API) — print it to logs as proof
- [ ] **Fallback checkpoint:** if NeMo Agent Toolkit setup isn't working by end of day, switch to raw `openai`-client calls against NIM and note the scope change in PRD Section 14 (risk already documented)

## M2 — Detection core (Day 2)
**Done when:** the Detector agent can find one hand-crafted semantic-drift case in a test repo, for both a doc file and an instruction file.

- [ ] Implement `discover_targets` tool: auto-detect `AGENTS.md`, `CLAUDE.md`, `.cursor/rules`, `conventions.md`, `README.md`, `/docs/**`
- [ ] Implement `.still.yml` override parsing (targets/ignore lists)
- [ ] Implement `get_diff` tool (wraps the Day 1 diff-fetching logic as a callable tool)
- [ ] Implement `extract_claims` tool: one NIM call, diff → list of doc/instruction claims it could affect
- [ ] Implement `check_claim_against_target` tool: one NIM call, claim + target file text → stale? quote + reason
- [ ] Build one hand-crafted test case in a scratch repo: a doc claim made false by a diff (e.g. README says a flag exists, diff removes it)
- [ ] Build one hand-crafted test case: an instruction-file claim made *semantically* false (e.g. `AGENTS.md` says "use `requests`", diff switches to `httpx`) — confirm this is caught, since it's the core differentiator
- [ ] Wire the above into the Detector agent's `tool_calling_agent` YAML workflow, confirm it flags both cases correctly

## M3 — Fix + delivery (Day 3)
**Done when:** a full PR gets either a suggestion comment or an auto-commit, correctly chosen based on who authored it.

- [ ] Implement `draft_fix` tool: one NIM call, stale line + reason → corrected text
- [ ] Implement `post_pr_suggestion` tool: GitHub API call posting a PR comment with a suggestion block
- [ ] Implement `detect_pr_origin` tool: parse commit trailers (`Co-Authored-By: Claude`, etc.) and known bot account names
- [ ] Implement `commit_fix_to_branch` tool: writes the corrected file content and pushes a commit to the PR branch
- [ ] Wire Fix-Writer agent's YAML workflow: route to suggestion-comment vs. auto-commit based on `detect_pr_origin` result
- [ ] Default to comment mode when origin is ambiguous (never default to auto-commit) — confirm this explicitly with a test case
- [ ] End-to-end test #1: open a real PR (as yourself) with a drift case → confirm comment + suggestion block appears and is clickable
- [ ] End-to-end test #2: open a real PR with a `Co-Authored-By: Claude` trailer and the same kind of drift → confirm the fix lands as a direct commit instead

## M4 — Benchmark (Day 4)
**Done when:** you have a table of real numbers comparing three approaches, and you've used it to fix at least one prompt issue.

- [ ] Build sample repo with a README, an `AGENTS.md`/`CLAUDE.md`, and some source code (pick a non-Python language for at least part of it, to prove the any-language claim)
- [ ] Write 12-15 drift-case PR diffs against that repo, split: deterministic-catchable vs. semantic-only, split across doc-target vs. instruction-target
- [ ] Write 8-10 clean-case PR diffs (no real drift) as the false-positive control group
- [ ] Add a few cases with agent-authorship trailers to test origin-detection accuracy
- [ ] Implement the deterministic baseline (Evidoc-style: does a referenced path/command/symbol still exist)
- [ ] Implement the single-prompt-LLM variant (one NIM call, no agent framework)
- [ ] Run all three approaches (baseline, single-prompt, full pipeline) against the full eval set
- [ ] Compute precision/recall/F1 (overall + split by doc vs. instruction-file), fix-quality rate, origin-detection accuracy, cost per PR
- [ ] Review false positives/negatives, tune prompts, re-run until numbers are presentable
- [ ] Write up results in a `BENCHMARK.md` or README section, framed honestly as a self-built eval set

## M5 — Ship (Day 5)
**Done when:** the entry is submitted with #NVIDIAGTC and a judge tag, before the Sep 10 deadline.

- [ ] Run the bonus model-comparison (swap `llm_name` between two NIM-catalog models on the same benchmark)
- [ ] Write the final README: problem, how it works, install instructions, competitive comparison table (PRD Section 12), benchmark results, "future work" section naming the live/mid-session update idea
- [ ] Record demo video (30-60s): show both delivery paths (comment vs. auto-commit), explain the RALPH-loop stakes, first 10 seconds state the problem clearly
- [ ] **Verify which judge to tag** — check Chorouk Malmoum's and Johnny Nunez's actual recent posts for their specific stated requirements before picking (open item from PRD Section 14)
- [ ] Publish the repo publicly, confirm license file present, confirm someone else could clone + run it from the README alone
- [ ] Post the submission (video/link) on the chosen platform, tag the chosen judge, hashtag #NVIDIAGTC
- [ ] Double check submission lands before the Entry Period ends (Sep 10, 2026)

---

## Notes
- If a day runs long, the first thing to cut is the bonus model-comparison (M5), not the core benchmark (M4) — a working benchmark beats an extra comparison row.
- The second thing to cut, if needed, is one of the two target types (docs *or* instruction files) rather than the origin-aware delivery split — the delivery split is more differentiated than target-type breadth.
