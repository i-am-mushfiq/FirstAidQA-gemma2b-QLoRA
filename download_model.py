"""
download_model.py -- Download or verify Gemma 2B weights + tokenizer
=====================================================================
Two modes:

  AUTO DOWNLOAD (needs HF login + internet):
    python download_model.py

  VERIFY MANUAL DOWNLOAD (no login needed):
    python download_model.py --verify_only
    -- Use this after manually placing files from the HuggingFace website.

Manual download instructions
------------------------------------------------------------
1. Go to: https://huggingface.co/google/gemma-2b-it/tree/main
2. Download these files and place them ALL in one flat folder:
     config.json
     generation_config.json
     tokenizer.json
     tokenizer.model
     tokenizer_config.json
     special_tokens_map.json
     model-00001-of-00002.safetensors   (~4.9 GB)
     model-00002-of-00002.safetensors   (~67 MB)
     model.safetensors.index.json
3. Put them here:
     C:\\Personal_Endeavours\\Fine_Tuning\\models\\gemma-2b-it\\
4. Run:  python download_model.py --verify_only
------------------------------------------------------------

Auto-download usage:
  python download_model.py                          # saves to ./models/gemma-2b-it/
  python download_model.py --save_dir D:/models/gemma-2b-it
  python download_model.py --verify_only            # skip download, just check files
  python download_model.py --skip_verify            # skip post-download load test
"""

import argparse
import os
import sys
import time


REQUIRED_FILES = [
    "config.json",
    "generation_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "model.safetensors.index.json",
]

# At least one weight shard must be present
WEIGHT_GLOB = "model-*.safetensors"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sizeof_fmt(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0:
            return f"{num:3.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"


def get_disk_free_gb(path: str) -> float:
    import shutil
    total, used, free = shutil.disk_usage(path)
    return free / 1e9


def check_hf_login() -> bool:
    try:
        from huggingface_hub import whoami
        info = whoami()
        print(f"[auth] Logged in as: {info['name']}")
        return True
    except Exception:
        print("[auth] Not logged in to Hugging Face.")
        print("       Options:")
        print("       A) huggingface-cli login  (then re-run this script)")
        print("       B) python download_model.py --verify_only  (if files already placed manually)")
        return False


def check_manual_files(save_dir: str) -> tuple:
    """
    Check whether all required files are present in save_dir.
    Returns (all_present: bool, report: list of strings).
    """
    import glob
    report = []
    all_ok = True

    for fn in REQUIRED_FILES:
        fp = os.path.join(save_dir, fn)
        if os.path.exists(fp):
            report.append(f"  OK  {fn:<45} {sizeof_fmt(os.path.getsize(fp))}")
        else:
            report.append(f"  !!  {fn:<45} MISSING")
            all_ok = False

    # Weight shards
    shards = sorted(glob.glob(os.path.join(save_dir, WEIGHT_GLOB)))
    if shards:
        total_weight = sum(os.path.getsize(s) for s in shards)
        for s in shards:
            report.append(f"  OK  {os.path.basename(s):<45} {sizeof_fmt(os.path.getsize(s))}")
        report.append(f"       Total weights: {sizeof_fmt(total_weight)}")
    else:
        report.append(f"  !!  model-*.safetensors              MISSING (download weight shards!)")
        all_ok = False

    return all_ok, report


def print_manifest(save_dir: str):
    print(f"\n[verify] Files in {save_dir}:")
    total = 0
    for root, _, files in os.walk(save_dir):
        for fn in sorted(files):
            fp = os.path.join(root, fn)
            size = os.path.getsize(fp)
            total += size
            rel = os.path.relpath(fp, save_dir)
            print(f"  {sizeof_fmt(size):>10}   {rel}")
    print(f"  {'':->10}   --------")
    print(f"  {sizeof_fmt(total):>10}   TOTAL")


# ---------------------------------------------------------------------------
# Download (auto)
# ---------------------------------------------------------------------------

def download_model(model_id: str, save_dir: str, force: bool = False) -> str:
    from huggingface_hub import snapshot_download

    abs_dir = os.path.abspath(save_dir)
    config_path = os.path.join(abs_dir, "config.json")

    if os.path.exists(config_path) and not force:
        print(f"\n[download] Model already present at: {abs_dir}")
        print(f"           Use --force to re-download.")
        print_manifest(abs_dir)
        return abs_dir

    parent = os.path.dirname(abs_dir) or "."
    os.makedirs(parent, exist_ok=True)
    free_gb = get_disk_free_gb(parent)
    print(f"\n[download] Free disk  : {free_gb:.1f} GB")
    if free_gb < 6.0:
        print("[download] WARNING: Gemma 2B needs ~5 GB. You may run out of space.")

    print(f"\n[download] Model      : {model_id}")
    print(f"[download] Saving to  : {abs_dir}")
    print(f"[download] Downloading (this may take several minutes) ...\n")

    os.makedirs(abs_dir, exist_ok=True)
    t0 = time.time()
    snapshot_download(
        repo_id=model_id,
        local_dir=abs_dir,
        local_dir_use_symlinks=False,
        ignore_patterns=["*.msgpack", "flax_model*", "tf_model*", "rust_model*"],
    )
    elapsed = time.time() - t0
    print(f"\n[download] Complete in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print_manifest(abs_dir)
    return abs_dir


# ---------------------------------------------------------------------------
# Verify local load
# ---------------------------------------------------------------------------

def verify_local_load(save_dir: str, quant: str = "4bit"):
    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig

    print(f"\n[verify] Loading tokenizer from: {save_dir}")
    tok = AutoTokenizer.from_pretrained(save_dir, local_files_only=True)
    print(f"[verify] Tokenizer OK  |  vocab size: {tok.vocab_size:,}")

    bnb = None
    if quant == "4bit":
        bnb = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16,
        )
    elif quant == "8bit":
        bnb = BitsAndBytesConfig(load_in_8bit=True)

    print(f"[verify] Loading model ({quant}) from local path ...")
    model = AutoModelForCausalLM.from_pretrained(
        save_dir,
        quantization_config=bnb,
        device_map="auto",
        trust_remote_code=True,
        dtype=torch.float16,
        local_files_only=True,
    )
    model.eval()
    n = sum(p.numel() for p in model.parameters())
    print(f"[verify] Model OK      |  params: {n:,}")

    if torch.cuda.is_available():
        print(f"[verify] VRAM used     |  {torch.cuda.memory_allocated()/1e6:.0f} MB")

    inputs = tok("Hello", return_tensors="pt").to(model.device)
    with torch.inference_mode():
        out = model.generate(**inputs, max_new_tokens=5,
                             pad_token_id=tok.eos_token_id)
    decoded = tok.decode(out[0], skip_special_tokens=True)
    print(f"[verify] Forward pass  |  'Hello' -> '{decoded}'")
    print(f"\n[verify] All checks passed. Model is ready for offline use.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Download or verify Gemma 2B for offline fine-tuning",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--model_id", default="google/gemma-2b-it")
    p.add_argument(
        "--save_dir",
        default=os.path.join(os.path.dirname(__file__), "models", "gemma-2b-it"),
        help="Local directory for model files (default: ./models/gemma-2b-it)",
    )
    p.add_argument("--force", action="store_true",
                   help="Re-download even if files already exist")
    p.add_argument("--verify_only", action="store_true",
                   help="Skip download -- just check manually placed files and optionally load them")
    p.add_argument("--skip_verify", action="store_true",
                   help="Skip the post-download GPU load test")
    p.add_argument("--verify_quant", default="4bit", choices=["4bit", "8bit", "fp16"],
                   help="Quantization mode to use for the load verification")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    abs_dir = os.path.abspath(args.save_dir)

    print("=" * 60)
    print("  Gemma 2B -- Local Model Setup")
    print("=" * 60)

    # --verify_only: check manually placed files, no HF login needed
    if args.verify_only:
        print(f"\n[check] Verifying manually placed files in:\n        {abs_dir}\n")
        if not os.path.isdir(abs_dir):
            print(f"[check] Directory not found: {abs_dir}")
            print(f"[check] Create it and place the model files there first.")
            sys.exit(1)

        all_ok, report = check_manual_files(abs_dir)
        for line in report:
            print(line)

        if not all_ok:
            print("\n[check] Some files are missing. Re-check the list above.")
            print("[check] Download page: https://huggingface.co/google/gemma-2b-it/tree/main")
            sys.exit(1)

        print(f"\n[check] All required files found.")

        if not args.skip_verify:
            print("\n[verify] Running GPU load test ...")
            try:
                verify_local_load(abs_dir, quant=args.verify_quant)
            except Exception as e:
                print(f"[verify] WARNING: Load test failed: {e}")
                print("[verify] Files look complete -- check CUDA/bitsandbytes installation.")

    else:
        # Auto-download path
        if not check_hf_login():
            print("\nTip: if you have the files already, run with --verify_only instead.")
            sys.exit(1)

        save_dir = download_model(args.model_id, abs_dir, force=args.force)

        if not args.skip_verify:
            print("\n[verify] Running post-download load test ...")
            try:
                verify_local_load(save_dir, quant=args.verify_quant)
            except Exception as e:
                print(f"[verify] WARNING: {e}")

    print("\n" + "=" * 60)
    print(f"  Model path : {abs_dir}")
    print(f"  Use with   : --model_path \"{abs_dir}\"")
    print("=" * 60)
