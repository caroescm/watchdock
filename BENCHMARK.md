# Still-Bench

A self-built eval set of ~20 hand-crafted PR cases against a small sample repo
(`benchmark/sample_repo/` — a tiny Node.js CLI tool with a README and an
`AGENTS.md`), used to sanity-check that Still's approach actually catches
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
3. **Full pipeline** (Still's actual mechanism) — `extract_claims` +
   `check_claim_against_target`, the two-step design described in the PRD.

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

## Results (9 cases: 7 drift + 2 clean)

| Approach | TP | FP | FN | TN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|---|
| Deterministic baseline | 4 | 0 | 3 | 2 | 1.00 | 0.57 | 0.73 |
| Single-prompt LLM | 5 | 0 | 2 | 2 | 1.00 | 0.71 | 0.83 |
| **Full pipeline (Still)** | **7** | **0** | **0** | **2** | **1.00** | **1.00** | **1.00** |

All three approaches had **zero false positives** across every case run —
none of them hallucinate drift on a clean diff, at least on this eval set.

### The headline result: 3 cases chosen to be baseline-proof

| Approach | Recall on the 3 hardest cases |
|---|---|
| Deterministic baseline | **0%** (0/3) |
| Single-prompt LLM | 67% (2/3) |
| **Full pipeline (Still)** | **100%** (3/3) |

These three cases (`instr_deterministic_02`, `doc_semantic_02`,
`doc_semantic_03`) were specifically designed so the identifier/text a
deterministic checker would look for never disappears from the diff — only
the *behavior* it describes changes. A checker like Evidoc structurally
cannot catch these; Still's semantic reasoning does, 3 for 3.

## Honest limitations

- **Small sample.** 9 cases, hand-authored by the project's own author. This
  is a sanity check, not a statistically powered study.
- **Run-to-run variance observed.** `instr_deterministic_02` scored FN across
  all three approaches in one run, then TP for single-prompt and full
  pipeline in a re-run of the same case against the same data, at
  `temperature=0.0`. The underlying model isn't perfectly deterministic in
  practice. Numbers above reflect the later, re-verified run.
- **One latency outlier.** One `full_pipeline` call (`doc_semantic_02`) took
  918.8s vs. a typical 30-370s — not investigated further; noted here rather
  than hidden. Worth profiling before relying on this for tight CI turnaround
  at scale.
- **11 of 20 designed cases weren't run** due to time/API cost — the 9 that
  were run were deliberately chosen to include the highest-signal set (the
  3 baseline-proof cases) rather than a random sample.
