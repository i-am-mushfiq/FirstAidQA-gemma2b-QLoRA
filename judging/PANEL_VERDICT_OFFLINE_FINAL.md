# Panel verdict — `OFFLINE_FINAL`

**Generated:** 2026-09-10 by `judging/panel_verdict.py` from the committed per-judge `stats.csv` files.
**Status:** machine-applied, awaiting human sign-off (see *Sign-off* at the end).

This file exists because the cross-judge confirmation rule is applied by hand by design (`DECISIONS.md`, 2026-09-05) and `aggregate.py` is single-judge by construction. It was applied to the July run in `judging/OPEN_FINDINGS.md` §2 and **had never been applied to this run**, which left the canonical experiment without a written headline verdict. Regenerate with:

```
python judging/panel_verdict.py --run_tag OFFLINE_FINAL
```

---

## Panel

| Role | Judge | Registered | Requested | Served (all rows) | Calls | INVALID |
|---|---|---|---|---|---|---|
| confirmatory | `deepseek` | `deepseek-v4-pro` | `deepseek-v4-pro` | `deepseek-v4-pro` | 664 | 0 |
| confirmatory | `claude_or` | `anthropic/claude-opus-4.8`<br>*amended to* `anthropic/claude-opus-5` | `anthropic/claude-opus-5` | `anthropic/claude-opus-5` | 664 | 0 |
| confirmatory | `gpt_ar` | `gpt-5.6-sol` | `gpt-5.6-sol` | `gpt-5.6-sol` | 664 | 0 |
| exploratory | `glm_ar` | `glm-5.3` | `glm-5.3` | `glm-5.3` | 664 | 0 |

**Instrument, identical across all judges** — verified by this script, not asserted:

- `template_hash` = `061b77993f5fdfd8686b661e972613a36c5e309b7a11535ddff990b3cf88d771`
- `quality_hash` = `865fc1149fda1714e9bfa6ba775db75e6e949c7e2c8269af7ba4511723bebbf2`
- `safety_hash` = `36e3a7ba2795d9b0209e53ce638d211a37d6ac22c11fe861d43525a9b5672360`
- `bank_sha256` = `8dfb4f4b221426c7ff597abe4bcb87e4d74f9c7d23510ea6fd9c442f1c4a18b0`
- `n_calls_total` = 664 per judge, `n_invalid` = 0 for every judge
- identical contrast names, order and `n_pairs` in every `stats.csv`
- `items.jsonl` still matches the bank recorded in the judgments

### Deviations to disclose

- claude_or: registered `anthropic/claude-opus-4.8`, run used `anthropic/claude-opus-5` under PRECOMMIT.md, Amendment 2026-09-10 -- POST-HOC, written after the run finished

## The rule, as registered

> A contrast is confirmed only when all three confirmatory judges agree in direction and each is independently significant by the rule above. Judges disagreeing on sign makes a contrast *inconclusive*, not null.
>
> — `judging/PRECOMMIT.md`, *Panel composition*

Per-judge significance is `bootstrap 95% CI excludes zero AND two-sided sign test p < 0.05`, read from each `stats.csv`'s `confirmed` column rather than recomputed here, so this file cannot disagree with the artifacts it summarises. `glm_ar` is the registered exploratory fourth judge and **never gates** a verdict.

**UNTESTABLE** marks a contrast where the registered sign test could not have reached p<0.05 at the observed tie count, whichever way the non-tied pairs fell. That is arithmetic about the registered test, not a substitute for it: with 4 non-tied pairs the smallest attainable two-sided p is 0.125, with 5 it is 0.0625, and 6 all-one-way is the minimum for significance. A contrast can only move from NO ESTABLISHED EFFECT to UNTESTABLE by this label, never to CONFIRMED.

## Verdicts

| # | Contrast | Pri | Panel mean Δ | Confirmatory spread | 3/3 dir | 3/3 sig | `glm_ar` (expl.) | Verdict |
|---|---|---|---|---|---|---|---|---|
| 1 | F−B overall | ★ | **+0.2358** | +0.171 … +0.268 | Yes | No | -0.024 ns | **NO ESTABLISHED EFFECT** |
| 2 | F−B SC | ★ | **+0.2424** | +0.091 … +0.364 | Yes | No | +0.000 ns | **UNTESTABLE** |
| 3 | B−A overall | ★ | **+1.0894** | +0.902 … +1.341 | Yes | **Yes** | +1.049 sig | **CONFIRMED** |
| 4 | E−B overall | — | **-0.0325** | -0.073 … +0.024 | **No** | No | +0.000 ns | **INCONCLUSIVE** |
| 5 | C−B overall | — | **+0.0000** | -0.049 … +0.098 | **No** | No | -0.098 ns | **INCONCLUSIVE** |
| 6 | G−B overall | — | **-0.9512** | -1.293 … -0.683 | Yes | **Yes** | -0.951 sig | **CONFIRMED** |
| 7 | G−F overall | — | **-1.1870** | -1.463 … -0.951 | Yes | **Yes** | -0.927 sig | **CONFIRMED** |
| 8 | D−B overall | — | **+0.0244** | +0.000 … +0.049 | **No** | No | +0.000 ns | **INCONCLUSIVE** |

★ = primary pre-registered contrast. Panel mean is the arithmetic mean of the three confirmatory judges — the same aggregation used for the July figures, which reconcile exactly as three-judge means (`judging/OPEN_FINDINGS.md` §2). **Report the panel mean with the spread visible, never the mean alone.**

## Per-judge detail

### 1. F−B overall ★ primary

| Judge | Role | n | Mean Δ | 95% CI | W/L/T | Sign p | Min attainable p | Sig? |
|---|---|---|---|---|---|---|---|---|
| `deepseek` | confirmatory | 41 | +0.2683 | [+0.0000, +0.5366] | 16/7/18 | 0.093140 | 0.0000 | no |
| `claude_or` | confirmatory | 41 | +0.1707 | [-0.0732, +0.4390] | 14/8/19 | 0.286279 | 0.0000 | no |
| `gpt_ar` | confirmatory | 41 | +0.2683 | [+0.0000, +0.5366] | 14/6/21 | 0.115318 | 0.0000 | no |
| `glm_ar` | exploratory | 41 | -0.0244 | [-0.2683, +0.2439] | 8/12/21 | 0.503445 | 0.0000 | no |

**Verdict: NO ESTABLISHED EFFECT** — direction agrees; 0 of 3 confirmatory judges significant. NOT an equivalence result -- the registered rule sets no margin. Report the estimate and CI.

### 2. F−B SC ★ primary

| Judge | Role | n | Mean Δ | 95% CI | W/L/T | Sign p | Min attainable p | Sig? |
|---|---|---|---|---|---|---|---|---|
| `deepseek` | confirmatory | 11 | +0.3636 | [-0.0909, +1.0000] | 3/1/7 | 0.625000 | 0.1250 **(no power)** | no |
| `claude_or` | confirmatory | 11 | +0.0909 | [-0.4545, +0.6364] | 3/2/6 | 1.000000 | 0.0625 **(no power)** | no |
| `gpt_ar` | confirmatory | 11 | +0.2727 | [-0.0909, +0.6364] | 4/1/6 | 0.375000 | 0.0625 **(no power)** | no |
| `glm_ar` | exploratory | 11 | +0.0000 | [-0.3636, +0.3636] | 2/2/7 | 1.000000 | 0.1250 **(no power)** | no |

**Verdict: UNTESTABLE** — direction agrees but the registered sign test could not reach p<0.05 at the observed tie count for: deepseek, claude_or, gpt_ar.

### 3. B−A overall ★ primary

| Judge | Role | n | Mean Δ | 95% CI | W/L/T | Sign p | Min attainable p | Sig? |
|---|---|---|---|---|---|---|---|---|
| `deepseek` | confirmatory | 41 | +1.0244 | [+0.6341, +1.4146] | 26/3/12 | 0.000015 | 0.0000 | **yes** |
| `claude_or` | confirmatory | 41 | +1.3415 | [+0.9268, +1.7561] | 31/4/6 | 0.000003 | 0.0000 | **yes** |
| `gpt_ar` | confirmatory | 41 | +0.9024 | [+0.5854, +1.2439] | 26/4/11 | 0.000059 | 0.0000 | **yes** |
| `glm_ar` | exploratory | 41 | +1.0488 | [+0.6585, +1.4146] | 30/3/8 | 0.000001 | 0.0000 | **yes** |

**Verdict: CONFIRMED** — 3/3 direction (positive) and 3/3 significant.

### 4. E−B overall (exploratory contrast)

| Judge | Role | n | Mean Δ | 95% CI | W/L/T | Sign p | Min attainable p | Sig? |
|---|---|---|---|---|---|---|---|---|
| `deepseek` | confirmatory | 41 | -0.0732 | [-0.2683, +0.1220] | 6/8/27 | 0.790527 | 0.0001 | no |
| `claude_or` | confirmatory | 41 | -0.0488 | [-0.1951, +0.0732] | 2/3/36 | 1.000000 | 0.0625 **(no power)** | no |
| `gpt_ar` | confirmatory | 41 | +0.0244 | [-0.2195, +0.2439] | 10/7/24 | 0.629059 | 0.0000 | no |
| `glm_ar` | exploratory | 41 | +0.0000 | [-0.1951, +0.1951] | 8/7/26 | 1.000000 | 0.0001 | no |

**Verdict: INCONCLUSIVE** — confirmatory judges disagree on sign; underpowered for claude_or.

### 5. C−B overall (exploratory contrast)

| Judge | Role | n | Mean Δ | 95% CI | W/L/T | Sign p | Min attainable p | Sig? |
|---|---|---|---|---|---|---|---|---|
| `deepseek` | confirmatory | 41 | +0.0976 | [-0.1951, +0.3902] | 11/8/22 | 0.647606 | 0.0000 | no |
| `claude_or` | confirmatory | 41 | -0.0488 | [-0.2927, +0.2195] | 9/12/20 | 0.663624 | 0.0000 | no |
| `gpt_ar` | confirmatory | 41 | -0.0488 | [-0.3171, +0.2195] | 10/10/21 | 1.000000 | 0.0000 | no |
| `glm_ar` | exploratory | 41 | -0.0976 | [-0.4146, +0.2195] | 10/12/19 | 0.831812 | 0.0000 | no |

**Verdict: INCONCLUSIVE** — confirmatory judges disagree on sign.

### 6. G−B overall (exploratory contrast)

| Judge | Role | n | Mean Δ | 95% CI | W/L/T | Sign p | Min attainable p | Sig? |
|---|---|---|---|---|---|---|---|---|
| `deepseek` | confirmatory | 41 | -0.8780 | [-1.2195, -0.5610] | 3/24/14 | 0.000049 | 0.0000 | **yes** |
| `claude_or` | confirmatory | 41 | -1.2927 | [-1.6829, -0.9024] | 4/33/4 | 0.000001 | 0.0000 | **yes** |
| `gpt_ar` | confirmatory | 41 | -0.6829 | [-1.0732, -0.2927] | 6/22/13 | 0.003719 | 0.0000 | **yes** |
| `glm_ar` | exploratory | 41 | -0.9512 | [-1.2683, -0.6341] | 4/30/7 | 0.000006 | 0.0000 | **yes** |

**Verdict: CONFIRMED** — 3/3 direction (negative) and 3/3 significant.

### 7. G−F overall (exploratory contrast)

| Judge | Role | n | Mean Δ | 95% CI | W/L/T | Sign p | Min attainable p | Sig? |
|---|---|---|---|---|---|---|---|---|
| `deepseek` | confirmatory | 41 | -1.1463 | [-1.4634, -0.8537] | 1/30/10 | 0.000000 | 0.0000 | **yes** |
| `claude_or` | confirmatory | 41 | -1.4634 | [-1.7805, -1.1463] | 3/34/4 | 0.000000 | 0.0000 | **yes** |
| `gpt_ar` | confirmatory | 41 | -0.9512 | [-1.2683, -0.6341] | 3/29/9 | 0.000003 | 0.0000 | **yes** |
| `glm_ar` | exploratory | 41 | -0.9268 | [-1.1463, -0.7073] | 0/29/12 | 0.000000 | 0.0000 | **yes** |

**Verdict: CONFIRMED** — 3/3 direction (negative) and 3/3 significant.

### 8. D−B overall (exploratory contrast)

| Judge | Role | n | Mean Δ | 95% CI | W/L/T | Sign p | Min attainable p | Sig? |
|---|---|---|---|---|---|---|---|---|
| `deepseek` | confirmatory | 41 | +0.0488 | [-0.1951, +0.2927] | 7/5/29 | 0.774414 | 0.0005 | no |
| `claude_or` | confirmatory | 41 | +0.0244 | [-0.2195, +0.2683] | 8/6/27 | 0.790527 | 0.0001 | no |
| `gpt_ar` | confirmatory | 41 | +0.0000 | [-0.2195, +0.2195] | 7/7/27 | 1.000000 | 0.0001 | no |
| `glm_ar` | exploratory | 41 | +0.0000 | [-0.1951, +0.1951] | 6/6/29 | 1.000000 | 0.0005 | no |

**Verdict: INCONCLUSIVE** — confirmatory judges disagree on sign (gpt_ar exactly zero).

## How to report these

| Verdict | Means | Wording for the paper |
|---|---|---|
| CONFIRMED | All three confirmatory judges agree in direction and each is independently significant | May be stated as a finding. Give the panel mean, the per-judge spread and the effect size, not the p-value alone. |
| NO ESTABLISHED EFFECT | Direction agrees; not all confirmatory judges reach the threshold; the test had the power to | "No statistically established effect under the pre-registered criterion." Give the estimate and CI. **Not** an equivalence result: the registered rule sets no margin, so this never licenses "X does not help". Do not upgrade on directional agreement alone either. |
| INCONCLUSIVE | Confirmatory judges disagree on sign | "Inconclusive: the panel disagreed on sign." **Not** a null result, and not evidence of equivalence. |
| UNTESTABLE | The registered test could not reach significance at the observed tie count | "The pre-registered test had no power to decide this contrast." Report the tie count and the attainable minimum p. |
| NOT RUN | A confirmatory judge has no judgments | Nothing may be said. |

## Sign-off

The rule is registered as a human step. This file is the machine's application of it; committing it is the human's ratification. Before committing, confirm:

- [ ] The **Panel** table's *Served* column is the panel you intend to publish, and every row under *Deviations to disclose* is stated in the paper.
- [ ] No `stats.csv` in this run was regenerated after the judgments were inspected.
- [ ] Verdicts here match the per-judge `FINAL_REPORT.md` files, which report the per-judge criterion only.
- [ ] Exploratory contrasts are labelled exploratory wherever they appear, however clean they look.

Signed: ______________________  Date: ____________
