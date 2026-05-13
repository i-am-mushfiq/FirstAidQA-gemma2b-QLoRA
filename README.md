# Offline Medical First-Aid Response via QLoRA Fine-Tuning of Gemma 2B Instruct

Fine-tuning Google's **Gemma 2B Instruct** model using **QLoRA (Quantised Low-Rank Adaptation)** to produce a compact, offline-capable assistant for emergency first-aid guidance. The goal is a model that runs entirely on a consumer device — no internet, no cloud — and gives accurate, step-by-step procedural answers for life-threatening and non-life-threatening first-aid scenarios.

> **Status:** Research complete (May 2026). Final adapter confirmed: `10cat_4bit_r16_lr1e-4_p3_v2_20260508_054337`. 8-bit trialled and rejected — systematic dangerous positioning heuristic discovered on 40-question evaluation. Enhanced inference (T4/T6) trialled and rejected — active harm at 2B scale. T5 (RAG) is the remaining high-priority experiment.

---

## Table of Contents

1. [Motivation](#1-motivation)
2. [Project Overview](#2-project-overview)
3. [Repository Structure](#3-repository-structure)
4. [Hardware Requirements](#4-hardware-requirements)
5. [Setup and Installation](#5-setup-and-installation)
6. [Dataset](#6-dataset)
7. [Training Pipeline](#7-training-pipeline)
8. [Evaluation Pipeline](#8-evaluation-pipeline)
9. [Experimental History and Key Findings](#9-experimental-history-and-key-findings)
10. [Pipeline v2 — What Changed and Why](#10-pipeline-v2--what-changed-and-why)
11. [Results Summary](#11-results-summary)
12. [Script Reference](#12-script-reference)
13. [Roadmap](#13-roadmap)
14. [Citation](#14-citation)
15. [Licence](#15-licence)

---

## 1. Motivation

Standard large language models require internet connectivity and cloud compute. In mass-casualty events, remote wilderness settings, offshore environments, or infrastructure failures, neither is guaranteed. An offline first-aid assistant running locally on a consumer device — phone, laptop, or ruggedised tablet — could provide reliable procedural guidance when professional medical help is unreachable.

Gemma 2B Instruct in 4-bit NF4 quantisation occupies approximately 1.5 GB of storage and requires around 10 GB of VRAM during LoRA fine-tuning, making it a realistic target for on-device deployment. The core research question is: how much domain-specific capability can be added to a 2B-parameter model through parameter-efficient fine-tuning, and does that capability survive aggressive quantisation?

The focus is strictly procedural first aid — step-by-step instructions for the first five to fifteen minutes of an emergency before professional help arrives. The model is not intended to diagnose or replace medical professionals. Its value is in the gap between incident and intervention.

---

## 2. Project Overview

### Architecture

- **Base model:** Google Gemma 2B Instruct (`google/gemma-2b-it`)
- **Adaptation method:** LoRA (Low-Rank Adaptation) via PEFT
- **Quantisation:** 4-bit NF4 (QLoRA) using BitsAndBytes — primary experiments
- **Planned:** 8-bit INT8 and FP16 full-precision LoRA for precision ablation
- **LoRA targets:** All 7 projection layers — `q_proj, k_proj, v_proj, o_proj` (attention) + `gate_proj, up_proj, down_proj` (FFN)
- **LoRA rank / alpha:** r=16, α=32 (best validated configuration)
- **Loss masking:** Answer-only — instruction and system prompt tokens masked to -100
- **Template:** Gemma instruct chat format via `tokenizer.apply_chat_template()` (v2+)

### Key design decisions

**Why Gemma 2B?** It is small enough to train on a single consumer GPU (12 GB VRAM) and deploy on a mid-range mobile device, while large enough to produce coherent procedural text. The 2B instruct variant has already undergone RLHF alignment, so LoRA updates only need to steer existing capabilities toward the medical domain rather than teaching instruction following from scratch.

**Why QLoRA?** The target hardware for both training and deployment is resource-constrained. 4-bit NF4 quantisation reduces the base model's memory footprint from ~5 GB (FP16) to ~1.5 GB, making training feasible on GPUs with 10–12 GB VRAM. The cost is increased quantisation noise in the gradient signal, which motivated the precision ablation study across 4-bit, 8-bit, and FP16.

**Why LoRA on all 7 modules including FFN?** Standard tutorials target only attention projections. However, this task requires the model to generate different *content* — structured procedural steps, clinical quantities, conditional escalation logic — not merely attend differently to input tokens. This content generation capability resides primarily in FFN layers. Targeting all 7 modules increases trainable parameter count by approximately 3× versus attention-only, with negligible inference overhead since adapters are merged before deployment.

**Why answer-only loss masking?** Computing loss over the full sequence (question + answer) wastes roughly 35–40% of gradient signal on predicting the question tokens, which the model already sees in context. Masking instruction tokens to -100 ensures every gradient step is driven entirely by the quality of the answer.

---

## 3. Repository Structure

```
Fine_Tuning/
│
├── data/                                    # Raw and enriched datasets
│   ├── firstaidqa_v1.json                   # Source dataset: 5,550 Q&A pairs
│   ├── firstaidqa_v1_enriched.json          # + NLI category and SC labels
│   ├── firstaidqa_v1_enriched_10cat.json    # 10-category scheme
│   ├── firstaidqa_v1_enriched_threshold020.json  # High-confidence subset
│   └── eval_questions_30.json               # 30 held-out evaluation questions
│
├── splits/                                  # Stratified train / val / test splits
│   ├── 10cat/                               # PRIMARY — used for all training
│   │   ├── train.json                       # 4,441 samples
│   │   ├── val.json                         # 556 samples
│   │   └── test.json                        # 553 samples (LOCKED — never train on)
│   ├── baseline/                            # Original unfiltered split
│   └── thresh020/                           # NLI confidence ≥ 0.20 subset
│
├── experiments/                             # One directory per training run
│   ├── <tag>_<quant>_r<r>_lr<lr>_p<pat>[_v2]_<timestamp>/
│   │   ├── adapter/                         # Saved LoRA weights + tokenizer
│   │   ├── training_curve.json              # Loss history + all hyperparameters
│   │   └── train.log / v2_baseline.log      # Full training stdout
│   ├── profile[1-5]_*.log                   # Logs from 5-profile batch run
│   └── _v1_archive/                         # Early unstructured experiments
│
├── evaluations/                             # One directory per eval run
│   └── eval_<YYYYMMDD>_<HHMMSS>/
│       ├── run.json                         # All model answers + timing metadata
│       ├── metrics.json                     # Aggregate ROUGE / performance metrics
│       ├── llm_judge_prompt.txt             # Ready-to-paste judge prompt
│       └── [judge_name].md                  # Individual judge score sheets
│
├── paper/                                   # Paper drafts and supporting materials
│   ├── QLoRA_Experiment_Report.docx         # Full findings report with tables
│   ├── Model_Behaviour_Report.docx
│   ├── V2_PIPELINE.md
│   └── notes/                               # LLM judge prompts, second opinions
│
├── utils/                                   # Helper and diagnostic scripts
│   ├── download_model.py                    # Cache Gemma 2B locally
│   ├── inference.py                         # Before/after inference comparison
│   ├── compare_quant.py                     # Cross-quantisation benchmark
│   ├── compare_models.py                    # Side-by-side model comparison
│   ├── classify_10cat.py                    # Standalone NLI classifier
│   ├── infer_fp16.py                        # FP16 inference helper
│   ├── reload_adapter_example.py            # Minimal adapter reload demo
│   ├── run_pipeline.sh                      # End-to-end Linux/WSL runner
│   └── eval_suite_v1.py                     # Legacy evaluation script
│
├── data.py                                  # v1 data pipeline (manual template)
├── data_v2.py                               # v2 data pipeline (apply_chat_template)
├── train.py                                 # v1 training script
├── train_v2.py                              # v2 training script (uses data_v2)
├── eval_suite.py                            # Multi-adapter evaluation runner
├── evaluate.py                              # ROUGE + metric aggregation
├── build_llm_judge_prompt.py               # Generates LLM judge prompt
├── verify_masking.py                        # Label masking sanity check
├── verify_template_v1.py                    # Template alignment diagnostic
├── garbage_audit.py                         # Dataset quality audit
├── run_profiles.ps1                         # PowerShell: 5-profile batch run
├── run_v2_baseline.ps1                      # PowerShell: corrected baseline run
├── requirements.txt
├── .gitignore
└── README.md
```

---

## 4. Hardware Requirements

| Quantisation | Min VRAM | Observed peak (Gemma 2B, batch=2, grad_accum=4) |
|---|---|---|
| 4-bit QLoRA | 10 GB | **9.8 GB** |
| 8-bit LoRA | ~14 GB | ~13–14 GB (estimated) |
| FP16 LoRA | ~24 GB | ~22–24 GB (estimated) |

All experiments to date used a single NVIDIA GPU at 12 GB VRAM under 4-bit QLoRA. Only one training run can occupy the GPU at a time — `run_profiles.ps1` runs the five profiles sequentially for this reason.

---

## 5. Setup and Installation

### 1. Create the environment

```bash
conda create -n fine_tuning python=3.11 -y
conda activate fine_tuning
pip install -r requirements.txt
```

`bitsandbytes` requires **CUDA 11.8 or later**. For CPU-only machines, remove it from `requirements.txt` and use `--quant fp16`.

### 2. Accept Gemma model terms

Gemma 2B is a gated model. Accept the licence at [huggingface.co/google/gemma-2b-it](https://huggingface.co/google/gemma-2b-it), then authenticate:

```bash
huggingface-cli login
```

### 3. Cache the model locally (strongly recommended)

```bash
python utils/download_model.py
# Saves to: models/gemma-2b-it/
```

Downloading at training time is unreliable over slow connections. All scripts resolve `models/gemma-2b-it/` first and fall back to HF Hub only if the local copy is absent.

### 4. Generate dataset splits

```bash
python data.py
# Produces: splits/10cat/train.json, val.json, test.json
```

The test split is locked — it is never used during training or hyperparameter search.

---

## 6. Dataset

### Source

`data/firstaidqa_v1.json` — 5,550 question–answer pairs covering emergency and general first-aid procedures. Questions span scenario-based, factual, and procedural framings across 10 clinical topic categories.

### Enrichment

Each sample is enriched with:
- **category** — assigned by zero-shot NLI using `cross-encoder/nli-deberta-v3-small`
- **question_type** — What / How / Why / When / Can-Is-Should / Other
- **safety_critical** — boolean flag for AHA time-sensitive emergencies (CPR, choking, anaphylaxis, severe bleeding, shock, spinal injury)
- **template_idx** — 0–3, cycles through four question framings for training diversity

### 10-category split (primary)

| Category | Train | Val | Test | SC |
|---|---|---|---|---|
| Bleeding & Wounds | 827 | 104 | 103 | |
| Cardiac & Resuscitation | 698 | 88 | 87 | **YES** |
| Minor Injuries & General | 512 | 64 | 64 | |
| Trauma & Musculoskeletal | 510 | 64 | 64 | |
| Neurological & Altered Consciousness | 478 | 60 | 59 | **YES** |
| Airway, Choking & Drowning | 444 | 56 | 55 | **YES** |
| Bites, Stings & Envenomation | 329 | 41 | 41 | |
| Burns & Environmental | 317 | 40 | 40 | |
| Poisoning, Overdose & Toxic | 208 | 26 | 26 | |
| Spinal Injuries & Movement | 118 | 13 | 14 | **YES** |
| **Total** | **4,441** | **556** | **553** | |

Safety-critical samples in train: **997 / 4,441 (22.4%)**

### Sequence length (full audit — May 2026)

| Metric | Estimated tokens |
|---|---|
| Median (full sequence) | 168 |
| 90th percentile | 190 |
| 99th percentile | 214 |
| Maximum | 314 |

`max_length=512` (v2 default) covers 100% of the dataset with a 63% buffer for future growth. The original `max_length=320` also covered 100% — no truncation of safety escalation content was occurring in any prior run.

### Instruction templates

Training examples rotate across four question framings for lexical diversity. Val and test always use template 0 for consistent evaluation.

| Index | Framing |
|---|---|
| 0 | `Question: {q}` — canonical |
| 1 | `A patient asks: {q}` |
| 2 | `Emergency situation: {q}` |
| 3 | `{q}` — direct |

---

## 7. Training Pipeline

### v2 (current — use for all new runs)

```bash
conda activate fine_tuning
python train_v2.py \
  --quant 4bit \
  --model_path models/gemma-2b-it \
  --splits_dir splits/10cat \
  --splits_tag 10cat \
  --lora_r 16 --lora_alpha 32 \
  --lr 1e-4 --patience 3 --seed 42
```

Or via PowerShell runner:

```powershell
conda activate fine_tuning
.\run_v2_baseline.ps1
```

### v1 (preserved for reproducibility)

`train.py` and `data.py` are intentionally unchanged. All prior experiment results remain fully reproducible.

### Training argument reference

| Argument | v1 default | v2 default | Description |
|---|---|---|---|
| `--quant` | `4bit` | `4bit` | `4bit` / `8bit` / `fp16` |
| `--model_path` | _(HF Hub)_ | _(HF Hub)_ | Local model directory |
| `--lora_r` | `16` | `16` | LoRA rank |
| `--lora_alpha` | `32` | `32` | LoRA alpha (scaling = alpha/r) |
| `--lora_dropout` | `0.05` | `0.05` | LoRA dropout |
| `--lr` | `2e-4` | `2e-4` | Learning rate |
| `--max_grad_norm` | `1.0` | `1.0` | Gradient clipping threshold |
| `--lr_scheduler` | `cosine` | `cosine` | `cosine` / `linear` / `constant` |
| `--warmup_ratio` | `0.03` | `0.03` | LR warmup fraction |
| `--grad_accum` | `4` | `4` | Gradient accumulation steps |
| `--weight_decay` | `0.01` | `0.01` | AdamW weight decay |
| `--patience` | `2` | `2` | Early stopping patience |
| `--epochs` | `10` | `10` | Max training epochs |
| `--batch_size` | `2` | `2` | Per-device batch size |
| `--max_length` | `320` | **`512`** | Max tokens per example |
| `--seed` | `42` | `42` | Random seed |

### What happens during a training run

1. Seeds fixed across Python, NumPy, PyTorch, and HuggingFace.
2. Tokenizer loaded from local disk or HF Hub.
3. Train and val splits loaded and formatted. In v2, `apply_chat_template()` is called per example with `add_special_tokens=False`.
4. Each example tokenised with instruction tokens masked to -100 (answer-only loss).
5. Model loaded with the specified quantisation config. LoRA adapters attach to all 7 projection layers.
6. `Trainer` runs with eval every 200 steps, cosine LR, gradient clipping, and early stopping on val loss.
7. Best checkpoint (lowest val loss) is loaded at training end.
8. Adapter weights and tokenizer saved to `experiments/.../adapter/`.
9. `training_curve.json` written with full loss history and all hyperparameters.

---

## 8. Evaluation Pipeline

### Step 1 — Generate model answers

```bash
python eval_suite.py
# Output: evaluations/eval_<timestamp>/run.json
```

Discovers all adapters under `experiments/` (skips `_v1_archive/`). Runs all 30 held-out evaluation questions against each adapter and the base FP16 model. Records answer text, generation time, VRAM usage, and tokens/sec per response.

### Step 2 — Compute lexical metrics

```bash
python evaluate.py
# Output: evaluations/eval_<timestamp>/metrics.json

python evaluate.py --all       # process all eval runs
python evaluate.py --rouge     # ROUGE only
python evaluate.py --bert      # include BERTScore (slow, GPU recommended)
```

Computes ROUGE-1, ROUGE-2, and ROUGE-L against reference answers, broken down by SC and Non-SC question groups.

### Step 3 — Generate LLM judge prompt

```bash
python build_llm_judge_prompt.py
# Output: evaluations/eval_<timestamp>/llm_judge_prompt.txt
```

All adapter variants anonymised to short tags (`_1` through `_N`). Paste into any frontier LLM for scoring.

### LLM judge scoring rubric

| Dimension | Max | Criterion |
|---|---|---|
| Medical Accuracy | 2 | All stated facts clinically correct |
| Critical Step Coverage | 2 | All essential procedural steps present |
| Safety & Escalation | 1 | Appropriately advises emergency services |
| Dangerous Advice Penalty | −1 | Applied if advice could directly cause harm |
| **Total per question** | **5** | |

SC questions are scored strictly. Judges used in this study: GPT-4o, Claude, Gemini, Grok, DeepSeek, Kimi K2.

### Verification tools

```bash
python verify_masking.py          # Confirm label masking correctness (no GPU)
python verify_template_v1.py      # Confirm template alignment (no GPU, ~30 sec)
```

Always run `verify_template_v1.py` before a new training run. PASS = templates match; MISMATCH = use `train_v2.py`.

---

## 9. Experimental History and Key Findings

### Phase 1 — Baseline adapters

| Adapter | r | α | α/r | Best val loss | Judge rank |
|---|---|---|---|---|---|
| `r8_lr1e4_p3` | 8 | 32 | 4.0 ← broken | 1.3750 | 2nd |
| `r16_lr1e4_p3` | 16 | 32 | 2.0 ✓ | **1.3600** | **1st** |

The r8 adapter used `alpha=32, r=8` giving a scaling ratio of 4.0 instead of the intended 2.0. This doubled the effective LR — formally incorrect, yet it ranked 2nd, suggesting the higher scaling accidentally improved content coverage for this task.

### Phase 2 — Five experimental profiles

| Profile | r / α | lr | clip | Scheduler | Eff. batch |
|---|---|---|---|---|---|
| 1 — Unleash | 16 / 32 | 1e-4 | 10.0 | cosine | 8 |
| 2 — Calibrate | 16 / 32 | 4e-4 | 0.3 | linear | 16 |
| 3 — Capacity | 32 / 64 | 1e-4 | 10.0 | cosine | 8 |
| 4 — Compress | 8 / 8 | 1e-4 | 1.0 | cosine | 8 |
| 5 — Synthesis | 32 / 32 | 4e-4 | 0.3 | linear | 16 |

None of the five profiles beat the r16 baseline. Four causes identified by post-hoc analysis:

**Finding 1 — Gradient clipping fired 100% of steps.** `max_grad_norm=1.0` engaged on every step with mean gradient norm 3.7. Effective LR ≈ 0.27 × nominal. This is structural at 4-bit — quantisation noise dominates gradient magnitude. Profile 1 "Unleash" (clip=10.0) revealed the norm was noise-driven, not signal-driven.

**Finding 2 — Epoch-2 cliff (memorisation event).** At step ~1,000 (epoch 2.014), train loss halved in a 200-step window while val loss spiked. Observed in all five profiles. Cosine-scheduled runs partially recovered; linear-scheduled runs (Profiles 2, 5) did not, because they maintained full LR into the cliff.

**Finding 3 — alpha/r ratio as implicit LR multiplier.** The LoRA scaling factor `alpha/r` acts as a multiplier on effective LR. Intended is 2.0. Profile 4 used 1.0 (underfit). The broken r8 adapter used 4.0 (accidentally competitive). Profile 3 used 2.0 at r=32, adding capacity without improving performance at 4-bit.

**Finding 4 — SC vs Non-SC ROUGE-L gap.** All adapters scored lower on safety-critical questions than non-SC questions. All six LLM judges confirmed this gap. Val loss predicted judge ranking accurately across all 7 variants.

**Finding 5 — Template alignment bug.** All v1 runs were missing a trailing `\n` token (ID 108) after `<end_of_turn>` in every training target. Verified by `verify_template_v1.py`: 0/8 PASS, 8/8 MISMATCH, each by exactly 1 token. The masking boundary was correct throughout — only the final target token was wrong. `data_v2.py` + `train_v2.py` correct this.

---

## 10. Pipeline v2 — What Changed and Why

### verify_template_v1.py

Pre-training diagnostic. No GPU required. Compares `data.py` manual templates against `tokenizer.apply_chat_template()` token by token. Checks BOS double-count risk and masking boundary correctness. Exits 0 on PASS, 1 on MISMATCH.

### data_v2.py

Replaces manual `_build_instruction()` / `_build_full_text()` with `build_hf_dataset_v2(samples, tokenizer)` calling `apply_chat_template()`. Uses `add_special_tokens=False` (template already contains `<bos>`). Max length default 320 → 512. All other functions identical to `data.py`.

### train_v2.py

Imports from `data_v2`. Loads tokenizer before dataset construction. Output folders include `_v2_`. `training_curve.json` records `script_version` and `template_method`. All LoRA config, training arguments, and CLI flags identical to `train.py`.

---

## 11. Results Summary

### 4-bit QLoRA adapters — val loss and judge ranking

| Tag | Experiment | r | α | lr | Val loss | Judge rank |
|---|---|---|---|---|---|---|
| _1 | Base FP16 (no fine-tuning) | — | — | — | — | 7th |
| **_2** | `r16_lr1e4_p3` — best v1 | 16 | 32 | 1e-4 | **1.3600** | **1st** |
| _3 | Profile 2 — Calibrate | 16 | 32 | 4e-4 | 1.4100 | 3rd |
| _4 | Profile 3 — Capacity | 32 | 64 | 1e-4 | 1.4300 | 5th |
| _5 | Profile 5 — Synthesis | 32 | 32 | 4e-4 | 1.4200 | 4th |
| _6 | Profile 4 — Compress | 8 | 8 | 1e-4 | 1.4500 | 6th |
| _7 | `r8_lr1e4_p3` (broken α/r=4) | 8 | 32 | 1e-4 | 1.3750 | 2nd |
| **v2-4bit** | **Corrected baseline ← FINAL** | 16 | 32 | 1e-4 | **1.3400** | **1st** |
| v2-8bit | 8-bit LoRA (rejected) | 16 | 32 | 1e-4 | 1.3614 | 2nd |

### V2 Cross-Quantisation Results (40 questions, DeepSeek judge)

| Adapter | Val loss | Mean score | SC mean | Dangerous penalty count |
|---|---|---|---|---|
| **4-bit V2 (FINAL)** | **1.3400** | **2.18 / 5** | **1.61** | 3 / 40 |
| 8-bit V2 (rejected) | 1.3614 | 1.80 / 5 | 1.19 | 5 / 40 |

**Key finding:** The V1 8-bit advantage (mean ~3.53 > 4-bit ~3.19 on 20Q) reversed on the 40-question bank. 8-bit applies a systematic recovery/lateral-position heuristic regardless of clinical context — confirmed dangerous on Q2 (cardiac arrest), Q16 (seizure), Q18 (spinal injury), Q28 (helmet removal), Q33 (child CPR). 4-bit NF4 quantisation noise partially disrupts this heuristic. 8-bit is formally rejected.

**Final adapter:** `experiments/10cat_4bit_r16_lr1e-4_p3_v2_20260508_054337/adapter`

### ROUGE-L (40-question eval set)

| Variant | ROUGE-L All | ROUGE-L SC | ROUGE-L Non-SC |
|---|---|---|---|
| Base FP16 | lowest | lowest | lowest |
| 4-bit V2 (final) | **highest** | lower than non-SC | **highest** |
| 8-bit V2 | mid | lowest | mid |

SC questions score lower than Non-SC across all adapters. Gap confirmed by all 6 LLM judges.

### LLM judge consensus

Six independent evaluators (GPT-4o, Claude, Gemini, Grok, DeepSeek, Kimi K2) reached unanimous agreement on ranking. Val loss predicted judge ranking accurately. 7-variant ranking: _2 > _7 > _3 > _5 > _4 > _6 > _1. V2 4-bit confirmed best overall.

---

## 12. Script Reference

| Script | Purpose | GPU | Approx. time |
|---|---|---|---|
| `utils/download_model.py` | Cache Gemma 2B locally | No | ~10 min |
| `data.py` | Enrich dataset + splits (v1) | No | ~4 min |
| `data_v2.py` | Same, apply_chat_template (v2) | No | ~4 min |
| `train.py` | Train LoRA adapter (v1) | **Yes** | ~90 min/run |
| `train_v2.py` | Train LoRA adapter (v2, template-corrected) | **Yes** | ~90 min/run |
| `eval_suite.py` | Run 40 questions across adapters | **Yes** | ~15–30 min |
| `evaluate.py` | ROUGE + aggregate metrics | No | ~1 min |
| `build_llm_judge_prompt.py` | Generate LLM judge prompt | No | <1 min |
| `enhanced_inference.py` | T2/T4/T5/T6 inference stack (experimental) | **Yes** | ~varies |
| `verify_masking.py` | Label masking correctness | No | <1 min |
| `verify_template_v1.py` | Template alignment check | No | ~30 sec |
| `garbage_audit.py` | Dataset quality audit | No | ~1 min |
| `utils/inference.py` | Before/after inference comparison | **Yes** | ~2 min |
| `utils/compare_quant.py` | Cross-quantisation benchmark | **Yes** | ~5 min |
| `run_v2_baseline.ps1` | Corrected baseline run (PowerShell) | **Yes** | ~90 min |
| `run_profiles.ps1` | All 5 profiles sequentially (PowerShell) | **Yes** | ~9–11 hrs |
| `run_enhanced_eval.ps1` | 5-run enhanced inference ablation (PowerShell) | **Yes** | ~varies |

---

## 13. Roadmap

- [x] 4-bit QLoRA baseline (r=16, α=32)
- [x] Automated multi-adapter evaluation pipeline
- [x] 40-question held-out evaluation set with reference answers
- [x] LLM judge scoring — 6 independent evaluators
- [x] ROUGE-L lexical evaluation
- [x] Five experimental hyperparameter profiles
- [x] Template alignment diagnostic (`verify_template_v1.py`)
- [x] Pipeline v2 — `apply_chat_template` correction
- [x] **V2 corrected 4-bit baseline** — val_loss=1.3400 (best)
- [x] **8-bit LoRA** — val_loss=1.3614 — evaluated and rejected (dangerous positioning heuristic)
- [x] Cross-precision comparison (val loss, ROUGE, judge scores) — 40 questions
- [x] **Final adapter selected:** `10cat_4bit_r16_lr1e-4_p3_v2_20260508_054337`
- [x] Enhanced inference ablation (T2, T4, T5, T6) — T4 and T6 rejected at 2B scale
- [ ] T5 RAG with sentence-transformers (untested — requires `pip install sentence-transformers rank_bm25`)
- [ ] System-prompt based safety anchoring (T1/T3 — requires clinical review)
- [ ] Paper write-up

---

## 14. Citation
Not available yet

---

## 15. Licence
The dataset (`data/firstaidqa_v1.json`) and all model artifacts are subject to their respective upstream licences. Gemma model weights are governed by the [Gemma Terms of Use](https://ai.google.dev/gemma/terms). Derivative models must comply with those terms and must not be used to provide unsupervised medical advice in place of qualified professionals.
