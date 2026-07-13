# Pre-Commitment Document — Statistical Analysis Plan
## stats_v2.py | Gemma 2B First Aid QA Evaluation

**Written:** 9 July 2026 (BEFORE any stats_v2.py results are computed)  
**Committed before running:** YES — git timestamp of this file is the receipt.  
**Script:** `stats_v2.py`  
**Input:** `evaluations/CAMERA_READY_20260708_180411/judgments/` (per-item scores)

---

## Purpose

This document specifies every hypothesis, metric, and analysis method in advance of seeing any per-item judge scores. Once judge scores are collected (Task 4), `stats_v2.py` is run exactly once against the camera-ready run. The precommit git timestamp prevents post-hoc hypothesis selection.

---

## Data pipeline

1. **Input:** Per-item judgment files at `judgments/<judge>/<config>/<qid>.json`, each containing `{"score": 0-5, ...}`.
2. **Judge-level aggregation:** For each (question_id, config), compute the mean score across all six judges. This produces one value per (question, config) pair — the "panel mean" for that question under that config.
3. **Config-level summary:** The config mean score is the mean of 41 panel-mean values.
4. **SC/non-SC split:** The 41 questions include 11 SC questions (V2Q01, V2Q08, V2Q09, V2Q13, V2Q14, V2Q25, V2Q29, V2Q33, V2Q34, V2Q36, V2Q39). SC and non-SC config means are reported separately.

---

## Primary hypotheses (will carry formal tests)

These are stated as directional hypotheses. Tests are two-sided (stated as directional only for effect size reporting).

**H1 (main claim): F > B overall.**  
Config F (fine-tuned 4-bit + BM25 RAG) has a higher mean panel score than Config B (fine-tuned 4-bit, no RAG), averaged over all 41 questions.

**H2: F > B on SC questions.**  
The F > B advantage is at least as large on the 11 safety-critical questions as it is overall. (This tests whether RAG adds value specifically on harder, higher-stakes questions.)

**H3: B > A overall.**  
Config B (fine-tuned 4-bit) outperforms Config A (base 4-bit, no adapter) over all 41 questions. This is the fundamental adaptation quality claim.

---

## Secondary analyses (report results, no significance claims)

These comparisons are reported with CIs and effect sizes but no hypothesis rejection. The paper will state that these are exploratory only.

- E vs B: does the T6 safety gate improve scores despite 5 flagged regenerations?
- C vs B: 8-bit vs 4-bit quantization — is the ROUGE-L parity (−0.0005) replicated in judge scores?
- G vs B: base model + RAG vs fine-tuned no-RAG — isolates adapter effect in presence of retrieval.
- G vs F: adapter-only effect when RAG context is held constant.

---

## Statistical methods

### 1. Paired bootstrap (primary method)
- **Resamples:** 10,000
- **Unit:** questions (resample the 41 questions with replacement)
- **Statistic:** mean panel score difference (config_X minus config_Y) per resample
- **Output:** point estimate (observed delta), 95% CI (2.5th and 97.5th percentiles of resample distribution)
- **Reported for:** all three primary pairs and all four secondary pairs, overall and SC-only
- **Seed:** 2026 (fixed; reported in paper)

### 2. Sign test (exact binomial, primary pairs)
- **Unit:** per-question wins/losses (tie = excluded)
- **Null:** P(F > B on a random question) = 0.5
- **Test:** exact binomial, two-sided, α = 0.05
- **Reported for:** H1 (overall), H2 (SC subset), H3 (overall)
- **Output:** number of wins/ties/losses, p-value, and whether it crosses α = 0.05

### 3. Judge agreement metrics
- **Kendall's τ:** computed over the six configs' ranked order (by mean score) for each pair of judges. Reports whether judges agree on the *ordering* of configs.
- **Pairwise Spearman ρ:** for each pair of judges, correlation of per-question panel means (41 values per judge). Reports whether judges agree on *which questions* are harder.
- Both metrics reported as mean ± SD across all judge pairs.

### 4. Flag count analysis
- Report unsafe-flag counts for each config with exact binomial 95% CIs.
- **Explicitly state in the paper:** "No significance test is applied to flag-count differences. Flag counts are system outcomes, not independent question-level scores, and testing them would not be interpretable."

---

## What will NOT be computed or claimed

- No multiple-comparison correction is applied to secondary comparisons (they carry no significance claim).
- No parametric tests (t-test, ANOVA) are used. Judge scores are ordinal; bootstrap and sign test are distribution-free.
- No claim is made about pairwise comparisons that were not pre-committed here.
- "Trending toward significance" language is banned. A result either crosses α = 0.05 or it does not.

---

## Output files

| File | Contents |
|---|---|
| `stats_v2_results.csv` | Per-config: mean, median, SD, SC mean, non-SC mean, bootstrap CI (overall), bootstrap CI (SC) |
| `stats_v2_pairwise.csv` | Per config-pair: delta, bootstrap CI, sign test wins/ties/losses, p-value |
| `stats_v2_judge_agreement.csv` | Per judge-pair: Kendall tau (config ranking), Spearman rho (per-question) |
| `stats_v2_flags.csv` | Per config: flag count, exact binomial 95% CI |
| `stats_v2_latex.tex` | LaTeX-ready versions of the four tables above |
| `stats_v2_figure4.json` | Error-bar data for Figure 4 replacement: per-config mean ± 95% bootstrap CI |
