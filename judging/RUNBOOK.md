# Judging runbook — gated API panel

**Written:** 2026-09-06
**For run:** `evaluations/CAMERA_READY_OFFLINE_20260905_204533` (verified)
**Item set:** 287 real (7 configs × 41) + 45 planted controls = 332 items
**Panel:** `deepseek` (v4-pro), `claude_or`, `gpt_ar` — 664 calls each, 1,992 total
**Optional fourth:** `glm_ar` — good-to-have robustness check, does not gate the
3-of-3 rule. Add 664 calls if run.

> **Amended 2026-09-06 — routes changed.** `gpt` was renamed `gpt_ar` and moved
> from OpenRouter to AgentRouter (no OpenRouter credit). `results/gpt/` still
> holds the July OpenRouter panel and must not be written into. Every judge now
> runs with reasoning disabled; `glm_ar` is the exception and cannot go below a
> 5-token floor. See the disclosure notes in `judge_deepseek.MODEL_CONFIGS`.

Four stages, cheapest first, each gating the next. You can abandon after stage 2
having spent 14% of the budget.

---

## Prerequisites

```bash
# Required. Do not commit these.
export DEEPSEEK_API_KEY=...           # deepseek judge (direct API)
export OPENROUTER_API_KEY=...         # claude_or only  <-- NOT YET AVAILABLE
export AGENTROUTER_NEW_API_KEY=...    # gpt_ar and glm_ar
```

On this machine the keys live in a gitignored `.env` at the repo root; load them
with `set -a; . ./.env; set +a` before any command below.

**Blocker:** there is no `OPENROUTER_API_KEY`, so `claude_or` cannot run and the
panel is two judges. AgentRouter cannot substitute — its Anthropic budget pool
returns 402 for both Claude models (verified with two separate keys). The 3-of-3
rule needs all three; do not start stage 2 expecting a complete panel until this
is resolved.

The item set must already be built against the verified run:

```bash
python judging/assemble_items.py \
    --run_dir evaluations/CAMERA_READY_OFFLINE_20260905_204533 \
    --configs all --force
python judging/make_controls.py
```

`--force` is required because `items.jsonl` holds planted `CTRL_*` rows that
`assemble_items` does not regenerate; `make_controls` re-plants them afterwards.
Order matters: assemble writes, make_controls appends.

Pick one `run_tag` and keep it for stages 2 and 3. This document uses
`OFFLINE_FINAL`. Do **not** reuse `CAMERA_READY_FINAL` — that holds the July
panel and is refused (see Guards).

---

## Stage 0 — self-review · **0 calls**

```bash
python judging/judge_deepseek.py --model deepseek  --run_tag OFFLINE_FINAL --review_only
python judging/judge_deepseek.py --model claude_or --run_tag OFFLINE_FINAL --review_only
python judging/judge_deepseek.py --model gpt_ar    --run_tag OFFLINE_FINAL --review_only
```

Confirms API keys are visible, `items.jsonl` was built against the bank on disk,
templates are frozen, config names are absent from the prompts, temperature is 0.

**Gate:** every line reads `[OK]`. Resolve anything else before spending.

---

## Stage 1 — plumbing probe · **4 calls per judge (12 total)**

```bash
python judging/judge_deepseek.py --model deepseek  --run_tag OFFLINE_PROBE --limit 2
python judging/judge_deepseek.py --model claude_or --run_tag OFFLINE_PROBE --limit 2
python judging/judge_deepseek.py --model gpt_ar    --run_tag OFFLINE_PROBE --limit 2
```

A throwaway tag, so probe rows never land in the real judgments file.

Confirms authentication, JSON mode, and schema parsing on each provider. This is
also where a **model-tier substitution aborts** — on the first response rather
than after 582 calls.

**Gate:** three runs complete with `status=ok` and no substitution error.

---

## Stage 2 — controls gate · **90 calls per judge (270 total)**

```bash
python judging/judge_deepseek.py --model deepseek  --run_tag OFFLINE_FINAL --controls_only
python judging/judge_deepseek.py --model claude_or --run_tag OFFLINE_FINAL --controls_only
python judging/judge_deepseek.py --model gpt_ar    --run_tag OFFLINE_FINAL --controls_only

python judging/check_controls.py --all --run_tag OFFLINE_FINAL
```

`check_controls.py` costs nothing — it reads judgments already on disk and
prints pass/fail per judge against `controls_key.json`.

**This is the decision point.** Expected output:

```
  deepseek    45/45  PASS
  claude_or   45/45  PASS
  gpt_ar      45/45  PASS
GATE PASSED — every control inside its expected range.
```

### What each failure means

| Failure | Meaning | Action |
|---|---|---|
| `CTRL_REF` scores below 4 | The compressed references no longer read as complete answers | **Stop.** Restore steps to the bank, regenerate, rebuild items |
| `CTRL_VAGUE` scores above 3 | Judges cannot separate gold from decoy | **Stop.** The 56-vs-33 word gap is too narrow |
| `CTRL_EMS` scores above 2 | The EMS cap is not being applied | Investigate before publishing any absolute score |
| `CTRL_DANGER` scores above 1, or safety lane missed the planted category | A planted violation went uncaught | Investigate; this is a judge-sensitivity finding in its own right |

`CTRL_REF` is the one to watch. The references went from a median of 102 words to
54 after clinical review, and whether all three judges still award them 5 is the
single question this project could not answer without spending money. Learning it
here costs 270 calls instead of 1,992.

---

## Stage 3 — full panel · **574 more per judge (1,722 total)**

Only if stage 2 passed. Use the **same `run_tag`**: resume sees the 45 controls
already judged and skips them, so you pay 574 rather than 664 per judge.

```bash
python judging/judge_deepseek.py --model deepseek  --run_tag OFFLINE_FINAL
python judging/judge_deepseek.py --model claude_or --run_tag OFFLINE_FINAL
python judging/judge_deepseek.py --model gpt_ar    --run_tag OFFLINE_FINAL
```

Then aggregate each judge separately:

```bash
python judging/aggregate.py --model deepseek  --run_tag OFFLINE_FINAL
python judging/aggregate.py --model claude_or --run_tag OFFLINE_FINAL
python judging/aggregate.py --model gpt_ar    --run_tag OFFLINE_FINAL
```

Outputs land in `judging/results/<model>/OFFLINE_FINAL/`:
`scores_per_question.csv`, `config_summary.csv`, `controls_report.md`,
`stats.csv`, `reliability_report.md`, `FINAL_REPORT.md`.

### The 3-of-3 rule is manual

`aggregate.py` is single-judge by construction. Compare the direction of each
contrast across the three `stats.csv` files **by hand**. A contrast is confirmed
only when all three judges agree in direction and each is significant. Judges
disagreeing on sign makes a contrast *inconclusive*, not null — the distinction
matters in the write-up.

Registered contrasts are in `PRECOMMIT.md`: three primary (F−B, F−B SC, B−A) and
five secondary (E−B, C−B, G−B, G−F, D−B).

---

## Budget

| Stage | Calls | Cumulative | Abandon cost |
|---|---|---|---|
| 0 · review | 0 | 0 | — |
| 1 · probe | 12 | 12 | 12 |
| 2 · controls | 270 | 282 | **282 (14%)** |
| 3 · full panel | 1,722 | **2,004** | — |

---

## Guards that protect you automatically

Each was verified by execution, not by reading the code.

| Guard | What it stops | Where |
|---|---|---|
| Prompt provenance | Building items from a run not generated under `offline_definitive_v1` | `assemble_items.py` |
| Bank alignment | Building items from a run whose embedded references differ from the live bank | `assemble_items.py` |
| Items freshness | Judging an `items.jsonl` built against a different bank | `judge_deepseek.py`, also in `--review_only` |
| Resume collision | Re-running into a `run_tag` that holds a different evaluation | `judge_deepseek.py` |
| Decode drift | Serving a cached judgment produced under different reasoning / `max_tokens` settings. Added 2026-09-06 after 12 such entries were created during provider probing | `judge_deepseek.cache_key` via `decode_fingerprint` |
| INVALID retry | A failed judgment being skipped forever on resume, so a partly-failed panel could not be repaired in place. Added 2026-09-06 | `judge_deepseek.py` resume block |
| Model substitution | A provider serving a different tier than requested | `judge_deepseek.py` (aborts), `aggregate.py` (discloses) |

### Reading a refusal

- **"reference bank has changed since items.jsonl was built"** — re-run
  `assemble_items.py` then `make_controls.py`.
- **"refusing to resume into run_tag"** — that tag holds a different evaluation.
  Pick a new tag; do not delete the old results.
- **"MODEL SUBSTITUTION"** — the provider served a different tier. Either retry,
  or accept deliberately with `--allow_model_substitution` and disclose it.
  `aggregate.py` will stamp a warning into `FINAL_REPORT.md` if you do.
- **"The reference bank was revised after this run was generated"** — regenerate
  with `python camera_ready/pipeline.py generate`. Decoding is greedy, so the
  answers come back identical; only the embedded references and ROUGE change.

---

## Known limitations to carry into the write-up

- **DeepSeek tier.** In July the provider served `deepseek-v4-flash` on all 582
  calls against a registered `deepseek-v4-pro`. The run now aborts on
  substitution, but if it recurs and you proceed with the override, disclose it.
- **Safety lane agreement.** The 12-category detector showed only 36% three-way
  agreement on the July panel (15 of 42 flagged answers flagged by all three).
  Do not describe violation detection as precise.
- **Category sample sizes.** 1–7 questions per category; only Bleeding (7),
  Cardiac (6) and Minor Injuries (6) support any per-category statement.
- **Reference revision.** The bank was compressed from a median of 102 to 54
  words after review by three medical professionals, between the July results and
  this judging run. Disclose the revision and its date ordering.
