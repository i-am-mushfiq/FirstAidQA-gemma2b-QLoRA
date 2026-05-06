"""
eval_suite.py -- Persistent multi-model evaluation suite
=========================================================
Runs an external first-aid question bank through every available model variant
(base, 4-bit, 8-bit, fp16) and saves results to a timestamped JSON file under
eval_results/.

Each run produces a NEW file -- results are never overwritten.
Designed for downstream evaluation with ROUGE-L, BERTScore, and LLM-judge.

Usage:
  python eval_suite_v2.py
  python eval_suite_v2.py --models 4bit gemma-2b-it_4bit google_gemma-2b-it_4bit
  python eval_suite_v2.py --models google_gemma-2b-it_4bit
  python eval_suite_v2.py --model_path ./models/gemma-2b-it
  python eval_suite_v2.py --max_new_tokens 200
  python eval_suite_v2.py --questions_file eval_questions_30.json
"""

import argparse
import gc
import json
import os
import time
from datetime import datetime

import torch
from peft import PeftModel
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

HERE         = os.path.dirname(os.path.abspath(__file__))
MODEL_ID     = "google/gemma-2b-it"
LOCAL_MODEL  = os.path.join(HERE, "models", "gemma-2b-it")
RESULTS_DIR  = os.path.join(HERE, "evaluations")
ADAPTERS_DIR = os.path.join(HERE, "experiments")


def infer_quant(name: str) -> str:
    lowered = name.lower()
    if "4bit" in lowered:
        return "4bit"
    if "8bit" in lowered:
        return "8bit"
    return "fp16"


def find_adapter_path(adapter_root: str) -> str:
    """
    Return the loadable PEFT adapter path inside one lora_adapters dataset row.
    Preferred layout is <row>/adapter, but checkpoint-only rows are supported too.
    """
    preferred = os.path.join(adapter_root, "adapter")
    if os.path.exists(os.path.join(preferred, "adapter_config.json")):
        return preferred

    if os.path.exists(os.path.join(adapter_root, "adapter_config.json")):
        return adapter_root

    checkpoints = []
    for child in os.listdir(adapter_root):
        child_path = os.path.join(adapter_root, child)
        if (
            os.path.isdir(child_path)
            and child.startswith("checkpoint-")
            and os.path.exists(os.path.join(child_path, "adapter_config.json"))
        ):
            checkpoints.append(child_path)
    if checkpoints:
        return max(checkpoints, key=os.path.getmtime)

    return ""


def make_variant_key(folder_name: str, used_keys: set[str]) -> str:
    aliases = {
        "gemma2b_4bit": "4bit",
        "gemma2b_8bit": "8bit",
        "gemma2b_fp16": "fp16",
    }
    key = aliases.get(folder_name, folder_name)
    return key if key not in used_keys else folder_name


def make_variant_label(key: str, folder_name: str) -> str:
    labels = {
        "base": "Base model (no fine-tuning)",
        "4bit": "Fine-tuned LoRA 4-bit",
        "8bit": "Fine-tuned LoRA 8-bit",
        "fp16": "Fine-tuned LoRA fp16",
        "google_gemma-2b-it_4bit": "Fine-tuned LoRA google/gemma-2b-it 4-bit",
    }
    if key in labels:
        return labels[key]
    return folder_name.replace("_", " ")


def discover_model_variants() -> list[dict]:
    variants = [{
        "key": "base",
        "label": "Base model (no fine-tuning)",
        "quant": "fp16",
        "adapter_path": "",
    }]

    if not os.path.isdir(ADAPTERS_DIR):
        return variants

    used_keys = {"base"}
    for folder_name in sorted(os.listdir(ADAPTERS_DIR)):
        if folder_name.startswith("_"):          # skip _v1_archive and similar
            continue
        adapter_root = os.path.join(ADAPTERS_DIR, folder_name)
        if not os.path.isdir(adapter_root):
            continue

        adapter_path = find_adapter_path(adapter_root)
        if not adapter_path:
            continue

        key = make_variant_key(folder_name, used_keys)
        used_keys.add(key)
        variants.append({
            "key": key,
            "label": make_variant_label(key, folder_name),
            "quant": infer_quant(folder_name),
            "adapter_path": adapter_path,
            "folder": folder_name,
        })

    return variants


MODEL_VARIANTS = discover_model_variants()
VARIANTS_BY_KEY = {variant["key"]: variant for variant in MODEL_VARIANTS}
DEFAULT_MODELS = [
    variant["key"]
    for variant in MODEL_VARIANTS
    if variant["quant"] == "4bit"
]

SYSTEM_PROMPT = (
    "You are a first aid assistant. Provide accurate, step-by-step emergency "
    "guidance. For life-threatening situations, always advise calling emergency "
    "services immediately."
)

# ---------------------------------------------------------------------------
# Question bank loading
# ---------------------------------------------------------------------------

DEFAULT_QUESTIONS_FILE = os.path.join(HERE, "data", "eval_questions_30.json")
REQUIRED_QUESTION_FIELDS = {"id", "category", "safety_critical", "question", "reference"}


def load_questions(path: str) -> list[dict]:
    """Load and validate an external first-aid question bank."""
    resolved = os.path.abspath(path)
    with open(resolved, encoding="utf-8") as f:
        payload = json.load(f)

    questions = payload.get("questions") if isinstance(payload, dict) else payload
    if not isinstance(questions, list) or not questions:
        raise ValueError(f"Question bank must contain a non-empty list: {resolved}")

    seen_ids = set()
    for i, question in enumerate(questions, start=1):
        if not isinstance(question, dict):
            raise ValueError(f"Question #{i} must be an object")

        missing = REQUIRED_QUESTION_FIELDS - set(question)
        if missing:
            missing_list = ", ".join(sorted(missing))
            raise ValueError(f"Question #{i} is missing fields: {missing_list}")

        qid = question["id"]
        if qid in seen_ids:
            raise ValueError(f"Duplicate question id in question bank: {qid}")
        seen_ids.add(qid)

        if not isinstance(question["safety_critical"], bool):
            raise ValueError(f"Question {qid} safety_critical must be true or false")
        for field in ["category", "question", "reference"]:
            if not isinstance(question[field], str) or not question[field].strip():
                raise ValueError(f"Question {qid} field '{field}' must be non-empty text")

    return questions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_model(model_path: str = "") -> tuple:
    for candidate in [p for p in [model_path, LOCAL_MODEL] if p]:
        if os.path.isdir(candidate) and os.path.exists(
            os.path.join(candidate, "config.json")
        ):
            return os.path.abspath(candidate), True
    return MODEL_ID, False


def unload(model):
    del model
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()


def get_bnb_config(quant: str):
    if quant == "4bit":
        return BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16,
        )
    if quant == "8bit":
        return BitsAndBytesConfig(load_in_8bit=True)
    return None


def load_variant(source: str, is_local: bool, quant: str, adapter_path: str = ""):
    bnb = get_bnb_config(quant)
    tokenizer = AutoTokenizer.from_pretrained(
        source, trust_remote_code=True, local_files_only=is_local
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token    = tokenizer.eos_token
        tokenizer.pad_token_id = tokenizer.eos_token_id
    tokenizer.padding_side = "left"

    model = AutoModelForCausalLM.from_pretrained(
        source,
        quantization_config=bnb,
        torch_dtype=torch.float16,
        device_map="auto",
        trust_remote_code=True,
        local_files_only=is_local,
    )
    if adapter_path and os.path.exists(adapter_path):
        model = PeftModel.from_pretrained(model, adapter_path)
    model.eval()
    return model, tokenizer


def build_prompt(question: str) -> str:
    return (
        f"<start_of_turn>user\n{SYSTEM_PROMPT}\n\n"
        f"Question: {question}<end_of_turn>\n"
        f"<start_of_turn>model\n"
    )


def _get_stop_token_ids(tokenizer) -> list[int]:
    """
    Return all token IDs that should terminate generation.
    Includes eos_token plus Gemma's <end_of_turn> if present.
    """
    stop_ids = [tokenizer.eos_token_id]
    for candidate in ["<end_of_turn>", "<|im_end|>", "[/INST]"]:
        tid = tokenizer.convert_tokens_to_ids(candidate)
        if tid is not None and tid != tokenizer.unk_token_id:
            stop_ids.append(tid)
    return list(set(stop_ids))


def generate(model, tokenizer, question: str, max_new_tokens: int) -> dict:
    prompt  = build_prompt(question)
    inputs  = tokenizer(prompt, return_tensors="pt").to(model.device)
    in_len  = inputs["input_ids"].shape[-1]

    stop_ids = _get_stop_token_ids(tokenizer)

    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

    try:
        with torch.inference_mode():
            t0  = time.time()
            out = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                pad_token_id=tokenizer.pad_token_id,
                eos_token_id=stop_ids,
                repetition_penalty=1.15,
            )
            elapsed = time.time() - t0
        new_ids = out[0][in_len:]
        n_tok   = len(new_ids)
        answer  = tokenizer.decode(new_ids, skip_special_tokens=True).strip()
        peak_mb = (
            torch.cuda.max_memory_allocated() / 1e6
            if torch.cuda.is_available() else 0.0
        )
        return {
            "answer":           answer,
            "tokens_generated": n_tok,
            "tokens_per_sec":   round(n_tok / elapsed, 2) if elapsed > 0 else 0.0,
            "elapsed_s":        round(elapsed, 2),
            "peak_vram_mb":     round(peak_mb, 0),
            "error":            None,
        }
    except Exception as e:
        return {
            "answer":           "",
            "tokens_generated": 0,
            "tokens_per_sec":   0.0,
            "elapsed_s":        0.0,
            "peak_vram_mb":     0.0,
            "error":            str(e),
        }


# ---------------------------------------------------------------------------
# Core evaluation loop
# ---------------------------------------------------------------------------

def run_model(variant_key: str, source: str, is_local: bool,
              questions: list[dict],
              max_new_tokens: int) -> dict:
    """Load one model variant, run all questions, unload. Returns result dict."""
    variant = VARIANTS_BY_KEY[variant_key]
    quant = variant["quant"]
    adapter_path = variant["adapter_path"]
    label = variant["label"]

    print(f"\n{'='*60}")
    print(f"  Model : {label}")
    print(f"  Quant : {quant}  |  Adapter: {adapter_path or 'none'}")
    print(f"{'='*60}")

    if adapter_path and not os.path.exists(adapter_path):
        print(f"  SKIP -- adapter not found: {adapter_path}")
        return {"variant": variant_key, "label": label, "skipped": True, "answers": []}

    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()

    print("  Loading...")
    model, tokenizer = load_variant(source, is_local, quant, adapter_path)
    mem_loaded = torch.cuda.memory_allocated() / 1e6 if torch.cuda.is_available() else 0.0
    print(f"  VRAM after load : {mem_loaded:.0f} MB")

    # warm-up pass (not saved)
    generate(model, tokenizer, "Hello", max_new_tokens=5)
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

    answers = []
    for q in questions:
        print(f"  Q{q['id']:02d}/{len(questions)}  {q['question'][:60]}...")
        result = generate(model, tokenizer, q["question"], max_new_tokens)
        answers.append({
            "question_id":    q["id"],
            "question":       q["question"],
            "reference":      q["reference"],
            "category":       q["category"],
            "safety_critical": q["safety_critical"],
            **result,
        })
        status = f"err: {result['error']}" if result["error"] else \
                 f"{result['tokens_per_sec']} tok/s  peak {result['peak_vram_mb']:.0f} MB"
        print(f"          {status}")

    unload(model)

    return {
        "variant":  variant_key,
        "label":    label,
        "quant":    quant,
        "skipped":  False,
        "answers":  answers,
    }

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def make_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def save_results(run_id: str, meta: dict, model_results: list):
    eval_folder = os.path.join(RESULTS_DIR, f"eval_{run_id}")
    os.makedirs(eval_folder, exist_ok=True)
    path = os.path.join(eval_folder, "run.json")
    payload = {
        "run_id":    run_id,
        "timestamp": datetime.now().isoformat(),
        "meta":      meta,
        "results":   model_results,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"\n[eval] Results saved -> {path}")
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    available_models = list(VARIANTS_BY_KEY.keys())
    default_models = DEFAULT_MODELS or available_models
    p = argparse.ArgumentParser(
        description="Run first-aid eval suite across discovered 4-bit model variants",
    )
    p.add_argument(
        "--models", nargs="+",
        default=default_models,
        choices=available_models,
        help=(
            f"Which variants to run (default: discovered 4-bit variants: "
            f"{', '.join(default_models)}). Available: {', '.join(available_models)}"
        ),
    )
    p.add_argument("--model_path", default="",
                   help="Local base model directory")
    p.add_argument("--max_new_tokens", type=int, default=250,
                   help="Max tokens per answer (default 250)")
    p.add_argument("--questions_file", default=DEFAULT_QUESTIONS_FILE,
                   help=f"Question bank JSON file (default: {DEFAULT_QUESTIONS_FILE})")
    args = p.parse_args()

    run_id = make_run_id()
    source, is_local = resolve_model(args.model_path)
    questions = load_questions(args.questions_file)

    meta = {
        "model_id":       MODEL_ID,
        "model_source":   source,
        "variants_run":   args.models,
        "model_variants": [
            {
                "key": variant["key"],
                "label": variant["label"],
                "quant": variant["quant"],
                "adapter_path": variant["adapter_path"],
            }
            for variant in MODEL_VARIANTS
        ],
        "max_new_tokens": args.max_new_tokens,
        "questions_file": os.path.abspath(args.questions_file),
        "n_questions":    len(questions),
        "system_prompt":  SYSTEM_PROMPT,
    }

    print("=" * 60)
    print(f"  Eval Suite -- {len(questions)}-Question First Aid Benchmark")
    print("=" * 60)
    print(f"  Run ID     : {run_id}")
    print(f"  Variants   : {args.models}")
    print(f"  Questions  : {len(questions)}")
    print(f"  Bank file  : {os.path.abspath(args.questions_file)}")
    print(f"  Max tokens : {args.max_new_tokens}")
    print(f"  Output dir : {RESULTS_DIR}/")
    print("=" * 60)

    model_results = []
    for variant in args.models:
        result = run_model(variant, source, is_local, questions, args.max_new_tokens)
        model_results.append(result)
        # Save incrementally after each model so a crash mid-run doesn't lose work
        save_results(run_id, meta, model_results)

    # --- Summary table ---
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    col = max((len(r["label"]) for r in model_results), default=25) + 4
    print(f"  {'Variant':<{col}} {'Answered':>8} {'Avg tok/s':>10} {'Peak MB':>9} {'Errors':>7}")
    print("  " + "-" * (col + 37))
    for r in model_results:
        if r.get("skipped"):
            print(f"  {r['label']:<{col}} {'SKIPPED':>8}")
            continue
        answered  = sum(1 for a in r["answers"] if not a["error"])
        avg_tps   = (
            sum(a["tokens_per_sec"] for a in r["answers"] if not a["error"]) / answered
            if answered else 0
        )
        peak_mb   = max(
            (a["peak_vram_mb"] for a in r["answers"] if not a["error"]),
            default=0.0,
        )
        errors    = sum(1 for a in r["answers"] if a["error"])
        print(f"  {r['label']:<{col}} {answered:>8} {avg_tps:>9.1f} {peak_mb:>8.0f} {errors:>7}")

    out_path = os.path.join(RESULTS_DIR, f"eval_{run_id}", "run.json")
    print(f"\n  Full results : {out_path}")
    print("=" * 60)
