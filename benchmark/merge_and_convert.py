"""
merge_and_convert.py — Merge LoRA adapters into base Gemma 2B-it model.

Produces standalone HuggingFace model directories ready for GGUF conversion.
Processes one adapter at a time to stay within 16 GB system RAM.

Usage:
    python benchmark/merge_and_convert.py
    python benchmark/merge_and_convert.py --adapter 4bit   # merge only the 4-bit adapter
    python benchmark/merge_and_convert.py --adapter 8bit   # merge only the 8-bit adapter
"""

import argparse
import gc
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths — all relative to the project root (ML4H/)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # ML4H/

BASE_MODEL_DIR = PROJECT_ROOT / "gemma-2b-it" / "gemma-2b-it"

ADAPTERS = {
    "4bit": PROJECT_ROOT / "10cat_4bit_r16_lr1e-4_p3_v2_20260508_054337"
                         / "10cat_4bit_r16_lr1e-4_p3_v2_20260508_054337"
                         / "adapter",
    "8bit": PROJECT_ROOT / "10cat_8bit_r16_lr1e-4_p3_20260508_195536"
                         / "10cat_8bit_r16_lr1e-4_p3_20260508_195536"
                         / "adapter",
}

OUTPUT_DIR = PROJECT_ROOT / "benchmark" / "models"


def merge_one(adapter_name: str, adapter_path: Path, output_path: Path) -> None:
    """Load base model + adapter, merge, save, then free all memory."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import PeftModel

    print(f"\n{'='*60}")
    print(f"  Merging adapter: {adapter_name}")
    print(f"  Base model:  {BASE_MODEL_DIR}")
    print(f"  Adapter:     {adapter_path}")
    print(f"  Output:      {output_path}")
    print(f"{'='*60}\n")

    if output_path.exists() and (output_path / "config.json").exists():
        print(f"  [OK] Output already exists, skipping. Delete the folder to re-merge.")
        return

    t0 = time.time()

    # Load base model in float16 on CPU (keeps VRAM free, uses ~5 GB RAM)
    print("  [1/5] Loading base model (float16, CPU)...")
    base_model = AutoModelForCausalLM.from_pretrained(
        str(BASE_MODEL_DIR),
        dtype=torch.float16,
        device_map="cpu",       # Force CPU — we only have 4 GB VRAM
        low_cpu_mem_usage=True,  # Load weights shard-by-shard
    )

    # Load LoRA adapter on top
    print("  [2/5] Loading LoRA adapter...")
    model = PeftModel.from_pretrained(
        base_model,
        str(adapter_path),
        device_map="cpu",
    )

    # Merge adapter weights into the base model
    print("  [3/5] Merging adapter into base model...")
    merged = model.merge_and_unload()

    # Save the merged model
    print("  [4/5] Saving merged model...")
    output_path.mkdir(parents=True, exist_ok=True)
    merged.save_pretrained(str(output_path), safe_serialization=True)

    # Save tokenizer from base model
    print("  [5/5] Saving tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(str(BASE_MODEL_DIR))
    tokenizer.save_pretrained(str(output_path))

    elapsed = time.time() - t0
    print(f"\n  [OK] Done in {elapsed:.1f}s - saved to {output_path}")

    # Aggressively free memory before the next merge
    del merged, model, base_model, tokenizer
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    print(f"  [OK] Memory freed.\n")


def main():
    parser = argparse.ArgumentParser(description="Merge LoRA adapters into base model")
    parser.add_argument(
        "--adapter",
        choices=["4bit", "8bit", "all"],
        default="all",
        help="Which adapter to merge (default: all)",
    )
    args = parser.parse_args()

    # Validate paths
    if not BASE_MODEL_DIR.exists():
        print(f"ERROR: Base model not found at {BASE_MODEL_DIR}")
        sys.exit(1)

    adapters_to_process = (
        ADAPTERS.items() if args.adapter == "all"
        else [(args.adapter, ADAPTERS[args.adapter])]
    )

    for name, path in adapters_to_process:
        if not path.exists():
            print(f"ERROR: Adapter not found at {path}")
            sys.exit(1)

    print("=" * 60)
    print("  HandsOnAid — LoRA Adapter Merge Script")
    print(f"  Project root: {PROJECT_ROOT}")
    print(f"  Output dir:   {OUTPUT_DIR}")
    print("=" * 60)

    for name, path in adapters_to_process:
        out = OUTPUT_DIR / f"merged_{name}"
        merge_one(name, path, out)

    print("\n" + "=" * 60)
    print("  All merges complete!")
    print(f"  Merged models are in: {OUTPUT_DIR}")
    print("  Next step: convert to GGUF with convert_to_gguf.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
