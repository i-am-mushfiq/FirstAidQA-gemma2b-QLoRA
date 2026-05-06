"""
verify_masking.py  --  Sanity-check label masking in tokenized training examples
=================================================================================
Loads the train split, tokenizes 10 examples, then decodes only the unmasked
label tokens (those != -100) and prints them alongside the expected answer.

Expected: decoded labels should exactly match the answer text (possibly with
a trailing <end_of_turn>/EOS token appended by the tokenizer).

Usage:
  python verify_masking.py
  python verify_masking.py --n 20 --splits_dir splits_10cat/
"""

import argparse
import textwrap

from transformers import AutoTokenizer

from data import SPLITS_DIR, build_hf_dataset, load_split, tokenize_dataset

DEFAULT_MODEL = "google/gemma-2b-it"


def verify(n: int, splits_dir: str, model_id: str):
    print(f"[verify] Loading tokenizer : {model_id}")
    tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.unk_token
        tokenizer.pad_token_id = tokenizer.unk_token_id
    tokenizer.padding_side = "right"

    print(f"[verify] Loading train split from : {splits_dir}")
    samples = load_split("train", splits_dir)
    hf = build_hf_dataset(samples[:n])
    ds = tokenize_dataset(hf, tokenizer, max_length=320)

    print(f"\n{'='*70}")
    print(f"  Checking {min(n, len(ds))} tokenized examples")
    print(f"{'='*70}\n")

    all_ok = True
    for i, ex in enumerate(ds):
        labels = ex["labels"]
        answer_text = samples[i]["answer"]

        # Decode only non-masked positions
        unmasked_ids = [t for t in labels if t != -100]
        decoded = tokenizer.decode(unmasked_ids, skip_special_tokens=False).strip()

        # Strip trailing special tokens for comparison
        decoded_clean = tokenizer.decode(unmasked_ids, skip_special_tokens=True).strip()

        ok = decoded_clean == answer_text.strip()
        status = "✓ OK" if ok else "✗ MISMATCH"
        if not ok:
            all_ok = False

        print(f"--- Example {i+1} [{status}]")
        print(f"  Expected : {textwrap.shorten(answer_text.strip(), 120)}")
        print(f"  Decoded  : {textwrap.shorten(decoded_clean, 120)}")
        print(f"  Raw decoded (with specials): {textwrap.shorten(decoded, 120)}")
        masked_count = labels.count(-100) if isinstance(labels, list) else (labels == -100).sum().item()
        total_count = len(labels) if isinstance(labels, list) else labels.shape[0]
        print(f"  Masked tokens (instruction): {masked_count} / {total_count} total")
        print()

    print("="*70)
    if all_ok:
        print("  RESULT: All examples pass — label masking is correct.")
    else:
        print("  RESULT: *** MISMATCHES DETECTED — review tokenize_dataset() ***")
    print("="*70)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=10, help="Number of examples to check")
    p.add_argument("--splits_dir", default=SPLITS_DIR)
    p.add_argument("--model_id", default=DEFAULT_MODEL)
    args = p.parse_args()
    verify(args.n, args.splits_dir, args.model_id)
