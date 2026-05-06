"""
compare_models.py -- Side-by-side comparison across all four model variants
===========================================================================
Runs a single question through:
  1. Base Gemma 2B (no adapter, no fine-tuning)
  2. QLoRA 4-bit  (fine-tuned, NF4 quantisation)
  3. LoRA  8-bit  (fine-tuned, INT8 quantisation)
  4. LoRA  fp16   (fine-tuned, native float16)

Each model is loaded, run, then fully unloaded before the next is loaded
so peak VRAM never exceeds the fp16 requirement (~15 GB).

Usage:
  python compare_models.py
  python compare_models.py --question "How do you treat a severe burn?"
  python compare_models.py --max_new_tokens 150
  python compare_models.py --model_path ./models/gemma-2b-it
"""

import argparse
import gc
import os
import time

import torch
from peft import PeftModel
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
)

# ---------------------------------------------------------------------------
# Paths and defaults
# ---------------------------------------------------------------------------

HERE = os.path.dirname(__file__)

MODEL_ID      = "google/gemma-2b-it"
DEFAULT_LOCAL = os.path.join(HERE, "models", "gemma-2b-it")

ADAPTERS = {
    "4bit":  os.path.join(HERE, "lora_adapters", "gemma2b_4bit",  "adapter"),
    "8bit":  os.path.join(HERE, "lora_adapters", "gemma2b_8bit",  "adapter"),
    "fp16":  os.path.join(HERE, "lora_adapters", "gemma2b_fp16",  "adapter"),
}

SYSTEM_PROMPT = (
    "You are a first aid assistant. Provide accurate, step-by-step emergency "
    "guidance. For life-threatening situations, always advise calling emergency "
    "services immediately."
)

DEFAULT_QUESTION = (
    "What is the correct ratio of chest compressions to rescue breaths in CPR?"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_model(model_path: str = "") -> tuple:
    for candidate in [p for p in [model_path, DEFAULT_LOCAL] if p]:
        if os.path.isdir(candidate) and os.path.exists(
            os.path.join(candidate, "config.json")
        ):
            return os.path.abspath(candidate), True
    return MODEL_ID, False


def gpu_mem_mb() -> float:
    if torch.cuda.is_available():
        return torch.cuda.memory_allocated() / 1e6
    return 0.0


def unload(model):
    del model
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()


def build_prompt(question: str) -> str:
    return (
        f"<start_of_turn>user\n{SYSTEM_PROMPT}\n\n"
        f"Question: {question}<end_of_turn>\n"
        f"<start_of_turn>model\nAnswer: "
    )


def generate(model, tokenizer, question: str, max_new_tokens: int) -> tuple:
    prompt  = build_prompt(question)
    inputs  = tokenizer(prompt, return_tensors="pt").to(model.device)
    in_len  = inputs["input_ids"].shape[-1]

    with torch.inference_mode():
        t0  = time.time()
        out = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            pad_token_id=tokenizer.pad_token_id,
        )
        elapsed = time.time() - t0

    new_ids = out[0][in_len:]
    tps     = len(new_ids) / elapsed if elapsed > 0 else 0.0
    answer  = tokenizer.decode(new_ids, skip_special_tokens=True).strip()
    peak_mb = torch.cuda.max_memory_allocated() / 1e6 if torch.cuda.is_available() else 0.0
    return answer, tps, peak_mb


# ---------------------------------------------------------------------------
# Per-variant loaders
# ---------------------------------------------------------------------------

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
    """Load base model + optional adapter. Returns (model, tokenizer)."""
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


# ---------------------------------------------------------------------------
# Main comparison loop
# ---------------------------------------------------------------------------

def run_comparison(question: str, model_path: str, max_new_tokens: int):
    source, is_local = resolve_model(model_path)

    variants = [
        ("Base (no fine-tuning)", "fp16", ""),
        ("Fine-tuned 4-bit QLoRA", "4bit", ADAPTERS["4bit"]),
        ("Fine-tuned 8-bit LoRA",  "8bit", ADAPTERS["8bit"]),
        ("Fine-tuned fp16 LoRA",   "fp16", ADAPTERS["fp16"]),
    ]

    results = []

    for label, quant, adapter in variants:
        missing = adapter and not os.path.exists(adapter)
        print(f"\n{'='*60}")
        print(f"  Loading: {label}  [{quant}]")
        if missing:
            print(f"  SKIP -- adapter not found: {adapter}")
            results.append((label, quant, None, None, None))
            continue
        print(f"{'='*60}")

        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
            torch.cuda.empty_cache()

        model, tokenizer = load_variant(source, is_local, quant, adapter)
        mem_loaded = gpu_mem_mb()
        print(f"  VRAM after load  : {mem_loaded:.0f} MB")

        # warm-up
        generate(model, tokenizer, "Hello", max_new_tokens=5)
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()

        answer, tps, peak_mb = generate(model, tokenizer, question, max_new_tokens)
        print(f"  Peak VRAM        : {peak_mb:.0f} MB")
        print(f"  Tok/s            : {tps:.1f}")
        results.append((label, quant, answer, tps, peak_mb))

        unload(model)

    # --- Final report ---
    print("\n\n" + "=" * 70)
    print("  COMPARISON REPORT")
    print(f"  Question: {question[:80]}{'...' if len(question) > 80 else ''}")
    print("=" * 70)

    for label, quant, answer, tps, peak_mb in results:
        print(f"\n[ {label} | {quant} ]")
        if answer is None:
            print("  SKIPPED (adapter not found)")
            continue
        print(f"  Peak VRAM : {peak_mb:.0f} MB   |   {tps:.1f} tok/s")
        print()
        # Word-wrap answer at 70 chars
        words   = answer.split()
        line    = "  "
        for word in words:
            if len(line) + len(word) + 1 > 72:
                print(line)
                line = "  " + word + " "
            else:
                line += word + " "
        if line.strip():
            print(line)

    print("\n" + "=" * 70)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="Compare base vs 4bit/8bit/fp16 fine-tuned Gemma 2B",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--question", default=DEFAULT_QUESTION,
                   help="Question to answer (default: CPR ratio question)")
    p.add_argument("--model_path", default="",
                   help="Local base model directory")
    p.add_argument("--max_new_tokens", type=int, default=200)
    args = p.parse_args()

    print("=" * 60)
    print("  Gemma 2B -- All Variants Comparison")
    print("=" * 60)
    print(f"  Question      : {args.question[:60]}...")
    print(f"  Max tokens    : {args.max_new_tokens}")
    print("=" * 60)

    run_comparison(args.question, args.model_path, args.max_new_tokens)
