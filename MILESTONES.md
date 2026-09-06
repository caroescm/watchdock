# Still — Milestones & Issue Tracker

Mirrors the 5-day build plan in [PRD.md](PRD.md) Section 13, broken into checkable issues. Check items off as you go; each issue is scoped to be a single sitting, not a multi-day task.

---

## M1 — Foundation (Day 1)
**Done when:** a GitHub Action triggers on a PR, reads the diff, and successfully round-trips one call through NeMo Agent Toolkit to NIM.

- [x] ~~Create public GitHub repo~~ — deferred: building inside existing `GTCberlin` repo (private for now), will rename + make public before submission (M5)
- [x] Sign up at build.nvidia.com, get free NIM API key, confirm a raw test call works — confirmed with `nvidia/nemotron-3.5-lightning-30b-a3b` via raw `openai` client
- [x] Install `nvidia-nat[langchain]` locally — required switching from Python 3.14 to 3.12 venv (nvidia-nat doesn't support 3.14 yet)
- [x] Get one YAML-configured agent workflow calling a NIM-hosted model end-to-end — `nat run` with a `react_agent` workflow hit NIM successfully (note: `react_agent`'s text-parsing output was messy with this reasoning model; real build uses `tool_calling_agent` instead, which sidesteps that)
- [x] Scaffold `action.yml` for the GitHub Action (inputs: `nvidia_api_key`, `github_token`; trigger: `pull_request`) — composite action, no Docker build needed
- [x] Action successfully checks out the PR and fetches the diff via GitHub API (PyGithub) — required adding `permissions: pull-requests: read, contents: read` to the workflow job, since the default `GITHUB_TOKEN` doesn't have PR-read access without it
- [x] **Fallback checkpoint:** not needed — NeMo Agent Toolkit is working, no fallback triggered

## M2 — Detection core (Day 2)
**Done when:** the Detector agent can find one hand-crafted semantic-drift case in a test repo, for both a doc file and an instruction file.

- [x] Implement `discover_targets` tool: auto-detect `AGENTS.md`, `CLAUDE.md`, `.cursor/rules`, `conventions.md`, `README.md`, `/docs/**` — `src/targets.py`, tested against this repo (empty-list case confirmed correct)
- [x] Implement `.still.yml` override parsing (targets/ignore lists) — tested: explicit `targets:` overrides auto-detect, `ignore:` subtracts correctly
- [x] Implement `get_diff` tool (wraps the Day 1 diff-fetching logic as a callable tool) — `src/github_api.py`, hit and fixed a `ModuleNotFoundError` from the `src.` import prefix (script execution puts `src/` itself on the path, not its parent), now passing
- [x] Implement `extract_claims` tool: one NIM call, diff → list of doc/instruction claims it could affect — `src/claims.py`, tested with a `requests`→`httpx` fake diff; correctly flagged the "stated conventions" contradiction case that's our core differentiator
- [x] Implement `check_claim_against_target` tool: one NIM call, claim + target file text → stale? quote + reason — `src/claims.py`, tested with a fake `AGENTS.md` correctly flagged "use `requests`" as semantic staleness while leaving the unrelated `pytest` line alone (minor: model still mentions unaffected lines as "N/A" instead of omitting — revisit during Day 4 prompt tuning if it causes comment noise)
- [x] Build one hand-crafted test case in a scratch repo: a doc claim made false by a diff (e.g. README says a flag exists, diff removes it) — `test_check_readme.py`, correctly classified as `TYPE: broken reference`
- [x] Build one hand-crafted test case: an instruction-file claim made *semantically* false (e.g. `AGENTS.md` says "use `requests`", diff switches to `httpx`) — `test_check.py`, correctly classified as `TYPE: semantic staleness` — this is the core differentiator, now proven working
- [x] Wire the above into a NAT workflow, confirm it runs end-to-end — built as a **deterministic NAT workflow** (`still_detector` package), not a `tool_calling_agent`: since the four steps always run in a fixed order, we chose reliability over free-form agent planning (see PRD note). Confirmed working via `nat run` against real PR #1, correctly returning "no targets found" for this repo

## M3 — Fix + delivery (Day 3)
**Done when:** a full PR gets either a suggestion comment or an auto-commit, correctly chosen based on who authored it.

- [x] Implement `draft_fix` tool: one NIM call, stale line + reason → corrected text — `src/fixes.py`, tested on the `requests`→`httpx` case, produced a clean correctly-styled fix
- [x] Implement `post_pr_suggestion` tool: GitHub API call posting a PR comment with a suggestion block — `src/fixes.py`, uses PyGithub's native `as_suggestion=True`; confirmed live on real PR #1 with a correctly-formatted ` ```suggestion` block on the exact target line (with user's explicit go-ahead before posting, since this is the first tool that writes to GitHub rather than just reading)
- [x] Implement `detect_pr_origin` tool: parse commit trailers (`Co-Authored-By: Claude`, etc.) and known bot account names — `src/github_api.py`, split into pure logic (`detect_pr_origin_from_data`, testable without API calls) + a thin real-data wrapper; tested 4 cases (human, Claude trailer, bot login, ambiguous-defaults-to-human) plus confirmed against real PR #1
- [x] Implement `commit_fix_to_branch` tool: writes the corrected file content and pushes a commit to the PR branch — `src/fixes.py`, refuses to commit if the stale line doesn't match verbatim (no guessing); confirmed live on the `test-still-action` branch with user's explicit go-ahead, verified the actual file content updated correctly
- [x] Wire routing logic: `still_detector.py` now runs the full pipeline end to end (targets → diff → claims → per-target check → per-finding draft_fix → route by `detect_pr_origin`) as one deterministic NAT workflow
- [x] Default to comment mode when origin is ambiguous — already guaranteed by `detect_pr_origin_from_data`'s design (only returns "agent" on an explicit trailer/bot match, defaults to "human" otherwise), verified in the earlier `detect_pr_origin` test cases
- [x] End-to-end test #1: real drift case (added `AGENTS.md` + a contradicting `app.py` using `httpx`) on a normal human commit → confirmed a real `` ```suggestion `` comment landed on the correct line of `AGENTS.md`
- [x] End-to-end test #2: same drift, but PR now includes a commit with a `Co-Authored-By: Claude` trailer → confirmed origin flipped to "agent" and the fix landed as a **direct commit** instead (verified by reading the actual updated file content on the branch)
- [x] Bonus: caught and fixed a real bug during testing — `parse_findings` crashed with `KeyError: 'reason'` when the model's response included an incomplete second finding block; fixed by only accepting findings with both `line` and `reason` present

## M4 — Benchmark (Day 4)
**Done when:** you have a table of real numbers comparing three approaches, and you've used it to fix at least one prompt issue.

- [x] Build sample repo with a README, an `AGENTS.md`/`CLAUDE.md`, and some source code — `benchmark/sample_repo/`, a small Node.js "todo-cli" tool (proves the any-language claim: JS, not Python)
- [x] Write drift-case PR diffs, split deterministic-catchable vs. semantic-only, split doc-target vs. instruction-target — `benchmark/cases.py`, 12 drift cases (3 per bucket × 4 buckets)
- [x] Write clean-case PR diffs as the false-positive control group — 8 clean cases in `benchmark/cases.py`
- [ ] Add cases with agent-authorship trailers to test origin-detection accuracy — not built (origin detection already has its own dedicated tests from M3; deprioritized for the benchmark specifically given time)
- [x] Implement the deterministic baseline — `benchmark/baseline.py`; caught two real case-design bugs while smoke-testing it (a short-circuit that defeated its own detection logic, and a fake non-diff-formatted patch)
- [x] Implement the single-prompt-LLM variant — `benchmark/single_prompt.py`
- [x] Run all three approaches against a representative subset (9 of 20 cases — 7 drift + 2 clean, chosen to include the 3 hardest baseline-proof cases) — full 20 not run, deliberate time/cost tradeoff, documented in `BENCHMARK.md`
- [x] Compute precision/recall/F1 — see `BENCHMARK.md`; fix-quality rate, origin-detection accuracy, and cost-per-PR were not separately measured (out of scope given the reduced case count)
- [x] Review results — found real run-to-run variance (same case scored differently across two runs) and one major latency outlier (918s vs. typical 30-370s); both documented rather than smoothed over, no prompt changes made this round
- [x] Write up results in `BENCHMARK.md`, framed honestly as a small self-built eval set with explicit limitations section

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
