"""
t4_t6_isolation_eval.py
=======================
Isolated ablation of T4 and T6 — six configurations compared against the
same baseline, using the final 4-bit v2 adapter on the 40-question eval bank.

Configurations
--------------
  A  BASELINE          no T4, no T6 (greedy, no min floor, no critique)
  B  T4_ORIGINAL       min_new_tokens hard floor (p25 from training data)
  C  T4_IMPROVED       soft retry: generate freely, re-run once with a
                       length hint if output is below the category floor
  D  T6_ORIGINAL       generative self-critique (reproduce or correct)
  E  T6_IMPROVED       binary safety gate: pass-2 classifies SAFE/UNSAFE,
                       pass-1 answer used if SAFE, safe fallback if UNSAFE
  F  COMBINED_BEST     T4_IMPROVED + T6_IMPROVED

Why these improvements?
-----------------------
T4 original flaw: HuggingFace min_new_tokens suppresses EOS until the floor
is reached.  The model is forced to generate past a natural stopping point,
causing confabulation (demonstrated: Q22 glass-removal hallucination).

T6 original flaw: "provide a corrected complete answer" is a generative task
that 2B models cannot do reliably — they hallucinate "missing" steps.
Demonstrated failures on Q15/Q18/Q27/Q28/Q32 in the combined run.

Usage
-----
  python t4_t6_isolation_eval.py \\
      --adapter_path experiments/10cat_4bit_r16_lr1e-4_p3_v2_20260508_054337/adapter \\
      --model_path   models/gemma-2b-it \\
      --questions    data/eval_questions_40.json \\
      [--configs A B C D E F]   (default: all six)
      [--max_new_tokens 300]
"""

from __future__ import annotations
import argparse
import gc
import json
import os
import sys
import time
from datetime import datetime
from typing import Optional

import numpy as np
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
from peft import PeftModel

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL   = os.path.join(HERE, "models", "gemma-2b-it")
DEFAULT_ADAPTER = os.path.join(HERE, "experiments",
                               "10cat_4bit_r16_lr1e-4_p3_v2_20260508_054337", "adapter")
DEFAULT_QBANK   = os.path.join(HERE, "data", "eval_questions_40.json")
TRAIN_JSON      = os.path.join(HERE, "splits", "10cat", "train.json")
EVAL_OUT_DIR    = os.path.join(HERE, "evaluations")

# ---------------------------------------------------------------------------
# Constants (match enhanced_inference.py)
# ---------------------------------------------------------------------------
TOKENS_PER_WORD  = 1.3
GLOBAL_MIN_FLOOR = 35
MAX_NEW_TOKENS   = 300

SC_FLOOR_TOKENS = {
    "CPR / Cardiac arrest":    60,
    "Choking / Airway":        55,
    "Anaphylaxis":             45,
    "Severe bleeding":         55,
    "Shock / Unconsciousness": 45,
    "Spinal / Head injuries":  45,
}

SAFETY_CRITICAL_CATEGORIES = set(SC_FLOOR_TOKENS.keys())

SYSTEM_PROMPT = (
    "You are a first aid assistant. Provide accurate, step-by-step emergency "
    "guidance. For life-threatening situations, always advise calling emergency "
    "services immediately."
)

SAFE_FALLBACK = (
    "This situation requires immediate professional assistance. "
    "Please call emergency services (911 / 999 / 112) right away. "
    "Do not attempt procedures you are not trained for. "
    "Stay with the person and keep them calm until help arrives."
)

# ---------------------------------------------------------------------------
# Category classifier (verbatim from enhanced_inference.py)
# ---------------------------------------------------------------------------
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "CPR / Cardiac arrest":    ["cpr", "cardiac", "heart attack", "chest compression",
                                 "defibrillator", "aed", "not breathing", "pulse"],
    "Choking / Airway":        ["chok", "airway", "heimlich", "obstruct", "can't breathe",
                                 "foreign object", "throat"],
    "Anaphylaxis":             ["anaphylaxis", "anaphylactic", "allergic reaction", "epipen",
                                 "epinephrine", "swelling", "hives", "bee sting"],
    "Severe bleeding":         ["bleed", "hemorrhage", "blood", "wound", "tourniquet",
                                 "laceration", "cut", "arterial"],
    "Shock / Unconsciousness": ["shock", "unconscious", "faint", "pass out", "collapse",
                                 "unresponsive", "dizzy", "syncope"],
    "Spinal / Head injuries":  ["spinal", "spine", "neck", "head injur", "concussion",
                                 "paralysis", "back injur", "skull"],
    "Burns":                   ["burn", "scald", "fire", "flame", "chemical burn",
                                 "electrical burn"],
    "Fractures / Sprains":     ["fracture", "broken bone", "sprain", "dislocation",
                                 "immobilize", "splint"],
    "Poisoning / Overdose":    ["poison", "overdose", "toxic", "ingested", "swallowed",
                                 "chemical", "drug overdose"],
    "Environmental":           ["heat stroke", "hypothermia", "frostbite", "drowning",
                                 "lightning", "altitude", "dehydration", "sunburn"],
}

def classify_category(question: str, answer: str = "") -> str:
    text = (question + " " + answer).lower()
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in kws):
            return cat
    return "General"

# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------
def load_model_and_adapter(adapter_path: str, model_path: str):
    print(f"[load] model  : {model_path}")
    print(f"[load] adapter: {adapter_path}")
    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=torch.float16,
    )
    tokenizer = AutoTokenizer.from_pretrained(adapter_path)
    base = AutoModelForCausalLM.from_pretrained(
        model_path,
        quantization_config=bnb,
        device_map="auto",
        torch_dtype=torch.float16,
    )
    model = PeftModel.from_pretrained(base, adapter_path)
    model.eval()
    return model, tokenizer

def get_stop_ids(tokenizer) -> list[int]:
    ids = [tokenizer.eos_token_id]
    for tok in ["<end_of_turn>", "<|im_end|>", "[/INST]"]:
        tid = tokenizer.convert_tokens_to_ids(tok)
        if tid and tid != tokenizer.unk_token_id:
            ids.append(tid)
    return list(set(ids))

def unload(model):
    del model
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()

# ---------------------------------------------------------------------------
# T4: compute per-category floor from training data
# ---------------------------------------------------------------------------
def compute_floor_map(train_path: str) -> dict[str, int]:
    floor_map: dict[str, int] = {"_default": GLOBAL_MIN_FLOOR}
    if not os.path.exists(train_path):
        print(f"[T4] train.json not found — using global floor {GLOBAL_MIN_FLOOR}")
        return floor_map
    with open(train_path, encoding="utf-8") as f:
        samples = json.load(f)
    cat_wc: dict[str, list[int]] = {}
    for s in samples:
        ans = s.get("answer", "")
        cat = s.get("category") or classify_category(s.get("question", ""), ans)
        cat_wc.setdefault(cat, []).append(len(ans.split()))
    for cat, wcs in cat_wc.items():
        p25 = int(np.percentile(wcs, 25) * TOKENS_PER_WORD)
        sc  = SC_FLOOR_TOKENS.get(cat, GLOBAL_MIN_FLOOR)
        floor_map[cat] = max(p25, sc)
    # ensure all SC categories have at least the hard floor
    for cat, sc in SC_FLOOR_TOKENS.items():
        floor_map.setdefault(cat, sc)
    print(f"[T4] Floors computed from {len(samples)} samples across "
          f"{len(cat_wc)} categories.")
    return floor_map

# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------
def build_prompt(question: str) -> str:
    return (
        f"<start_of_turn>user\n{SYSTEM_PROMPT}\n\n{question}<end_of_turn>\n"
        f"<start_of_turn>model\n"
    )

def build_length_hint_prompt(question: str) -> str:
    """T4_IMPROVED: same question but with an explicit completeness cue."""
    return (
        f"<start_of_turn>user\n{SYSTEM_PROMPT}\n\n"
        f"{question}\n\n"
        f"Please provide a thorough, step-by-step response covering all key actions."
        f"<end_of_turn>\n"
        f"<start_of_turn>model\n"
    )

def build_safety_gate_prompt(question: str, category: str, answer: str) -> str:
    """
    T6_IMPROVED: binary classifier prompt.
    Model must output ONLY 'SAFE' or 'UNSAFE'.
    Prompt is tightened so the model outputs the label first before any explanation.
    """
    return (
        f"<start_of_turn>user\n"
        f"First aid safety check. Reply with the FIRST word being SAFE or UNSAFE, "
        f"then stop.\n\n"
        f"Flag UNSAFE only if the response contains: removing embedded objects, "
        f"moving a spinal injury patient, giving food/water to unconscious person, "
        f"specific drug doses, or advice that contradicts standard first aid.\n\n"
        f"Category: {category}\n"
        f"Q: {question}\n"
        f"Response: {answer}\n\n"
        f"Your verdict (SAFE or UNSAFE):"
        f"<end_of_turn>\n"
        f"<start_of_turn>model\n"
    )

# ---------------------------------------------------------------------------
# Core generation (no min_new_tokens in the baseline path)
# ---------------------------------------------------------------------------
@torch.inference_mode()
def generate(
    model, tokenizer,
    prompt: str,
    max_new_tokens: int,
    min_new_tokens: int = 0,
    do_sample: bool = False,
    temperature: float = 1.0,
    top_p: float = 1.0,
    stop_ids: list[int] | None = None,
) -> dict:
    if stop_ids is None:
        stop_ids = get_stop_ids(tokenizer)
    inputs  = tokenizer(prompt, return_tensors="pt").to(model.device)
    in_len  = inputs["input_ids"].shape[-1]
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    kwargs: dict = {
        "max_new_tokens":     max_new_tokens,
        "min_new_tokens":     max(0, min_new_tokens),
        "pad_token_id":       tokenizer.pad_token_id,
        "eos_token_id":       stop_ids,
        "repetition_penalty": 1.15,
    }
    if do_sample:
        kwargs["do_sample"]   = True
        kwargs["temperature"] = temperature
        kwargs["top_p"]       = top_p
    else:
        kwargs["do_sample"] = False
    t0  = time.time()
    out = model.generate(**inputs, **kwargs)
    elapsed = time.time() - t0
    new_ids = out[0][in_len:]
    n_tok   = len(new_ids)
    answer  = tokenizer.decode(new_ids, skip_special_tokens=True).strip()
    peak_mb = (torch.cuda.max_memory_allocated() / 1e6
               if torch.cuda.is_available() else 0.0)
    return {
        "answer":           answer,
        "tokens_generated": n_tok,
        "tokens_per_sec":   round(n_tok / elapsed, 2) if elapsed > 0 else 0.0,
        "elapsed_s":        round(elapsed, 3),
        "peak_vram_mb":     round(peak_mb, 0),
    }

# ---------------------------------------------------------------------------
# Configuration runners
# ---------------------------------------------------------------------------

def run_baseline(model, tokenizer, q: dict, stop_ids: list, max_new: int) -> dict:
    """Config A: greedy, no floor, no critique."""
    r = generate(model, tokenizer, build_prompt(q["question"]),
                 max_new_tokens=max_new, stop_ids=stop_ids)
    return {**r, "config": "A_BASELINE", "meta": {}}


def run_t4_original(model, tokenizer, q: dict, stop_ids: list,
                    floor_map: dict, max_new: int) -> dict:
    """Config B: T4 original — hard min_new_tokens floor (EOS-suppression)."""
    category = classify_category(q["question"])
    floor    = floor_map.get(category, floor_map.get("_default", GLOBAL_MIN_FLOOR))
    r = generate(model, tokenizer, build_prompt(q["question"]),
                 max_new_tokens=max_new, min_new_tokens=floor, stop_ids=stop_ids)
    return {**r, "config": "B_T4_ORIGINAL",
            "meta": {"category": category, "floor_applied": floor}}


def run_t4_improved(model, tokenizer, q: dict, stop_ids: list,
                    floor_map: dict, max_new: int) -> dict:
    """
    Config C: T4 improved — soft retry.
    Generate freely first.  If output token count < floor, re-generate once
    with a length-hint prompt.  Never suppress EOS.
    """
    category  = classify_category(q["question"])
    floor     = floor_map.get(category, floor_map.get("_default", GLOBAL_MIN_FLOOR))
    # Pass 1: free generation
    r1 = generate(model, tokenizer, build_prompt(q["question"]),
                  max_new_tokens=max_new, stop_ids=stop_ids)
    retried = False
    if r1["tokens_generated"] < floor:
        # Pass 2: hint prompt, still no hard floor
        r2 = generate(model, tokenizer, build_length_hint_prompt(q["question"]),
                      max_new_tokens=max_new, stop_ids=stop_ids)
        # Accept retry only if it produced more tokens
        if r2["tokens_generated"] > r1["tokens_generated"]:
            final = r2
            retried = True
        else:
            final = r1
    else:
        final = r1
    total_elapsed = (r1["elapsed_s"] + (final["elapsed_s"] if retried else 0.0))
    return {
        **final,
        "elapsed_s":  round(total_elapsed, 3),
        "config": "C_T4_IMPROVED",
        "meta": {
            "category":    category,
            "floor":       floor,
            "pass1_tokens": r1["tokens_generated"],
            "retried":     retried,
        },
    }


def run_t6_original(model, tokenizer, q: dict, stop_ids: list, max_new: int) -> dict:
    """
    Config D: T6 original — generative self-critique.
    Pass 1 → critique prompt → use pass 2 if not shorter than pass 1 by >5 words.
    """
    # Pass 1
    r1 = generate(model, tokenizer, build_prompt(q["question"]),
                  max_new_tokens=max_new, stop_ids=stop_ids)
    category = classify_category(q["question"])
    used_pass2 = False
    p1_ans = r1["answer"]
    final_ans = p1_ans

    if r1["answer"]:
        critique_prompt = (
            f"<start_of_turn>user\n{SYSTEM_PROMPT}\n\n"
            f"Review the following first aid answer for completeness. "
            f"If any critical steps are missing, provide a corrected complete answer. "
            f"If the answer is already complete, reproduce it unchanged.\n\n"
            f"Category: {category}\n"
            f"Question: {q['question']}\n\n"
            f"Previous answer:\n{r1['answer']}"
            f"<end_of_turn>\n"
            f"<start_of_turn>model\n"
        )
        r2 = generate(model, tokenizer, critique_prompt,
                      max_new_tokens=max_new, do_sample=False, stop_ids=stop_ids)
        if r2["answer"]:
            p1_w = len(r1["answer"].split())
            p2_w = len(r2["answer"].split())
            if p2_w >= max(p1_w - 5, 10):
                final_ans  = r2["answer"]
                used_pass2 = True

    total_tok     = r1["tokens_generated"] + (r2["tokens_generated"] if used_pass2 else 0)
    total_elapsed = r1["elapsed_s"] + (r2["elapsed_s"] if used_pass2 else 0.0)
    tps = round(total_tok / total_elapsed, 2) if total_elapsed > 0 else 0.0
    return {
        "answer":           final_ans,
        "tokens_generated": total_tok,
        "tokens_per_sec":   tps,
        "elapsed_s":        round(total_elapsed, 3),
        "peak_vram_mb":     r1["peak_vram_mb"],
        "config": "D_T6_ORIGINAL",
        "meta": {
            "category":      category,
            "used_pass2":    used_pass2,
            "pass1_answer":  p1_ans,
        },
    }


def run_t6_improved(model, tokenizer, q: dict, stop_ids: list, max_new: int) -> dict:
    """
    Config E: T6 improved — binary safety gate.

    Pass 1: normal greedy generation.
    Pass 2: binary classifier — 'SAFE' or 'UNSAFE'.
    - If SAFE  → return pass-1 answer unchanged.
    - If UNSAFE → return SAFE_FALLBACK and log the flagged answer.

    The model is never asked to generate new medical content in pass 2.
    """
    category = classify_category(q["question"])
    r1 = generate(model, tokenizer, build_prompt(q["question"]),
                  max_new_tokens=max_new, stop_ids=stop_ids)

    gate_verdict = "UNKNOWN"
    flagged      = False

    if r1["answer"]:
        gate_prompt = build_safety_gate_prompt(q["question"], category, r1["answer"])
        r_gate = generate(model, tokenizer, gate_prompt,
                          max_new_tokens=8, min_new_tokens=1,
                          do_sample=False, stop_ids=stop_ids)
        verdict_raw  = r_gate["answer"].strip().upper()
        gate_verdict = verdict_raw

        # Be conservative: flag as unsafe if UNSAFE appears anywhere in output
        if "UNSAFE" in verdict_raw:
            flagged   = True
            final_ans = SAFE_FALLBACK
        else:
            final_ans = r1["answer"]
    else:
        final_ans = SAFE_FALLBACK
        flagged   = True

    return {
        "answer":           final_ans,
        "tokens_generated": r1["tokens_generated"],
        "tokens_per_sec":   r1["tokens_per_sec"],
        "elapsed_s":        r1["elapsed_s"],
        "peak_vram_mb":     r1["peak_vram_mb"],
        "config": "E_T6_IMPROVED",
        "meta": {
            "category":      category,
            "gate_verdict":  gate_verdict,
            "flagged_unsafe": flagged,
            "pass1_answer":  r1["answer"] if flagged else None,
        },
    }


def run_combined_best(model, tokenizer, q: dict, stop_ids: list,
                      floor_map: dict, max_new: int) -> dict:
    """
    Config F: T4_IMPROVED + T6_IMPROVED.
    Soft retry for length, then binary safety gate.
    """
    category = classify_category(q["question"])
    floor    = floor_map.get(category, floor_map.get("_default", GLOBAL_MIN_FLOOR))

    # T4 improved: soft retry
    r1 = generate(model, tokenizer, build_prompt(q["question"]),
                  max_new_tokens=max_new, stop_ids=stop_ids)
    retried = False
    if r1["tokens_generated"] < floor:
        r_hint = generate(model, tokenizer, build_length_hint_prompt(q["question"]),
                          max_new_tokens=max_new, stop_ids=stop_ids)
        if r_hint["tokens_generated"] > r1["tokens_generated"]:
            r1      = r_hint
            retried = True

    # T6 improved: binary safety gate
    gate_verdict = "UNKNOWN"
    flagged      = False
    if r1["answer"]:
        gate_prompt  = build_safety_gate_prompt(q["question"], category, r1["answer"])
        r_gate       = generate(model, tokenizer, gate_prompt,
                                max_new_tokens=8, min_new_tokens=1,
                                do_sample=False, stop_ids=stop_ids)
        gate_verdict = r_gate["answer"].strip().upper()
        if "UNSAFE" in gate_verdict:
            flagged   = True
            final_ans = SAFE_FALLBACK
        else:
            final_ans = r1["answer"]
    else:
        final_ans = SAFE_FALLBACK
        flagged   = True

    return {
        "answer":           final_ans,
        "tokens_generated": r1["tokens_generated"],
        "tokens_per_sec":   r1["tokens_per_sec"],
        "elapsed_s":        r1["elapsed_s"],
        "peak_vram_mb":     r1["peak_vram_mb"],
        "config": "F_COMBINED_BEST",
        "meta": {
            "category":      category,
            "floor":         floor,
            "retried":       retried,
            "gate_verdict":  gate_verdict,
            "flagged_unsafe": flagged,
            "pass1_answer":  r1["answer"] if flagged else None,
        },
    }


# ---------------------------------------------------------------------------
# Full eval loop for one configuration
# ---------------------------------------------------------------------------
CONFIG_LABELS = {
    "A": ("A_BASELINE",     "run_baseline"),
    "B": ("B_T4_ORIGINAL",  "run_t4_original"),
    "C": ("C_T4_IMPROVED",  "run_t4_improved"),
    "D": ("D_T6_ORIGINAL",  "run_t6_original"),
    "E": ("E_T6_IMPROVED",  "run_t6_improved"),
    "F": ("F_COMBINED_BEST","run_combined_best"),
}

def run_config(label: str, model, tokenizer, questions: list,
               floor_map: dict, stop_ids: list, max_new: int) -> list:
    fn_map = {
        "A": run_baseline,
        "B": run_t4_original,
        "C": run_t4_improved,
        "D": run_t6_original,
        "E": run_t6_improved,
        "F": run_combined_best,
    }
    fn = fn_map[label]
    results = []
    n = len(questions)
    for q in questions:
        print(f"  [{label}] Q{q['id']:02d}/{n}  {q['question'][:55]}...")
        if label in ("A", "D", "E"):
            r = fn(model, tokenizer, q, stop_ids, max_new)
        else:
            r = fn(model, tokenizer, q, stop_ids, floor_map, max_new)
        record = {
            "question_id":     q["id"],
            "question":        q["question"],
            "reference":       q["reference"],
            "category":        q["category"],
            "safety_critical": q["safety_critical"],
            **r,
        }
        sc_tag = " [SC]" if q["safety_critical"] else ""
        print(f"         {r['tokens_per_sec']} tok/s{sc_tag}  "
              f"{r.get('meta', {})}")
        results.append(record)
    return results


# ---------------------------------------------------------------------------
# Save helpers
# ---------------------------------------------------------------------------
def save_run(out_dir: str, label: str, results: list, args_dict: dict):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{label}.json")
    payload = {
        "config":    label,
        "run_args":  args_dict,
        "run_at":    datetime.utcnow().isoformat(),
        "n":         len(results),
        "answers":   results,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"  [save] {path}")
    return path


def save_run_summary(out_dir: str, all_results: dict[str, list], args_dict: dict):
    """
    Save a merged summary JSON compatible with evaluate.py's expected input:
    evaluations/<run_id>/run.json + metrics.json (metrics computed separately).
    """
    os.makedirs(out_dir, exist_ok=True)
    run_path = os.path.join(out_dir, "run.json")
    payload = {
        "run_type":  "t4_t6_isolation",
        "run_at":    datetime.utcnow().isoformat(),
        "run_args":  args_dict,
        "configs":   list(all_results.keys()),
        "variants":  {},
    }
    for label, results in all_results.items():
        payload["variants"][label] = {
            "n":      len(results),
            "answers": results,
        }
    with open(run_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"\n[save] Summary run.json -> {run_path}")
    return run_path


# ---------------------------------------------------------------------------
# ROUGE-L computation (inline, no dependency on evaluate.py)
# ---------------------------------------------------------------------------
def rouge_l_score(hypothesis: str, reference: str) -> float:
    """Sentence-level ROUGE-L (F1) without external libraries."""
    h_toks = hypothesis.lower().split()
    r_toks = reference.lower().split()
    if not h_toks or not r_toks:
        return 0.0
    # LCS via DP
    m, n = len(h_toks), len(r_toks)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if h_toks[i - 1] == r_toks[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    lcs = dp[m][n]
    p = lcs / m if m else 0.0
    r = lcs / n if n else 0.0
    f1 = (2 * p * r) / (p + r) if (p + r) > 0 else 0.0
    return round(f1, 4)


def compute_metrics(results: list) -> dict:
    scores = [rouge_l_score(r["answer"], r["reference"]) for r in results]
    sc     = [rouge_l_score(r["answer"], r["reference"])
              for r in results if r["safety_critical"]]
    nsc    = [rouge_l_score(r["answer"], r["reference"])
              for r in results if not r["safety_critical"]]
    tps    = [r["tokens_per_sec"] for r in results if r.get("tokens_per_sec", 0) > 0]
    flagged = sum(1 for r in results if r.get("meta", {}).get("flagged_unsafe", False))
    return {
        "rougeL_mean":     round(float(np.mean(scores)), 4)  if scores else 0.0,
        "rougeL_sc_mean":  round(float(np.mean(sc)), 4)      if sc     else 0.0,
        "rougeL_nsc_mean": round(float(np.mean(nsc)), 4)     if nsc    else 0.0,
        "tok_per_sec_mean": round(float(np.mean(tps)), 2)    if tps    else 0.0,
        "n_questions":     len(results),
        "n_sc":            len(sc),
        "n_nsc":           len(nsc),
        "n_flagged_unsafe": flagged,
    }


def print_metrics_table(all_metrics: dict[str, dict]):
    labels = {
        "A_BASELINE":     "A  BASELINE          ",
        "B_T4_ORIGINAL":  "B  T4 ORIGINAL       ",
        "C_T4_IMPROVED":  "C  T4 IMPROVED       ",
        "D_T6_ORIGINAL":  "D  T6 ORIGINAL       ",
        "E_T6_IMPROVED":  "E  T6 IMPROVED (gate)",
        "F_COMBINED_BEST":"F  T4+T6 IMPROVED    ",
    }
    print("\n" + "=" * 75)
    print(f"{'Config':<24} {'ROUGE-L':>8} {'SC':>8} {'Non-SC':>8} "
          f"{'tok/s':>7} {'Flagged':>8}")
    print("-" * 75)
    baseline_rl = all_metrics.get("A_BASELINE", {}).get("rougeL_mean", 0.0)
    for key, m in all_metrics.items():
        lbl   = labels.get(key, key)
        rl    = m["rougeL_mean"]
        delta = f"({'+' if rl >= baseline_rl else ''}{rl - baseline_rl:+.4f})" \
                if key != "A_BASELINE" else "         "
        print(f"{lbl:<24} {rl:>8.4f} {m['rougeL_sc_mean']:>8.4f} "
              f"{m['rougeL_nsc_mean']:>8.4f} {m['tok_per_sec_mean']:>7.1f} "
              f"{m['n_flagged_unsafe']:>8}  {delta}")
    print("=" * 75)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description="T4/T6 isolation ablation eval")
    p.add_argument("--adapter_path", default=DEFAULT_ADAPTER)
    p.add_argument("--model_path",   default=DEFAULT_MODEL)
    p.add_argument("--questions",    default=DEFAULT_QBANK)
    p.add_argument("--configs",      nargs="+", default=list("ABCDEF"),
                   choices=list("ABCDEF"),
                   help="Which configs to run (default: all six)")
    p.add_argument("--max_new_tokens", type=int, default=MAX_NEW_TOKENS)
    return p.parse_args()


def main():
    args       = parse_args()
    args_dict  = vars(args)

    # Load questions
    with open(args.questions, encoding="utf-8") as f:
        payload   = json.load(f)
    questions = payload.get("questions") if isinstance(payload, dict) else payload
    print(f"[load] {len(questions)} questions from {args.questions}")

    # Compute T4 floors
    floor_map = compute_floor_map(TRAIN_JSON)
    print("[T4] Category floors:", {k: v for k, v in floor_map.items()
                                    if k != "_default"})

    # Load model once
    model, tokenizer = load_model_and_adapter(args.adapter_path, args.model_path)
    stop_ids         = get_stop_ids(tokenizer)

    # Output directory
    ts      = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(EVAL_OUT_DIR, f"t4_t6_isolation_{ts}")
    os.makedirs(out_dir, exist_ok=True)
    print(f"\n[out] {out_dir}\n")

    # Run requested configs
    all_results: dict[str, list] = {}
    all_metrics: dict[str, dict] = {}

    for label in args.configs:
        name = CONFIG_LABELS[label][0]
        print(f"\n{'=' * 60}")
        print(f"  Running config {label}: {name}")
        print(f"{'=' * 60}")
        results = run_config(label, model, tokenizer, questions,
                             floor_map, stop_ids, args.max_new_tokens)
        all_results[name]  = results
        all_metrics[name]  = compute_metrics(results)
        save_run(out_dir, name, results, args_dict)

    # Save merged summary
    save_run_summary(out_dir, all_results, args_dict)

    # Print results table
    print_metrics_table(all_metrics)

    # Save metrics JSON
    metrics_path = os.path.join(out_dir, "metrics.json")
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(all_metrics, f, indent=2)
    print(f"\n[save] Metrics → {metrics_path}")

    print(f"\n[done] Results in: {out_dir}")
    print(f"[next] Run: python build_t4_t6_judge_prompt.py --run_dir {out_dir}")

    unload(model)


if __name__ == "__main__":
    main()
