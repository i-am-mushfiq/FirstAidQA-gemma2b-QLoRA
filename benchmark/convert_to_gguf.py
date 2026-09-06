"""
convert_to_gguf.py — Convert HuggingFace models to GGUF and quantize.

This script:
1. Clones llama.cpp (for the convert_hf_to_gguf.py script)
2. Downloads pre-built Windows binaries (for llama-quantize.exe)
3. Converts each model (base + 2 merged) -> F16 GGUF
4. Quantizes each F16 GGUF -> Q4_K_M and Q8_0
5. Optionally cleans up intermediate F16 GGUFs

Usage:
    python benchmark/convert_to_gguf.py
    python benchmark/convert_to_gguf.py --skip-clone       # if llama.cpp already cloned
    python benchmark/convert_to_gguf.py --no-cleanup        # keep F16 GGUF files
    python benchmark/convert_to_gguf.py --model base        # only convert base model
    python benchmark/convert_to_gguf.py --model merged_4bit # only convert 4-bit merged
"""

import argparse
import gc
import urllib.request
import os
import platform
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from urllib.request import urlretrieve

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # ML4H/
BENCHMARK_DIR = PROJECT_ROOT / "benchmark"
MODELS_DIR = BENCHMARK_DIR / "models"
LLAMA_CPP_DIR = BENCHMARK_DIR / "llama.cpp"
LLAMA_CPP_BIN_DIR = BENCHMARK_DIR / "llama_cpp_bin"

# Models to convert
MODEL_SOURCES = {
    "base": PROJECT_ROOT / "gemma-2b-it" / "gemma-2b-it",
    "merged_4bit": MODELS_DIR / "merged_4bit",
    "merged_8bit": MODELS_DIR / "merged_8bit",
}

# Quantization targets
QUANT_TYPES = ["Q4_K_M", "Q8_0"]

# llama.cpp release URL for pre-built Windows binaries (CPU-only)
LLAMA_CPP_RELEASE_URL = (
    "https://github.com/ggml-org/llama.cpp/releases/latest/download/"
    "llama-bin-win-cpu-x64.zip"
)


def run_cmd(cmd: list[str], cwd: str | Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command and print it."""
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(
        [str(c) for c in cmd],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        # Print last 20 lines to avoid flooding the console
        lines = result.stdout.strip().split("\n")
        if len(lines) > 20:
            print(f"  ... ({len(lines)-20} lines omitted)")
        for line in lines[-20:]:
            print(f"    {line}")
    if result.returncode != 0 and check:
        print(f"  ERROR (exit code {result.returncode}):")
        if result.stderr.strip():
            for line in result.stderr.strip().split("\n")[-20:]:
                print(f"    {line}")
        sys.exit(1)
    return result


def clone_llama_cpp() -> None:
    """Clone llama.cpp repo (we only need the Python conversion scripts)."""
    if LLAMA_CPP_DIR.exists() and (LLAMA_CPP_DIR / "convert_hf_to_gguf.py").exists():
        print("  [OK] llama.cpp already cloned, skipping.")
        return

    print("  [clone] Cloning llama.cpp (shallow)...")
    if LLAMA_CPP_DIR.exists():
        shutil.rmtree(str(LLAMA_CPP_DIR))

    run_cmd([
        "git", "clone", "--depth=1",
        "https://github.com/ggml-org/llama.cpp.git",
        str(LLAMA_CPP_DIR),
    ])

    # Install Python requirements for the conversion script
    requirements_file = LLAMA_CPP_DIR / "requirements.txt"
    if requirements_file.exists():
        print("  [clone] Installing llama.cpp Python requirements...")
        run_cmd([sys.executable, "-m", "pip", "install", "-r", str(requirements_file)])
    
    # Also install gguf package specifically
    print("  [clone] Installing gguf package...")
    run_cmd([sys.executable, "-m", "pip", "install", "gguf"])

    print("  [OK] llama.cpp cloned and dependencies installed.")


def download_prebuilt_binaries() -> Path:
    """Download pre-built llama.cpp Windows binaries. Returns path to llama-quantize.exe."""
    quantize_exe = LLAMA_CPP_BIN_DIR / "llama-quantize.exe"

    if quantize_exe.exists():
        print(f"  [OK] llama-quantize.exe already exists at {quantize_exe}")
        return quantize_exe

    LLAMA_CPP_BIN_DIR.mkdir(parents=True, exist_ok=True)
    
    # Fetch the latest releases
    print("  [download] Fetching release metadata...")
    import json
    
    url = "https://api.github.com/repos/ggml-org/llama.cpp/releases"
    try:
        response = urllib.request.urlopen(url)
        releases = json.loads(response.read().decode('utf-8'))
        
        # Find the first release with assets
        download_url = None
        for release in releases:
            for asset in release.get("assets", []):
                if asset["name"].startswith("llama-") and asset["name"].endswith("-bin-win-cpu-x64.zip"):
                    download_url = asset["browser_download_url"]
                    break
            if download_url:
                break
                
        if not download_url:
            raise Exception("Could not find win-cpu-x64.zip asset in recent releases")
            
    except Exception as e:
        print(f"  ERROR: Failed to fetch release metadata: {e}")
        sys.exit(1)

    print(f"  [download] Downloading pre-built binaries...")
    print(f"    URL: {download_url}")
    zip_path = LLAMA_CPP_BIN_DIR / "llama-bin.zip"
    
    try:
        urllib.request.urlretrieve(download_url, str(zip_path))
    except Exception as e:
        print(f"  ERROR: Failed to download: {e}")
        print(f"  Please manually download from: https://github.com/ggml-org/llama.cpp/releases")
        print(f"  Extract llama-quantize.exe to: {LLAMA_CPP_BIN_DIR}")
        sys.exit(1)

    print(f"  [download] Extracting...")
    with zipfile.ZipFile(str(zip_path), 'r') as z:
        # Find and extract just the executables we need
        for member in z.namelist():
            basename = os.path.basename(member)
            if basename in ("llama-quantize.exe", "llama-cli.exe", "llama-bench.exe"):
                # Extract to flat directory
                target = LLAMA_CPP_BIN_DIR / basename
                with z.open(member) as src, open(str(target), 'wb') as dst:
                    dst.write(src.read())
                print(f"    Extracted: {basename}")
        
        # Also extract any DLLs that might be needed
        for member in z.namelist():
            basename = os.path.basename(member)
            if basename.endswith(".dll"):
                target = LLAMA_CPP_BIN_DIR / basename
                if not target.exists():
                    with z.open(member) as src, open(str(target), 'wb') as dst:
                        dst.write(src.read())

    # Clean up zip
    zip_path.unlink()

    if not quantize_exe.exists():
        print(f"  ERROR: llama-quantize.exe not found after extraction.")
        print(f"  Contents of {LLAMA_CPP_BIN_DIR}:")
        for f in LLAMA_CPP_BIN_DIR.iterdir():
            print(f"    {f.name}")
        print(f"\n  The zip structure may have changed. Please manually extract")
        print(f"  llama-quantize.exe to: {LLAMA_CPP_BIN_DIR}")
        sys.exit(1)

    print(f"  [OK] Pre-built binaries ready at {LLAMA_CPP_BIN_DIR}")
    return quantize_exe


def convert_hf_to_gguf(model_name: str, model_path: Path) -> Path:
    """Convert a HuggingFace model to F16 GGUF. Returns the output path."""
    output_file = MODELS_DIR / f"{model_name}_f16.gguf"

    if output_file.exists():
        size_gb = output_file.stat().st_size / (1024**3)
        print(f"  [OK] {output_file.name} already exists ({size_gb:.2f} GB), skipping.")
        return output_file

    if not model_path.exists():
        print(f"  ERROR: Model directory not found: {model_path}")
        print(f"  Have you run merge_and_convert.py first?")
        sys.exit(1)

    convert_script = LLAMA_CPP_DIR / "convert_hf_to_gguf.py"
    if not convert_script.exists():
        print(f"  ERROR: convert_hf_to_gguf.py not found at {convert_script}")
        sys.exit(1)

    print(f"  [convert] {model_name}: HuggingFace -> F16 GGUF...")
    run_cmd([
        sys.executable,
        str(convert_script),
        str(model_path),
        "--outfile", str(output_file),
        "--outtype", "f16",
    ])

    if output_file.exists():
        size_gb = output_file.stat().st_size / (1024**3)
        print(f"  [OK] Created {output_file.name} ({size_gb:.2f} GB)")
    else:
        print(f"  ERROR: Expected output not found: {output_file}")
        sys.exit(1)

    return output_file


def quantize_gguf(f16_path: Path, model_name: str, quant_type: str, quantize_exe: Path) -> Path:
    """Quantize an F16 GGUF to a specific quantization type."""
    output_file = MODELS_DIR / f"{model_name}_{quant_type}.gguf"

    if output_file.exists():
        size_mb = output_file.stat().st_size / (1024**2)
        print(f"  [OK] {output_file.name} already exists ({size_mb:.0f} MB), skipping.")
        return output_file

    print(f"  [quantize] {model_name}: F16 -> {quant_type}...")
    run_cmd([
        str(quantize_exe),
        str(f16_path),
        str(output_file),
        quant_type,
    ])

    if output_file.exists():
        size_mb = output_file.stat().st_size / (1024**2)
        print(f"  [OK] Created {output_file.name} ({size_mb:.0f} MB)")
    else:
        print(f"  ERROR: Expected output not found: {output_file}")
        sys.exit(1)

    return output_file


def main():
    parser = argparse.ArgumentParser(description="Convert HF models to GGUF and quantize")
    parser.add_argument("--skip-clone", action="store_true", help="Skip cloning llama.cpp")
    parser.add_argument("--no-cleanup", action="store_true", help="Keep intermediate F16 GGUF files")
    parser.add_argument(
        "--model",
        choices=["base", "merged_4bit", "merged_8bit", "all"],
        default="all",
        help="Which model to convert (default: all)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  HandsOnAid — GGUF Conversion & Quantization")
    print(f"  Output dir: {MODELS_DIR}")
    print("=" * 60)

    # Step 1: Clone llama.cpp for the conversion script
    if not args.skip_clone:
        print("\n--- Step 1: Clone llama.cpp ---")
        clone_llama_cpp()

    # Step 2: Download pre-built binaries for quantization
    print("\n--- Step 2: Download pre-built llama.cpp binaries ---")
    quantize_exe = download_prebuilt_binaries()

    # Step 3: Convert and quantize each model
    models_to_process = (
        MODEL_SOURCES.items() if args.model == "all"
        else [(args.model, MODEL_SOURCES[args.model])]
    )

    f16_files = []

    for model_name, model_path in models_to_process:
        print(f"\n--- Step 3: Convert {model_name} ---")

        # Convert HF -> F16 GGUF
        f16_path = convert_hf_to_gguf(model_name, model_path)
        f16_files.append(f16_path)

        # Quantize F16 -> Q4_K_M and Q8_0
        for qtype in QUANT_TYPES:
            quantize_gguf(f16_path, model_name, qtype, quantize_exe)

    # Step 4: Cleanup intermediate F16 files (they're large)
    if not args.no_cleanup:
        print(f"\n--- Step 4: Cleanup intermediate F16 files ---")
        for f16_path in f16_files:
            if f16_path.exists():
                size_gb = f16_path.stat().st_size / (1024**3)
                f16_path.unlink()
                print(f"  [DEL]  Deleted {f16_path.name} ({size_gb:.2f} GB)")
        print(f"  [OK] Cleanup complete.")
    else:
        print(f"\n--- Step 4: Skipping cleanup (--no-cleanup) ---")

    # Summary
    print(f"\n{'='*60}")
    print(f"  Conversion complete! Final GGUF files:")
    print(f"{'='*60}")

    total_size = 0
    for f in sorted(MODELS_DIR.glob("*.gguf")):
        size_mb = f.stat().st_size / (1024**2)
        total_size += size_mb
        print(f"  {f.name:40s}  {size_mb:8.1f} MB")

    print(f"  {'':40s}  {'─'*12}")
    print(f"  {'Total':40s}  {total_size:8.1f} MB")
    print(f"\n  Next step: Run Docker benchmarks with run_benchmark.sh")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
