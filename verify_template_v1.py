"""
verify_template_v1.py  --  Template alignment verification
===========================================================
VERSION: v1
PURPOSE: Pre-training diagnostic only. No GPU required.

Compares the manual Gemma chat template strings in data.py against the
official tokenizer.apply_chat_template() output, token-by-token.

What this catches:
  - Whitespace / newline differences in the turn markers
  - BOS double-counting: if apply_chat_template embeds <bos> in the string
    AND the tokenizer adds it again via add_special_tokens=True, you get a
    ghost BOS that inflates instruction_length by 1 and shifts the mask
    boundary by one token.
  - Any template version drift if the HF model card template ever changes.

Verdict meanings:
  PASS    -- manual template and apply_chat_template produce identical token
             sequences. Current data.py is correct. No action needed.
  MISMATCH -- sequences differ. Run train_v2.py which uses apply_chat_template
              natively instead of manual strings.

Usage (CPU only, ~30 seconds):
  python verify_template_v1.py
  python verify_template_v1.py --model_path ./models/gemma-2b-it
  python verify_template_v1.py --n 10      # check 10 train samples
"""

import argparse
import json
import os
import sys
import textwrap


# ---------------------------------------------------------------------------
# Resolve model source (mirrors train.py logic so no divergence)
# ---------------------------------------------------------------------------

DEFAULT_LOCAL_PATH = os.path.join(os.path.dirname(__file__), "models", "gemma-2b-it")
HF_MODEL_ID = "google/gemma-2b-it"


def resolve_model(model_path: str = ""):
    for candidate in [p for p in [model_path, DEFAULT_LOCAL_PATH] if p]:
        if os.path.isdir(candidate) and os.path.exists(
            os.path.join(candidate, "config.json")
        ):
            return os.path.abspath(candidate), True
    return HF_MODEL_ID, False


# ---------------------------------------------------------------------------
# Core comparison
# ---------------------------------------------------------------------------

def compare_tokenizations(tokenizer, current_ids: list, act_ids: list, label: str):
    """Return True if sequences match; print diff details if not."""
    if current_ids == act_ids:
        return True
    print(f"    *** {label} MISMATCH  (len: current={len(current_ids)}, template={len(act_ids)}) ***")
    min_len = min(len(current_ids), len(act_ids))
    for j in range(min_len):
        if current_ids[j] != act_ids[j]:
            tok_curr = repr(tokenizer.decode([current_ids[j]]))
            tok_act  = repr(tokenizer.decode([act_ids[j]]))
            print(f"    First diff at position {j}: "
                  f"current={current_ids[j]} {tok_curr}  vs  "
                  f"template={act_ids[j]} {tok_act}")
            # Show surrounding context
            lo, hi = max(0, j-2), min(min_len, j+3)
            print(f"    Current  [{lo}:{hi}] : {current_ids[lo:hi]}")
            print(f"    Template [{lo}:{hi}] : {act_ids[lo:hi]}")
            break
    if len(current_ids) != len(act_ids):
        print(f"    Length difference: {abs(len(current_ids) - len(act_ids))} token(s)")
        if len(current_ids) > len(act_ids):
            extra = current_ids[len(act_ids):]
            print(f"    Extra tokens in current : {extra} = "
                  f"{[repr(tokenizer.decode([t])) for t in extra]}")
        else:
            extra = act_ids[len(current_ids):]
            print(f"    Extra tokens in template: {extra} = "
                  f"{[repr(tokenizer.decode([t])) for t in extra]}")
    return False


def verify(model_path: str = "", n_samples: int = 5, splits_dir: str = ""):
    # --- Tokenizer ---
    from transformers import AutoTokenizer

    model_source, is_local = resolve_model(model_path)
    print(f"\n[verify] Loading tokenizer : {model_source}")
    print(f"[verify] Source            : {'local disk' if is_local else 'HuggingFace Hub'}")

    tokenizer = AutoTokenizer.from_pretrained(
        model_source,
        trust_remote_code=True,
        local_files_only=is_local,
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.unk_token
        tokenizer.pad_token_id = tokenizer.unk_token_id
    tokenizer.padding_side = "right"

    bos_id = tokenizer.bos_token_id
    print(f"[verify] BOS token id      : {bos_id}  ({repr(tokenizer.decode([bos_id]))})")
    print(f"[verify] Transformers ver  : ", end="")
    import transformers
    print(transformers.__version__)

    # --- Check chat_template attribute ---
    if hasattr(tokenizer, "chat_template") and tokenizer.chat_template:
        print("[verify] tokenizer.chat_template : PRESENT (apply_chat_template will use it)")
    else:
        print("[verify] tokenizer.chat_template : NOT FOUND -- template may be hardcoded in tokenizer class")

    # --- Import current data.py builders ---
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from data import SYSTEM_PROMPT, _build_instruction, _build_full_text, SPLITS_DIR, load_split

    # --- Load test samples ---
    sd = splits_dir or SPLITS_DIR
    try:
        samples = load_split("train", sd)
    except FileNotFoundError:
        print(f"[verify] WARNING: Could not load splits from {sd}. "
              "Using 3 built-in test cases only.")
        samples = []

    # Built-in test cases always run (cover edge cases)
    builtin_cases = [
        {
            "question": "What is the correct ratio of chest compressions to rescue breaths in CPR?",
            "answer": "30 chest compressions to 2 rescue breaths (30:2), at 100-120 "
                      "compressions per minute and at least 5 cm deep.",
            "template_idx": 0,
            "label": "builtin-SC-template0",
        },
        {
            "question": "A patient asks: how do I treat a minor burn at home?",
            "answer": "Cool the burn under running cool water for at least 10-20 minutes. "
                      "Do not use ice, butter, or toothpaste. Cover with a non-fluffy sterile dressing. "
                      "Seek medical advice if the burn is larger than a 50c coin.",
            "template_idx": 1,
            "label": "builtin-nonSC-template1",
        },
        {
            "question": "What should I do if someone is choking and cannot speak or breathe?",
            "answer": "Call emergency services immediately. Give up to 5 firm back blows between "
                      "the shoulder blades. If unsuccessful, perform up to 5 abdominal thrusts. "
                      "Alternate back blows and abdominal thrusts until the object is dislodged "
                      "or the person becomes unconscious. If unconscious, begin CPR.",
            "template_idx": 3,
            "label": "builtin-SC-template3",
        },
    ]

    # Add real samples from the split
    dataset_cases = []
    if samples:
        import random
        rng = random.Random(42)
        chosen = rng.sample(samples, min(n_samples, len(samples)))
        for i, s in enumerate(chosen):
            dataset_cases.append({
                "question": s["question"],
                "answer": s["answer"],
                "template_idx": s.get("template_idx", 0),
                "label": f"train-sample-{i+1}",
            })

    all_cases = builtin_cases + dataset_cases
    PREFIXES = ["Question: ", "A patient asks: ", "Emergency situation: ", ""]

    print(f"\n{'='*70}")
    print(f"  Template Alignment Check  ({len(all_cases)} cases)")
    print(f"{'='*70}\n")

    passes = 0
    fails = 0

    for case in all_cases:
        q   = case["question"]
        a   = case["answer"]
        tidx = case["template_idx"]
        lbl  = case["label"]

        # ----------------------------------------------------------------
        # A) Current data.py approach
        #    - String does NOT contain <bos>
        #    - Tokenizer adds BOS via add_special_tokens=True
        # ----------------------------------------------------------------
        current_instr_str = _build_instruction(q, tidx)
        current_full_str  = _build_full_text(q, a, tidx)

        current_instr_ids = tokenizer(
            current_instr_str,
            add_special_tokens=True,
            return_tensors=None,
        )["input_ids"]
        current_full_ids  = tokenizer(
            current_full_str,
            add_special_tokens=True,
            return_tensors=None,
        )["input_ids"]

        # ----------------------------------------------------------------
        # B) apply_chat_template approach (transformers >= 4.40)
        #    - String DOES contain <bos>
        #    - Must use add_special_tokens=False to avoid double BOS
        # ----------------------------------------------------------------
        prefix       = PREFIXES[tidx % 4]
        user_content = SYSTEM_PROMPT + "\n\n" + prefix + q

        instr_msgs = [{"role": "user", "content": user_content}]
        full_msgs  = [
            {"role": "user",  "content": user_content},
            {"role": "model", "content": a},
        ]

        act_instr_str = tokenizer.apply_chat_template(
            instr_msgs,
            tokenize=False,
            add_generation_prompt=True,
        )
        act_full_str = tokenizer.apply_chat_template(
            full_msgs,
            tokenize=False,
            add_generation_prompt=False,
        )

        # Tokenize with add_special_tokens=False (template string already has <bos>)
        act_instr_ids = tokenizer(
            act_instr_str,
            add_special_tokens=False,
            return_tensors=None,
        )["input_ids"]
        act_full_ids = tokenizer(
            act_full_str,
            add_special_tokens=False,
            return_tensors=None,
        )["input_ids"]

        # ----------------------------------------------------------------
        # BOS sanity checks
        # ----------------------------------------------------------------
        # Current: tokenizer adds BOS, string doesn't contain it
        current_str_has_bos  = "<bos>" in current_instr_str or "â" in current_instr_str
        # apply_chat_template: string should start with <bos>
        act_str_has_bos      = act_instr_str.startswith("<bos>") or (
            len(act_instr_ids) > 0 and act_instr_ids[0] == bos_id
        )
        double_bos_risk      = current_str_has_bos and current_instr_ids[0] == bos_id

        # ----------------------------------------------------------------
        # Compare
        # ----------------------------------------------------------------
        instr_ok = (current_instr_ids == act_instr_ids)
        full_ok  = (current_full_ids  == act_full_ids)
        case_ok  = instr_ok and full_ok

        status = "PASS ✓" if case_ok else "MISMATCH ✗"
        print(f"[{status}]  {lbl}  (template_idx={tidx})")
        print(f"  Q: {textwrap.shorten(q, 80)}")

        # Token counts
        print(f"  Instruction tokens  : current={len(current_instr_ids)}  "
              f"apply_chat={len(act_instr_ids)}")
        print(f"  Full text tokens    : current={len(current_full_ids)}  "
              f"apply_chat={len(act_full_ids)}")

        # BOS report
        print(f"  BOS in current seq  : {current_instr_ids[0] == bos_id}  "
              f"(via add_special_tokens=True)")
        print(f"  BOS in template seq : {act_instr_ids[0] == bos_id if act_instr_ids else 'N/A'}  "
              f"(embedded in string)")
        if double_bos_risk:
            print("  *** WARNING: double BOS risk detected in current approach ***")

        if not instr_ok:
            compare_tokenizations(tokenizer, current_instr_ids, act_instr_ids, "INSTRUCTION")
            print(f"  Current  instr text : {repr(current_instr_str[:120])}")
            print(f"  Template instr text : {repr(act_instr_str[:120])}")

        if not full_ok:
            compare_tokenizations(tokenizer, current_full_ids, act_full_ids, "FULL TEXT")

        # Masking boundary check: how many instruction tokens will be masked?
        mask_len_current  = len(current_instr_ids)
        mask_len_template = len(act_instr_ids)
        # Verify mask does not eat into answer
        answer_ids = tokenizer(a, add_special_tokens=False, return_tensors=None)["input_ids"]
        if full_ok:
            # Check that position mask_len_current in full_ids is first answer token
            first_ans_tok = answer_ids[0] if answer_ids else None
            if first_ans_tok and len(current_full_ids) > mask_len_current:
                actual_tok = current_full_ids[mask_len_current]
                if actual_tok != first_ans_tok:
                    print(f"  *** MASK BOUNDARY WARNING: token at mask_len={mask_len_current} "
                          f"is {actual_tok} ({repr(tokenizer.decode([actual_tok]))}), "
                          f"expected first answer token {first_ans_tok} "
                          f"({repr(tokenizer.decode([first_ans_tok]))}) ***")
                else:
                    print(f"  Mask boundary OK: position {mask_len_current} = "
                          f"{repr(tokenizer.decode([actual_tok]))} (first answer token ✓)")

        if case_ok:
            passes += 1
        else:
            fails += 1
        print()

    # --- Final verdict ---
    print("=" * 70)
    print(f"  Results: {passes} PASS  |  {fails} FAIL  (out of {len(all_cases)} cases)")
    print("=" * 70)

    if fails == 0:
        print("""
  VERDICT: ALL PASS
  ─────────────────
  The manual templates in data.py produce token-for-token identical output
  to tokenizer.apply_chat_template(). Current data.py is correct.

  You may still choose to run train_v2.py (uses apply_chat_template natively)
  for long-term robustness — it will produce identical training examples but
  will automatically adapt if the tokenizer template is ever updated.
""")
        return 0
    else:
        print(f"""
  VERDICT: {fails} MISMATCH(ES) DETECTED
  ──────────────────────────────────────
  The manual template in data.py diverges from tokenizer.apply_chat_template().
  This means some training examples are formatted differently from what the
  model expects, degrading gradient quality.

  ACTION REQUIRED: Use train_v2.py for all future training runs.
  train_v2.py imports from data_v2.py which uses apply_chat_template natively.
""")
        return 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="Verify data.py chat template matches tokenizer.apply_chat_template()",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--model_path", default="",
                   help="Local model dir (default: ./models/gemma-2b-it -> HF Hub)")
    p.add_argument("--n", type=int, default=5,
                   help="Number of real training samples to check (default 5)")
    p.add_argument("--splits_dir", default="",
                   help="Path to splits directory (default: data.SPLITS_DIR)")
    args = p.parse_args()

    print("=" * 70)
    print("  verify_template_v1.py  --  Template Alignment Diagnostic")
    print("  No GPU required. Uses tokenizer only.")
    print("=" * 70)

    sys.exit(verify(args.model_path, args.n, args.splits_dir))
