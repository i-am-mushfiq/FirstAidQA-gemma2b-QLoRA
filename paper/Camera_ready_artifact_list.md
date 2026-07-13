# Camera-Ready Artifact List

**Project:** Gemma 2B Instruct QLoRA Fine-Tuning — Offline Android First-Aid Assistant
**Run tag:** `CAMERA_READY_FINAL`
**Judges:** DeepSeek V4 Pro · Claude Opus 4.8 · GPT-5.6
**Date:** 2026-07-14

---

## Per-Judge Result Directories

Each of the three judge directories contains the identical file structure below.

### 1. `judging/results/deepseek/CAMERA_READY_FINAL/judgments.jsonl`
582 raw judgments (291 items × 2 prompt types: quality + safety). 0 INVALID. Ground-truth scoring record for DeepSeek V4 Pro.

### 2. `judging/results/deepseek/CAMERA_READY_FINAL/manifest.json`
Run metadata: model string, template hash, timestamp, item count, concurrency settings.

### 3. `judging/results/deepseek/CAMERA_READY_FINAL/FINAL_REPORT.md`
Per-config quality summary narrative for DeepSeek.

### 4. `judging/results/deepseek/CAMERA_READY_FINAL/config_summary.csv`
Mean ± SD quality score per config (A–G) for DeepSeek.

### 5. `judging/results/deepseek/CAMERA_READY_FINAL/controls_report.md`
Control item pass/fail audit (CTRL_REF, CTRL_EMS, CTRL_VAGUE, CTRL_DANGER) for DeepSeek.

### 6. `judging/results/deepseek/CAMERA_READY_FINAL/reliability_report.md`
Inter-item consistency metrics for DeepSeek.

### 7. `judging/results/deepseek/CAMERA_READY_FINAL/scores_per_question.csv`
Item-level scores (per qid × config) for DeepSeek.

### 8. `judging/results/deepseek/CAMERA_READY_FINAL/stats.csv`
Aggregate statistics for DeepSeek.

### 9. `judging/results/claude_or/CAMERA_READY_FINAL/`
Same 8 files (items 1–8) for Claude Opus 4.8 via OpenRouter. 582/582, 0 INVALID.

### 10. `judging/results/gpt/CAMERA_READY_FINAL/`
Same 8 files (items 1–8) for GPT-5.6 via OpenRouter. 582/582, 0 INVALID.

---

## Judging Infrastructure Files

### 11. `judging/PRECOMMIT_PANEL.md`
Pre-registered panel document. Pins 3 model strings, defines the 3/3 direction rule, records Gemini exclusion rationale (same model family as subject), gap-gate PASS result, and quantization probe result (C−B null, p=0.319).

### 12. `judging/PRECOMMIT.md`
Earlier precommit checkpoint (controls-first sequence and gap-gate audit).

### 13. `judging/PROMPT_ITERATION_LOG.md`
Full prompt iteration history. Template frozen at hash `80c50ee9919e00db...` after controls-only validation. No prompt changes after first real-config scores were observed.

### 14. `judging/TEMPLATE_FROZEN_HASH.txt`
Single-line file recording the frozen template SHA256 hash for integrity verification.

### 15. `judging/blind_map.json`
Salted SHA256 blind ID → config name mapping. Salt: `first_aid_v2_judging_2026`. Opaque IDs (e.g. `BID_A3F9...`) used in all judge prompts; config names never appear in prompts.

### 16. `judging/aggregate.py`
Aggregation script used to produce per-judge report files and 3-judge bootstrap CI results.

### 17. `judging/judge_deepseek.py`
Main judging harness (~766 lines). Handles all three judges, caching, blinding, INVALID suppression, fence-stripping, and ThreadPoolExecutor concurrency.

---

## Key Results Summary

| Comparison | Δ mean | 95% CI | p-value | Finding |
|---|---|---|---|---|
| B − A (fine-tuned vs base) | +0.756 | [+0.496, +1.016] | ≈0 | **CONFIRMED** |
| G − B (base+RAG vs fine-tuned) | −0.992 | [−1.244, −0.732] | ≈0 | **CONFIRMED** |
| C − B (8-bit vs 4-bit quant) | +0.073 | [−0.138, +0.285] | 0.53 | null |
| E − B (T6 prompt vs base FT) | −0.008 | [−0.228, +0.220] | 0.999 | null |
| F − B (RAG+FT vs FT alone) | −0.041 | [−0.285, +0.203] | 0.76 | null |
| G − A (base+RAG vs bare base) | −0.236 | [−0.520, +0.057] | 0.115 | null |

Bootstrap CI: 10,000 resamples, seed=2026, paired differences.

### Config Key
| ID | Description |
|---|---|
| A | BASE_4BIT — base Gemma 2B, 4-bit quantized, no fine-tuning |
| B | FINETUNED_4BIT — QLoRA fine-tuned, 4-bit NF4 (primary deliverable) |
| C | FINETUNED_8BIT — QLoRA fine-tuned, 8-bit quantized |
| E | T6_IMPROVED — fine-tuned + improved T6 system prompt |
| F | RAG_BM25 — fine-tuned + BM25 retrieval augmentation |
| G | BASE_RAG — base model + BM25 retrieval (no fine-tuning) |

### Judge Distribution Summary
| Judge | Mean | SD | Mode | Harshest-scorer rank |
|---|---|---|---|---|
| DeepSeek V4 Pro | 2.114 | 1.112 | 2 | Most lenient |
| Claude Opus 4.8 | 2.037 | 1.031 | 2 | Middle |
| GPT-5.6 | 1.720 | 1.080 | 2 | Harshest |
