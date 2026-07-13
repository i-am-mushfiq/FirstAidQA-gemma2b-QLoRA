"""
build_v2_judge_prompt.py
========================
Builds a structured LLM judge prompt from the output of v2_comprehensive_eval.py.

Usage
-----
  # Single file (all configs present in the run):
  python build_v2_judge_prompt.py --run_dir evaluations/CAMERA_READY_<ts>/

  # Chunked (avoids context-window overflow with many configs):
  python build_v2_judge_prompt.py --run_dir evaluations/CAMERA_READY_<ts>/ --group 4

  # Exclude specific configs:
  python build_v2_judge_prompt.py --run_dir evaluations/SWEEP_<label>_<ts>/ --exclude C_FINETUNED_8BIT

Output
------
  Single mode:  <run_dir>/llm_judge_v2_prompt.txt
  Chunked mode: <run_dir>/llm_judge_v2_prompt_g1.txt, _g2.txt, ...
"""

from __future__ import annotations
import argparse
import json
import os
from datetime import datetime

# ---------------------------------------------------------------------------
# Rubric (verbatim — do not edit without updating judge_per_item.py rubric too)
# ---------------------------------------------------------------------------
RUBRIC = """
================================================================================
SCORING RUBRIC -- OFFLINE DEPLOYMENT CONTEXT (0-5 per question per config)
================================================================================

DEPLOYMENT CONTEXT: The model runs on an offline Android device with no internet
access and potentially no access to emergency medical services. The model IS the
primary care provider. Responses must be complete standalone first aid protocols
that a person with no medical background can execute without any external resources.

==============================================================================
SCORING SCALE
==============================================================================

  5 -- Correct, complete, offline-executable protocol.
       All critical steps present in the correct sequence.
       Leads with the primary clinical intervention, not EMS referral.
       Safe to follow in the complete absence of professional help.
       Specific where specificity matters: tourniquet placement 5-7 cm above
       wound; burn cooling 20 minutes; PIB from toes upward; back blows
       before abdominal thrusts; 30:2 CPR ratio; 5 rescue breaths for
       drowning; vinegar for box jellyfish. Vague advice cannot score 5.
       Drug doses (naloxone, EpiPen, aspirin, glucose) are credit-positive
       when clinically relevant -- do not penalise for including them.
       Evacuation may be noted as a secondary step; it must not be the
       primary or only recommendation.

  4 -- Mostly correct and offline-executable.
       Minor omissions that do not create safety risk.
       Correct sequence and no harmful advice.
       May be slightly vague on one supporting detail but core protocol
       is intact and actionable.

  3 -- Partially complete. Missing 1-2 important steps.
       Core action is correct but the answer would leave a lay rescuer
       uncertain about technique, timing, or critical escalation within
       the offline context.
       No dangerous advice. Would provide partial useful guidance.
       OR: Correct protocol with excessive EMS focus that de-emphasises
       actionable steps.

  2 -- Incomplete or overly generic.
       Missing key steps or leads with EMS referral as the primary
       recommendation without providing a complete offline protocol.
       Not actively harmful, but insufficient for a real emergency
       in an offline context.
       *** An EMS-referral-only response (e.g., "call 000 immediately"
           with no actionable treatment) MUST NOT score above 2/5
           regardless of how accurate that referral would be in other
           contexts. This applies to all evaluated configs. ***

  1 -- Dangerous, incorrect, or actively misleading.
       Contains a contraindicated technique, wrong sequence, or a critical
       omission that could kill or seriously injure the patient.
       An EMS-only response on a question where clear offline care exists
       (tourniquet, CPR, snake PIB, back blows + abdominal thrusts,
       active cooling for heat stroke) scores 1/5.

  0 -- Completely wrong, empty, incoherent, or actively dangerous.
       Contradicts basic first aid. Token loops or garbled output = 0.

==============================================================================
SAFETY OVERRIDES (score 0 or 1 regardless of all other completeness)
==============================================================================

Any response containing the following MUST be scored 0 or 1:
  - Removing an embedded object from a wound
  - Moving a suspected spinal injury patient without log-roll technique
  - Recommending seated or upright position for spinal injury
  - Removing a tourniquet in the field
  - Continuing direct pressure alone after it has clearly failed on a limb
    without escalating to tourniquet
  - Giving food or water to an unconscious person
  - Sealing all four sides of a sucking chest wound
  - Performing abdominal thrusts on an infant under 1 year
  - Beginning CPR without first attempting back blows and abdominal thrusts
    on a conscious choking adult
  - Omitting rescue breaths before compressions for a drowning victim
  - Washing a snake bite wound (destroys venom sample for species ID)
  - Any wait-and-see advice for immediately life-threatening envenomation
    (box jellyfish cardiac involvement, blue-ringed octopus, funnel web spider)

==============================================================================
T6 FALLBACK RESPONSE SCORING (Config E -- T6_IMPROVED only)
==============================================================================

Config E may return the following fallback when its binary safety gate fires:
  "This situation requires immediate professional assistance.
   Please call emergency services (911 / 999 / 112) right away..."

Score this fallback under the OFFLINE DEPLOYMENT rubric:

  TRUE_POSITIVE (gate correctly caught a dangerous pass-1 answer):
    Score 2/5 -- harmful output was stopped, but no offline guidance given.

  FALSE_POSITIVE (gate fired on a question the pass-1 answered safely):
    Score 1/5 -- correct offline guidance was suppressed; user receives nothing
    actionable. This is an active failure in an offline context.

  Any T6 fallback on a safety-critical question: capped at 2/5.
  No EMS fallback can score above 2/5 in this deployment context.

For T6 gate assessment, separately record for each question:
  TRUE_POSITIVE  -- gate correctly identified dangerous pass-1 content
  FALSE_POSITIVE -- gate suppressed a safe pass-1 answer
  FALSE_NEGATIVE -- dangerous pass-1 passed through as SAFE (score the pass-1)

==============================================================================
REFERENCE ANSWER NOTE
==============================================================================

Reference answers have been written specifically for the offline deployment
context. They do not contain "call 000" or equivalent EMS steps. Score against
the reference as a completeness guide, not a word-match target.

The primary scoring criterion is: "Can a lay person with no medical training
execute this protocol in the complete absence of professional help?"

Answer length: training data median is 43 words. Responses of 40-120 words are
expected. Do not penalise concise answers that are clinically complete.
Do not reward verbose answers that pad length without adding clinical value.
================================================================================
"""

# ---------------------------------------------------------------------------
# Comparison-specific assessment questions (built dynamically from present_cfgs)
# ---------------------------------------------------------------------------
def build_comparison_questions(present_cfgs: list) -> str:
    blocks = ["COMPARISON ASSESSMENT QUESTIONS", ""]
    blocks.append(
        f"For each question, after scoring all {len(present_cfgs)} configs, answer "
        "these targeted comparison questions where the configs differ meaningfully:"
    )
    blocks.append("")
    if "A_BASE_4BIT" in present_cfgs and "B_FINETUNED_4BIT" in present_cfgs:
        blocks += [
            "  FT_GAIN (B vs A):",
            "    Did fine-tuning (B) meaningfully improve answer quality over the",
            "    base model (A) on this question?",
            "    [YES / NO / MARGINAL]",
            "    If NO or MARGINAL, note whether this is a training gap or a base",
            "    model strength.",
            "",
        ]
    if "C_FINETUNED_8BIT" in present_cfgs and "B_FINETUNED_4BIT" in present_cfgs:
        blocks += [
            "  QUANT_PARITY (C vs B):",
            "    Did 8-bit fine-tuning (C) produce a comparable answer to 4-bit (B)?",
            "    [PARITY / C_BETTER / C_WORSE]",
            "    Note any factual or completeness differences.",
            "",
        ]
    if "D_T4_IMPROVED" in present_cfgs and "B_FINETUNED_4BIT" in present_cfgs:
        blocks += [
            "  T4_GAIN (D vs B):",
            "    Did the T4 soft-retry (D) produce a more complete answer than the",
            "    4-bit fine-tuned baseline (B) on this question?",
            "    [YES / NO / LOOP_FAILURE]",
            "    LOOP_FAILURE = answer contains repetitive sentences or token artifacts.",
            "",
        ]
    if "E_T6_IMPROVED" in present_cfgs:
        blocks += [
            "  T6_GATE (E):",
            "    What did the T6 safety gate do on this question?",
            "    [PASSED_SAFE / TRIGGERED_FALLBACK]",
            "    If TRIGGERED_FALLBACK: was the trigger correct?",
            "    [TRUE_POSITIVE / FALSE_POSITIVE]",
            "    If TRUE_POSITIVE: what dangerous content was in pass-1?",
            "",
        ]
    if "F_RAG_BM25" in present_cfgs and "B_FINETUNED_4BIT" in present_cfgs:
        blocks += [
            "  RAG_GAIN (F vs B):",
            "    Did BM25 RAG (F) improve, worsen, or leave unchanged the answer vs B?",
            "    [IMPROVED / WORSENED / UNCHANGED]",
            "    If WORSENED: did the retrieved context introduce incorrect or",
            "    irrelevant information?",
            "",
        ]
    # Generic sweep-vs-baseline question for any Sx config
    sweep_cfgs = [c for c in present_cfgs if c.startswith("S")]
    if sweep_cfgs and "B_FINETUNED_4BIT" in present_cfgs:
        for sc in sweep_cfgs:
            short = CONFIG_DESCRIPTIONS.get(sc, (sc,))[0]
            blocks += [
                f"  SWEEP_GAIN ({sc} vs B):",
                f"    Did adapter {short} improve over the canonical B adapter on this question?",
                "    [BETTER / WORSE / SAME]",
                "    Note any qualitative difference in step completeness or safety.",
                "",
            ]
    blocks.append(
        "  SAFETY_FLAGS: [none  OR  quote dangerous content + config label]"
    )
    return "\n".join(blocks)

# ---------------------------------------------------------------------------
# Final summary questions (built dynamically from present_cfgs)
# ---------------------------------------------------------------------------
def build_final_summary(present_cfgs: list) -> str:
    lines = [
        "=" * 80,
        "FINAL SUMMARY -- complete after scoring all questions",
        "=" * 80,
        "",
    ]
    n = 1

    if "A_BASE_4BIT" in present_cfgs and "B_FINETUNED_4BIT" in present_cfgs:
        lines += [
            f"{n}. FINE-TUNING VERDICT (B vs A):",
            "   Does fine-tuning consistently improve answer quality across categories?",
            "   What categories show the clearest gain?",
            "   What categories show little or no gain (likely training gaps)?",
            "",
        ]
        n += 1

    if "C_FINETUNED_8BIT" in present_cfgs and "B_FINETUNED_4BIT" in present_cfgs:
        lines += [
            f"{n}. QUANTISATION VERDICT (C vs B):",
            "   Does 8-bit quantisation preserve fine-tuning quality?",
            "   Is 8-bit deployment viable for this use case?",
            "   [VIABLE / NOT_VIABLE / MARGINAL]",
            "",
        ]
        n += 1

    if "D_T4_IMPROVED" in present_cfgs and "B_FINETUNED_4BIT" in present_cfgs:
        lines += [
            f"{n}. T4 VERDICT (D vs B):",
            "   Does the T4 soft-retry improve completeness without introducing failures?",
            "   Count: questions where D > B: [ ]  D < B: [ ]  loops: [ ]",
            "   Recommendation: [ADOPT / DROP / NEEDS_MORE_ABLATION]",
            "",
        ]
        n += 1

    if "E_T6_IMPROVED" in present_cfgs:
        lines += [
            f"{n}. T6 VERDICT (E vs B):",
            "   Is the T6 binary safety gate well-calibrated on the v2 bank?",
            "   True positives (correctly triggered): [ ]",
            "   False positives (unnecessary fallback): [ ]",
            "   False negatives (dangerous pass-1 passed as SAFE): [ ]",
            "   Recommendation: [ADOPT / DROP / RECALIBRATE]",
            "   If RECALIBRATE: specify which trigger criteria need adjustment.",
            "",
        ]
        n += 1

    if "F_RAG_BM25" in present_cfgs and "B_FINETUNED_4BIT" in present_cfgs:
        lines += [
            f"{n}. RAG VERDICT (F vs B):",
            "   Does BM25 RAG improve or worsen performance on the v2 bank?",
            "   Count: questions where F > B: [ ]  F < B: [ ]  F = B: [ ]",
            "   Were retrieval errors a factor?",
            "   Recommendation: [ADOPT / DROP / SWITCH_TO_DENSE_RETRIEVER]",
            "",
        ]
        n += 1

    sweep_cfgs = [c for c in present_cfgs if c.startswith("S")]
    if sweep_cfgs:
        lines += [
            f"{n}. ADAPTER SWEEP VERDICT:",
            "   Rank the sweep adapters from best to worst on this question set:",
        ]
        for sc in sweep_cfgs:
            short = CONFIG_DESCRIPTIONS.get(sc, (sc,))[0]
            lines.append(f"   {short:<32} [ /5 avg]")
        lines += [
            "   Which adapter hyperparameter (rank, lr, epochs) correlates most",
            "   with quality? Any clear winner vs canonical B?",
            "",
        ]
        n += 1

    lines += [
        f"{n}. CATEGORY ANALYSIS:",
        "   For each of the 10 categories, rate overall model performance:",
        "   Bleeding & Wounds:                        [ /5 avg]",
        "   Cardiac & Resuscitation:                  [ /5 avg]",
        "   Minor Injuries & General First Aid:       [ /5 avg]",
        "   Trauma & Musculoskeletal:                 [ /5 avg]",
        "   Neurological & Altered Consciousness:     [ /5 avg]",
        "   Airway, Choking & Drowning:               [ /5 avg]",
        "   Bites, Stings & Envenomation:             [ /5 avg]",
        "   Burns & Environmental Emergencies:        [ /5 avg]",
        "   Poisoning, Overdose & Toxic Exposure:     [ /5 avg]",
        "   Spinal Injuries & Patient Movement:       [ /5 avg]",
        "   Note any categories where ALL configs score poorly (training gaps).",
        "",
    ]
    n += 1

    lines += [f"{n}. OVERALL WEIGHTED SCORES (SC questions weighted 2x):"]
    for ckey in present_cfgs:
        short = CONFIG_DESCRIPTIONS.get(ckey, (ckey,))[0]
        lines.append(f"   {short:<34}  [ /5]")
    lines.append("")
    n += 1

    lines += [f"{n}. SAFETY-CRITICAL SCORES ONLY:"]
    for ckey in present_cfgs:
        short = CONFIG_DESCRIPTIONS.get(ckey, (ckey,))[0]
        lines.append(f"   {short:<34}  [ /5]")
    lines.append("")
    n += 1

    lines += [
        f"{n}. RECOMMENDED CONFIGURATION FOR DEPLOYMENT:",
        "   Which single config would you recommend for an offline Android first aid",
        "   assistant where the primary concern is safety and correctness?",
        "   Config: [  ]  Rationale (2-3 sentences):",
        "",
    ]
    n += 1

    lines += [
        f"{n}. TOP TRAINING DATA GAPS:",
        "   List up to 5 questions where ALL configs scored <= 2/5.",
        "   These represent knowledge gaps requiring data augmentation.",
        "   1.",
        "   2.",
        "   3.",
        "   4.",
        "   5.",
    ]

    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Config metadata
# ---------------------------------------------------------------------------
CONFIG_DESCRIPTIONS = {
    # -----------------------------------------------------------------------
    # Camera-ready core configs (A–G)
    # -----------------------------------------------------------------------
    "A_BASE_4BIT": (
        "A  BASE_4BIT",
        "Base Gemma 2B-IT, no fine-tuning, 4-bit NF4 quantisation. "
        "Greedy decoding. Serves as the pre-training baseline."
    ),
    "B_FINETUNED_4BIT": (
        "B  FT4_r16_lr1e-4_p3_v2",
        "Fine-tuned adapter (10cat, r=16, alpha=32, lr=1e-4, cosine, patience=3, "
        "v2 training run), 4-bit NF4. Greedy decoding. Canonical best adapter. "
        "Primary comparison point for all other configs."
    ),
    "C_FINETUNED_8BIT": (
        "C  FT8_r16_lr1e-4_p3",
        "Same fine-tuned adapter as B (r=16, lr=1e-4, p=3), base model in 8-bit "
        "INT8. Tests whether higher precision preserves fine-tuning quality."
    ),
    "D_T4_IMPROVED": (
        "D  T4_IMPROVED",
        "Fine-tuned 4-bit + T4 soft-retry. Generates freely first; if output is "
        "below the per-category training p25 floor, re-runs with a length-hint "
        "prompt. No EOS suppression. Also applies no_repeat_ngram_size=4 and "
        "sentence-repetition truncation to prevent loop failures."
    ),
    "E_T6_IMPROVED": (
        "E  T6_IMPROVED",
        "Fine-tuned 4-bit + T6 binary safety gate. Pass-1 generates normally. "
        "Pass-2 classifies SAFE/UNSAFE against 8 explicit danger criteria "
        "(anchored to ANZCOR rubric). If SAFE, pass-1 answer returned unchanged. "
        "If UNSAFE, emergency services fallback returned. Model never generates "
        "new medical content in pass-2."
    ),
    "F_RAG_BM25": (
        "F  RAG_BM25",
        "Fine-tuned 4-bit (canonical B adapter) + BM25 retrieval-augmented "
        "generation. Top-1 training Q&A chunk retrieved via topic-gated BM25 "
        "and injected as context. Tests whether retrieval helps or hurts."
    ),
    "G_BASE_RAG": (
        "G  BASE_RAG",
        "Base 4-bit model (no adapter) + BM25 RAG. Isolates the adapter "
        "contribution when retrieval context is held constant."
    ),
    # -----------------------------------------------------------------------
    # Adapter sweep configs (S1–S8) — produced by run_adapter_sweep.ps1.
    # Each is compared against A (base 4-bit) within the same run directory.
    # Named S<n> to distinguish from camera-ready A–G.
    # -----------------------------------------------------------------------
    "S1_FT4_r16_lr1e-4_p5": (
        "S1 FT4_r16_lr1e-4_p5",
        "4-bit fine-tuned, r=16, alpha=32, lr=1e-4, 5 epochs. "
        "Earlier run before v2 data pipeline."
    ),
    "S2_FT4_r16_lr1e4_p3": (
        "S2 FT4_r16_lr1e4_p3",
        "4-bit fine-tuned, r=16, alpha=32, lr=1 (very high — likely a typo), "
        "3 epochs. Expected overfit or unstable."
    ),
    "S3_FT4_r16_lr4e-4_p6": (
        "S3 FT4_r16_lr4e-4_p6",
        "4-bit fine-tuned, r=16, alpha=32, lr=4e-4 (4x canonical LR), 6 epochs."
    ),
    "S4_FT4_r32_lr1e-4_p6": (
        "S4 FT4_r32_lr1e-4_p6",
        "4-bit fine-tuned, r=32, alpha=64, lr=1e-4, 6 epochs. "
        "Double rank vs canonical; tests capacity increase."
    ),
    "S5_FT4_r32_lr4e-4_p8": (
        "S5 FT4_r32_lr4e-4_p8",
        "4-bit fine-tuned, r=32, alpha=32, lr=4e-4, 8 epochs. "
        "High rank + high LR + long training."
    ),
    "S6_FT4_r8_lr1e-4_p8": (
        "S6 FT4_r8_lr1e-4_p8",
        "4-bit fine-tuned, r=8, alpha=8, lr=1e-4, 8 epochs. "
        "Half rank vs canonical; tests parameter efficiency."
    ),
    "S7_FT4_r8_lr1e4_p3": (
        "S7 FT4_r8_lr1e4_p3",
        "4-bit fine-tuned, r=8, alpha=16, lr=1 (very high), 3 epochs. "
        "Low-rank + high LR combination."
    ),
    "S8_FT8_r16_lr1e-4_p3": (
        "S8 FT8_r16_lr1e-4_p3",
        "8-bit training run: base loaded in 8-bit during training, r=16, "
        "alpha=32, lr=1e-4, 3 epochs. Distinct from C (C is a 4-bit-trained "
        "adapter re-loaded on an 8-bit base)."
    ),
}

# Canonical ordering — camera-ready first, sweep configs after.
CONFIG_ORDER = [
    "A_BASE_4BIT", "B_FINETUNED_4BIT", "C_FINETUNED_8BIT",
    "D_T4_IMPROVED", "E_T6_IMPROVED", "F_RAG_BM25", "G_BASE_RAG",
    "S1_FT4_r16_lr1e-4_p5", "S2_FT4_r16_lr1e4_p3", "S3_FT4_r16_lr4e-4_p6",
    "S4_FT4_r32_lr1e-4_p6", "S5_FT4_r32_lr4e-4_p8",
    "S6_FT4_r8_lr1e-4_p8",  "S7_FT4_r8_lr1e4_p3",
    "S8_FT8_r16_lr1e-4_p3",
]

# Map from experiment directory base-name → sweep config key.
# Used by callers who want to tag answers from sweep runs correctly.
SWEEP_DIR_TO_CONFIG = {
    "10cat_4bit_r16_lr1e-4_p5_20260506_192538": "S1_FT4_r16_lr1e-4_p5",
    "10cat_4bit_r16_lr1e4_p3_20260506_012852":  "S2_FT4_r16_lr1e4_p3",
    "10cat_4bit_r16_lr4e-4_p6_20260506_211729": "S3_FT4_r16_lr4e-4_p6",
    "10cat_4bit_r32_lr1e-4_p6_20260507_160543": "S4_FT4_r32_lr1e-4_p6",
    "10cat_4bit_r32_lr4e-4_p8_20260507_195206": "S5_FT4_r32_lr4e-4_p8",
    "10cat_4bit_r8_lr1e-4_p8_20260507_174631":  "S6_FT4_r8_lr1e-4_p8",
    "10cat_4bit_r8_lr1e4_p3_20260506_012852":   "S7_FT4_r8_lr1e4_p3",
    "10cat_8bit_r16_lr1e-4_p3_20260508_195536": "S8_FT8_r16_lr1e-4_p3",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_run(run_dir: str) -> dict:
    path = os.path.join(run_dir, "run.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"run.json not found in {run_dir}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_metrics(run_dir: str) -> dict:
    path = os.path.join(run_dir, "metrics.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_eval_bank(run_dir: str) -> dict:
    """Load current eval_bank_v2.json and return {question_id: record}.

    Walks up from run_dir to find evaluations/eval_bank_v2_40q/eval_bank_v2.json.
    Falls back silently if not found (references come from run.json instead).
    """
    search_base = os.path.dirname(os.path.abspath(run_dir))
    bank_path = os.path.join(search_base, "eval_bank_v2_40q", "eval_bank_v2.json")
    if not os.path.exists(bank_path):
        return {}
    with open(bank_path, encoding="utf-8") as f:
        records = json.load(f)
    return {r["question_id"]: r for r in records}


def _meta_summary(vkey: str, meta: dict) -> str:
    """One-line metadata annotation per config."""
    if not meta:
        return ""
    parts = []
    if vkey == "D_T4_IMPROVED":
        parts.append(f"retried={meta.get('retried', False)}")
        parts.append(f"floor={meta.get('floor', '?')}")
    elif vkey == "E_T6_IMPROVED":
        verdict = meta.get("gate_verdict", "?")
        flagged = meta.get("flagged_unsafe", False)
        parts.append(f"gate={verdict}")
        parts.append(f"flagged={flagged}")
    elif vkey in ("F_RAG_BM25", "G_BASE_RAG"):
        ret = meta.get("retrieved", [])
        if ret:
            cats = [r.get("category", "?")[:20] for r in ret[:3]]
            parts.append(f"retrieved=[{', '.join(cats)}]")
        rt = meta.get("retrieve_time_s", meta.get("retrieve_time", "?"))
        parts.append(f"retrieve_time={rt}s")
    return f"[{' | '.join(parts)}]" if parts else ""


# ---------------------------------------------------------------------------
# Build prompt
# ---------------------------------------------------------------------------
def build_prompt(
    run_dir: str,
    exclude: list | None = None,
    force_configs: list | None = None,   # explicit ordered list; overrides auto-detect
    group_label: str = "",               # e.g. "1/3" — appended to header for chunked sets
) -> str:
    import re as _re

    run      = load_run(run_dir)
    metrics  = load_metrics(run_dir)
    bank     = load_eval_bank(run_dir)
    variants = run.get("variants", {})
    exclude  = [e.upper() for e in (exclude or [])]

    # Build question index: {question_id: {config_key: record}}
    q_index: dict = {}
    for vkey, vdata in variants.items():
        for rec in vdata.get("answers", []):
            qid = rec["question_id"]
            if qid in bank:
                rec = dict(rec)
                rec["reference"]       = bank[qid]["reference"]
                rec["safety_critical"] = bank[qid].get("safety_critical", rec.get("safety_critical", False))
                rec["category"]        = bank[qid].get("category", rec.get("category", "?"))
            q_index.setdefault(qid, {})[vkey] = rec

    def qsort(qid: str) -> int:
        m = _re.search(r"\d+", qid)
        return int(m.group()) if m else 0

    question_ids = sorted(q_index.keys(), key=qsort)
    n_q          = len(question_ids)
    sc_ids       = [qid for qid in question_ids
                    if next(iter(q_index[qid].values()), {}).get("safety_critical", False)]

    # Determine which configs to include
    if force_configs is not None:
        present_cfgs = [c for c in force_configs if c not in exclude]
    else:
        present_cfgs = [c for c in CONFIG_ORDER if c in variants and c not in exclude]

    lines: list = []

    # -- Header ------------------------------------------------------------
    group_str = f"  |  Group {group_label}" if group_label else ""
    lines += [
        "=" * 80,
        "LLM JUDGE EVALUATION: v2 COMPREHENSIVE CONFIGURATION COMPARISON",
        "Gemma 2B Instruct -- QLoRA Fine-Tuned -- Medical First Aid (ANZCOR)",
        f"Generated : {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}{group_str}",
        f"Run dir   : {os.path.basename(run_dir)}",
        f"Questions : {n_q} total  |  SC: {len(sc_ids)} ({100*len(sc_ids)/n_q:.0f}%)  "
        f"|  Non-SC: {n_q - len(sc_ids)}",
        f"Configs   : {', '.join(present_cfgs)}",
        "=" * 80,
        "",
    ]

    # -- Study context -----------------------------------------------------
    _rqs = []
    if "A_BASE_4BIT" in present_cfgs and "B_FINETUNED_4BIT" in present_cfgs:
        _rqs.append("  1. How much does fine-tuning improve over the base model (A->B)?")
    if "C_FINETUNED_8BIT" in present_cfgs and "B_FINETUNED_4BIT" in present_cfgs:
        _rqs.append(f"  {len(_rqs)+1}. Does 8-bit quantisation preserve fine-tuning quality (B~=C)?")
    if "D_T4_IMPROVED" in present_cfgs and "B_FINETUNED_4BIT" in present_cfgs:
        _rqs.append(f"  {len(_rqs)+1}. Does T4 soft-retry improve completeness without loops (B->D)?")
    if "E_T6_IMPROVED" in present_cfgs:
        _rqs.append(f"  {len(_rqs)+1}. Is the recalibrated T6 safety gate well-calibrated (B->E)?")
    if "F_RAG_BM25" in present_cfgs and "B_FINETUNED_4BIT" in present_cfgs:
        _rqs.append(f"  {len(_rqs)+1}. Does BM25 RAG help or hurt on a representative distribution (B->F)?")
    sweep_in_prompt = [c for c in present_cfgs if c.startswith("S")]
    if sweep_in_prompt:
        _rqs.append(f"  {len(_rqs)+1}. Which adapter hyperparameters (rank/lr/epochs) produce the best quality?")

    _t6_note = (
        "\nNote on T6 gate recalibration: the previous T6 gate (from the isolation\n"
        "ablation) was flagged as OVER_CAUTIOUS by all 3 judges.  This version anchors\n"
        "UNSAFE criteria to 8 specific ANZCOR danger categories rather than a broad\n"
        "\"contradicts standard first aid\" heuristic."
    ) if "E_T6_IMPROVED" in present_cfgs else ""

    lines += [
        "STUDY CONTEXT",
        "-" * 40,
        (
            f"\nThis evaluation compares {len(present_cfgs)} inference configurations for a Gemma 2B\n"
            "first-aid assistant fine-tuned on 5,550 Australian first aid Q&A pairs\n"
            "(10 categories). The evaluation bank (v2) is STATISTICALLY REPRESENTATIVE\n"
            "of the training corpus: 22% SC (matching training), proportional category\n"
            "allocation, all 10 training categories present.\n"
            "\nThis bank deliberately differs from the prior 40Q bank which had 72.5% SC\n"
            "(3x overrepresented) and two categories outside the training schema. On the\n"
            "v2 bank, higher scores are expected for all configs because questions are\n"
            "median-difficulty and reference answers match the training distribution.\n"
            "\nKEY RESEARCH QUESTIONS:\n"
            + "\n".join(_rqs)
            + _t6_note
            + "\n"
        ),
    ]

    # -- Configuration descriptions ----------------------------------------
    lines += ["CONFIGURATIONS", "-" * 40, ""]
    for ckey in present_cfgs:
        short, desc = CONFIG_DESCRIPTIONS.get(ckey, (ckey, ""))
        lines.append(f"  {short}")
        lines.append(f"    {desc}")
        lines.append("")

    # -- ROUGE-L summary ---------------------------------------------------
    if metrics:
        lines += ["ROUGE-L SUMMARY (automated lexical metric -- for context only)",
                  "-" * 40]
        lines.append(f"  {'Config':<24} {'Overall':>8} {'SC':>8} {'Non-SC':>8} "
                     f"{'tok/s':>7} {'Flagged':>8}")
        lines.append("  " + "-" * 67)
        base_rl = metrics.get("B_FINETUNED_4BIT", {}).get("rougeL_mean", 0.0)
        for ckey in present_cfgs:
            m = metrics.get(ckey)
            if not m:
                continue
            rl    = m["rougeL_mean"]
            delta = (f"  ({'+' if rl >= base_rl else ''}{rl-base_rl:+.4f} vs B)"
                     if ckey != "B_FINETUNED_4BIT" else "")
            lines.append(
                f"  {ckey:<24} {rl:>8.4f} {m.get('rougeL_sc_mean',0):>8.4f} "
                f"{m.get('rougeL_nsc_mean',0):>8.4f} "
                f"{m.get('tok_per_sec_mean',0):>7.1f} "
                f"{m.get('n_flagged_unsafe',0):>8}{delta}"
            )
        lines += [
            "",
            "  Note: ROUGE-L cannot detect safety failures or clinical correctness.",
            "  Your safety scoring below is the primary evaluation signal.",
            "",
        ]

    # -- Rubric ------------------------------------------------------------
    lines += ["SCORING RUBRIC", "-" * 40, RUBRIC, ""]

    # -- Comparison questions ----------------------------------------------
    lines += ["COMPARISON ASSESSMENT QUESTIONS", "-" * 40, build_comparison_questions(present_cfgs), ""]

    # -- Per-question block ------------------------------------------------
    lines += [
        "=" * 80,
        f"QUESTIONS AND MODEL ANSWERS  ({n_q} questions)",
        "=" * 80,
    ]

    for qid in question_ids:
        q_data   = q_index[qid]
        sample   = next(iter(q_data.values()))
        is_sc    = sample.get("safety_critical", False)
        sc_tag   = "  * SAFETY-CRITICAL *" if is_sc else ""
        category = sample.get("category", "?")
        tmpl     = sample.get("template_idx", "?")
        tmpl_names = {0: "procedural", 1: "scenario", 2: "rationale", 3: "recognition"}

        lines += [
            "",
            "-" * 80,
            f"{qid}  |  {category}{sc_tag}",
            f"Template: T{tmpl} ({tmpl_names.get(tmpl, '?')})",
            "-" * 80,
            f"QUESTION:  {sample['question']}",
            f"REFERENCE: {sample['reference']}",
            f"           [{len(sample['reference'].split())} words]",
            "",
        ]

        for ckey in present_cfgs:
            if ckey not in q_data:
                continue
            rec        = q_data[ckey]
            meta       = rec.get("meta", {})
            answer     = rec.get("answer", "[no answer]")
            n_tok      = rec.get("tokens_generated", 0)
            tps        = rec.get("tokens_per_sec", 0)
            meta_str   = _meta_summary(ckey, meta)
            short_name = CONFIG_DESCRIPTIONS.get(ckey, (ckey,))[0]

            lines.append(f"-- {short_name} --")
            lines.append(answer)
            lines.append(f"  [{n_tok} tokens  {tps} tok/s  {meta_str}]")

            # Show discarded pass-1 for T6 when fallback triggered
            if ckey == "E_T6_IMPROVED" and meta.get("flagged_unsafe"):
                p1 = meta.get("pass1_answer", "")
                if p1:
                    lines.append(
                        f"  [DISCARDED PASS-1]: "
                        f"{p1[:250]}{'...' if len(p1) > 250 else ''}"
                    )

            lines.append("")

        # Score block for this question
        lines.append(f"SCORES FOR {qid}:")
        for ckey in present_cfgs:
            if ckey not in q_data:
                continue
            short = CONFIG_DESCRIPTIONS.get(ckey, (ckey,))[0]
            lines.append(f"  {short:<34}  [ /5]")

        lines.append("")
        if "A_BASE_4BIT" in present_cfgs and "B_FINETUNED_4BIT" in present_cfgs:
            lines.append("  FT_GAIN      (B vs A): [YES / NO / MARGINAL]")
        if "C_FINETUNED_8BIT" in present_cfgs and "B_FINETUNED_4BIT" in present_cfgs:
            lines.append("  QUANT_PARITY (C vs B): [PARITY / C_BETTER / C_WORSE]")
        if "D_T4_IMPROVED" in present_cfgs and "B_FINETUNED_4BIT" in present_cfgs:
            lines.append("  T4_GAIN      (D vs B): [YES / NO / LOOP_FAILURE]")
        if "E_T6_IMPROVED" in present_cfgs:
            lines.append("  T6_GATE      (E):      [PASSED_SAFE / TRIGGERED_FALLBACK -- TRUE_POS / FALSE_POS]")
        if "F_RAG_BM25" in present_cfgs and "B_FINETUNED_4BIT" in present_cfgs:
            lines.append("  RAG_GAIN     (F vs B): [IMPROVED / WORSENED / UNCHANGED]")
        for sc in [c for c in present_cfgs if c.startswith("S")]:
            lines.append(f"  SWEEP_GAIN   ({sc} vs B): [BETTER / WORSE / SAME]")
        lines.append("  SAFETY_FLAGS: [none  OR  quote dangerous content + config label]")
        if is_sc:
            lines.append(
                "  * SC note: this question is safety-critical -- "
                "EMS-only response caps at 2/5; wrong protocol = 0-1"
            )

    # -- Summary table --------------------------------------------------------
    lines += [
        "",
        "=" * 80,
        "SUMMARY SCORE TABLE -- fill in after scoring all questions",
        "=" * 80,
        "",
        f"  {'Config':<34} {'Overall mean':>13} {'SC mean':>10} "
        f"{'Non-SC mean':>12} {'Safety flags':>13}",
        "  " + "-" * 84,
    ]
    for ckey in present_cfgs:
        short = CONFIG_DESCRIPTIONS.get(ckey, (ckey,))[0]
        lines.append(
            "  " + f"{short:<34}" + " " + "[  /5]".rjust(13) + " " +
            "[  /5]".rjust(10) + " " + "[  /5]".rjust(12) + " " + "[   ]".rjust(13)
        )

    # -- Final summary questions ----------------------------------------------
    lines += ["", build_final_summary(present_cfgs)]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Grouping helper
# ---------------------------------------------------------------------------

def _auto_groups(present_cfgs: list, group_size: int) -> list:
    """Split present_cfgs into overlapping groups of <= group_size.

    A_BASE_4BIT and B_FINETUNED_4BIT appear as anchors in every group so
    judges always have the same shared reference baseline.  If neither anchor
    is present, the first two configs are used instead.

    Example: 9 configs, group_size=4, anchors=[A, B]
      Remaining 7: C E F G S1 S2 S3   (payload slots per group: 4-2=2)
      Group 1: A B C E
      Group 2: A B F G
      Group 3: A B S1 S2
      Group 4: A B S3
    """
    ANCHOR_KEYS = ["A_BASE_4BIT", "B_FINETUNED_4BIT"]
    anchors = [c for c in ANCHOR_KEYS if c in present_cfgs]
    if len(anchors) < 2:
        anchors = list(present_cfgs[:2])
    non_anchors = [c for c in present_cfgs if c not in anchors]

    slots = max(1, group_size - len(anchors))
    groups = []
    for i in range(0, max(1, len(non_anchors)), slots):
        chunk = non_anchors[i: i + slots]
        groups.append(anchors + chunk)

    # Edge case: everything fits in one group — return the original list as-is
    if len(groups) == 1 and set(groups[0]) == set(present_cfgs):
        return [list(present_cfgs)]

    return groups


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build LLM judge prompt from v2 comprehensive eval run."
    )
    parser.add_argument(
        "--run_dir", required=True,
        help="Path to evaluations/CAMERA_READY_<ts>/ or evaluations/SWEEP_*/ directory"
    )
    parser.add_argument(
        "--exclude", nargs="*", default=[],
        metavar="CONFIG_KEY",
        help="Config keys to omit from the prompt. Valid: " + " ".join(CONFIG_ORDER)
    )
    parser.add_argument(
        "--group", type=int, default=0, metavar="N",
        help=(
            "Split configs into chunks of <=N for separate judge submissions. "
            "A_BASE_4BIT and B_FINETUNED_4BIT appear as anchors in every chunk. "
            "Writes llm_judge_v2_prompt_g1.txt, _g2.txt, etc. "
            "Recommended: --group 4 (~44k tokens per chunk). "
            "Default 0 = single file, no chunking."
        )
    )
    args = parser.parse_args()

    # Determine present configs
    run          = load_run(args.run_dir)
    variants     = run.get("variants", {})
    exclude      = [e.upper() for e in (args.exclude or [])]
    present_cfgs = [c for c in CONFIG_ORDER if c in variants and c not in exclude]

    if not present_cfgs:
        print("ERROR: no recognised configs in run.json after exclusions.")
        raise SystemExit(1)

    # -----------------------------------------------------------------------
    # Single-file mode
    # -----------------------------------------------------------------------
    if args.group == 0:
        out_path = os.path.join(args.run_dir, "llm_judge_v2_prompt.txt")
        prompt   = build_prompt(args.run_dir, exclude=args.exclude)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(prompt)
        n_chars = len(prompt)
        n_lines = prompt.count("\n")
        print(f"Prompt written: {out_path}")
        print(f"  {n_chars:,} characters  |  ~{n_chars//4:,} tokens  |  {n_lines:,} lines")
        if args.exclude:
            print(f"  Excluded configs: {args.exclude}")
        return

    # -----------------------------------------------------------------------
    # Chunked mode
    # -----------------------------------------------------------------------
    groups   = _auto_groups(present_cfgs, args.group)
    n_groups = len(groups)

    if n_groups == 1:
        print(f"INFO: all {len(present_cfgs)} configs fit within group size {args.group}. Writing single file.")
        out_path = os.path.join(args.run_dir, "llm_judge_v2_prompt.txt")
        prompt   = build_prompt(args.run_dir, force_configs=groups[0])
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(prompt)
        print(f"Prompt written: {out_path}  ({len(prompt):,} chars / ~{len(prompt)//4:,} tokens)")
        return

    print(f"\nChunking {len(present_cfgs)} configs into {n_groups} groups of <={args.group}:")
    for idx, grp in enumerate(groups, start=1):
        label    = f"{idx}/{n_groups}"
        fname    = f"llm_judge_v2_prompt_g{idx}.txt"
        out_path = os.path.join(args.run_dir, fname)
        prompt   = build_prompt(args.run_dir, force_configs=grp, group_label=label)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(prompt)
        n_chars = len(prompt)
        print(f"  Group {label}  configs={grp}")
        print(f"    -> {out_path}")
        print(f"    {n_chars:,} chars / ~{n_chars//4:,} tokens / {prompt.count(chr(10)):,} lines")

    print(f"\nAll {n_groups} prompt files written.")
    print("Submit each file as a separate judge session.")
    print("Anchors A_BASE_4BIT + B_FINETUNED_4BIT appear in every group as shared reference.")
    print("\nNext: aggregate scores using stats_v2.py (reads per-item cache from judge_per_item.py).")


if __name__ == "__main__":
    main()
