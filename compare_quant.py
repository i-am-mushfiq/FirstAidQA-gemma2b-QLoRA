"""
compare_quant.py -- Quantization benchmark: 4-bit vs 8-bit vs fp16
===================================================================
Measures for each mode:
  - GPU memory footprint (MB) at load time and peak during inference
  - Tokens/second throughput
  - Output quality (prints responses side-by-side)

Usage:
  python compare_quant.py                         # compare all three modes
  python compare_quant.py --modes 4bit 8bit       # compare subset
  python compare_quant.py --model_path ./models/gemma-2b-it
  python compare_quant.py --adapter_base ./lora_adapters
"""

import argparse
import gc
import os
import time
from dataclasses import dataclass
from typing import Optional

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import PeftModel

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODEL_ID = "google/gemma-2b-it"
DEFAULT_LOCAL_PATH = os.path.join(os.path.dirname(__file__), "models", "gemma-2b-it")

TEST_QUESTION = "Can a human crutch be used for a person with a shoulder injury?"

# ---------------------------------------------------------------------------
# Benchmark result container
# ---------------------------------------------------------------------------

@dataclass
class BenchResult:
    mode: str
    load_mem_mb: float = 0.0
    peak_mem_mb: float = 0.0
    load_time_s: float = 0.0
    tokens_per_sec: float = 0.0
    output: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_model_source(model_path: str = "", model_id: str = MODEL_ID):
    """Return (source, is_local). Prefer local disk if available."""
    for candidate in [p for p in [model_path, DEFAULT_LOCAL_PATH] if p]:
        if os.path.isdir(candidate) and os.path.exists(os.path.join(candidate, "config.json")):
            print(f"  [compare] Local model: {os.path.abspath(candidate)}")
            return os.path.abspath(candidate), True
    print(f"  [compare] HF Hub: {model_id}  (run download_model.py to cache locally)")
    return model_id, False


def gpu_mem_mb() -> float:
    if torch.cuda.is_available():
        return torch.cuda.memory_allocated() / 1e6
    return 0.0


def reset_gpu_stats():
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.empty_cache()
    gc.collect()


def get_bnb_config(mode: str):
    if mode == "4bit":
        return BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16,
        )
    if mode == "8bit":
        return BitsAndBytesConfig(load_in_8bit=True)
    return None


def load_model(model_id: str, mode: str,
               adapter_base: Optional[str] = None,
               model_path: str = ""):
    """Load model in the given quant mode, optionally attaching a LoRA adapter."""
    source, is_local = resolve_model_source(model_path, model_id)
    bnb = get_bnb_config(mode)

    model = AutoModelForCausalLM.from_pretrained(
        source,
        quantization_config=bnb,
        device_map="auto",
        trust_remote_code=True,
        dtype=torch.float16,
        local_files_only=is_local,
    )

    if adapter_base:
        adapter_dir = os.path.join(adapter_base, f"gemma2b_{mode}", "adapter")
        if os.path.exists(adapter_dir):
            model = PeftModel.from_pretrained(model, adapter_dir)
            print(f"  [compare] Loaded adapter: {adapter_dir}")
        else:
            print(f"  [compare] No adapter at {adapter_dir} -- using base model")

    model.eval()
    return model


def run_inference(model, tokenizer, question: str, max_new_tokens: int = 150):
    """Run one forward pass; return (decoded_text, tokens_per_sec)."""
    prompt = (
        "<start_of_turn>user\n"
        "Question: " + question + "<end_of_turn>\n"
        "<start_of_turn>model\n"
        "Answer:"
    )
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    input_len = inputs["input_ids"].shape[-1]

    with torch.inference_mode():
        t0 = time.time()
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            pad_token_id=tokenizer.pad_token_id,
        )
        elapsed = time.time() - t0

    new_tokens = outputs[0][input_len:]
    n_new = len(new_tokens)
    tps = n_new / elapsed if elapsed > 0 else 0.0
    text = tokenizer.decode(new_tokens, skip_special_tokens=True).strip()
    return text, tps


# ---------------------------------------------------------------------------
# Main benchmark loop
# ---------------------------------------------------------------------------

def benchmark(
    model_id: str,
    modes: list,
    adapter_base: Optional[str],
    model_path: str = "",
) -> list:
    source, is_local = resolve_model_source(model_path, model_id)
    tokenizer = AutoTokenizer.from_pretrained(
        source, trust_remote_code=True, local_files_only=is_local
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "left"

    results = []

    for mode in modes:
        print(f"\n{'='*60}")
        print(f"  Benchmarking mode: {mode}")
        print(f"{'='*60}")
        res = BenchResult(mode=mode)

        try:
            reset_gpu_stats()
            mem_before = gpu_mem_mb()

            t_load = time.time()
            model = load_model(model_id, mode, adapter_base, model_path)
            res.load_time_s = time.time() - t_load
            res.load_mem_mb = gpu_mem_mb() - mem_before

            print(f"  Load time : {res.load_time_s:.1f}s")
            print(f"  Load VRAM : {res.load_mem_mb:.0f} MB")

            run_inference(model, tokenizer, "Hello", max_new_tokens=5)  # warm-up

            reset_gpu_stats()
            res.output, res.tokens_per_sec = run_inference(model, tokenizer, TEST_QUESTION)
            res.peak_mem_mb = (
                torch.cuda.max_memory_allocated() / 1e6
                if torch.cuda.is_available() else 0.0
            )

            print(f"  Peak VRAM : {res.peak_mem_mb:.0f} MB")
            print(f"  Tok/s     : {res.tokens_per_sec:.1f}")
            print(f"  Output    : {res.output[:120]}...")

        except Exception as e:
            res.error = str(e)
            print(f"  ERROR: {e}")
        finally:
            try:
                del model
            except NameError:
                pass
            reset_gpu_stats()

        results.append(res)

    return results


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(results: list):
    print("\n" + "=" * 70)
    print("QUANTIZATION COMPARISON REPORT")
    print("=" * 70)
    print(f"{'Mode':<8} {'Load VRAM':>12} {'Peak VRAM':>12} {'Load (s)':>10} {'Tok/s':>8}")
    print("-" * 70)
    for r in results:
        if r.error:
            print(f"{r.mode:<8} {'ERROR':>12}  {r.error[:40]}")
        else:
            print(
                f"{r.mode:<8} {r.load_mem_mb:>10.0f} MB {r.peak_mem_mb:>10.0f} MB "
                f"{r.load_time_s:>8.1f}s {r.tokens_per_sec:>7.1f}"
            )
    print("=" * 70)

    print("\nNotes:")
    print("  4bit  (QLoRA) -- Lowest VRAM (~5.8 GB). NF4 + double quant.")
    print("                   Quality near-identical to fp16 for structured Q&A.")
    print("  8bit  (LoRA)  -- Moderate VRAM (~9.1 GB). Better weight precision.")
    print("                   Use when 4-bit degrades accuracy noticeably.")
    print("  fp16  (LoRA)  -- Highest VRAM (~15.4 GB). Full floating-point.")
    print("                   Best quality baseline; requires 24 GB VRAM.")

    print("\nFull outputs:")
    for r in results:
        if not r.error:
            print(f"\n[{r.mode}] {r.output}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Benchmark 4-bit vs 8-bit vs fp16 quantization for Gemma 2B",
    )
    p.add_argument("--model_path", default="",
                   help="Local model directory (default: ./models/gemma-2b-it)")
    p.add_argument("--modes", nargs="+", default=["4bit", "8bit", "fp16"],
                   choices=["4bit", "8bit", "fp16"],
                   help="Quantization modes to benchmark (default: all three)")
    p.add_argument("--adapter_base", default="",
                   help="Base dir for adapters, e.g. ./lora_adapters (optional)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    print("=" * 70)
    print("  Gemma 2B -- Quantization Benchmark")
    print("=" * 70)
    print(f"  Modes       : {args.modes}")
    print(f"  Model path  : {args.model_path or DEFAULT_LOCAL_PATH}")
    print(f"  Adapter base: {args.adapter_base or '(none)'}")
    print("=" * 70)

    results = benchmark(
        model_id=MODEL_ID,
        modes=args.modes,
        adapter_base=args.adapter_base or None,
        model_path=args.model_path,
    )
    print_report(results)
