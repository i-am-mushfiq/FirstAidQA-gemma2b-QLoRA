# Multi-Judge Panel Precommit
## Committed: 2026-07-11

> **READ FIRST — added 2026-09-10.**
>
> This file is a **July registration** and the result figures in it are **July
> results**, computed on `CAMERA_READY_FINAL` (582 calls, 3 judges, the
> pre-ANZCOR rubric and the pre-compression reference bank). That run was
> retired on 2026-09-05 (`DECISIONS.md`, "The aligned offline run will SUPERSEDE
> the July run"). **Do not quote any number below in the paper.**
>
> Three specific corrections, so the stale figures are not merely unmarked but
> contradicted where they are wrong:
>
> - *"§6.2 verdict: quantization is neutral at this scale"* — **does not hold.**
>   On the canonical run C−B is **inconclusive**: the confirmatory judges split
>   +0.098 / −0.049 / −0.049, and sign disagreement is inconclusive rather than
>   null under this project's own rule. The study is underpowered to establish
>   quantization equivalence.
> - *"B−A = +0.902 CONFIRMED"*, *"G−B = −1.024"* — superseded. Canonical panel
>   means are **B−A +1.089** and **G−B −0.951**.
> - The panel table below lists `anthropic/claude-opus-4.8`. The canonical run
>   used `anthropic/claude-opus-5`; see `PRECOMMIT.md`, Amendment 2026-09-10.
>
> A registration file should not carry result verdicts at all. The canonical
> verdicts live in **`judging/PANEL_VERDICT_OFFLINE_FINAL.md`**, generated from
> the committed `stats.csv` files by `judging/panel_verdict.py`. The rule stated
> in this file (3/3 direction, Gemini exclusion, controls-first) is unchanged and
> still governs.

### Active panel (3 judges)
| Handle     | Model string                  | API           | Key env var          |
|------------|-------------------------------|---------------|----------------------|
| deepseek   | deepseek-v4-pro               | api.deepseek.com | DEEPSEEK_API_KEY  |
| claude_or  | anthropic/claude-opus-4.8     | OpenRouter    | OPENROUTER_API_KEY   |
| gpt        | openai/gpt-5.6-sol            | OpenRouter    | OPENROUTER_API_KEY   |

### 3/3 direction rule
A contrast is reported as confirmed only if all three judges independently
show the same direction (all positive or all negative). Any judge disagreeing
on direction → the contrast is reported as inconclusive.

### Gemini exclusion (principled)
google/gemini-3-pro-image excluded: Gemini and Gemma share the same model
family and training lineage (Google DeepMind). Using Gemini to evaluate
Gemma outputs risks systematic bias in favour of the subject model's
architectural choices. The exclusion is pre-registered here before any
real results were examined.

### Gap-gate audit (Hard condition 1) — PASSED 2026-07-11
- Run: CAMERA_READY_FINAL (DeepSeek V4 Pro)
- 582/582 calls complete, 0 INVALID, 0 missing
- All 6 real configs: 82/82 each ✓
- All 4 control configs: complete ✓
- Template hash: 80c50ee9919e00db...

### Quantization positioning probe (Soft condition 4) — COMPLETE 2026-07-11
- C−B = +0.122, 95% CI [−0.098, +0.341], p=0.319 → NULL
- §6.2 verdict: quantization is neutral at this scale
- 4-bit adapter is the deployment recommendation
- B−A = +0.902 CONFIRMED [+0.512, +1.268] p≈0 (fine-tuning works)
- G−B = −1.024 CONFIRMED [−1.463, −0.585] p≈0 (base+RAG < fine-tuned)

### Controls-first protocol (Hard condition 2)
Before each new judge's full 582-call run:
  1. Run --controls_only (~90 calls)
  2. Verify controls report passes gate (expected pattern confirmed)
  3. Then run remaining ~492 real items

### Sequence
1. deepseek  — DONE (CAMERA_READY_FINAL, 582/582)
2. claude_or — PENDING (controls first, then full run)
3. gpt       — PENDING (controls first, then full run)
