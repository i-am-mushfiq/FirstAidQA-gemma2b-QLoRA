# Multi-Judge Panel Precommit
## Committed: 2026-07-11

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
