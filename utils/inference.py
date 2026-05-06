"""
inference.py  --  Before / after LoRA inference comparison for Gemma 2B
=======================================================================
Usage:
  # With a trained adapter
  python inference.py --adapter_path ./lora_adapters/gemma2b_4bit/adapter

  # Load model from local disk (after download_model.py)
  python inference.py --model_path ./models/gemma-2b-it \
                      --adapter_path ./lora_adapters/gemma2b_4bit/adapter

  # Base model only (no adapter)
  python inference.py --base_only

  # Custom question
  python inference.py --adapter_path ./lora_adapters/gemma2b_4bit/adapter \
                      --question "How do you treat a minor burn?"
"""

import argparse
import os
import time
from typing import Optional

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODEL_ID = "google/gemma-2b-it"
DEFAULT_LOCAL_PATH = os.path.join(os.path.dirname(__file__), "models", "gemma-2b-it")

DEFAULT_QUESTIONS = [
    "Can a human crutch be used for a person with a shoulder injury?",
    "What is the correct ratio of chest compressions to rescue breaths in CPR?",
    "How do you treat a minor burn at home?",
]


# ---------------------------------------------------------------------------
# Model loader helpers
# ---------------------------------------------------------------------------

def resolve_model_source(model_path: str = "", model_id: str = MODEL_ID):
    """Return (source, is_local). Prefer local disk if available."""
    for candidate in [p for p in [model_path, DEFAULT_LOCAL_PATH] if p]:
        if os.path.isdir(candidate) and os.path.exists(os.path.join(candidate, "config.json")):
            print(f"[inference] Using local model : {os.path.abspath(candidate)}")
            return os.path.abspath(candidate), True
    print(f"[inference] Using HF Hub : {model_id}  (run download_model.py to cache locally)")
    return model_id, False


def load_base_model(model_id: str, quant: str = "4bit", model_path: str = ""):
    """Load base model (no adapter) in the specified quantization."""
    source, is_local = resolve_model_source(model_path, model_id)
    print(f"[inference] Loading base model: {source}  ({quant})")
    bnb_config = None
    if quant == "4bit":
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16,
        )
    elif quant == "8bit":
        bnb_config = BitsAndBytesConfig(load_in_8bit=True)

    model = AutoModelForCausalLM.from_pretrained(
        source,
        quantization_config=bnb_config,
        device_map="auto",
        trust_remote_code=True,
        dtype=torch.float16,
        local_files_only=is_local,
    )
    model.eval()
    return model


def load_tokenizer(model_id: str, model_path: str = ""):
    source, is_local = resolve_model_source(model_path, model_id)
    tok = AutoTokenizer.from_pretrained(
        source, trust_remote_code=True, local_files_only=is_local
    )
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    tok.padding_side = "left"
    return tok


def load_adapter_model(base_model, adapter_path: str):
    """Merge LoRA adapter on top of an already-loaded base model."""
    print(f"[inference] Loading adapter from: {adapter_path}")
    model = PeftModel.from_pretrained(base_model, adapter_path)
    model.eval()
    return model


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def build_prompt(question: str) -> str:
    """Wrap a question in Gemma's chat template."""
    return (
        f"<start_of_turn>user\n"
        f"Question: {question}<end_of_turn>\n"
        f"<start_of_turn>model\n"
        f"Answer:"
    )


@torch.inference_mode()
def generate(
    model,
    tokenizer,
    question: str,
    max_new_tokens: int = 200,
    temperature: float = 0.3,
    top_p: float = 0.9,
):
    """Generate an answer and return (text, latency_seconds)."""
    prompt = build_prompt(question)
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)

    t0 = time.time()
    outputs = model.generate(
        **inputs,
        max_new_tokens=max_new_tokens,
        temperature=temperature,
        top_p=top_p,
        do_sample=True,
        pad_token_id=tokenizer.pad_token_id,
        eos_token_id=tokenizer.eos_token_id,
    )
    latency = time.time() - t0

    new_tokens = outputs[0][inputs["input_ids"].shape[-1]:]
    answer = tokenizer.decode(new_tokens, skip_special_tokens=True).strip()
    return answer, latency


# ---------------------------------------------------------------------------
# Comparison runner
# ---------------------------------------------------------------------------

def run_comparison(
    model_id: str,
    adapter_path: Optional[str],
    questions: list,
    quant: str = "4bit",
    model_path: str = "",
):
    """
    Load base model, run inference, then load adapter and run again.
    Prints a side-by-side before/after comparison.
    """
    tokenizer = load_tokenizer(model_id, model_path)

    # ---- BASE model ----
    print("\n" + "=" * 70)
    print("PHASE 1 -- BASE MODEL (no adapter)")
    print("=" * 70)
    base_model = load_base_model(model_id, quant, model_path)
    base_answers = []
    for q in questions:
        ans, lat = generate(base_model, tokenizer, q)
        base_answers.append((ans, lat))
        print(f"\nQ: {q}")
        print(f"A (base): {ans}")
        print(f"   Latency: {lat:.2f}s")

    # ---- FINE-TUNED model ----
    if adapter_path:
        print("\n" + "=" * 70)
        print("PHASE 2 -- FINE-TUNED MODEL (with LoRA adapter)")
        print("=" * 70)
        ft_model = load_adapter_model(base_model, adapter_path)
        ft_answers = []
        for q in questions:
            ans, lat = generate(ft_model, tokenizer, q)
            ft_answers.append((ans, lat))
            print(f"\nQ: {q}")
            print(f"A (fine-tuned): {ans}")
            print(f"   Latency: {lat:.2f}s")

        # ---- Side-by-side summary ----
        print("\n" + "=" * 70)
        print("COMPARISON SUMMARY")
        print("=" * 70)
        for i, q in enumerate(questions):
            print(f"\nQ{i+1}: {q}")
            print(f"  [Base]       {base_answers[i][0][:120]}...")
            print(f"  [Fine-tuned] {ft_answers[i][0][:120]}...")
    else:
        print("\n[inference] No adapter_path provided -- skipping fine-tuned comparison.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Before/after LoRA inference comparison for Gemma 2B",
    )
    p.add_argument("--model_id", default=MODEL_ID,
                   help="HuggingFace model ID (default: google/gemma-2b-it)")
    p.add_argument("--model_path", default="",
                   help="Local model directory (skips HF download if present)")
    p.add_argument("--adapter_path", default="",
                   help="Path to saved LoRA adapter directory")
    p.add_argument("--quant", default="4bit", choices=["4bit", "8bit", "fp16"],
                   help="Quantization mode (default: 4bit)")
    p.add_argument("--base_only", action="store_true",
                   help="Run base model only, skip fine-tuned comparison")
    p.add_argument("--question", default="",
                   help="Single custom question (default: runs 3 built-in questions)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()

    questions = [args.question] if args.question else DEFAULT_QUESTIONS
    adapter_path = None if args.base_only else (args.adapter_path or None)

    print("=" * 70)
    print("  Gemma 2B -- Inference Comparison")
    print("=" * 70)
    print(f"  Quant       : {args.quant}")
    print(f"  Model path  : {args.model_path or DEFAULT_LOCAL_PATH}")
    print(f"  Adapter     : {adapter_path or '(none -- base model only)'}")
    print(f"  Questions   : {len(questions)}")
    print("=" * 70)

    run_comparison(
        model_id=args.model_id,
        adapter_path=adapter_path,
        questions=questions,
        quant=args.quant,
        model_path=args.model_path,
    )
