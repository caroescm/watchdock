# Watchdock-Bench

A self-built eval set of ~20 hand-crafted PR cases against a small sample repo
(`benchmark/sample_repo/` — a tiny Node.js CLI tool with a README and an
`AGENTS.md`), used to sanity-check that Watchdock's approach actually catches
drift better than the alternatives. This is **not** an industry benchmark —
it's a small, self-authored set designed to test one specific claim: that
semantic reasoning catches drift a deterministic checker structurally cannot.

## Setup

Three approaches are run on the same cases:

1. **Deterministic baseline** (`benchmark/baseline.py`) — mimics the closest
   existing agent-instruction-file tool (Evidoc): flags drift only if a
   backtick-quoted identifier from the target file no longer appears (as
   added) anywhere in the diff. No LLM involved.
2. **Single-prompt LLM** (`benchmark/single_prompt.py`) — one NIM call does
   the whole "find + explain" job at once, no agent framework, no separate
   claim-extraction step.
3. **Full pipeline** (Watchdock's actual mechanism) — `extract_claims` +
   a **3-way parallel ensemble** of `check_claim_against_target` calls,
   unioned. See "The optimization journey" below for why it's an ensemble,
   not a single call.

Cases are split into drift cases (further split by target type — docs vs.
`AGENTS.md` — and by whether a deterministic checker *should* be able to
catch them) and clean/no-drift control cases, to measure false positives.

## What actually ran

Of the 20 designed cases, **9 were run** against all three approaches
(the rest were skipped to save time/API cost — see Limitations below):

- 4 cases a deterministic checker should plausibly catch (a literal
  identifier disappears from the diff)
- 3 cases chosen specifically because the baseline *cannot* catch them
  (the identifier/text persists; only the described behavior changes)
- 2 clean/no-drift control cases

## Final results (9 cases: 7 drift + 2 clean)

| Approach | TP | FP | FN | TN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|---|
| Deterministic baseline | 4 | 0 | 3 | 2 | 1.00 | 0.57 | 0.73 |
| Single-prompt LLM | 5 | 0 | 2 | 2 | 1.00 | 0.71 | 0.83 |
| **Full pipeline (Watchdock)** | **7** | **0** | **0** | **2** | **1.00** | **1.00** | **1.00** |

All three approaches had **zero false positives** across every run — none of
them hallucinate drift on a clean diff, at least on this eval set.

### The headline result: 3 cases chosen to be baseline-proof

| Approach | Recall on the 3 hardest cases |
|---|---|
| Deterministic baseline | **0%** (0/3) |
| Single-prompt LLM | 67% (2/3) |
| **Full pipeline (Watchdock)** | **100%** (3/3) |

These three cases (`instr_deterministic_02`, `doc_semantic_02`,
`doc_semantic_03`) were specifically designed so the identifier/text a
deterministic checker would look for never disappears from the diff — only
the *behavior* it describes changes. A checker like Evidoc structurally
cannot catch these; Watchdock's semantic reasoning does, 3 for 3.

## The optimization journey (worth reading, not just the final number)

The 100% result above didn't come from one run — it came from actually
diagnosing a real bug, which is a more useful thing to document than a
clean final table pretending it was always this way.

1. **First run, default settings: 100% recall (7/7).** Looked great.
2. **Latency check revealed a real problem**: a real triggered GitHub Actions
   run took ~23 minutes end to end, with one NIM call alone taking ~19
   minutes. Individual benchmark calls ranged 30s to 918.8s. Unusable for a
   CI check people expect back in minutes.
3. **Disabled the model's "thinking" mode** (Nemotron-3.5-lightning is a
   reasoning model; NVIDIA's own reference examples explicitly toggle this).
   Speed improved 20-100x (calls dropped to 1.5-18s). **But recall dropped to
   71%** — it now missed 2 of the 7 hard cases. Raising `max_tokens` 4x didn't
   recover them; the exact same 2 cases failed both times, ruling out
   truncation as the cause at that point.
4. **Turned thinking back on for the judgment-critical steps** (kept it off
   only for `draft_fix`, which is mechanical rewriting). Recall was *still*
   71% — but now *different* cases failed than with thinking off. That's the
   signature of genuine run-to-run nondeterminism at `temperature=0.0`, not
   something the thinking toggle alone explains.
5. **Added a 3x parallel ensemble** (run `check_claim_against_target` three
   times concurrently, union the findings) to hedge against that
   nondeterminism, on the theory that a real finding rarely gets missed by
   *all three* independent tries. This got recall to **86%** with zero
   precision cost, and cut wall-clock time too (parallel calls, not
   sequential) — average ~180s per case vs. ~301s before, worst case 329s vs.
   919s.
6. **Root-caused the last remaining miss** (`doc_deterministic_01`) instead of
   just accepting 86%: raw-output inspection showed the model correctly
   identifying the exact right answer, then getting stuck deliberating at
   length over an irrelevant classification detail ("semantic staleness" vs.
   "broken reference" — a label that doesn't even affect what fix gets
   drafted) and **running out of its 8000-token thinking budget before ever
   emitting the actual answer.** Two targeted fixes: raised the thinking
   budget to 24000, and told the prompt explicitly not to waste time
   deliberating over that label. Re-ran the same case: complete, correct
   answer, no truncation.
7. **Final full 9-case run with the fix: 100% recall, 100% precision.**
8. **Tried to push latency down further, reverted.** Two more changes were
   tested together on the 3 hardest cases: capping the ensemble to proceed
   with whichever samples finish within 300s (instead of waiting for all 3),
   and disabling thinking for `extract_claims` specifically (never tested in
   isolation before). Result: recall dropped to 67% (2/3) — `doc_semantic_02`
   failed again, at 317.7s, just past the 300s cap. That's the same case
   that has needed the most deliberation time throughout this entire
   investigation (918s → 259s → 233s in every prior *successful* run).
   **Reverted both changes.** The ensemble's full wait time is load-bearing,
   not padding — the same lesson as the original thinking-mode finding,
   just rediscovered one layer deeper.

The lesson: a "100% result" from a single run is not proof of anything by
itself, given the demonstrated nondeterminism — what makes this 100% more
trustworthy than the first one is that it came with an actual diagnosed and
fixed root cause (a real truncation bug), not just a lucky sample. And every
further attempt to trade correctness for speed on this pipeline has cost
real recall when actually measured — the current configuration is not
under-optimized out of neglect, it's the result of repeatedly testing
"faster" and finding it wasn't free.

## Current architecture (post-optimization)

- `extract_claims`: thinking **on** (judgment call: what could this diff
  affect)
- `check_claim_against_target`: thinking **on**, run as a **3x parallel
  ensemble**, findings unioned across samples (judgment call, plus hedges
  against nondeterminism)
- `draft_fix`: thinking **off** (mechanical rewrite, consistently fine
  without it across every test)
- Diff filtering (test files, `LICENSE`, lockfiles excluded before
  `extract_claims`) and target-level parallelization (independent target
  files checked concurrently) — both real speed wins with zero observed
  accuracy cost.
- Client timeout raised to 1500s — a real triggered Action run showed a
  legitimate (correct, non-truncated) call taking ~19 minutes, so a short
  timeout would have killed a right answer.

## Latency ideas considered and not built (with reasoning why)

A further redesign was proposed and evaluated against what this investigation
actually showed, rather than implemented on faith:

- **Lexical target pre-filter** (skip targets with zero lexical overlap with
  the diff before running expensive analysis): correctly reasoned, but
  `still_detector.py` already checks all target files **concurrently** with
  each other, not sequentially — so for the common case (1-3 target files)
  this wouldn't reduce wall-clock time at all, only API cost. It would help
  a repo with many (5+) target files approaching NIM's free-tier rate limit,
  which is a narrow, not-yet-observed scenario.
- **Send only relevant sections of a target file, not the whole file**: no
  evidence this addresses the actual bottleneck. Every target file in this
  benchmark is already small (20-35 lines); the worst latency we've measured
  came from the model deliberating over a nuanced judgment call (the
  truncation bug), not from processing too much input text. Untested and
  speculative on a genuinely large real-world file, which this benchmark
  doesn't contain.
- **Progressive/sequential ensemble** (trust a confident first sample
  immediately, escalate to more samples only on `NONE`/malformed output):
  actually implemented and tested. Accuracy held (100% recall on the 3
  hardest cases), but the implementation was structurally slower for any
  case that escalates — it waited for sample 1 to fully finish *before*
  starting the escalation samples, adding sequential time on top of the
  original all-3-at-once design rather than saving any. A correct version
  would need to fire all 3 samples simultaneously and return as soon as any
  one gives a confident answer (a true "race"), which wasn't built given
  time constraints. Reverted rather than shipped half-working.
- **CI time budget with honest "incomplete" reporting** (bound the wait,
  report confirmed findings plus "analysis incomplete, re-run for full
  verification" instead of silently claiming completeness): the one idea
  here that's actually sound and low-risk, since it changes reporting policy
  rather than the model's reasoning. Not built due to time — a real,
  concrete next step for anyone continuing this project.

## Honest limitations

- **Small sample.** 9 cases, hand-authored by the project's own author. This
  is a sanity check, not a statistically powered study.
- **Real, demonstrated nondeterminism at `temperature=0.0`.** The same case
  scored differently across independent runs multiple times during this
  investigation (see the optimization journey above). The ensemble mitigates
  this but doesn't eliminate it — 3 samples make missing a real finding
  unlikely, not impossible.
- **Latency remains real, if improved.** Ensemble calls with thinking on
  still take on the order of 1-10 minutes per target file in practice (though
  target files run concurrently with each other). One anomalous 35528s
  (~9.9 hour) timing was observed in testing, almost certainly the local
  machine sleeping mid-call rather than real work — but it also means our
  1500s client timeout apparently didn't fire as expected during that gap,
  which wasn't independently root-caused given time constraints.
- **11 of 20 designed cases weren't run** due to time/API cost — the 9 that
  were run were deliberately chosen to include the highest-signal set (the
  3 baseline-proof cases) rather than a random sample.
- **API cost per PR is now 3x higher** for the ensemble step specifically,
  trading NIM free-tier credits for reliability.

## Scoring note (September 2026)

`run_eval.score_case` used to count a drift case whose finding named the
*wrong* line as a false negative only. It now also counts it as a false
positive, since the flagged line is a false alarm. The precision figures
above were computed under the old rule; in the runs recorded here no drift
case produced a wrong-line finding, so the numbers stand, but rerun
`benchmark/run_eval.py` rather than compare a future result to them
directly.

## Operational findings from live runs

The code used to carry these as comments next to the constants they justify.
They are the *reason* for each setting; the code now states only the rule and
points here, so the history can't drift out from under the comment.

| Setting / rule | What happened in a real run | Consequence |
|---|---|---|
| `nim_client.TIMEOUT_SECONDS = 1500` | One correct, non-truncated thinking-mode call took ~19 minutes in a triggered Action run. | The client timeout sits well above that, so a slow-but-right answer is not failed. |
| `nim_client.CLIENT_MAX_RETRIES = 3` (OpenAI client's own initial-request retries) | Two separate runs hit a connection reset ~4–4.5 min into a call: once on a single `extract_claims` call (recovered on its one retry), once on all three ensemble calls at once (did not recover, crashed the run). Same timing with and without concurrency. | Raised from 1 so a single reset is not one unlucky retry away from taking down the ensemble. |
| `nim_client.MAX_TOKENS_THINKING = 24000` | A diagnosed failure showed the model reasoning through a case correctly, then running out of budget mid-thought before emitting the structured answer. 8000 was not enough; NVIDIA's reference examples budget 16384 for the chain of thought alone. | Generous headroom for thinking calls; mechanical (thinking-off) calls keep a small budget (800). |
| `nim_client.MAX_STREAM_ATTEMPTS = 2` (whole-call retry) | A run got a clean 200, streamed for 10 minutes, then the server returned "Internal server error" mid-stream. The client's built-in retries only cover the initial request, so nothing retried it; `extract_claims` has no ensemble to fall back on, so the run died. | The whole streamed call is retried from scratch (a partial stream cannot be resumed), with exponential backoff. |
| Streaming instead of one non-streaming reply | Two runs saw a non-streaming call reset after ~4.3–4.6 minutes of silence on every retry, consistent with an intermediate idle timeout. A direct replay of the same call, streamed, ran 411 s with no disconnect and a complete answer. | Every call streams. Only `content` deltas are accumulated; thinking tokens land in a separate field. |
| Thinking mode on by default | A 9-case comparison: disabling thinking recovered 20–100x speed but dropped full-pipeline recall from 100% to 71%, missing exactly the cases that need inference (a rename invalidating a reference, a flag's scope narrowing). Raising `max_tokens` 4x did not recover them, so it was reasoning, not truncation. | Thinking stays on; only `fixes.draft_fix` (a mechanical rewrite) opts out. |
| One claim per check call | Two runs died at exactly 10:00 mid-stream, immune to streaming and retries: NVIDIA's free-tier NIM endpoint caps server-side generation at ~10 minutes. Holding the whole multi-claim blob against a large file in one reasoning pass is what hit it. | Each claim is checked in its own call. This is also the granularity the 100%-recall result was measured at. |
| Ensemble of 3 samples per claim, unioned | The same case was caught in some independent `temperature=0.0` runs and missed in others. Every run tested had zero false positives even when it missed real findings. | Union across samples buys recall with no measured precision cost. |
| Wait for every sample (no capped wait) | A capped-wait variant (proceed with samples that finish within 300 s) dropped recall to 67% on the three hardest cases; the failure landed at 317.7 s, just past the cap. | Latency there is doing real work; all samples are awaited. A dropped sample is a missing vote, not a fatal error. |
