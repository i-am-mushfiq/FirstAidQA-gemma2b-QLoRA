"""
infer_fp16.py  --  Inference with the fp16 LoRA-fine-tuned Gemma 2B
====================================================================
Loads the model in native float16 (no quantisation) and attaches the
saved LoRA adapter from lora_adapters/gemma2b_fp16/adapter/.

Modes
-----
  interactive   Live Q&A loop (default)
  batch         Run on splits/test.json and save outputs to JSON
  single        Answer one question passed via --question

Usage
-----
  python infer_fp16.py
  python infer_fp16.py --mode batch
  python infer_fp16.py --mode single --question "How do you treat a burn?"
  python infer_fp16.py --adapter_path ./lora_adapters/gemma2b_fp16/adapter
  python infer_fp16.py --model_path  ./models/gemma-2b-it
"""

import argparse
import json
import os
import time

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

HERE             = os.path.dirname(__file__)
MODEL_ID         = "google/gemma-2b-it"
DEFAULT_LOCAL    = os.path.join(HERE, "models", "gemma-2b-it")
DEFAULT_ADAPTER  = os.path.join(HERE, "lora_adapters", "gemma2b_fp16", "adapter")
DEFAULT_TEST     = os.path.join(HERE, "splits", "test.json")
BATCH_OUTPUT     = os.path.join(HERE, "infer_fp16_outputs.json")

SYSTEM_PROMPT = (
    "You are a first aid assistant. Provide accurate, step-by-step emergency "
    "guidance. For life-threatening situations, always advise calling emergency "
    "services immediately."
)

# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

def resolve_model(model_path: str = "") -> tuple:
    for candidate in [p for p in [model_path, DEFAULT_LOCAL] if p]:
        if os.path.isdir(candidate) and os.path.exists(
            os.path.join(candidate, "config.json")
        ):
            return os.path.abspath(candidate), True
    return MODEL_ID, False


def load_model(model_path: str = "", adapter_path: str = DEFAULT_ADAPTER):
    source, is_local = resolve_model(model_path)

    print(f"[infer] Model source  : {source}")
    print(f"[infer] Adapter path  : {adapter_path}")
    print(f"[infer] Quantisation  : fp16 (none)")

    if not os.path.exists(adapter_path):
        raise FileNotFoundError(
            f"Adapter not found at: {adapter_path}\n"
            f"Run: python train.py --quant fp16 --model_path ./models/gemma-2b-it"
        )

    print("[infer] Loading base model in fp16...")
    tokenizer = AutoTokenizer.from_pretrained(
        source, trust_remote_code=True, local_files_only=is_local
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token    = tokenizer.eos_token
        tokenizer.pad_token_id = tokenizer.eos_token_id
    tokenizer.padding_side = "left"

    model = AutoModelForCausalLM.from_pretrained(
        source,
        torch_dtype=torch.float16,
        device_map="auto",
        trust_remote_code=True,
        local_files_only=is_local,
    )

    print("[infer] Attaching fp16 LoRA adapter...")
    model = PeftModel.from_pretrained(model, adapter_path)
    model.eval()

    if torch.cuda.is_available():
        mem = torch.cuda.memory_allocated() / 1e6
        print(f"[infer] VRAM in use   : {mem:.0f} MB")

    print("[infer] Ready.\n")
    return model, tokenizer


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def build_prompt(question: str) -> str:
    return (
        f"<start_of_turn>user\n{SYSTEM_PROMPT}\n\n"
        f"Question: {question}<end_of_turn>\n"
        f"<start_of_turn>model\nAnswer: "
    )


def generate(
    model,
    tokenizer,
    question: str,
    max_new_tokens: int = 256,
    temperature: float = 0.0,
) -> tuple:
    """
    Run one generation pass.
    Returns (answer_text, tokens_per_second).
    temperature=0.0 uses greedy decoding (deterministic, best for eval).
    """
    prompt  = build_prompt(question)
    inputs  = tokenizer(prompt, return_tensors="pt").to(model.device)
    in_len  = inputs["input_ids"].shape[-1]

    gen_kwargs = dict(
        max_new_tokens=max_new_tokens,
        pad_token_id=tokenizer.pad_token_id,
        eos_token_id=tokenizer.eos_token_id,
    )
    if temperature > 0.0:
        gen_kwargs.update(do_sample=True, temperature=temperature, top_p=0.9)
    else:
        gen_kwargs["do_sample"] = False

    with torch.inference_mode():
        t0      = time.time()
        out_ids = model.generate(**inputs, **gen_kwargs)
        elapsed = time.time() - t0

    new_ids   = out_ids[0][in_len:]
    n_tokens  = len(new_ids)
    tps       = n_tokens / elapsed if elapsed > 0 else 0.0
    answer    = tokenizer.decode(new_ids, skip_special_tokens=True).strip()
    return answer, tps


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def run_interactive(model, tokenizer, max_new_tokens: int, temperature: float):
    print("=" * 60)
    print("  fp16 LoRA Model -- Interactive Mode")
    print("  Type your first aid question. Enter 'quit' to exit.")
    print("=" * 60)
    while True:
        try:
            question = input("\nQuestion: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[infer] Exiting.")
            break
        if not question or question.lower() in ("quit", "exit", "q"):
            print("[infer] Exiting.")
            break

        answer, tps = generate(model, tokenizer, question, max_new_tokens, temperature)
        print(f"\nAnswer: {answer}")
        print(f"        ({tps:.1f} tok/s)")


def run_single(model, tokenizer, question: str, max_new_tokens: int, temperature: float):
    print(f"Question: {question}\n")
    answer, tps = generate(model, tokenizer, question, max_new_tokens, temperature)
    print(f"Answer: {answer}")
    print(f"\n[infer] {tps:.1f} tok/s")


def run_batch(model, tokenizer, test_path: str, max_new_tokens: int, temperature: float):
    if not os.path.exists(test_path):
        raise FileNotFoundError(
            f"Test split not found: {test_path}\n"
            f"Run python data.py first to generate splits."
        )

    with open(test_path, encoding="utf-8") as f:
        test_samples = json.load(f)

    print(f"[infer] Running batch inference on {len(test_samples)} test samples...")
    print(f"[infer] Output -> {BATCH_OUTPUT}\n")

    results   = []
    total_tps = []

    for i, sample in enumerate(test_samples):
        q = sample["question"]
        a_ref = sample["answer"]

        answer, tps = generate(model, tokenizer, q, max_new_tokens, temperature)
        total_tps.append(tps)

        results.append({
            "question":          q,
            "reference_answer":  a_ref,
            "model_answer":      answer,
            "category":          sample.get("category", ""),
            "safety_critical":   sample.get("safety_critical", False),
            "tokens_per_sec":    round(tps, 2),
        })

        if (i + 1) % 50 == 0:
            print(f"  [{i+1:>4}/{len(test_samples)}]  avg {sum(total_tps)/len(total_tps):.1f} tok/s")

    with open(BATCH_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    avg_tps = sum(total_tps) / len(total_tps)
    sc_count = sum(1 for r in results if r["safety_critical"])

    print(f"\n[infer] Batch complete.")
    print(f"  Samples          : {len(results)}")
    print(f"  Safety-critical  : {sc_count}")
    print(f"  Avg tok/s        : {avg_tps:.1f}")
    print(f"  Saved to         : {BATCH_OUTPUT}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Inference with fp16 fine-tuned Gemma 2B",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--mode", default="interactive",
                   choices=["interactive", "batch", "single"],
                   help="interactive (default), batch (full test set), single")
    p.add_argument("--question", default="",
                   help="Question for --mode single")
    p.add_argument("--adapter_path", default=DEFAULT_ADAPTER,
                   help=f"Path to LoRA adapter dir (default: {DEFAULT_ADAPTER})")
    p.add_argument("--model_path", default="",
                   help="Local base model dir (default: auto-detect)")
    p.add_argument("--test_path", default=DEFAULT_TEST,
                   help=f"Test split for batch mode (default: {DEFAULT_TEST})")
    p.add_argument("--max_new_tokens", type=int, default=256)
    p.add_argument("--temperature", type=float, default=0.0,
                   help="0.0 = greedy (default, reproducible). >0 = sampling.")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()

    print("=" * 60)
    print("  Gemma 2B fp16 LoRA -- Inference")
    print("=" * 60)
    print(f"  Mode        : {args.mode}")
    print(f"  Adapter     : {args.adapter_path}")
    print(f"  Max tokens  : {args.max_new_tokens}")
    print(f"  Temperature : {args.temperature} ({'greedy' if args.temperature == 0 else 'sampling'})")
    print("=" * 60 + "\n")

    model, tokenizer = load_model(args.model_path, args.adapter_path)

    if args.mode == "interactive":
        run_interactive(model, tokenizer, args.max_new_tokens, args.temperature)

    elif args.mode == "single":
        if not args.question:
            args.question = "What is the correct ratio of chest compressions to rescue breaths in CPR?"
        run_single(model, tokenizer, args.question, args.max_new_tokens, args.temperature)

    elif args.mode == "batch":
        run_batch(model, tokenizer, args.test_path, args.max_new_tokens, args.temperature)
