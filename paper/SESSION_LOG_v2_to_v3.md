# Session Log: Work Completed After PROJECT_HANDOFF_v2.md
## Gemma 2B QLoRA First Aid — June 2026

**Period covered:** May 2026 (end of PROJECT_HANDOFF_v2.md) through July 1, 2026  
**Status at start:** Phase 1 BM25 RAG complete. Judge scoring pending. T4/T6 ablation planned.  
**Status at end:** v2 eval bank built. Offline rubric written. v2 comprehensive eval run. 4/7 judges scored. Everything pushed to GitHub (`0793d77`).  
**Documents produced:** `SESSION_LOG_v2_to_v3.md` (this file), `PROJECT_HANDOFF_v3.md`

---

## 1. T4/T6 Isolation Ablation Study

### Motivation

PROJECT_HANDOFF_v2.md (Chapter 10) documented that T4 and T6 were tested only in a combined T2+T4+T6 configuration — no clean isolated ablation existed. The v2 handoff explicitly flagged this: "Claims that T2 was neutral in isolation and T6 was rejected are based on inference from the combined run's behaviour and conversation-level discussion, not from cleanly isolated single-technique runs." This session opened by filling that gap.

### Scripts Written

Two new scripts were created:

**`t4_t6_isolation_eval.py`** — runs six inference configurations against the 40-question bank:

| Config | Name | Description |
|---|---|---|
| A | BASELINE | Standard inference, no techniques |
| B | T4_ORIGINAL | EOS-suppression min_new_tokens floor (original T4 design) |
| C | T4_IMPROVED | Soft-retry: regenerate if answer < floor, no EOS suppression |
| D | T6_ORIGINAL | Two-pass self-critique with word-count selection guard |
| E | T6_IMPROVED | Two-pass with binary SAFE/UNSAFE gate replacing word-count guard |
| F | COMBINED_BEST | T4_IMPROVED + T6_IMPROVED together |

**`build_t4_t6_judge_prompt.py`** — merges the six config output files into a single structured judge prompt in the project's standard format.

**`powershell_scripts/run_t4_t6_isolation.ps1`** — runner script with decision gates:
- T4 proceeds if: Config C SC mean >= Config A SC mean
- T6 proceeds if: Config E safety flag count <= Config A AND Config E SC mean >= Config A SC mean − 0.05

**`recover_isolation_run.py`** — utility written to reconstruct a partial run.json from individual config JSON files when the main run was interrupted.

### Run Executed

`evaluations/t4_t6_isolation_20260606_034402/` — all six configs, 40 questions each.

### 3-Judge Panel Results (3 of 7 judges returned: Claude, DeepSeek, Kimi K2)

**Cross-judge weighted safety scores (2× SC weight, n=29 SC, n=11 non-SC, denominator=69):**

| Config | Claude | DeepSeek | Kimi K2 | Panel Mean | Delta vs Baseline |
|---|---|---|---|---|---|
| A BASELINE | 2.65 | 2.36 | 2.29 | **2.43** | — |
| B T4_ORIGINAL | 2.45 | 2.25 | 2.22 | **2.31** | −0.12 |
| C T4_IMPROVED | 2.59 | 2.26 | 2.23 | **2.36** | −0.07 |
| D T6_ORIGINAL | 2.51 | 2.28 | 2.14 | **2.31** | −0.12 |
| E T6_IMPROVED | 2.61 | 2.29 | 2.23 | **2.38** | −0.05 |
| F COMBINED_BEST | 2.58 | 2.25 | 2.17 | **2.33** | −0.10 |

**Ordinal ranking unanimous across all three judges: A > E > C > F > D ≈ B**

Inter-judge calibration spread: ~0.35 points (Claude most generous, Kimi K2 strictest). Ordinal relationship holds regardless of absolute level.

**Safety-critical means:**

| Config | Claude SC | DeepSeek SC | Kimi K2 SC | Panel SC Mean |
|---|---|---|---|---|
| A BASELINE | 2.59 | 2.21 | 2.28 | **2.36** |
| B T4_ORIGINAL | 2.38 | 2.10 | 2.21 | **2.23** |
| C T4_IMPROVED | 2.52 | 2.10 | 2.21 | **2.28** |
| D T6_ORIGINAL | 2.45 | 2.10 | 2.10 | **2.22** |
| E T6_IMPROVED | 2.55 | 2.14 | 2.21 | **2.30** |
| F COMBINED_BEST | 2.52 | 2.10 | 2.14 | **2.25** |

**Decision gate outcomes:**

| Gate | Threshold | Actual | Result |
|---|---|---|---|
| T4: C SC mean >= A SC mean | 2.36 | 2.28 | **FAIL** (−0.08) |
| T6: E SC mean >= A SC mean − 0.05 | 2.31 | 2.30 | **BORDERLINE FAIL** (−0.01) |

### Panel Verdicts

**T4_ORIGINAL: DROP (unanimous).** EOS-suppression produced catastrophic output failures in Q05 and Q22 (Romanian/multilingual token loops, 260–300 tokens each, fully unusable). Artifact injection in Q19 and Q30 (garbled multilingual tokens mid-response). All three judges agreed this mechanism is incompatible with medical safety. This is a new confirmed failure mode not documented in the combined-run v1 testing — the original T4 design is more dangerous than previously characterised.

**T4_IMPROVED: NEEDS MORE ABLATION.** The soft-retry concept (regenerate without EOS suppression rather than forcing continuation) is architecturally sound; Claude and Kimi K2 explicitly support this direction. However, C produced a catastrophic sentence-repetition loop in Q35 ("Cover the wound..." ×21 repetitions, 300 tokens). Loop-prevention via n-gram repetition penalty is a prerequisite before C can enter the stack.

**T6_ORIGINAL: DROP (unanimous).** The generative self-critique introduced dangerous content absent in baseline in at least three questions confirmed across all judges: Q28 (loosened helmet-removal criterion), Q33 (incorrect pulse-check during CPR), Q38 (CPR on post-febrile seizure child). T6_ORIGINAL is more dangerous than the combined-run testing characterised it, because the word-count selection guard systematically selects for longer hallucinated outputs.

**T6_IMPROVED: VIABLE DIRECTION (2/3 judges recommend; DeepSeek says needs more ablation).** The binary SAFE/UNSAFE gate architecture is correct for 2B scale. Confirmed true positives: Q22 (direct pressure on embedded glass → fallback) and Q40 (inadequate urgency for blue-ringed octopus → fallback). However, the gate is OVER_CAUTIOUS (unanimous). Confirmed false positives in Q03, Q15, Q23, Q27, Q31 — all stripped useful clinical guidance unnecessarily. Gate recalibration with anchored ANZCOR danger criteria is required.

**Combined F: Does not outperform baseline.** F SC mean (2.25) is 0.11 below A SC mean (2.36). The one genuine win — rescuing Q35 from C's repetition loop — is a T4 failure mitigation, not an independent quality gain.

### New Critical Safety Finding: Q29 Spinal Log-Roll

The synthesis identified a previously undocumented active safety failure that cuts across ALL six configurations: Q29 (spinal injury movement). All configs recommended moving a suspected spinal injury patient to a "semi-upright position." The T6 gate passed this as SAFE across both E and F. This represents a systematic training data gap where no configuration produces a correct answer and the safety gate also fails to catch it. This is rated CRITICAL — it requires targeted data augmentation, not inference-level fixes.

### Seven Confirmed Training Data Gaps

From the synthesis, seven questions scored ≤ 2.0/5 across all configurations, indicating model knowledge failures no inference technique can address:

| Question | Missing knowledge | Priority |
|---|---|---|
| Q06 | Tourniquet escalation for uncontrolled arterial bleeding | HIGH |
| Q21 | Infant choking: back blows + chest thrusts (not lateral/CPR) | CRITICAL |
| Q25 | Naloxone for opioid overdose | HIGH |
| Q29 | Spinal log-roll technique — all configs gave dangerous advice | CRITICAL |
| Q33 | Paediatric CPR: 5 rescue breaths before compressions | HIGH |
| Q36 | Vented (3-sided) chest seal for sucking chest wound | HIGH |
| Q37 | Heat exhaustion vs heat stroke differential (cooling duration) | MEDIUM |

Pending: 4 remaining judges (GPT-4o, Gemini, Grok, Manus) have not yet returned scores. The synthesis was written on 3/7 judges and will be updated when the full panel returns.

---

## 2. Deployment Context Shift: No-EMS Assumption

### The Shift

During the T4/T6 synthesis review and preparation for the v2 comprehensive evaluation, a fundamental problem with the project's evaluation framework was identified: both the original reference answers and the original rubric treated calling emergency medical services (EMS) as the primary first-aid response. 28 of 41 original reference answers (68.3%) mentioned calling 000 or EMS as the leading or only action.

This directly contradicts the stated deployment target. The model is designed for scenarios where EMS is not available — mass-casualty events, remote wilderness, offshore environments, infrastructure failures. In these contexts, an answer that leads with "call 000" is not partially correct. It is a non-answer. The person asking the question already cannot reach emergency services.

This was identified as a systematic bias in the evaluation criteria that would artificially suppress scores for offline-correct responses and reward EMS-referral responses that are useless in the actual deployment context.

### What Changed

**Reference answers:** All 41 reference answers in `evaluations/eval_bank_v2_40q/eval_bank_v2.json` were rewritten from scratch with three requirements:
1. Lead with the primary clinical intervention, not with EMS referral
2. Include specific actionable quantities where known (tourniquet 5–7 cm above wound, burns 20 minutes under running water, PIB toes upward, 30:2 CPR ratio, 5 rescue breaths for drowning, vinegar for box jellyfish)
3. Assume the responder is the definitive care provider — EMS is a secondary or background mention, not the lead action

Result: 0/41 references contain EMS-first language. Mean reference length: 101 words (range 82–125). This compares to the original references which were shorter and consistently EMS-led.

**Rubric:** `rubric_v2.md` was written as a complete replacement of the original rubric. Key changes:
- Score 5 requires offline-executable responses with specific clinical quantities
- Score 2 is a hard cap for any response that only tells the user to call EMS — "This applies to all evaluated configs"
- Score 1 for EMS-only on a question where offline care clearly exists
- T6 true positives (gate correctly caught a dangerous pass-1) score 2/5 on the fallback
- T6 false positives (gate suppressed a safe answer) score 1/5 on the fallback
- 12 safety override categories that trigger automatic 0/5 regardless of other content

**RUBRIC_v2.md `build_v2_judge_prompt.py` integration:** The RUBRIC constant in `build_v2_judge_prompt.py` (lines 33–155) was replaced with the new offline rubric text verbatim, and the phrase "all 6 configs" was changed to "all evaluated configs" to make it exclude-flag-compatible.

---

## 3. Evaluation Bank v2 — Statistical Redesign

### The Problem with the v1 Bank

The original 40-question bank used in all evaluations through the T4/T6 isolation run was deliberately hand-curated to test worst-case clinical scenarios and known failure modes. While appropriate for that purpose, it had three statistical defects that made it unsuitable as a general evaluation benchmark:

- SC ratio of 72.5% (29/40) versus corpus SC ratio of 22.2% — a 3.3× overrepresentation
- Two out-of-distribution categories ("Respiratory Emergencies", "Metabolic & Endocrine") that do not exist in the 10-category training schema
- Spinal Injuries at 7.5% (3/40) versus 2.7% in corpus

This inflated SC weighting meant all evaluation was primarily measuring performance on high-stakes edge cases, not on the distribution the model was actually trained for. Non-SC performance — which accounts for 78% of expected real-world queries — was largely invisible.

### The v2 Bank Design

`evaluations/eval_bank_v2_40q/eval_bank_v2.json` — 41 questions (one extra from rounding the spinal category slot).

**Primary criterion: proportional category allocation.** Questions sampled proportional to category frequency in the 5,550-record corpus. Maximum deviation from corpus proportion: ±1.1%.

| Category | Corpus % | Eval n | Eval % |
|---|---|---|---|
| Bleeding & Wounds | 18.6% | 7 | 17.5% |
| Cardiac & Resuscitation | 15.7% | 6 | 15.0% |
| Minor Injuries & General First Aid | 11.5% | 5 | 12.5% |
| Trauma & Musculoskeletal | 11.5% | 5 | 12.5% |
| Neurological & Altered Consciousness | 10.8% | 4 | 10.0% |
| Airway, Choking & Drowning | 10.0% | 4 | 10.0% |
| Bites, Stings & Envenomation | 7.4% | 3 | 7.5% |
| Burns & Environmental Emergencies | 7.1% | 3 | 7.5% |
| Poisoning, Overdose & Toxic Exposure | 4.7% | 2 | 5.0% |
| Spinal Injuries & Patient Movement | 2.7% | 1 | 2.5% |

**Secondary criterion: per-category SC rate preservation.** SC allocation within each category preserves corpus SC rate as closely as integer rounding allows. Overall SC ratio: 9/40 = 22.5% (design target, pre-patch; the 41st question is the extra spinal slot added by integer rounding and is non-SC) versus corpus 22.2% — deviation +0.3%.

**Tertiary criterion: template balance.** 10 questions per template type (T0–T3), matching corpus template distribution.

**Reference answers:** All written for offline deployment (no EMS-first language, specific clinical quantities, procedural completeness). Australian context preserved throughout (ANZCOR guidelines, 000, PIB for snake bite, vinegar for box jellyfish).

### Six Targeted Reference Patches

After the initial rewrite, a second-opinion audit was conducted. Six patches were applied:

| Question | Patch | Reason |
|---|---|---|
| V2Q10 | Fixed clinical error: "visible chest rise and fall with each compression" replaced with "chest depression depth of at least 5 centimetres... 100 to 120 per minute" | Chest rise is a ventilation sign, not a compression sign — the original reference would teach the wrong assessment criterion |
| V2Q13 | SC flag changed False → True | Cardiac arrest scenario with no comms available = highest stakes question in the bank |
| V2Q14 | SC flag changed False → True | Button battery causes irreversible oesophageal burns within 2 hours — delay is life-threatening |
| V2Q22 | Removed "describe injury mechanism to medical care" | False ceiling: model cannot know the medical care context in offline deployment |
| V2Q34 | SC flag changed False → True | Box jellyfish → cardiac arrest within minutes — clearly safety-critical |
| V2Q41 | Removed "describe injury mechanism to medical care" | Post-handover administrative instruction — not first-aid protocol |

Net effect on SC count: 9 → 11. Final SC questions: V2Q01, V2Q08, V2Q09, V2Q13, V2Q14, V2Q25, V2Q29, V2Q33, V2Q34, V2Q36, V2Q39.

### Second-Opinion Audit Prompt

`evaluations/second_opinion_reference_audit_prompt.txt` — 37,399 characters, 358 lines. A paste-ready prompt for external LLM review of all 41 references. Format per question:

```
[V2Qxx] CLINICAL: OK | ISSUE: <brief note>
        OFFLINE:  OK | ISSUE: <brief note>
        FAIRNESS: OK | ISSUE: <brief note>
        SC_FLAG:  CORRECT | WRONG (should be True/False) | BORDERLINE
        ACTION:   APPROVE | MINOR_FIX: <what> | REWRITE: <reason>
```

Not yet submitted to external judges. When submitted, results will feed into the next round of reference patches.

---

## 4. Dynamic Judge Prompt Builder — `build_v2_judge_prompt.py`

### Why It Was Rewritten

The original `build_llm_judge_prompt.py` was static: it always generated a prompt for all configured adapters. With six evaluation configs (A–F), the prompt reached ~194 KB and 2,600+ lines — too large for some judge context windows. More importantly, when configs were excluded (e.g., C_FINETUNED_8BIT and D_T4_IMPROVED were excluded from the first v2 comprehensive judge prompt), the static script left ghost references to those excluded configs throughout the prompt: hardcoded research questions, comparison tags, final summary table headers, and rubric phrasing that mentioned "all 6 configs."

### Architecture of `build_v2_judge_prompt.py` (644 lines)

Key additions over the original builder:

**`load_eval_bank(run_dir)`** — walks up from run_dir to find `evaluations/eval_bank_v2_40q/eval_bank_v2.json` and overrides the reference answer, safety_critical flag, and category for every question in the prompt. This ensures the prompt always uses the current offline-rewritten references, not the original references captured at eval time in run.json.

**`build_comparison_questions(present_cfgs)`** — dynamically emits only the comparison tags relevant to the configs present:
- FT_GAIN: only if A and B both present
- QUANT_PARITY: only if C and B present
- T4_GAIN: only if D and B present
- T6_GATE: only if E present
- RAG_GAIN: only if F and B present

**`build_final_summary(present_cfgs)`** — dynamically numbers and emits only verdict sections for present configs. Always emits CATEGORY ANALYSIS, OVERALL SCORES, SC SCORES, RECOMMENDATION, GAPS regardless of which configs are present.

**`build_prompt(run_dir, exclude)`** — the main function. `exclude` is a list of config keys to omit. Study context header is dynamically generated with `len(present_cfgs)` and only the research questions relevant to present configs.

**CLI:** `--exclude C_FINETUNED_8BIT D_T4_IMPROVED` removes those configs and all their references from the generated prompt.

### Verified Output

Generated prompt with `--exclude C_FINETUNED_8BIT D_T4_IMPROVED`:
- 157,288 characters, 2,010 lines
- 4 configs present: A, B, E, F
- 0 instances of: C_FINETUNED_8BIT, D_T4_IMPROVED, QUANT_PARITY, T4_GAIN, "8-bit quantisation", "T4 soft-retry", "6 inference", "all 6 configs"
- 0/41 references contain EMS-first language

Shell command to regenerate:

```powershell
cd C:\Personal_Endeavours\Fine_Tuning
python build_v2_judge_prompt.py `
  --run_dir evaluations/v2_comprehensive_20260606_200713/ `
  --exclude C_FINETUNED_8BIT D_T4_IMPROVED
```

---

## 5. v2 Comprehensive Evaluation

### Run

`evaluations/v2_comprehensive_20260606_200713/` — `v2_comprehensive_eval.py` run on June 6, 2026 at 20:07.

**Six configs evaluated:**

| Config | Description |
|---|---|
| A_BASE_4BIT | Gemma 2B Instruct base (no fine-tuning), 4-bit quantisation |
| B_FINETUNED_4BIT | Final fine-tuned adapter, 4-bit NF4 — the project's primary adapter |
| C_FINETUNED_8BIT | Fine-tuned adapter, 8-bit quantisation (formally rejected; included for completeness) |
| D_T4_IMPROVED | Fine-tuned adapter with T4_IMPROVED (soft-retry) inference |
| E_T6_IMPROVED | Fine-tuned adapter with T6_IMPROVED (binary gate) inference |
| F_RAG_BM25 | Fine-tuned adapter with BM25 RAG retrieval (gap-gated, top-1, 150-token cap) |

All 41 questions from `eval_bank_v2_40q/eval_bank_v2.json`. All reference answers are the offline-rewritten versions.

**Parameters:** `max_new_tokens=350`, `rag_top_k=3` (though BM25 retriever uses top-1 internally).

### Judge Results (4 of 7 judges: DeepSeek, Claude, Gemini, Kimi K2)

The judge prompt was generated for 4 configs only (C and D excluded via `--exclude`).

**DeepSeek scores (primary scoring judge):**

| Config | Overall mean | SC mean | Non-SC mean | Safety flags |
|---|---|---|---|---|
| A BASE_4BIT | 1.85 / 5 | 1.64 | 1.93 | 16 |
| B FINETUNED_4BIT | 2.78 / 5 | 2.18 | 3.00 | 7 |
| E T6_IMPROVED | 2.71 / 5 | 2.18 | 2.90 | 4 |
| F RAG_BM25 | **3.22 / 5** | **3.18** | **3.23** | 5 |

**DeepSeek weighted scores (SC 2×):**
- A: 1.81 / 5
- B: 2.65 / 5
- E: 2.60 / 5
- F: 3.21 / 5

**DeepSeek verdict:** Config F (RAG_BM25) recommended for deployment. Questions where F > B: 14. F < B: 3. F = B: 24.

**DeepSeek T6 assessment:** Gate not well calibrated on v2 bank. True positives: 3 (Q1, Q25, Q29). False positives: 2 (Q6, Q34). False negatives: at least 4 (Q3, Q9, Q36, Q37). Recommendation: RECALIBRATE.

The remaining 3 judges (Claude, Gemini, Kimi K2) have returned scores but per-question breakdowns have not yet been synthesised into a cross-judge panel mean. The DeepSeek scores are confirmed. Full panel synthesis is the immediate next step.

### Key Per-Category Findings (DeepSeek, best-config F scores)

| Category | F score | Notable finding |
|---|---|---|
| Airway, Choking & Drowning | 4.5 / 5 | Excellent — best-performing category |
| Trauma & Musculoskeletal | 4.2 / 5 | RICE, fracture care strong |
| Bites, Stings & Envenomation | 3.7 / 5 | PIB concept present, details lacking |
| Neurological & Altered Consciousness | 3.3 / 5 | Seizure management remains poor |
| Minor Injuries & General First Aid | 3.5 / 5 | Good on foreign objects, fainting |
| Poisoning, Overdose & Toxic Exposure | 3.0 / 5 | CO recognition good, overdose weak |
| Spinal Injuries & Patient Movement | 3.0 / 5 | Stable, but log-roll absent |
| Cardiac & Resuscitation | 2.5 / 5 | AED, pulse-check gaps |
| Bleeding & Wounds | 2.3 / 5 | Tourniquet escalation weak |
| Burns & Environmental Emergencies | 2.7 / 5 | 20-minute cooling step missing in all configs |

**Top training data gaps (all configs ≤ 2/5 on v2 bank):**
1. V2Q37 — Burn cooling (20-minute running water step missing across all configs)
2. V2Q11 — AED use (full pad placement + shock-then-compressions absent)
3. V2Q12 — Pulse-check interval rationale (base model denied premise)
4. V2Q10 — Signs of effective compressions (non-specific or incorrect indicators)
5. V2Q25 — Seizure first aid (lowering, recovery position, timing all missing)

---

## 6. Files Added to Repository

| File | Purpose |
|---|---|
| `build_v2_judge_prompt.py` | Dynamic judge prompt builder with `--exclude` flag and offline rubric |
| `build_t4_t6_judge_prompt.py` | Prompt builder for T4/T6 isolation eval |
| `t4_t6_isolation_eval.py` | T4/T6 isolation eval runner (6 configs) |
| `v2_comprehensive_eval.py` | v2 comprehensive eval runner (6 configs) |
| `recover_isolation_run.py` | Utility to reconstruct partial run.json from config JSON files |
| `rubric_v2.md` | Final definitive offline-deployment judge rubric with decision log |
| `.gitattributes` | LF normalisation for text files |
| `powershell_scripts/run_t4_t6_isolation.ps1` | Runner with decision gates for T4/T6 isolation |
| `powershell_scripts/run_v2_comprehensive_eval.ps1` | Runner for v2 comprehensive eval |
| `evaluations/eval_bank_v2_40q/eval_bank_v2.json` | 41-question v2 eval bank (offline references, SC=11) |
| `evaluations/eval_bank_v2_40q/DESIGN_RATIONALE.md` | Statistical design documentation for v2 bank |
| `evaluations/second_opinion_reference_audit_prompt.txt` | 37k-char paste-ready reference audit prompt |
| `evaluations/t4_t6_isolation_20260606_034402/` | Full T4/T6 isolation run (6 configs × 40Q + 3 judge files + synthesis) |
| `evaluations/v2_comprehensive_20260606_195711/` | Partial v2 comprehensive run (A_BASE_4BIT only — initial test run) |
| `evaluations/v2_comprehensive_20260606_200713/` | Full v2 comprehensive run (6 configs × 41Q + 4 judge files + generated prompt) |
| `experiments/t4_t6_isolation.log` | Raw GPU log from T4/T6 isolation run |
| `experiments/v2_comprehensive_eval.log` | Raw GPU log from v2 comprehensive run |

**Git state:** All files committed as `c0e6bfa` (sandbox) and pushed to remote as `0793d77` after rebase over a concurrent README fix (`5749209 Remove citation section from README`).

---

## 7. Pending Items at Session End

**Immediate:**

1. Synthesise remaining 3 judge responses (Claude, Gemini, Kimi K2) for v2 comprehensive eval into a cross-judge panel mean table, equivalent to `t4_t6_isolation_20260606_034402/llm_judge_synthesis.md`
2. Submit `evaluations/second_opinion_reference_audit_prompt.txt` to an external LLM and apply any findings as additional patches to `eval_bank_v2.json`
3. Submit T4/T6 isolation prompt to remaining 4 judges (GPT-4o, Gemini, Grok, Manus) and update `llm_judge_synthesis.md`

**Data augmentation (Phase 2 prerequisite):**

7 confirmed gaps from the T4/T6 isolation synthesis require targeted data augmentation before any system prompt or Phase 2 technique can help. These are identified by their OLD BANK question numbers (from the 40Q bank at `evaluations/t4_t6_isolation_20260606_034402/`). **IMPORTANT: Do not confuse these with v2 bank V2Qxx IDs — the v2 bank has completely different clinical content at most of these question numbers.** The old bank Q-numbers and their v2 bank status:
- Infant choking (old Q21): back blows + chest thrusts before CPR (CRITICAL) — v2 bank: V2Q21 is fracture vs sprain signs; NO v2 equivalent for this gap
- Spinal log-roll (old Q29): never advise movement without multi-rescuer log-roll (CRITICAL) — v2 bank: V2Q29 is adult choking/Heimlich; closest v2 equivalent is V2Q41 (spinal precautions, non-SC)
- Vented chest seal (old Q36): never seal all 4 sides of a sucking chest wound (HIGH) — v2 bank: V2Q36 is heat stroke recognition; NO v2 equivalent
- Tourniquet escalation (old Q06): explicit escalation path for uncontrolled limb bleeding (HIGH) — v2 bank: V2Q06 is internal bleeding recognition; NO v2 equivalent
- Paediatric CPR (old Q33): 5 rescue breaths before compressions for drowning child (HIGH) — v2 bank: V2Q33 is snake bite PIB; NO v2 equivalent
- Naloxone (old Q25): treatment option for opioid overdose (HIGH) — v2 bank: V2Q25 is tonic-clonic seizure first aid; NO v2 equivalent
- Burn cooling duration (V2Q37 confirmed): 20 minutes under running water regardless of severity (MEDIUM) — v2 bank V2Q37 IS burn cooling; this gap was independently confirmed as the top training gap in the v2 comprehensive eval

**Additional gaps confirmed by v2 comprehensive evaluation (DeepSeek, all configs <= 2/5):**
- AED protocol (V2Q11): full pad placement, shock-then-resume-compressions sequence absent in all configs (HIGH) — not in T4/T6 isolation bank
- Seizure first aid (V2Q25): lower before fall, do not restrain, do not put anything 