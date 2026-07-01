"""
v2_comprehensive_eval.py
========================
Runs six inference configurations against the statistically representative
v2 40-question evaluation bank (evaluations/eval_bank_v2_40q/eval_bank_v2.json).

Configurations
--------------
  A  BASE_4BIT       Base Gemma 2B-IT (no fine-tuning), 4-bit NF4
  B  FINETUNED_4BIT  Best v2 adapter, 4-bit NF4                   (canonical)
  C  FINETUNED_8BIT  Best v2 adapter, 8-bit INT8
  D  T4_IMPROVED     4-bit fine-tuned + T4 soft-retry length floor
  E  T6_IMPROVED     4-bit fine-tuned + T6 binary safety gate
  F  RAG_BM25        4-bit fine-tuned + BM25 RAG (top-3, train split)

Model load order (minimises GPU reloads):
  Pass 1: base  4-bit  (no adapter)  -> A
  Pass 2: ft    4-bit  (with adapter) -> B, D, E, F
  Pass 3: ft    8-bit  (with adapter) -> C

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
      [--configs       A B C D E F]               \\
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
# BM25 retriever (inline -- no sentence-transformers dependency)
# ---------------------------------------------------------------------------
class BM25Retriever:
    """Minimal BM25 implementation over the training Q&A split."""

    def __init__(self, splits_path: str):
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            print("[RAG] rank_bm25 not found. Install with: pip install rank_bm25 --break-system-packages")
            sys.exit(1)

        with open(splits_path, encoding="utf-8") as f:
            samples = json.load(f)

        self.chunks: list[str] = []
        self.metadata: list[dict] = []
        for s in samples:
            cat   = s.get("category", "General")
            q_txt = s["question"].strip()
            a_txt = s["answer"].strip()
            chunk = f"[{cat}] Q: {q_txt}\nA: {a_txt}"
            self.chunks.append(chunk)
            self.metadata.append({"category": cat, "question": q_txt})

        tokenized = [c.lower().split() for c in self.chunks]
        self.index = BM25Okapi(tokenized)
        print(f"[RAG] BM25 index built over {len(self.chunks):,} chunks")

    def retrieve(self, query: str, top_k: int = 3) -> list[tuple[int, float]]:
        tokens = query.lower().split()
        scores = self.index.get_scores(tokens)
        top_indices = np.argsort(scores)[::-1][:top_k]
        return [(int(i), float(scores[i])) for i in top_indices]

    def build_context_block(self, results: list[tuple[int, float]]) -> str:
        lines = []
        for rank, (idx, _score) in enumerate(results, 1):
            lines.append(f"[Reference {rank}]\n{self.chunks[idx]}")
        return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Model loading / unloading
# ---------------------------------------------------------------------------
def load_model(
    model_path: str,
    adapter_path: Optional[str],
    quant: str,          # "4bit" | "8bit"
) -> tuple:
    """
    Load base model with optional LoRA adapter.
    Returns (model, tokenizer).
    adapter_path=None -> base model only (config A).
    """
    print(f"\n[load] model={model_path}  quant={quant}  "
          f"adapter={adapter_path or '(none)'}")

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
        raise ValueError(f"Unknown quant: {quant}")

    # Tokenizer: load from adapter dir if available (has chat template),
    # otherwise load from model dir.
    tok_source = adapter_path if adapter_path else model_path
    tokenizer = AutoTokenizer.from_pretrained(tok_source)
    if tokenizer.pad_token is None:
        tokenizer.pad_token    = tokenizer.unk_token
        tokenizer.pad_token_id = tokenizer.unk_token_id

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


def run_rag_bm25(model, tokenizer, q: dict, stop_ids: list,
                 retriever: BM25Retriever, max_new: int) -> dict:
    """Config F: BM25 RAG -- retrieve top-3 training chunks, inject as context."""
    t_ret = time.time()
    hits  = retriever.retrieve(q["question"], top_k=3)
    t_ret = time.time() - t_ret
    context  = retriever.build_context_block(hits)
    prompt   = prompt_rag(q["question"], context)
    r = generate(model, tokenizer, prompt,
                 max_new_tokens=max_new, stop_ids=stop_ids)
    retrieved = [
        {"rank": rank, "score": round(score, 4),
         "category": retriever.metadata[idx]["category"],
         "question": retriever.metadata[idx]["question"]}
        for rank, (idx, score) in enumerate(hits, 1)
    ]
    return {
        **r,
        "config": "F_RAG_BM25",
        "meta": {
            "retrieve_time_s": round(t_ret, 3),
            "retrieved":       retrieved,
        },
    }


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
    retriever: BM25Retriever | None = None,
) -> list[dict]:
    results = []
    n = len(questions)
    for q in questions:
        qid = q["question_id"]
        sc_tag = " [SC]" if q["safety_critical"] else ""
        print(f"  [{config_label}] {qid}{sc_tag}  {q['question'][:55]}...")
        # Dispatch
        if config_label == "A_BASE_4BIT":
            r = run_base_4bit(model, tokenizer, q, stop_ids, max_new)
        elif config_label in ("B_FINETUNED_4BIT", "C_FINETUNED_8BIT"):
            r = run_finetuned_greedy(model, tokenizer, q, stop_ids, max_new, config_label)
        elif config_label == "D_T4_IMPROVED":
            r = run_t4_improved(model, tokenizer, q, stop_ids, floor_map or {}, max_new)
        elif config_label == "E_T6_IMPROVED":
            r = run_t6_improved(model, tokenizer, q, stop_ids, max_new)
        elif config_label == "F_RAG_BM25":
            r = run_rag_bm25(model, tokenizer, q, stop_ids, retriever, max_new)
        else:
            raise ValueError(f"Unknown config: {config_label}")

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
        "A_BASE_4BIT":      "A  Base 4-bit (no FT)   ",
        "B_FINETUNED_4BIT": "B  Fine-tuned 4-bit      ",
        "C_FINETUNED_8BIT": "C  Fine-tuned 8-bit      ",
        "D_T4_IMPROVED":    "D  T4 Improved (4-bit)   ",
        "E_T6_IMPROVED":    "E  T6 Improved (4-bit)   ",
        "F_RAG_BM25":       "F  RAG BM25   (4-bit)    ",
    }
    print("\n" + "=" * 80)
    print(f"{'Config':<26} {'ROUGE-L':>8} {'SC':>8} {'Non-SC':>8} "
          f"{'tok/s':>7} {'Flagged':>8}")
    print("-" * 80)
    base_rl = all_metrics.get("B_FINETUNED_4BIT", {}).get("rougeL_mean", 0.0)
    for key in ["A_BASE_4BIT","B_FINETUNED_4BIT","C_FINETUNED_8BIT",
                "D_T4_IMPROVED","E_T6_IMPROVED","F_RAG_BM25"]:
        m = all_metrics.get(key)
        if not m:
            continue
        lbl   = labels.get(key, key)
        rl    = m["rougeL_mean"]
        delta = (f"  ({'+' if rl >= base_rl else ''}{rl - base_rl:+.4f} vs B)"
                 if key != "B_FINETUNED_4BIT" else "")
        print(f"{lbl:<26} {rl:>8.4f} {m['rougeL_sc_mean']:>8.4f} "
              f"{m['rougeL_nsc_mean']:>8.4f} {m['tok_per_sec_mean']:>7.1f} "
              f"{m['n_flagged_unsafe']:>8}{delta}")
    print("=" * 80)


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
ALL_CONFIGS = ["A", "B", "C", "D", "E", "F"]
CONFIG_MAP  = {
    "A": "A_BASE_4BIT",
    "B": "B_FINETUNED_4BIT",
    "C": "C_FINETUNED_8BIT",
    "D": "D_T4_IMPROVED",
    "E": "E_T6_IMPROVED",
    "F": "F_RAG_BM25",
}


def parse_args():
    p = argparse.ArgumentParser(description="v2 comprehensive eval -- 6 configs")
    p.add_argument("--adapter_4bit",  default=DEFAULT_ADAPTER_4BIT)
    p.add_argument("--adapter_8bit",  default=DEFAULT_ADAPTER_8BIT)
    p.add_argument("--model_path",    default=DEFAULT_MODEL)
    p.add_argument("--questions",     default=DEFAULT_QBANK)
    p.add_argument("--configs",       nargs="+", default=ALL_CONFIGS,
                   choices=ALL_CONFIGS,
                   help="Configs to run (default: all). A=base, B=ft4, C=ft8, "
                        "D=T4, E=T6, F=RAG")
    p.add_argument("--max_new_tokens",type=int, default=MAX_NEW_TOKENS)
    p.add_argument("--rag_top_k",     type=int, default=3)
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
            q["id"] = int(re.sub(r"\D", "", q["question_id"]))
    print(f"[load] {len(questions)} questions from {args.questions}")
    sc_count = sum(1 for q in questions if q["safety_critical"])
    print(f"       SC={sc_count}/{len(questions)} ({100*sc_count/len(questions):.1f}%)")

    # T4 floor map
    floor_map = compute_floor_map(TRAIN_SPLIT)

    # Output dir
    ts      = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(EVAL_OUT_DIR, f"v2_comprehensive_{ts}")
    os.makedirs(out_dir, exist_ok=True)
    print(f"[out]  {out_dir}\n")

    all_results: dict[str, list] = {}
    all_metrics: dict[str, dict] = {}

    # -- Pass 1: Base model 4-bit (no adapter) -----------------------------
    if "A_BASE_4BIT" in requested:
        print("\n" + "=" * 60)
        print("  PASS 1 -- Base model 4-bit (no fine-tuning)")
        print("=" * 60)
        model, tokenizer = load_model(args.model_path, None, "4bit")
        stop_ids = get_stop_ids(tokenizer)
        res = run_questions("A_BASE_4BIT", model, tokenizer, questions,
                            stop_ids, args.max_new_tokens)
        all_results["A_BASE_4BIT"] = res
        all_metrics["A_BASE_4BIT"] = compute_metrics(res)
        save_config_json(out_dir, "A_BASE_4BIT", res, args_dict)
        unload(model)

    # -- Pass 2: Fine-tuned 4-bit (B, D, E, F) ----------------------------
    pass2_configs = [c for c in ["B_FINETUNED_4BIT", "D_T4_IMPROVED",
                                  "E_T6_IMPROVED", "F_RAG_BM25"]
                     if c in requested]
    if pass2_configs:
        print("\n" + "=" * 60)
        print(f"  PASS 2 -- Fine-tuned 4-bit: {pass2_configs}")
        print("=" * 60)

        # Build BM25 retriever if RAG is needed (before loading model)
        retriever = None
        if "F_RAG_BM25" in pass2_configs:
            if not os.path.exists(TRAIN_SPLIT):
                print(f"[RAG] WARNING: train split not found at {TRAIN_SPLIT}")
                print("[RAG] Skipping F_RAG_BM25")
                pass2_configs = [c for c in pass2_configs if c != "F_RAG_BM25"]
            else:
                retriever = BM25Retriever(TRAIN_SPLIT)

        model, tokenizer = load_model(args.model_path, args.adapter_4bit, "4bit")
        stop_ids = get_stop_ids(tokenizer)

        for config_label in pass2_configs:
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

    # -- Pass 3: Fine-tuned 8-bit (C) -------------------------------------
    if "C_FINETUNED_8BIT" in requested:
        print("\n" + "=" * 60)
        print("  PASS 3 -- Fine-tuned 8-bit")
        print("=" * 60)
        model, tokenizer = load_model(args.model_path, args.adapter_8bit, "8bit")
        stop_ids = get_stop_ids(tokenizer)
        res = run_questions("C_FINETUNED_8BIT", model, tokenizer, questions,
                            stop_ids, args.max_new_tokens)
        all_results["C_FINETUNED_8BIT"] = res
        all_metrics["C_FINETUNED_8BIT"] = compute_metrics(res)
        save_config_json(out_dir, "C_FINETUNED_8BIT", res, args_dict)
        unload(model)

    # -- Save & report -----------------------------------------------------
    save_run_json(out_dir, all_results, args_dict)

    metrics_path = os.path.join(out_dir, "metrics.json")
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(all_metrics, f, indent=2)
    print(f"\n[save] metrics.json -> {metrics_path}")

    print_table(all_metrics)

    print(f"\n[done] Results in: {out_dir}")
    print(f"[next] python build_v2_judge_prompt.py --run_dir {out_dir}")


if __name__ == "__main__":
    main()
