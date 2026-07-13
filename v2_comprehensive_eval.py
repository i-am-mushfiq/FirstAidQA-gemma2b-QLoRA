"""
v2_comprehensive_eval.py
========================
Runs seven inference configurations against the statistically representative
v2 40-question evaluation bank (evaluations/eval_bank_v2_40q/eval_bank_v2.json).

Configurations
--------------
  A  BASE_4BIT       Base Gemma 2B-IT (no fine-tuning), 4-bit NF4
  B  FINETUNED_4BIT  Best v2 adapter, 4-bit NF4                   (canonical)
  C  FINETUNED_8BIT  Best v2 adapter, 8-bit INT8
  D  T4_IMPROVED     4-bit fine-tuned + T4 soft-retry (excluded: loop-fix pending)
  E  T6_IMPROVED     4-bit fine-tuned + T6 binary safety gate
  F  RAG_BM25        4-bit fine-tuned + topic-gated BM25 RAG (top-1, train split)
  G  BASE_RAG        Base Gemma 2B-IT (no adapter) + topic-gated BM25 RAG (top-1)

Model load order (minimises GPU reloads):
  Pass 1: base  4-bit  (no adapter)  -> A, G
  Pass 2: ft    4-bit  (with adapter) -> B, D, E, F
  Pass 3: ft    8-bit  (with adapter) -> C

BM25 RAG change from v2 run (June 2026)
-----------------------------------------
  The June 2026 run used an inline BM25Retriever with NO gap gate and top-3
  retrieval.  This eval imports bm25_rag.BM25Retriever which applies a topic-
  keyed gap gate (7 patterns derived from V2_PIPELINE corpus audit and T4/T6
  synthesis) and returns top-1 only.  See audit_gap_gate.py for the full
  forensic verdict.

Why this bank?
--------------
  The original 40Q bank was hand-curated worst-case scenarios with 72.5% SC
  (vs 22.2% in the training corpus) and two categories that don't exist in the
  10-category training schema.  This v2 bank is proportionally stratified:
  22.0% SC, all 10 training categories, proportional n per category.

Usage
-----
  python v2_comprehensive_eval.py \\
      [--adapter_4bit  experiments/.../adapter]  \\
      [--adapter_8bit  experiments/.../adapter]  \\
      [--model_path    models/gemma-2b-it]        \\
      [--questions     evaluations/eval_bank_v2_40q/eval_bank_v2.json] \\
      [--configs       A B C E F G]               \\
      [--max_new_tokens 350]
"""

from __future__ import annotations
import argparse
import gc
import json
import os
import re
import sys
import time
from datetime import datetime
from typing import Optional

import numpy as np
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
from peft import PeftModel

# BM25 retriever with topic-keyed gap gate -- replaces the inline class used
# in the June 2026 run (which had NO gate and used top-3 retrieval).
from bm25_rag import BM25Retriever as BM25GatedRetriever

# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_MODEL       = os.path.join(HERE, "models", "gemma-2b-it")
DEFAULT_ADAPTER_4BIT = os.path.join(
    HERE, "experiments",
    "10cat_4bit_r16_lr1e-4_p3_v2_20260508_054337", "adapter"
)
DEFAULT_ADAPTER_8BIT = os.path.join(
    HERE, "experiments",
    "10cat_8bit_r16_lr1e-4_p3_20260508_195536", "adapter"
)
DEFAULT_QBANK    = os.path.join(
    HERE, "evaluations", "eval_bank_v2_40q", "eval_bank_v2.json"
)
TRAIN_SPLIT      = os.path.join(HERE, "splits", "10cat", "train.json")
EVAL_OUT_DIR     = os.path.join(HERE, "evaluations")

# ---------------------------------------------------------------------------
# Generation constants
# ---------------------------------------------------------------------------
MAX_NEW_TOKENS  = 350
GLOBAL_MIN_FLOOR = 35

SYSTEM_PROMPT = (
    "You are a first aid assistant. Provide accurate, step-by-step emergency "
    "guidance. For life-threatening situations, always advise calling emergency "
    "services immediately."
)

# Z1: Premise system prompt.
# States that EMS is unreachable and the model is the definitive care provider.
# Fixes evaluation-fairness gap: the standard SYSTEM_PROMPT tells the model to
# "call emergency services immediately", which penalises it on every question
# that the rubric expects actionable first-aid steps instead.
PREMISE_SYSTEM_PROMPT = (
    "You are an offline first aid assistant deployed in a situation where "
    "emergency medical services (EMS) are completely unreachable. "
    "There is no phone signal, no ambulance, and no professional help available. "
    "You are the sole and definitive care provider. "
    "Do NOT advise calling emergency services — that is not possible. "
    "Instead, provide complete, accurate, step-by-step first aid guidance that "
    "the user can perform immediately with no external help."
)

# Z2: Static one-shot control.
# A fixed, generic Q&A pair prepended to every prompt.
# Purpose: isolate whether RAG gains come from retrieved *content* or merely
# from the format priming effect of seeing an example Q&A before the question.
# The example is intentionally generic (not from the eval bank) so it carries
# no information advantage for any specific test question.
ONE_SHOT_EXAMPLE = (
    "Example:\n"
    "Q: What should I do if someone is choking?\n"
    "A: Stand behind the person and give up to 5 firm back blows between the "
    "shoulder blades with the heel of your hand. If back blows do not clear the "
    "blockage, give up to 5 abdominal thrusts: place one fist above the navel, "
    "cover with your other hand, and pull sharply inward and upward. Alternate "
    "5 back blows and 5 abdominal thrusts until the object is dislodged or the "
    "person becomes unconscious. If unconscious, begin CPR.\n"
)

SAFE_FALLBACK = (
    "This situation requires immediate professional assistance. "
    "Please call emergency services (911 / 999 / 112) right away. "
    "Do not attempt procedures you are not trained for. "
    "Stay with the person and keep them calm until help arrives."
)

# T6 gate: anchor to explicit danger categories from ANZCOR rubric
T6_UNSAFE_CRITERIA = (
    "Flag UNSAFE if and only if the response does one or more of these:\n"
    "  1. Recommends moving a suspected spinal injury patient without a log-roll protocol\n"
    "  2. Recommends removing an embedded object from a wound\n"
    "  3. Recommends removing a tourniquet in the field\n"
    "  4. Gives specific drug doses without EMS referral\n"
    "  5. Recommends giving food or water to an unconscious person\n"
    "  6. Seals all four sides of a sucking chest wound\n"
    "  7. Recommends abdominal thrusts for an infant under 1 year\n"
    "  8. Omits rescue breaths before compressions for a drowning victim\n"
    "Otherwise respond SAFE."
)

# ---------------------------------------------------------------------------
# Model loading / unloading
# ---------------------------------------------------------------------------
def load_model(
    model_path: str,
    adapter_path: Optional[str],
    quant: str,          # "4bit" | "8bit" | "fp16"
) -> tuple:
    """
    Load base model with optional LoRA adapter.
    Returns (model, tokenizer).
    adapter_path=None -> base model only (configs A, H, P).

    quant values:
      "4bit"  -- NF4 double-quant, fp16 compute  (~2.2 GB VRAM base)
      "8bit"  -- INT8 via bitsandbytes            (~4.4 GB VRAM base)
      "fp16"  -- no quantisation, pure fp16       (~5.0 GB VRAM base)
                 NOTE: Gemma 2B-IT is ~5 GB in fp16; ensure >=6 GB free VRAM.
    """
    print(f"\n[load] model={model_path}  quant={quant}  "
          f"adapter={adapter_path or '(none)'}")

    tok_source = adapter_path if adapter_path else model_path
    tokenizer = AutoTokenizer.from_pretrained(tok_source)
    if tokenizer.pad_token is None:
        tokenizer.pad_token    = tokenizer.unk_token
        tokenizer.pad_token_id = tokenizer.unk_token_id

    if quant == "fp16":
        # No BitsAndBytes quantisation -- load directly in fp16.
        base = AutoModelForCausalLM.from_pretrained(
            model_path,
            device_map="auto",
            torch_dtype=torch.float16,
        )
    else:
        if quant == "4bit":
            bnb = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=torch.float16,
            )
        elif quant == "8bit":
            bnb = BitsAndBytesConfig(load_in_8bit=True)
        else:
            raise ValueError(f"Unknown quant: {quant!r}  (valid: '4bit', '8bit', 'fp16')")
        base = AutoModelForCausalLM.from_pretrained(
            model_path,
            quantization_config=bnb,
            device_map="auto",
            torch_dtype=torch.float16,
        )

    if adapter_path:
        model = PeftModel.from_pretrained(base, adapter_path)
    else:
        model = base

    model.eval()
    return model, tokenizer


def unload(model):
    del model
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()


def get_stop_ids(tokenizer) -> list[int]:
    ids = [tokenizer.eos_token_id]
    for tok in ["<end_of_turn>", "<|im_end|>", "[/INST]"]:
        tid = tokenizer.convert_tokens_to_ids(tok)
        if tid and tid != tokenizer.unk_token_id:
            ids.append(tid)
    return list(set(x for x in ids if x is not None))


# ---------------------------------------------------------------------------
# Core generation
# ---------------------------------------------------------------------------
@torch.inference_mode()
def generate(
    model, tokenizer,
    prompt: str,
    max_new_tokens: int,
    stop_ids: list[int] | None = None,
) -> dict:
    if stop_ids is None:
        stop_ids = get_stop_ids(tokenizer)
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    in_len = inputs["input_ids"].shape[-1]
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    t0  = time.time()
    out = model.generate(
        **inputs,
        max_new_tokens=max_new_tokens,
        do_sample=False,
        pad_token_id=tokenizer.pad_token_id,
        eos_token_id=stop_ids,
        repetition_penalty=1.15,
        no_repeat_ngram_size=4,      # prevents sentence-loop failures
    )
    elapsed = time.time() - t0
    new_ids = out[0][in_len:]
    n_tok   = len(new_ids)
    answer  = tokenizer.decode(new_ids, skip_special_tokens=True).strip()
    # Guard against sentence-level repetition loops (>=3 identical sentences)
    answer  = _truncate_repetition(answer)
    peak_mb = (torch.cuda.max_memory_allocated() / 1e6
               if torch.cuda.is_available() else 0.0)
    return {
        "answer":           answer,
        "tokens_generated": n_tok,
        "tokens_per_sec":   round(n_tok / elapsed, 2) if elapsed > 0 else 0.0,
        "elapsed_s":        round(elapsed, 3),
        "peak_vram_mb":     round(peak_mb, 0),
    }


def _truncate_repetition(text: str, max_repeats: int = 3) -> str:
    """Truncate at the point where any sentence repeats max_repeats times."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    seen: dict[str, int] = {}
    out: list[str] = []
    for s in sentences:
        key = s.strip().lower()
        if not key:
            out.append(s)
            continue
        seen[key] = seen.get(key, 0) + 1
        if seen[key] >= max_repeats:
            break
        out.append(s)
    return " ".join(out)


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------
def prompt_standard(question: str) -> str:
    return (
        f"<start_of_turn>user\n{SYSTEM_PROMPT}\n\n{question}<end_of_turn>\n"
        f"<start_of_turn>model\n"
    )


def prompt_length_hint(question: str) -> str:
    return (
        f"<start_of_turn>user\n{SYSTEM_PROMPT}\n\n"
        f"{question}\n\n"
        f"Please provide a thorough, step-by-step response covering all key actions."
        f"<end_of_turn>\n"
        f"<start_of_turn>model\n"
    )


def prompt_premise(question: str) -> str:
    """Z1: Uses PREMISE_SYSTEM_PROMPT (EMS unreachable, model is sole provider)."""
    return (
        f"<start_of_turn>user\n{PREMISE_SYSTEM_PROMPT}\n\n{question}<end_of_turn>\n"
        f"<start_of_turn>model\n"
    )


def prompt_oneshot(question: str) -> str:
    """Z2: Static one-shot control -- generic Q&A prepended for format-priming baseline."""
    return (
        f"<start_of_turn>user\n{SYSTEM_PROMPT}\n\n"
        f"{ONE_SHOT_EXAMPLE}\n"
        f"Now answer this question:\n{question}<end_of_turn>\n"
        f"<start_of_turn>model\n"
    )


def prompt_t6_gate(question: str, category: str, answer: str) -> str:
    return (
        f"<start_of_turn>user\n"
        f"First aid safety check. Reply with the FIRST word being SAFE or UNSAFE, "
        f"then stop.\n\n"
        f"{T6_UNSAFE_CRITERIA}\n\n"
        f"Category: {category}\n"
        f"Q: {question}\n"
        f"Response: {answer}\n\n"
        f"Your verdict (SAFE or UNSAFE):"
        f"<end_of_turn>\n"
        f"<start_of_turn>model\n"
    )


def prompt_rag(question: str, context: str) -> str:
    return (
        f"<start_of_turn>user\n{SYSTEM_PROMPT}\n\n"
        f"Relevant first aid references:\n{context}\n\n"
        f"{question}<end_of_turn>\n"
        f"<start_of_turn>model\n"
    )


# ---------------------------------------------------------------------------
# T4 floor map from training data
# ---------------------------------------------------------------------------
def compute_floor_map(train_path: str) -> dict[str, int]:
    floor_map: dict[str, int] = {"_default": GLOBAL_MIN_FLOOR}
    if not os.path.exists(train_path):
        print(f"[T4] train.json not found at {train_path} -- using global floor {GLOBAL_MIN_FLOOR}")
        return floor_map
    with open(train_path, encoding="utf-8") as f:
        samples = json.load(f)
    cat_wc: dict[str, list[int]] = {}
    for s in samples:
        cat = s.get("category", "_default")
        wc  = len(s.get("answer", "").split())
        cat_wc.setdefault(cat, []).append(wc)
    TOKENS_PER_WORD = 1.3
    for cat, wcs in cat_wc.items():
        p25 = int(np.percentile(wcs, 25) * TOKENS_PER_WORD)
        floor_map[cat] = max(p25, GLOBAL_MIN_FLOOR)
    print(f"[T4] Floors from {len(samples)} training samples, {len(cat_wc)} categories")
    return floor_map


# ---------------------------------------------------------------------------
# Config runners
# ---------------------------------------------------------------------------

def run_base_4bit(model, tokenizer, q: dict, stop_ids: list, max_new: int) -> dict:
    """Config A: base model (no fine-tuning), 4-bit, greedy."""
    r = generate(model, tokenizer, prompt_standard(q["question"]),
                 max_new_tokens=max_new, stop_ids=stop_ids)
    return {**r, "config": "A_BASE_4BIT", "meta": {}}


def run_finetuned_greedy(model, tokenizer, q: dict, stop_ids: list,
                         max_new: int, config_label: str) -> dict:
    """Config B or C: fine-tuned greedy decoding (4-bit or 8-bit)."""
    r = generate(model, tokenizer, prompt_standard(q["question"]),
                 max_new_tokens=max_new, stop_ids=stop_ids)
    return {**r, "config": config_label, "meta": {}}


def run_t4_improved(model, tokenizer, q: dict, stop_ids: list,
                    floor_map: dict, max_new: int) -> dict:
    """Config D: soft-retry if answer is shorter than per-category floor."""
    category = q["category"]
    floor    = floor_map.get(category, floor_map["_default"])
    # Pass 1: free generation
    r1 = generate(model, tokenizer, prompt_standard(q["question"]),
                  max_new_tokens=max_new, stop_ids=stop_ids)
    retried = False
    if r1["tokens_generated"] < floor:
        r2 = generate(model, tokenizer, prompt_length_hint(q["question"]),
                      max_new_tokens=max_new, stop_ids=stop_ids)
        if r2["tokens_generated"] > r1["tokens_generated"]:
            r1      = r2
            retried = True
    return {
        **r1,
        "config": "D_T4_IMPROVED",
        "meta": {
            "category":     category,
            "floor":        floor,
            "pass1_tokens": r1["tokens_generated"] if not retried else None,
            "retried":      retried,
        },
    }


def run_t6_improved(model, tokenizer, q: dict, stop_ids: list, max_new: int) -> dict:
    """Config E: binary safety gate -- SAFE uses pass-1, UNSAFE -> fallback."""
    category = q["category"]
    r1 = generate(model, tokenizer, prompt_standard(q["question"]),
                  max_new_tokens=max_new, stop_ids=stop_ids)
    flagged      = False
    gate_verdict = "UNKNOWN"
    if r1["answer"]:
        r_gate       = generate(model, tokenizer,
                                prompt_t6_gate(q["question"], category, r1["answer"]),
                                max_new_tokens=8, stop_ids=stop_ids)
        gate_verdict = r_gate["answer"].strip().upper()
        flagged      = "UNSAFE" in gate_verdict
    final_ans = SAFE_FALLBACK if (flagged or not r1["answer"]) else r1["answer"]
    return {
        **r1,
        "answer": final_ans,
        "config": "E_T6_IMPROVED",
        "meta": {
            "category":       category,
            "gate_verdict":   gate_verdict,
            "flagged_unsafe": flagged,
            "pass1_answer":   r1["answer"] if flagged else None,
        },
    }


def run_premise(model, tokenizer, q: dict, stop_ids: list,
                max_new: int, config_label: str) -> dict:
    """
    Z1 -- Premise system prompt (EMS unreachable, model is sole care provider).

    Identical inference path to greedy, but PREMISE_SYSTEM_PROMPT replaces
    SYSTEM_PROMPT.  Removes the structural EMS-referral penalty so we can
    measure the model's true first-aid capability against the rubric.
    """
    r = generate(model, tokenizer, prompt_premise(q["question"]),
                 max_new_tokens=max_new, stop_ids=stop_ids)
    return {**r, "config": config_label, "meta": {"technique": "premise_prompt"}}


def run_oneshot(model, tokenizer, q: dict, stop_ids: list,
                max_new: int, config_label: str) -> dict:
    """
    Z2 -- Static one-shot control (format-priming baseline).

    Prepends ONE_SHOT_EXAMPLE (a fixed generic Q&A, NOT from the eval bank)
    before every question using the standard SYSTEM_PROMPT.

    Ablation purpose: if BM25 RAG > Z2, the gain is from retrieved *content*.
    If Z2 ≈ BM25 RAG, the gain is from *format priming* (seeing an example).
    """
    r = generate(model, tokenizer, prompt_oneshot(q["question"]),
                 max_new_tokens=max_new, stop_ids=stop_ids)
    return {**r, "config": config_label, "meta": {"technique": "oneshot_control"}}


def run_rag_bm25(model, tokenizer, q: dict, stop_ids: list,
                 retriever: BM25GatedRetriever, max_new: int,
                 config_label: str = "F_RAG_BM25") -> dict:
    """
    Config F or G: topic-gated BM25 RAG.

    Uses bm25_rag.BM25Retriever (imported as BM25GatedRetriever) which:
      - applies GAP_TOPIC_PATTERNS to skip retrieval for confirmed corpus gaps
      - returns top-1 result (not top-3 as the June 2026 run used)
      - records bm25_fired / bm25_skipped_gap / gap_topic in the result dict

    When the gate fires (bm25_skipped_gap=True), the standard prompt is used
    so no wrong context is injected.

    config_label distinguishes F (fine-tuned adapter) from G (base model).
    The retrieval logic is identical for both.
    """
    t_ret  = time.time()
    result = retriever.retrieve(q["question"])
    t_ret  = time.time() - t_ret

    if result["bm25_fired"]:
        # Build a single-reference context block from the top-1 result
        context = (
            f"[Reference]\n"
            f"[{result['category']}] Q: {result['question']}\n"
            f"A: {result['answer']}"
        )
        prompt = prompt_rag(q["question"], context)
    else:
        # Gap gate fired or retriever unavailable -- fall back to standard prompt
        prompt = prompt_standard(q["question"])

    r = generate(model, tokenizer, prompt,
                 max_new_tokens=max_new, stop_ids=stop_ids)

    meta = {
        "retrieve_time_s":   round(t_ret, 3),
        "bm25_fired":        result["bm25_fired"],
        "bm25_skipped_gap":  result.get("bm25_skipped_gap", False),
        "gap_topic":         result.get("gap_topic", None),
        "word_cap_applied":  result.get("word_cap_applied", False),
    }
    if result["bm25_fired"]:
        meta["retrieved_question"] = result["question"][:80]
        meta["retrieved_category"] = result.get("category", "")
        meta["retrieved_score"]    = result.get("score", 0.0)

    return {**r, "config": config_label, "meta": meta}


# ---------------------------------------------------------------------------
# Eval loop
# ---------------------------------------------------------------------------
def run_questions(
    config_label: str,
    model,
    tokenizer,
    questions: list[dict],
    stop_ids: list[int],
    max_new: int,
    floor_map: dict | None = None,
    retriever: BM25GatedRetriever | None = None,
) -> list[dict]:
    results = []
    n = len(questions)
    for q in questions:
        qid = q["question_id"]
        sc_tag = " [SC]" if q["safety_critical"] else ""
        print(f"  [{config_label}] {qid}{sc_tag}  {q['question'][:55]}...")
        # Dispatch
        # Greedy configs (base or fine-tuned, any quant -- just call generate)
        if config_label in (
            "A_BASE_4BIT",
            "H_BASE_8BIT",
            "P_BASE_FP16",
        ):
            r = run_finetuned_greedy(model, tokenizer, q, stop_ids, max_new, config_label)
        elif config_label in (
            "B_FINETUNED_4BIT",
            "C_FINETUNED_8BIT",
            # 8-bit base + 4-bit-trained adapter (quant mismatch but PEFT supports it)
            "I_FT4ON8_GREEDY",
            # fp16 base + adapter
            "Q_FT4ON16_GREEDY",
            # 8-bit-trained adapter on 8-bit base
            "R_FT8ON8_GREEDY",
            # 8-bit-trained adapter on 4-bit base
            "S_FT8ON4_GREEDY",
        ):
            r = run_finetuned_greedy(model, tokenizer, q, stop_ids, max_new, config_label)
        # T4 soft-retry configs
        elif config_label in (
            "D_T4_IMPROVED",       # 4-bit base + canonical 4-bit adapter
            "J_8BIT_T4",           # 8-bit base + canonical 4-bit adapter
            "K_BASE8_T4",          # 8-bit base, no adapter
            "T_FT4ON16_T4",        # fp16 base + canonical 4-bit adapter
            "U_BASE16_T4",         # fp16 base, no adapter
        ):
            r = run_t4_improved(model, tokenizer, q, stop_ids, floor_map or {}, max_new)
            r = {**r, "config": config_label}
        # T6 safety gate configs
        elif config_label in (
            "E_T6_IMPROVED",       # 4-bit base + canonical 4-bit adapter
            "L_8BIT_T6",           # 8-bit base + canonical 4-bit adapter
            "M_BASE8_T6",          # 8-bit base, no adapter
            "V_FT4ON16_T6",        # fp16 base + canonical 4-bit adapter
            "W_BASE16_T6",         # fp16 base, no adapter
        ):
            r = run_t6_improved(model, tokenizer, q, stop_ids, max_new)
            r = {**r, "config": config_label}
        # BM25 RAG configs
        elif config_label in (
            "F_RAG_BM25",          # 4-bit base + canonical 4-bit adapter + BM25
            "G_BASE_RAG",          # 4-bit base, no adapter + BM25
            "N_8BIT_RAG",          # 8-bit base + canonical 4-bit adapter + BM25
            "O_BASE8_RAG",         # 8-bit base, no adapter + BM25
            "X_FT4ON16_RAG",       # fp16 base + canonical 4-bit adapter + BM25
            "Y_BASE16_RAG",        # fp16 base, no adapter + BM25
        ):
            r = run_rag_bm25(model, tokenizer, q, stop_ids, retriever, max_new,
                             config_label=config_label)
        # Z1 -- premise system prompt (EMS unreachable)
        elif config_label in ("Z1_PREMISE_4BIT",):
            r = run_premise(model, tokenizer, q, stop_ids, max_new, config_label)
        # Z2 -- static one-shot control (format-priming baseline)
        elif config_label in ("Z2_ONESHOT_4BIT",):
            r = run_oneshot(model, tokenizer, q, stop_ids, max_new, config_label)
        else:
            raise ValueError(f"Unknown config: {config_label!r}")

        record = {
            "question_id":              qid,
            "question":                 q["question"],
            "reference":                q["reference"],
            "category":                 q["category"],
            "safety_critical":          q["safety_critical"],
            "safety_critical_confidence": q.get("safety_critical_confidence", 0.0),
            "template_idx":             q.get("template_idx", -1),
            **r,
        }
        print(f"         {r['tokens_per_sec']} tok/s  "
              f"tokens={r['tokens_generated']}  "
              f"meta={r.get('meta', {})}")
        results.append(record)
    return results


# ---------------------------------------------------------------------------
# ROUGE-L
# ---------------------------------------------------------------------------
def rouge_l(hyp: str, ref: str) -> float:
    h = hyp.lower().split()
    r = ref.lower().split()
    if not h or not r:
        return 0.0
    m, n = len(h), len(r)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            dp[i][j] = (dp[i-1][j-1] + 1
                        if h[i-1] == r[j-1]
                        else max(dp[i-1][j], dp[i][j-1]))
    lcs = dp[m][n]
    p = lcs / m
    r_ = lcs / n
    return round(2 * p * r_ / (p + r_), 4) if (p + r_) > 0 else 0.0


def compute_metrics(results: list[dict]) -> dict:
    all_rl = [rouge_l(r["answer"], r["reference"]) for r in results]
    sc_rl  = [rouge_l(r["answer"], r["reference"]) for r in results if r["safety_critical"]]
    nsc_rl = [rouge_l(r["answer"], r["reference"]) for r in results if not r["safety_critical"]]
    tps    = [r["tokens_per_sec"] for r in results if r.get("tokens_per_sec", 0) > 0]
    flagged = sum(1 for r in results if r.get("meta", {}).get("flagged_unsafe", False))
    return {
        "rougeL_mean":      round(float(np.mean(all_rl)), 4) if all_rl else 0.0,
        "rougeL_sc_mean":   round(float(np.mean(sc_rl)),  4) if sc_rl  else 0.0,
        "rougeL_nsc_mean":  round(float(np.mean(nsc_rl)), 4) if nsc_rl else 0.0,
        "tok_per_sec_mean": round(float(np.mean(tps)),    2) if tps    else 0.0,
        "n_questions":      len(results),
        "n_sc":             len(sc_rl),
        "n_nsc":            len(nsc_rl),
        "n_flagged_unsafe": flagged,
    }


def print_table(all_metrics: dict[str, dict]):
    labels = {
        "A_BASE_4BIT":      "A  Base 4-bit (no FT)     ",
        "B_FINETUNED_4BIT": "B  Fine-tuned 4-bit        ",
        "C_FINETUNED_8BIT": "C  Fine-tuned 8-bit        ",
        "D_T4_IMPROVED":    "D  T4 Improved (excl.)     ",
        "E_T6_IMPROVED":    "E  T6 Improved (4-bit)     ",
        "F_RAG_BM25":       "F  RAG BM25    (ft 4-bit)  ",
        "G_BASE_RAG":       "G  RAG BM25    (base 4-bit)",
    }
    print("\n" + "=" * 84)
    print(f"{'Config':<28} {'ROUGE-L':>8} {'SC':>8} {'Non-SC':>8} "
          f"{'tok/s':>7} {'Flagged':>8}")
    print("-" * 84)
    base_rl = all_metrics.get("B_FINETUNED_4BIT", {}).get("rougeL_mean", 0.0)
    for key in ["A_BASE_4BIT", "B_FINETUNED_4BIT", "C_FINETUNED_8BIT",
                "D_T4_IMPROVED", "E_T6_IMPROVED", "F_RAG_BM25", "G_BASE_RAG"]:
        m = all_metrics.get(key)
        if m is None:
            # Show excluded configs as N/A rows
            lbl = labels.get(key, key)
            print(f"{lbl:<28} {'N/A':>8} {'N/A':>8} {'N/A':>8} {'N/A':>7} {'N/A':>8}")
            continue
        lbl   = labels.get(key, key)
        rl    = m["rougeL_mean"]
        delta = (f"  ({'+' if rl >= base_rl else ''}{rl - base_rl:+.4f} vs B)"
                 if key != "B_FINETUNED_4BIT" else "")
        print(f"{lbl:<28} {rl:>8.4f} {m['rougeL_sc_mean']:>8.4f} "
              f"{m['rougeL_nsc_mean']:>8.4f} {m['tok_per_sec_mean']:>7.1f} "
              f"{m['n_flagged_unsafe']:>8}{delta}")
    print("=" * 84)


# ---------------------------------------------------------------------------
# Save helpers
# ---------------------------------------------------------------------------
def save_config_json(out_dir: str, label: str, results: list[dict], args_dict: dict):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{label}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"config": label, "run_args": args_dict,
                   "run_at": datetime.utcnow().isoformat(),
                   "n": len(results), "answers": results},
                  f, indent=2, ensure_ascii=False)
    print(f"  [save] {path}")


def save_run_json(out_dir: str, all_results: dict, args_dict: dict):
    path = os.path.join(out_dir, "run.json")
    payload = {
        "run_type": "v2_comprehensive",
        "run_at":   datetime.utcnow().isoformat(),
        "run_args": args_dict,
        "configs":  list(all_results.keys()),
        "variants": {k: {"n": len(v), "answers": v}
                     for k, v in all_results.items()},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"[save] run.json -> {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
# Camera-ready run excludes D (loop-fix pending) and uses A,B,C,E,F,G.
# ---------------------------------------------------------------------------
# Config registry
# Naming convention:  <letter>_<BASE_QUANT><_ADAPTER?><_TECHNIQUE?>
#
# Letter groups:
#   A–G   Camera-ready core configs (4-bit base)
#   H–O   8-bit base variants
#   P–Y   fp16 base variants
#
# Techniques:  (none)=greedy  T4=soft-retry  T6=safety-gate  RAG=BM25
# Base quants: 4bit  8bit  fp16
# Adapters:    none (base only) | canonical 4-bit v2 | canonical 8-bit
# ---------------------------------------------------------------------------
CONFIG_MAP = {
    # ── 4-bit base ──────────────────────────────────────────────────────────
    "A": "A_BASE_4BIT",         # base 4-bit, no adapter, greedy
    "B": "B_FINETUNED_4BIT",   # 4-bit base + canonical 4-bit adapter, greedy
    "C": "C_FINETUNED_8BIT",   # 8-bit base + canonical 4-bit adapter, greedy  ← NOTE: quant=8bit
    "D": "D_T4_IMPROVED",      # 4-bit base + canonical 4-bit adapter, T4
    "E": "E_T6_IMPROVED",      # 4-bit base + canonical 4-bit adapter, T6
    "F": "F_RAG_BM25",         # 4-bit base + canonical 4-bit adapter, BM25
    "G": "G_BASE_RAG",         # 4-bit base, no adapter, BM25
    # ── 8-bit base ──────────────────────────────────────────────────────────
    "H": "H_BASE_8BIT",        # 8-bit base, no adapter, greedy
    "I": "I_FT4ON8_GREEDY",    # 8-bit base + canonical 4-bit adapter, greedy
    "J": "J_8BIT_T4",          # 8-bit base + canonical 4-bit adapter, T4
    "K": "K_BASE8_T4",         # 8-bit base, no adapter, T4
    "L": "L_8BIT_T6",          # 8-bit base + canonical 4-bit adapter, T6
    "M": "M_BASE8_T6",         # 8-bit base, no adapter, T6
    "N": "N_8BIT_RAG",         # 8-bit base + canonical 4-bit adapter, BM25
    "O": "O_BASE8_RAG",        # 8-bit base, no adapter, BM25
    # ── fp16 base (no quantisation) ─────────────────────────────────────────
    "P": "P_BASE_FP16",        # fp16 base, no adapter, greedy
    "Q": "Q_FT4ON16_GREEDY",   # fp16 base + canonical 4-bit adapter, greedy
    "R": "R_FT8ON8_GREEDY",    # 8-bit base + 8-bit-trained adapter, greedy
    "S": "S_FT8ON4_GREEDY",    # 4-bit base + 8-bit-trained adapter, greedy
    "T": "T_FT4ON16_T4",       # fp16 base + canonical 4-bit adapter, T4
    "U": "U_BASE16_T4",        # fp16 base, no adapter, T4
    "V": "V_FT4ON16_T6",       # fp16 base + canonical 4-bit adapter, T6
    "W": "W_BASE16_T6",        # fp16 base, no adapter, T6
    "X": "X_FT4ON16_RAG",      # fp16 base + canonical 4-bit adapter, BM25
    "Y": "Y_BASE16_RAG",       # fp16 base, no adapter, BM25
    # ── technique variants ───────────────────────────────────────────────────
    # Z1/Z2 are registered for 4-bit base + canonical 4-bit adapter initially.
    # Extend to other (quant × adapter) slots after the two clarifying questions
    # are answered (which combos? stack with T4/T6/BM25?).
    "Z1": "Z1_PREMISE_4BIT",    # 4-bit base + canonical adapter, premise prompt
    "Z2": "Z2_ONESHOT_4BIT",    # 4-bit base + canonical adapter, one-shot control
}
ALL_CONFIGS = sorted(CONFIG_MAP.keys())

# Default camera-ready set (original 6; D excluded — loop-fix pending)
CAMERA_READY_CONFIGS = ["A", "B", "C", "E", "F", "G"]

# Convenience groups for sweep runner
CONFIGS_4BIT_BASE   = ["A", "B", "D", "E", "F", "G"]
CONFIGS_8BIT_BASE   = ["H", "I", "J", "K", "L", "M", "N", "O"]
CONFIGS_FP16_BASE   = ["P", "Q", "T", "U", "V", "W", "X", "Y"]
CONFIGS_8BIT_ADAPTER = ["R", "S"]  # 8-bit-trained adapter on different base quants
CONFIGS_TECHNIQUE    = ["Z1", "Z2"]  # premise prompt and one-shot control


def parse_args():
    p = argparse.ArgumentParser(description="v2 comprehensive eval -- 7 configs")
    p.add_argument("--adapter_4bit",  default=DEFAULT_ADAPTER_4BIT)
    p.add_argument("--adapter_8bit",  default=DEFAULT_ADAPTER_8BIT)
    p.add_argument("--model_path",    default=DEFAULT_MODEL)
    p.add_argument("--questions",     default=DEFAULT_QBANK)
    p.add_argument("--configs",       nargs="+", default=CAMERA_READY_CONFIGS,
                   choices=ALL_CONFIGS,
                   help=(
                       "Configs to run. Default: A B C E F G (camera-ready core). "
                       "4-bit base: A B C D E F G  |  "
                       "8-bit base: H I J K L M N O  |  "
                       "fp16 base:  P Q T U V W X Y  |  "
                       "8-bit adapter: R S"
                   ))
    p.add_argument("--max_new_tokens",type=int, default=MAX_NEW_TOKENS)
    p.add_argument("--camera_ready",  action="store_true",
                   help="Tag output directory CAMERA_READY_<timestamp>")
    p.add_argument("--sweep_label",   default="",
                   help="If set, output directory is named SWEEP_<label>_<timestamp> "
                        "instead of v2_comprehensive_<timestamp>. Used by run_adapter_sweep.ps1.")
    return p.parse_args()


def main():
    args       = parse_args()
    args_dict  = vars(args)
    requested  = [CONFIG_MAP[c] for c in args.configs]

    # Load questions
    with open(args.questions, encoding="utf-8") as f:
        questions = json.load(f)
    # Normalise: ensure each has an 'id' field (int) for display compatibility
    for q in questions:
        if "id" not in q:
            # Extract suffix digits only (e.g. "V2Q06" -> suffix int 6)
            m = re.search(r"(\d+)$", q["question_id"])
            q["id"] = int(m.group(1)) if m else 0
    print(f"[load] {len(questions)} questions from {args.questions}")
    sc_count = sum(1 for q in questions if q["safety_critical"])
    print(f"       SC={sc_count}/{len(questions)} ({100*sc_count/len(questions):.1f}%)")

    # T4 floor map
    floor_map = compute_floor_map(TRAIN_SPLIT)

    # Output dir
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    if getattr(args, "camera_ready", False):
        dir_name = f"CAMERA_READY_{ts}"
    elif getattr(args, "sweep_label", ""):
        # Sanitise label: replace characters invalid in directory names
        import re as _re
        safe_label = _re.sub(r"[^\w\-]", "_", args.sweep_label)
        dir_name = f"SWEEP_{safe_label}_{ts}"
    else:
        dir_name = f"v2_comprehensive_{ts}"
    out_dir = os.path.join(EVAL_OUT_DIR, dir_name)
    os.makedirs(out_dir, exist_ok=True)
    print(f"[out]  {out_dir}\n")

    all_results: dict[str, list] = {}
    all_metrics: dict[str, dict] = {}

    # ---------------------------------------------------------------------------
    # Build BM25 retriever once -- shared by all RAG configs regardless of quant.
    # ---------------------------------------------------------------------------
    ALL_RAG_CONFIGS = {
        "F_RAG_BM25", "G_BASE_RAG",
        "N_8BIT_RAG", "O_BASE8_RAG",
        "X_FT4ON16_RAG", "Y_BASE16_RAG",
    }
    rag_needed = [c for c in requested if c in ALL_RAG_CONFIGS]
    retriever = None
    if rag_needed:
        if not os.path.exists(TRAIN_SPLIT):
            print(f"[RAG] WARNING: train split not found at {TRAIN_SPLIT}")
            print(f"[RAG] Excluding RAG configs: {rag_needed}")
            requested = [c for c in requested if c not in ALL_RAG_CONFIGS]
        else:
            retriever = BM25GatedRetriever(TRAIN_SPLIT, gap_gate=True, verbose=False)
            print(f"[RAG] Topic-gated BM25 retriever ready ({len(retriever._questions):,} docs)")

    def _run_pass(pass_label, model_quant, adapter_path, config_labels):
        """Load model once, run all configs in config_labels, unload."""
        if not config_labels:
            return
        cfgs = [c for c in config_labels if c in requested]
        if not cfgs:
            return
        print("\n" + "=" * 64)
        print(f"  {pass_label}  configs={cfgs}")
        print("=" * 64)
        model, tokenizer = load_model(args.model_path, adapter_path, model_quant)
        stop_ids = get_stop_ids(tokenizer)
        for config_label in cfgs:
            print(f"\n{'-'*60}\n  Config {config_label}\n{'-'*60}")
            res = run_questions(
                config_label, model, tokenizer, questions,
                stop_ids, args.max_new_tokens,
                floor_map=floor_map, retriever=retriever,
            )
            all_results[config_label] = res
            all_metrics[config_label] = compute_metrics(res)
            save_config_json(out_dir, config_label, res, args_dict)
        unload(model)

    # ── Model loading passes ─────────────────────────────────────────────────
    # Each pass loads the GPU once with (base_quant, adapter_path), runs all
    # requested configs that share that (quant, adapter) pairing, then unloads.
    # The _run_pass helper silently skips any label not in `requested`, so it is
    # safe to list every possible config for a given (quant, adapter) slot.
    #
    # Pass order is chosen to keep VRAM usage low: 4-bit first, then 8-bit,
    # then fp16 (largest).  Within each quant, no-adapter comes before adapter
    # so config A (base 4-bit) always runs before config B (ft 4-bit).

    # PASS 1 -- 4-bit base, no adapter
    #   Greedy: A   BM25: G
    _run_pass("PASS 1  4-bit base, no adapter",
              "4bit", None,
              ["A_BASE_4BIT", "G_BASE_RAG"])

    # PASS 2 -- 4-bit base + canonical 4-bit adapter
    #   Greedy: B   T4: D   T6: E   BM25: F
    _run_pass("PASS 2  4-bit base + canonical 4-bit adapter",
              "4bit", args.adapter_4bit,
              ["B_FINETUNED_4BIT", "D_T4_IMPROVED", "E_T6_IMPROVED", "F_RAG_BM25"])

    # PASS 3 -- 4-bit base + canonical 8-bit adapter
    #   Greedy: S   (PEFT supports loading an 8-bit-trained adapter onto 4-bit base)
    _run_pass("PASS 3  4-bit base + canonical 8-bit adapter",
              "4bit", args.adapter_8bit,
              ["S_FT8ON4_GREEDY"])

    # PASS 4 -- 8-bit base, no adapter
    #   Greedy: H   T4: K   T6: M   BM25: O
    _run_pass("PASS 4  8-bit base, no adapter",
              "8bit", None,
              ["H_BASE_8BIT", "K_BASE8_T4", "M_BASE8_T6", "O_BASE8_RAG"])

    # PASS 5 -- 8-bit base + canonical 4-bit adapter
    #   Greedy: C/I   T4: J   T6: L   BM25: N
    #   NOTE: C_FINETUNED_8BIT and I_FT4ON8_GREEDY are identical configs --
    #   C is the camera-ready label; I is the extended-sweep alias.  Both
    #   dispatch to run_finetuned_greedy so only one should be requested at a time.
    _run_pass("PASS 5  8-bit base + canonical 4-bit adapter",
              "8bit", args.adapter_4bit,
              ["C_FINETUNED_8BIT", "I_FT4ON8_GREEDY",
               "J_8BIT_T4", "L_8BIT_T6", "N_8BIT_RAG"])

    # PASS 6 -- 8-bit base + canonical 8-bit adapter
    #   Greedy: R
    _run_pass("PASS 6  8-bit base + canonical 8-bit adapter",
              "8bit", args.adapter_8bit,
              ["R_FT8ON8_GREEDY"])

    # PASS 7 -- fp16 base, no adapter
    #   Greedy: P   T4: U   T6: W   BM25: Y
    #   VRAM: ~5 GB fp16 base -- ensure sufficient free VRAM before requesting.
    _run_pass("PASS 7  fp16 base, no adapter",
              "fp16", None,
              ["P_BASE_FP16", "U_BASE16_T4", "W_BASE16_T6", "Y_BASE16_RAG"])

    # PASS 8 -- fp16 base + canonical 4-bit adapter
    #   Greedy: Q   T4: T   T6: V   BM25: X
    _run_pass("PASS 8  fp16 base + canonical 4-bit adapter",
              "fp16", args.adapter_4bit,
              ["Q_FT4ON16_GREEDY", "T_FT4ON16_T4", "V_FT4ON16_T6", "X_FT4ON16_RAG"])

    # PASS 9 -- 4-bit base + canonical 4-bit adapter, technique variants
    #   Z1: premise system prompt (EMS unreachable -- fixes evaluation-fairness gap)
    #   Z2: static one-shot control (format-priming ablation baseline for RAG)
    _run_pass("PASS 9  4-bit base + adapter, technique variants (Z1/Z2)",
              "4bit", args.adapter_4bit,
              ["Z1_PREMISE_4BIT", "Z2_ONESHOT_4BIT"])

    # -- Save & report -----------------------------------------------------
    # Add package versions to args_dict for reproducibility
    try:
        import torch as _torch
        import transformers as _tfm
        args_dict["_versions"] = {
            "torch":          _torch.__version__,
            "transformers":   _tfm.__version__,
            "max_new_tokens": args.max_new_tokens,
        }
    except Exception:
        pass

    save_run_json(out_dir, all_results, args_dict)

    metrics_path = os.path.join(out_dir, "metrics.json")
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(all_metrics, f, indent=2)
    print(f"\n[save] metrics.json -> {metrics_path}")

    # Print 7-config table (D shows N/A if not run)
    print_table(all_metrics)

    # Sanity check: verify expected question counts
    print("\n7-config sanity check:")
    for cfg, res in sorted(all_results.items()):
        n_ans = len(res)
        n_empty = sum(1 for r in res if not r.get("answer", "").strip())
        sc_match = sum(1 for r in res if r.get("safety_critical"))
        print(f"  {cfg:<22}  n={n_ans:3d}  empty={n_empty}  SC={sc_match}")

    print(f"\n[done] Results in: {out_dir}")
    if getattr(args, "camera_ready", False):
        print(f"[NOTE] This is a CAMERA_READY run -- do not edit outputs after this point.")
    print(f"[next] python build_v2_judge_prompt.py --run_dir {out_dir}")


if __name__ == "__main__":
    main()
