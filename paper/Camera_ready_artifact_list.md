# Camera-Ready Artifact List

**Project:** Gemma 2B Instruct QLoRA Fine-Tuning — Offline Android First-Aid Assistant
**Canonical judging run:** `OFFLINE_FINAL`
**Canonical generation run:** `evaluations/CAMERA_READY_OFFLINE_20260905_204533`
**Panel:** DeepSeek V4 Pro · Claude Opus 5 · GPT-5.6-sol *(confirmatory)* · GLM-5.3 *(exploratory)*
**Rewritten:** 2026-09-10

---

> **REWRITTEN 2026-09-10 — read this before comparing against any earlier copy.**
>
> Until this date, this file described the **July run** (`CAMERA_READY_FINAL`:
> 582 calls, 291 items, 6 configs, 3 judges, Claude Opus 4.8, the pre-ANZCOR
> rubric and the pre-compression reference bank). That run was retired on
> 2026-09-05 — `DECISIONS.md`, *"The aligned offline run will SUPERSEDE the July
> run … Every published number is replaced. Nothing from the July run survives
> into the results tables."* The replacement was carried out in the experiment
> but never in this file, so the project's paper-facing artifact list pointed at
> a superseded experiment for five days.
>
> The July artifacts are **not deleted**: `judging/results/*/CAMERA_READY_FINAL/`
> is untouched on disk and the previous version of this file is in git history.
> What changed is which run this document points the paper at.
>
> **Correction, 2026-09-10, and its retraction the same day:** a draft of this
> banner claimed `FINDINGS_20260905.md` §A6's `july` branch "does not exist",
> on the strength of a `git branch -a` run against a checkout that had not been
> fetched. **That was wrong.** `origin/july` exists, its tip is `bac28cf`
> (2026-09-05 15:03 +0600), and it contains no
> `CAMERA_READY_OFFLINE_20260905_204533` — so it does preserve the pre-session
> state exactly as §A6 says. §A6 is correct and needs no fix. The lesson is
> recorded rather than erased: `git fetch` before asserting anything about
> remote refs.
>
> Every superseded statement is itemised in **§7 Corrections log**, including one
> contrast that has been **withdrawn** rather than restated. Nothing was silently
> replaced.

---

## 1. Canonical experiment identity

Everything below is recorded in the artifacts, not asserted here. These are the
values to cite in the paper.

| | |
|---|---|
| Generation run | `evaluations/CAMERA_READY_OFFLINE_20260905_204533` |
| Generation commit | `eda9a9e8`, `source_tree_clean: true` |
| Prompt policy | `offline_definitive_v1`, sha256 `0d97fe5d60f5…`, 447 chars (full text in `run.json`) |
| Question bank | `evaluations/eval_bank_v2_40q/eval_bank_v2.json`, sha256 `8dfb4f4b221426c7ff597abe4bcb87e4d74f9c7d23510ea6fd9c442f1c4a18b0` |
| Bank size | 41 questions, 11 safety-critical, 10 categories |
| Base model | `gemma-2b-it`, sha256 `b38a09b55153…`, 25 files, 5,034,199,257 bytes |
| Adapter | `experiments/10cat_4bit_r16_lr1e-4_p3_v2_20260508_054337/adapter`, sha256 `0eef1fb6b352…`, 6 files, 112,837,074 bytes |
| Adapter checkpoint | checkpoint-1000, val loss 1.3400, `load_best_model_at_end=True` |
| Train split | `splits/10cat/train.json`, sha256 `a84114ae01c9…`, 4,441 rows |
| Decoding | `do_sample=false`, `max_new_tokens=350`, `repetition_penalty=1.15`, `no_repeat_ngram_size=4` |
| Item set | 287 real (7 configs × 41) + 45 planted controls = **332 items** |
| Calls per judge | **664** (332 items × 2 prompt types: quality + safety) |
| Total judgments | **2,656** (664 × 4 judges), **0 INVALID**, 0 duplicate keys |
| Judge templates | quality `865fc1149fda…`, safety `36e3a7ba2795…`, combined **`061b77993f5fdfd8686b661e972613a36c5e309b7a11535ddff990b3cf88d771`** |
| Template freeze | iteration 5, run tag `ANZCOR_CHOKING_20260906`, commit `4fa6186`, 4 h 37 min before the first canonical judgment |
| Judging commits | `b230d82` (deepseek, glm_ar) · `68c44cb` (gpt_ar) · `32b027c` (claude_or) |

**Determinism evidence.** Two offline generation runs 46 minutes apart
(`_195908` and `_204533`) produce **byte-identical answers for all 7 configs ×
41 questions**. Only `_204533` is canonical — it was generated after the
clinically approved references landed — but the pair is the project's evidence
that the generation lane is deterministic rather than merely claimed to be.

---

## 2. Per-judge result directories

Four directories, identical structure, eight files each:

```
judging/results/<judge>/OFFLINE_FINAL/
  judgments.jsonl        664 raw judgments (332 items x 2 prompt types), 0 INVALID
  manifest.json          model requested + served, base_url, temperature, max_tokens,
                         extra_body, decode_fingerprint, template/quality/safety hashes,
                         bank_sha256, git_commit, shuffle_seed, call counts
  stats.csv              all 8 pre-registered contrasts: n_pairs, mean_delta, ci_lo,
                         ci_hi, wins, losses, ties, sign_p, confirmed,
                         expected/observed direction, direction_match
  config_summary.csv     per config: n, overall/SC/non-SC/SC-weighted means,
                         n_sc, n_nonsc, n_unscreened, danger_any, danger_sc_only
  scores_per_question.csv  item level: qid, config, sc_flag, category, quality_score,
                         answer_chars, n_violations, safety_screened, violated_categories
  controls_report.md     45 planted controls, pass/fail vs expected band, plus
                         planted-violation detection counted separately
  reliability_report.md  intra-judge stability, optional re-score, length-bias check
  FINAL_REPORT.md        per-judge narrative. Reports the PER-JUDGE criterion only;
                         states explicitly that cross-judge confirmation is not
                         computed there
```

| `<judge>` | Model requested | Model served, all 664 rows | Route |
|---|---|---|---|
| `deepseek` | `deepseek-v4-pro` | `deepseek-v4-pro` | DeepSeek direct API |
| `claude_or` | `anthropic/claude-opus-5` | `anthropic/claude-opus-5` | OpenRouter |
| `gpt_ar` | `gpt-5.6-sol` | `gpt-5.6-sol` | AgentRouter (reseller) |
| `glm_ar` | `glm-5.3` | `glm-5.3` | AgentRouter (reseller) |

`config_summary.csv` reports **means, not mean ± SD** — there is no SD column.
Per-judge score dispersion is in §6.

---

## 3. Panel verdict — the authoritative results file

### `judging/PANEL_VERDICT_OFFLINE_FINAL.md`

The cross-judge confirmation rule is registered as a **manual** step
(`DECISIONS.md`, 2026-09-05) and `aggregate.py` is single-judge by construction,
so no per-judge file contains a panel verdict. This file is the application of
the rule to the canonical run. It is generated from the four committed
`stats.csv` files by `judging/panel_verdict.py`, which also verifies before
combining anything that all four judges used an identical `template_hash`,
`quality_hash`, `safety_hash` and `bank_sha256`, identical `n_calls_total`,
zero INVALID rows, and identical contrast names, order and `n_pairs`.

Regenerate, or check for drift, with:

```
python judging/panel_verdict.py --run_tag OFFLINE_FINAL
python judging/panel_verdict.py --run_tag OFFLINE_FINAL --check   # exit 3 if stale
```

**This file did not exist until 2026-09-10.** `DECISIONS.md` listed
re-application of the rule as consequence 2 of retiring July, and it was not
done, so the canonical experiment had no written headline verdict. See §7.

---

## 4. Key results — canonical run

Panel mean is the arithmetic mean of the **three confirmatory** judges, the same
aggregation used for the July figures. **Always report it with the spread
visible.** `glm_ar` is the registered exploratory fourth judge and does not gate
any verdict.

| # | Contrast | Pri | Panel mean Δ | Confirmatory spread | 3/3 dir | 3/3 sig | `glm_ar` | Verdict |
|---|---|---|---|---|---|---|---|---|
| 3 | B − A (fine-tuned vs base) | ★ | **+1.089** | +0.902 … +1.341 | Yes | **Yes** | +1.049 sig | **CONFIRMED** |
| 1 | F − B (RAG on fine-tuned) | ★ | +0.236 | +0.171 … +0.268 | Yes | No | −0.024 ns | **NULL** |
| 2 | F − B, safety-critical only | ★ | +0.242 | +0.091 … +0.364 | Yes | No | 0.000 ns | **UNTESTABLE** — see below |
| 6 | G − B (base+RAG vs fine-tuned) | — | **−0.951** | −1.293 … −0.683 | Yes | **Yes** | −0.951 sig | **CONFIRMED** *(exploratory)* |
| 7 | G − F (base+RAG vs FT+RAG) | — | **−1.187** | −1.463 … −0.951 | Yes | **Yes** | −0.927 sig | **CONFIRMED** *(exploratory)* |
| 5 | C − B (8-bit vs 4-bit base) | — | +0.000 | −0.049 … +0.098 | **No** | No | −0.098 ns | **INCONCLUSIVE** |
| 4 | E − B (T6 safety gate) | — | −0.033 | −0.073 … +0.024 | **No** | No | 0.000 ns | **INCONCLUSIVE** |
| 8 | D − B (T4 length floor) | — | +0.024 | 0.000 … +0.049 | **No** | No | 0.000 ns | **INCONCLUSIVE** |

★ = primary pre-registered contrast. Per-judge criterion: bootstrap 95% CI
excludes zero **and** exact two-sided sign test p < 0.05. Bootstrap: 10,000
paired resamples, seed 2026, pairing on `question_id`, n = 41 (n = 11 for the SC
filter).

### Effect sizes for the confirmed contrasts

| Contrast | Judge | Mean Δ | 95% CI | Paired dz | Sign p |
|---|---|---|---|---|---|
| B − A | deepseek | +1.024 | [+0.634, +1.415] | +0.804 | 1.5e-05 |
| B − A | claude_or | +1.341 | [+0.927, +1.756] | +0.978 | 3.0e-06 |
| B − A | gpt_ar | +0.902 | [+0.585, +1.244] | +0.810 | 5.9e-05 |
| B − A | glm_ar *(expl.)* | +1.049 | [+0.659, +1.415] | +0.830 | 1.0e-06 |
| G − B | deepseek / claude_or / gpt_ar | −0.878 / −1.293 / −0.683 | see `stats.csv` | −0.798 / −1.018 / −0.545 | 4.9e-05 / 1.0e-06 / 0.0037 |
| G − F | deepseek / claude_or / gpt_ar | −1.146 / −1.463 / −0.951 | see `stats.csv` | −1.131 / −1.392 / −0.908 | <1e-06 / <1e-06 / 3.0e-06 |

**All confirmed results survive Bonferroni correction** across the eight
registered contrasts (threshold p < 0.00625; largest confirmed p-value 5.9e-05).
No multiplicity correction was pre-registered; state this, and state that no
conclusion changes under one.

### Why F − B SC is UNTESTABLE and not null

The registered sign test is an exact two-sided binomial on **non-tied pairs
only**. On the SC subset (n = 11) the observed tie counts leave 4–5 non-tied
pairs per judge:

| Judge | W / L / T | Non-tied | Smallest attainable two-sided p |
|---|---|---|---|
| deepseek | 3 / 1 / 7 | 4 | 0.125 |
| claude_or | 3 / 2 / 6 | 5 | 0.0625 |
| gpt_ar | 4 / 1 / 6 | 5 | 0.0625 |
| glm_ar | 2 / 2 / 7 | 4 | 0.125 |

Six non-tied pairs, all falling one way, is the minimum for p < 0.05. **No
arrangement of the observed data could have confirmed this contrast for any
judge.** The wording used in the per-judge `FINAL_REPORT.md` files — *"not
confirmed at the pre-specified threshold"* — reads as evidence of no effect;
the truth is that the pre-registered test had no power. Report it as such, and
note that no minimum-sample or power criterion was registered for the SC subset.

---

## 5. Per-config results

Overall quality mean (0–5), per judge, real configs only, n = 41 each:

| Config | What it is | deepseek | claude_or | gpt_ar | glm_ar | Panel (3 conf.) |
|---|---|---|---|---|---|---|
| A_BASE_4BIT | base Gemma-2B, 4-bit, no adapter | 1.220 | 0.902 | 1.415 | 1.512 | 1.179 |
| B_FINETUNED_4BIT | QLoRA adapter, 4-bit NF4 — **primary deliverable** | 2.244 | 2.244 | 2.317 | 2.561 | 2.268 |
| C_FINETUNED_8BIT | same 4-bit-trained adapter on an 8-bit base | 2.341 | 2.195 | 2.268 | 2.463 | 2.268 |
| D_T4_IMPROVED | adapter + length-floor re-prompt | 2.293 | 2.268 | 2.317 | 2.561 | 2.293 |
| E_T6_IMPROVED | adapter + binary SAFE/UNSAFE gate | 2.171 | 2.195 | 2.341 | 2.561 | 2.236 |
| F_RAG_BM25 | adapter + topic-gated BM25 top-1 | 2.512 | 2.415 | 2.585 | 2.537 | 2.504 |
| G_BASE_RAG | base + topic-gated BM25 top-1 | 1.366 | 0.951 | 1.634 | 1.610 | 1.317 |

**Config C is an inference-time quantization contrast, not an 8-bit adapter.**
`run.json`'s `_config_resolution` records C as `base_quant: 8bit` with
`adapter: adapter_4bit`. No claim may be made about the 8-bit *adapter*: it was
trained by the older `train.py`/`data.py` script with a manual template, which
confounds quantization with training template (`FINDINGS_20260905.md` §29,
`DECISIONS.md` standing constraints).

### Safety lane

Violations are the 12 ANZCOR-derived override categories SO01–SO12, scored by a
separate second call per item with no reference answer shown.

| Config | Any-violation answers / 41 (ds / cl / gpt / glm) | SC-only violations / 11 |
|---|---|---|
| A_BASE_4BIT | 8 / 12 / 9 / 6 | 5 / 6 / 5 / 4 |
| B_FINETUNED_4BIT | 7 / 7 / 9 / 4 | 2 / 2 / 3 / 2 |
| C_FINETUNED_8BIT | 7 / 8 / 8 / 4 | 3 / 2 / 2 / 1 |
| D_T4_IMPROVED | 5 / 8 / 8 / 5 | 2 / 2 / 2 / 2 |
| E_T6_IMPROVED | 4 / 4 / 9 / 4 | **0 / 0** / 3 / 1 |
| F_RAG_BM25 | 5 / 5 / 8 / 4 | 2 / 2 / 3 / 2 |
| G_BASE_RAG | 9 / 15 / 12 / 7 | 5 / 5 / 4 / 4 |

**No safety contrast was pre-registered, and post-hoc tests are null.** Exact
McNemar on the any-violation flag, B vs A: p = 1.00 (deepseek), 0.125
(claude_or), 1.00 (gpt_ar), 0.625 (glm_ar). Two of four judges show exactly zero
net change. **The paper may not claim that fine-tuning improves safety.** The
deliverable still produces an override violation on 2–3 of 11 safety-critical
questions under every judge, which also rules out any deployment-readiness
claim. Config E reaches 0/11 for two judges, but by withholding the answer,
which the rubric caps at 2/5 — report that as a utility/safety trade-off, and
note that E is not the same intervention as July's E (`SAFE_FALLBACK` was
rewritten).

---

## 6. Judge behaviour

| Judge | Mean | SD | Mode | Leniency rank | No-reasoning policy |
|---|---|---|---|---|---|
| DeepSeek V4 Pro | 2.021 | 1.061 | 1 | 3rd | plausible, **unverifiable** (field never reported) |
| Claude Opus 5 | 1.882 | 1.134 | 2 | 4th — **harshest** | **held, and verified** (0 on all 664) |
| GPT-5.6-sol | 2.125 | 1.006 | 1 | 2nd | **breached on 15 of 664**; unverifiable on 649 |
| GLM-5.3 *(expl.)* | 2.258 | 1.015 | 2 | 1st — most lenient | declared deviation: 285 of 664 nonzero, max **185** |

Computed over 287 real-config quality scores per judge. The 0.38-point leniency
spread does not bias any contrast, because every contrast is paired
within-judge — but **a raw cross-judge mean score must never be reported without
this spread visible.**

### Inter-judge reliability

| Pair | Quality exact | Quality within ±1 | Safety binary agreement | Safety Cohen's κ |
|---|---|---|---|---|
| deepseek × claude_or | 64.1% | 98.3% | 90.2% | +0.673 |
| deepseek × gpt_ar | 59.2% | 92.3% | 90.2% | +0.683 |
| deepseek × glm_ar | 57.8% | 96.9% | 94.1% | +0.751 |
| claude_or × gpt_ar | 53.3% | 94.4% | 88.9% | +0.667 |
| claude_or × glm_ar | 56.8% | 97.2% | 90.6% | +0.658 |
| gpt_ar × glm_ar | 60.3% | 96.9% | 89.2% | +0.622 |

n = 287 per pair. Intra-judge stability (deepseek, 20 items, nonce re-score):
90% exact, 95% within ±1.

> These reliability figures and the length-bias check are **absent from the four
> `reliability_report.md` files**, which read *"scipy not available — install to
> compute Spearman rho"*: scipy was missing on the machine when the canonical
> run was aggregated. `requirements.txt` now pins `scipy>=1.11.0`. Re-running
> `aggregate.py` for all four judges regenerates the reports with these
> statistics in place, reads only committed `judgments.jsonl`, and costs nothing.

### Control battery

45 planted controls per judge: 13 `CTRL_REF` (the reference verbatim), 6
`CTRL_DANGER` (an explicit override violation), 13 `CTRL_EMS` (referral only),
13 `CTRL_VAGUE` (passive non-actionable advice).

**45/45 within the expected band for all four judges. 6/6 planted violations
detected by all four judges.** Re-verified 2026-09-10 with
`python judging/check_controls.py --model <judge> --run_tag OFFLINE_FINAL`.

Two limits on what the battery evidences, both of which must be stated:

- `CTRL_REF` scores exactly 5.00 on 52 of 52 judgments — but its answers are
  **byte-identical to the `reference` field shown in the same prompt** (verified,
  13/13). It evidences that the judge reads the prompt, not clinical
  discrimination.
- The 6 `CTRL_DANGER` plants cover **5 distinct override categories, not 6**
  (SO05, SO06, SO12, SO06 again, SO09, SO11). SO01–SO04, SO07, SO08 and SO10 are
  never planted. Do not claim detection across the override taxonomy.

---

## 7. Corrections log

Every statement this file previously made that has been changed or withdrawn,
with the reason. Nothing was replaced silently.

| Previously stated | Status | Why |
|---|---|---|
| Run tag `CAMERA_READY_FINAL`; "Date: 2026-07-14" | **Replaced** | July was retired 2026-09-05 (`DECISIONS.md`). Canonical run is `OFFLINE_FINAL` on `CAMERA_READY_OFFLINE_20260905_204533`. |
| "582 raw judgments (291 items × 2 prompt types)" | **Replaced** | 664 judgments, 332 items (287 real + 45 controls) per judge. |
| Three judge directories | **Replaced** | Four: `deepseek`, `claude_or`, `gpt_ar`, `glm_ar`. |
| "Judges: DeepSeek V4 Pro · Claude Opus 4.8 · GPT-5.6"; "`results/gpt/`… via OpenRouter" | **Replaced** | The canonical claude arm is **Claude Opus 5**, not 4.8 — see `PRECOMMIT.md`, Amendment 2026-09-10. The gpt judge moved to AgentRouter and was renamed `gpt_ar`; `results/gpt/` holds the July OpenRouter panel and must not be written into. |
| Config key listing A, B, C, E, F, G | **Replaced** | Seven configs: **D_T4_IMPROVED** is in the canonical run with a registered D−B contrast (`PRECOMMIT.md` contrast 8, registered 2026-09-06 before any D data existed). |
| Δ / CI / p table (B−A +0.756, G−B −0.992, C−B +0.073, E−B −0.008, F−B −0.041) | **Replaced, and the interval estimates withdrawn** | The **means** are correct as July three-judge averages and reconcile exactly. The **CIs and p-values reproduce under no method in this repository** — not the per-judge bootstrap in `aggregate.py`, not a judge-averaged bootstrap at seed 2026, not a paired t-test. They are withdrawn as untraceable. §4 gives canonical values that regenerate from `stats.csv`. |
| **"G − A \| −0.236 \| [−0.520, +0.057] \| 0.115"** | **WITHDRAWN** | G−A **appears in no pre-registration and in no `stats.csv` in the repository.** Its mean reconciles as a July three-judge average (−0.2358) but its CI and p-value are unreproducible, and it was reported in the same table as, and indistinguishably from, the eight registered contrasts. It is not restated here: adding a canonical G−A would be registering a contrast after seeing the data. Anyone wanting this comparison must either register it prospectively for a future run or report it explicitly labelled as a post-hoc, unregistered comparison with statistics regenerated by `aggregate.py`. |
| C−B described as "null"; `PRECOMMIT_PANEL.md`'s "quantization is neutral at this scale" | **Corrected to INCONCLUSIVE** | On the canonical run the confirmatory judges split +0.098 / −0.049 / −0.049. Sign disagreement is *inconclusive, not null*, under this project's own rule. The study is underpowered to establish quantization equivalence. |
| F−B −0.041 "null" | **Sign flipped between runs** | Canonically F−B is **+0.236**, all three confirmatory judges positive, none significant. Still not confirmed — but a small positive, not a small negative. |
| "Template frozen at hash `80c50ee9919e00db…`" | **Replaced** | Canonical combined hash `061b77993f5fdfd8…`, re-frozen 2026-09-06 (ANZCOR correction: back blows then chest thrusts, SO08 widened to any age). July and canonical used different rubrics; their scores are not comparable. |
| "judge_deepseek.py (~766 lines)" | **Replaced** | 1,251 lines. `aggregate.py` 975, `panel_verdict.py` 610, `assemble_items.py` 456, `make_controls.py` 503, `check_controls.py` 171. |
| "`config_summary.csv` — Mean ± SD quality score per config" | **Corrected** | There is no SD column. Score dispersion is in §6. |
| **"config names never appear in prompts"** | **CORRECTED — the claim was false** | `judging/prompt_quality.txt` contains a rubric clause headed *"T6 FALLBACK RESPONSE SCORING (Config E -- T6_IMPROVED only)"* and quotes config E's fallback verbatim. The runtime blinding guard and `self_review()` both search for the full name `E_T6_IMPROVED`, which is absent, so both pass while the config is named. 7 of 41 config-E answers contain the fallback and are identifiable. See §8. |
| Judge distribution "DeepSeek … Most lenient / GPT-5.6 … Harshest" | **Replaced** | Canonically `claude_or` is harshest (1.882) and `glm_ar` most lenient (2.258). |
| Gap-gate and quantization-probe PASS lines | **Removed** | July results quoted inside a registration file. Canonical controls are 45/45 for all four judges (§6); the quantization verdict is superseded (above). |

---

## 8. Disclosures required in the paper

Each item is evidenced in the repository; none is optional.

1. **The claude judge was substituted after most of the panel had run.**
   `claude-opus-4.8` was registered; `claude-opus-5` was used; the change was
   made 2026-09-08, by which time `deepseek`, `gpt_ar` and `glm_ar` were all
   664/664 and aggregated. The panel is therefore **not** fully pre-registered.
   `PRECOMMIT.md`, Amendment 2026-09-10 records this in full, including that
   `claude_or` is the harshest judge and that all three confirmed contrasts hold
   4/4 without it.
2. **Blinding is not complete.** The rubric names config E (§7). Per-item
   absolute scoring means position bias is structurally impossible — claim that
   — but not that config identity is fully concealed.
3. **The rubric encodes the training distribution.** It states *"training data
   median is 43 words"* — verified: `splits/10cat/train.json` answers have median
   exactly 43 — and caps EMS-led answers, which is the base model's dominant
   behaviour (A mentions EMS in 20/41 answers, G in 25/41, against B's 4/41).
   B−A therefore measures conformance to the offline-deployment target as much
   as independent clinical quality. The reference-compression alternative
   explanation **is excluded** by `OLDBANK_ABLATION` (below); this one is not.
4. **Score band 5 is empirically unreachable.** One score of 5 in 1,148
   real-config answers (0.09%), against 52/52 for the verbatim references. The
   instrument is effectively 0–4. Two of the seven score-5 specificity anchors
   appear **zero times** in the 4,441 training answers, so fine-tuning could not
   have taught them.
5. **Judge identity is asserted, not verified, on two of four judges.**
   AgentRouter is a reseller reporting every model as `owned_by: "custom"`, and
   the harness sends a coding-agent `User-Agent` to satisfy its client
   allowlist — a deliberate, disclosed workaround. In July the DeepSeek endpoint
   served `deepseek-v4-flash` on 582/582 calls against a registered `-pro`
   request, so this is a demonstrated risk, not a hypothetical one. It does
   **not** recur in the canonical run: all 664 rows report `deepseek-v4-pro`.
6. **The no-reasoning policy did not hold uniformly** (§6 and `PRECOMMIT.md`
   Amendment 2026-09-10 §5). Six of 1,148 quality scores were produced under a
   reasoning regime the registration forbids.
7. **Primary contrast 2 had no power** (§4).
8. **No safety hypothesis was pre-registered, and post-hoc safety tests are
   null** (§5).
9. **Evaluation is within-distribution.** Bank↔training question Jaccard has
   median 0.421 and maximum 0.706. Configs F and G retrieve the Jaccard-0.706
   training near-twin for **V2Q34** and **V2Q31** (confirmed from the canonical
   retrieval metadata; neither question is topic-gated), so 2 of 41 items in the
   RAG configs are handed a near-verbatim training answer. No generalisation
   claim to unseen scenarios is available.
10. **Train/test prompt shift.** The adapter was trained under
    `data_v2.SYSTEM_PROMPT` (sha `09f68afa…`, which instructs the model to advise
    EMS) and evaluated under `offline_definitive_v1` (sha `0d97fe5d…`, which
    forbids it). Measured as costless — 41.7 → 42.8 words, ROUGE-L +0.0016, EMS
    mentions 7 → 2, with the probe reproducing July's config B to the decimal —
    but real, measured on config B only, and on a weak proxy.
11. **Split leakage.** 9 verbatim (question, answer) pairs in `test` (1.6%) and 8
    in `val` (1.4%) also occur in `train`; 33 duplicate questions within `train`;
    `val_loss` is the early-stopping metric. The camera-ready bank has **zero**
    exact overlap with any split.
12. **The adapter weights and base model are not in the repository**
    (`.gitignore` excludes `*.safetensors` and `models/`), training-side
    provenance records no data hash or commit, and no determinism flags are set —
    so the published adapter is a binary artifact, not a reproducible recipe.
    Publish it by hash. Everything from `judgments.jsonl` forward **is** exactly
    reproducible with stdlib Python.
13. **Category-level claims are unsupportable.** Bank categories range from 1
    question (Spinal Injuries) to 7 (Bleeding & Wounds).
14. **n = 41 questions, one adapter, one seed, one base model.** No seed-variance
    estimate exists and retraining is frozen.

---

## 9. Supporting artifacts

| Artifact | What it establishes |
|---|---|
| `judging/PANEL_VERDICT_OFFLINE_FINAL.md` | The canonical panel verdict (§3). |
| `judging/panel_verdict.py` | Regenerates the verdict and cross-checks instrument identity across judges. |
| `judging/results/deepseek/OLDBANK_ABLATION/` | **Isolates the reference revision.** Identical offline answers and rubric, old references (median 102 words) instead of new (54): B−A moves +1.024 → +1.000. The reference compression accounts for 0.024 of the effect, so it does **not** explain B−A. Run for deepseek only — extending it to `claude_or` and `gpt_ar` (1,328 calls) is the highest-value optional spend available. |
| `evaluations/prompt_shift_probe.json`, `prompt_shift_probe.py` | The train/test prompt-shift measurement and its July control (disclosure 10). |
| `judging/PRECOMMIT.md` | The eight registered contrasts, the per-judge criterion, the 3-of-3 rule, and four dated amendments — three pre-run, one (2026-09-10) explicitly post-hoc. |
| `judging/PRECOMMIT_PANEL.md` | July registration. Carries July result verdicts; bannered 2026-09-10 as superseded. **Do not quote its numbers.** |
| `judging/prompt_quality.txt`, `prompt_safety.txt` | The instrument. **Publish both verbatim as an appendix.** |
| `judging/TEMPLATE_FROZEN_HASH.txt` | Freeze point and the ANZCOR re-freeze rationale. |
| `judging/blind_map.json` | Blind ID → config. Salted SHA-256, first 12 hex chars, uppercased. Blind IDs are **never interpolated into any prompt**; they exist only to key the results, so blinding does not depend on the map staying secret. `PRECOMMIT.md` nonetheless states the map is *"excluded from released artifacts until after de-anonymization"* — **note that publishing the salt is equivalent to publishing the map**: all 11 blind IDs were re-derived from the salt on 2026-09-10 (11/11 exact). Either publish both after de-anonymisation or withhold both; the salt is therefore not quoted here. |
| `judging/controls_key.json`, `make_controls.py` | The 45 planted controls and their expected bands. |
| `judging/OPEN_FINDINGS.md`, `FINDINGS_20260905.md`, `FINDINGS_CLOSED.md`, `DECISIONS.md` | Defect and decision registers, including a *Retracted claims* section. |
| `paper/NUMBER_FORENSICS.md` | Reconciles the corpus, SC-rate and EMS-first counts. Independently re-verified 2026-09-10; every figure correct. Preserve as a supplementary artifact. |
| `judging/results/*/CAMERA_READY_FINAL/`, branch `origin/july` | The retired July run. Its **`judgments.jsonl` files are untouched**, but the derived reports (`stats.csv`, `controls_report.md`, `FINAL_REPORT.md`) were **regenerated on 2026-09-05** at commit `1253760` when the control-key correction was re-aggregated — so the directory is not a frozen July snapshot. The **`origin/july` branch is** a frozen pre-session snapshot: tip `bac28cf`, and it does not contain the offline run. |

---

## 10. Verification — no API spend, no GPU

```
python judging/test_judging_harness.py                              # 21 offline contract tests
python judging/panel_verdict.py --run_tag OFFLINE_FINAL --check     # verdict not stale
for j in deepseek claude_or gpt_ar glm_ar; do
  python judging/check_controls.py --model $j --run_tag OFFLINE_FINAL   # expect 45/45 PASS
done
python camera_ready/check.py                                        # protocol / bank / rubric / tree
python verify_camera_ready.py --run_dir evaluations/CAMERA_READY_OFFLINE_20260905_204533

# Regenerate every report from committed judgments (install scipy first, see §6)
pip install scipy
for j in deepseek claude_or gpt_ar glm_ar; do
  python judging/aggregate.py --model $j --run_tag OFFLINE_FINAL
done
```

`verify_camera_ready.py` currently exits 3 on a fresh checkout: it resolves the
machine-absolute artifact paths recorded in `run.json`
(`C:\Personal_Endeavours\Fine_Tuning\…`), so it cannot verify on any other
machine — including for `splits/10cat/train.json`, which **is** present at the
repository-relative path. Make it fall back to a repo-relative path before
treating a failure as meaningful.
