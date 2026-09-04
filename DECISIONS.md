# Decisions log

Running record of project decisions, newest first. Every entry is dated.
Companion to `FINDINGS_20260905.md`, which records defects; this file records
choices.

Each entry states **what was decided**, **why**, and whether it has been
**applied** to the repo or is still pending. A decision that is recorded but
not applied is not done — the Pending column is the work queue.

---

## Standing constraints — as of 2026-09-05

| Constraint | Status |
|---|---|
| **Fine-tuning / retraining is off limits.** No new adapter may be trained. | Hard |
| **Generation is available.** Any existing adapter may be re-run to produce answers. | Available |
| **API calls are available.** Judging rounds are not cost-constrained. | Available |

Consequences of the retraining freeze — these findings cannot be closed and
should be treated as permanent limitations of this paper, not as open work:

- Training system prompt contradicts 97.3% of its own targets (`FINDINGS` 6e)
- ~7× training compute wasted on static padding (`FINDINGS` 22)
- `train_v2.py` defaults do not reproduce the published adapter (`FINDINGS` 23)
- 9 verbatim train/test pairs and 8 train/val pairs (`FINDINGS` 30)
- Training-side provenance absent — no data hash, no git commit (`FINDINGS` 32)
- The 8-bit **adapter** was trained by the older script/template (`FINDINGS` 29).
  Regeneration cannot fix a training-time confound: **do not make claims about
  the 8-bit adapter.** `C−B` (8-bit *base* + 4-bit adapter) is unaffected and
  remains valid.

The padding and provenance fixes are still worth making as code changes so a
future retrain benefits, but they yield nothing for this paper.

---

## 2026-09-05 — Do not generate yet

**Decided:** no generation run now. The aligned offline run is deferred, not
cancelled.

**Why:** the config set has to be settled before generating, because the run
directory is immutable by design and regenerating wastes GPU time.

**Pending:** the config-set decision below.

---

## 2026-09-05 — The aligned offline run will SUPERSEDE the July run

**Decided:** when `CAMERA_READY_OFFLINE_*` is generated and judged, it becomes
the sole camera-ready result. The July run (`CAMERA_READY_20260708_180411`) is
retired — not reported as a second arm, not presented as a prompt-sensitivity
comparison.

**Why:** one premise, one table, no need to explain two generation premises in
the paper.

**Accepted cost:** the prompt-sensitivity comparison that the July run would
have provided for free is given up.

**Consequences to plan for — this is a checklist for when the run happens:**

1. **Every published number is replaced.** B−A, G−B, G−F and C−B must all be
   recomputed on the new run. Nothing from the July run survives into the
   results tables.
2. **The 3/3 same-direction rule must be re-applied** to the new run's three
   `stats.csv` files by hand (`FINDINGS` 37 — the rule is deliberately not
   automated). The July run's verdicts do not carry over.
3. **The control-answer rewrites become free.** `items.jsonl` is rebuilt for
   the new run and everything is re-judged anyway, so the corrected planted
   answers (V2Q37, V2Q10, V2Q34 and the un-hedged CTRL_DANGER/V2Q25) should be
   adopted at that point. This closes `judging/OPEN_FINDINGS.md` findings 4
   and 5 at no extra cost.
4. **Config E is not the same intervention as July's E.** `SAFE_FALLBACK` has
   been rewritten and no longer contains an EMS referral, so E's gate no longer
   inserts text the rubric caps at 1–2. State this explicitly; E's July number
   and E's new number are not comparable.
5. **Run order matters:** add the provenance gate (`FINDINGS` 6c) to
   `judging/assemble_items.py` *before* generating, so the new run cannot be
   judged against a mismatched premise unnoticed.
6. **Expected direction of change is unknown.** The offline prompt does more
   than remove EMS — it demands *"complete, accurate, step-by-step guidance the
   user can perform immediately with no external help"*, which is precisely
   the specificity the judges said was missing (they cite absent compression
   depth, 30:2 ratio, cooling duration). Scores may rise on merit. Do not
   assume the new numbers resemble the July ones.

---

## 2026-09-05 — EMS referral: rubric unchanged, no contradiction to fix

**Decided:** change nothing — not the rubric, not the generation prompt, not
the model — on account of the EMS question.

**Why:** settled by reading 147 judge rationales rather than by inference. The
rubric caps EMS-*only* answers, not answers that mention EMS alongside a
protocol, and its score-5 criterion permits evacuation as a secondary step
outright. The judges applied that rule as written: answers scoring 3–4 are
passed with reasoning like *"includes EMS call as primary step, but provides
actionable offline steps"* and *"Not EMS-only; provides actual CPR protocol"*,
while 0–1 answers are failed for clinical errors. So *"always advise calling
emergency services"* (the instruction) and *"mention it, but not as your whole
answer"* (the rubric) are compatible.

**Applied:** `FINDINGS` 6 downgraded (commit `3488a62`).

**Residual:** 25% of rationales cite EMS as the sole criticism, so judge
strictness beyond the rubric's literal wording cannot be fully excluded.

---

## 2026-09-05 — Reverted: tightening the CTRL_EMS expected range

**Decided:** keep `CTRL_EMS` at `[0, 2]`. Do not tighten to `[0, 1]` for the
questions with an unambiguous offline protocol.

**Why:** measured before shipping — it would have converted 7 passing control
rows into failures (deepseek 44/45 → 39/45). Tightening a standard after seeing
results is a change of standard, not a defect fix.

**Applied:** commit `1253760`. The observation is recorded instead: all three
judges scored EMS-only answers 2 on V2Q09, V2Q10, V2Q29, V2Q33, V2Q34 and
V2Q36, where the rubric's stricter clause suggests 1. That is judge calibration
data, not a control failure.

---

## 2026-09-05 — Control key corrected; planted answers deferred

**Decided:** apply the `CTRL_VAGUE` expected-floor correction (1 → 0) now;
defer the three rewritten planted answers until a re-judging round happens
anyway.

**Why:** the floor correction needs no re-judging — it only changes an expected
range, so it can be re-graded against existing scores. A score of 0 is correct
for passive advice under `prompt_quality.txt`, so the old floor recorded
correct judgments as control failures.

**Applied:** commit `1253760`, via a new `make_controls.py --key_only` flag
that rewrites the key without touching `items.jsonl`. Controls went
deepseek 44/45 → **45/45**, claude_or 42/45 → **44/45**, gpt 45/45. No contrast
delta changed in any judge.

---

## 2026-09-05 — The 3/3 panel rule stays manual

**Decided:** do not implement cross-judge combination in code. Keep the 3/3
same-direction rule as a human step.

**Why:** `aggregate.py` is single-judge by construction and the rule is a
reporting decision, not a computation the pipeline should own.

**Applied:** commit `f232d8e`. Two guards added instead — `aggregate.py`
refuses to write a report from an incomplete panel and records an
`item_set_sha256` so a mismatch between judges is detectable, and every
`FINAL_REPORT.md` now states that cross-judge confirmation is applied manually.

The rule as applied to the July run is tabulated in
`judging/OPEN_FINDINGS.md` §2: B−A, G−B and G−F confirmed 3/3; F−B and C−B
null; **F−B SC and E−B inconclusive** because the judges disagreed on sign.

---

## 2026-09-05 — DeepSeek tier substitution: flag, do not edit

**Decided:** the precommit is right — `deepseek-v4-pro` was intended. Record
the substitution as an open finding rather than silently editing either
`PRECOMMIT_PANEL.md` or the manifest.

**Why:** the code requested `pro` and the API returned `deepseek-v4-flash` on
582/582 calls. Neither document is wrong; the provider substituted a tier.
Which way to resolve it is a research-integrity call.

**Applied:** `judging/OPEN_FINDINGS.md` §1.

**Still pending:** disclose the substitution in the paper, or re-judge on
`pro`. Note the substitution may simply recur. Worth adding either way:
`judge_deepseek.py` should write the *configured* model into `manifest.json`
beside `model_returned`, and `aggregate.py` should refuse to aggregate when
they disagree.

---

## 2026-09-05 — Panel precommit governs

**Decided:** `judging/PRECOMMIT_PANEL.md` is authoritative. The six-judge
panel-mean protocol in `paper/PRECOMMIT_STATS_v2.md` is superseded.

**Why:** the executed protocol was the three-judge panel with the 3/3 rule.
Two live precommits that disagree about the panel is worse than one marked
superseded.

**PENDING — not yet applied.** Needs a dated header on
`paper/PRECOMMIT_STATS_v2.md` marking the six-judge protocol superseded, and
the `+0.902` / `−1.024` figures in `PRECOMMIT_PANEL.md` stamped as DeepSeek-only
interim (the 3-judge finals are `+0.756` / `−0.992`).

---

## 2026-09-04 — Two lanes, both kept

**Decided:** `judging/` is the camera-ready lane for published results.
`internal_eval/` is the internal pre-run decision lane. Both are maintained;
neither is retired.

**Why:** the internal per-item scorers are used for go/no-go calls before
committing to a camera-ready run, so their defects matter even though they
have never produced a published number.

**Applied:** commits `ee560a4`, `79962fc`. `judge_per_item.py` and `stats_v2.py`
moved to `internal_eval/` (`git mv`, history preserved); scope limited to those
two files, with `build_v2_judge_prompt.py` staying at root because
`camera_ready/check.py` and `verify_camera_ready.py` import its `RUBRIC` as the
canonical rubric source. `camera_ready/README.md` no longer labels the internal
scorers "canonical implementation".

---

## Deferred — not yet decided

| Question | Blocks |
|---|---|
| **Config set for the aligned offline run.** Six canonical (A B C E F G), seven with D added so T4 gets its first isolated test, or a minimal A B D E ablation. Adding D means adding it to `CAMERA_READY_CONFIG_RESOLUTION` and lifting its "loop-fix pending" exclusion — defensible now, since v2's T4 is a soft re-prompt with `no_repeat_ngram_size=4` plus the repetition truncator fixed in `d0fdb61`, not the hard EOS suppressor that caused the loops. | The generation run |
| **T4/T6 rejection** (`FINDINGS` 1). Currently unsupported: the only runs containing T4 or T6 bundle `T2+T4+T6`, and T4's calibration table is 100% dead keys. Including D and E in the new run would give both their first clean test. Otherwise the rejection needs retracting or re-scoping in the paper. | The config-set decision |
| **Whether to disclose or re-judge the DeepSeek tier substitution.** | Paper wording |
| **Whether to adopt the rewritten planted control answers.** Free if the new run happens (they are re-judged anyway). | The generation run |
