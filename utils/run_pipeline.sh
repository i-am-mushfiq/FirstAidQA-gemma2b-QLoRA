#!/usr/bin/env bash
# run_pipeline.sh  —  End-to-end Gemma 2B LoRA/QLoRA pipeline launcher
# ======================================================================
# Prerequisites:
#   1. pip install -r requirements.txt
#   2. python download_model.py          ← downloads Gemma 2B to ./models/gemma-2b-it
#   3. (First time only) huggingface-cli login   ← only needed for download step
#
# Usage:
#   bash run_pipeline.sh            # 4-bit QLoRA (default)
#   bash run_pipeline.sh 8bit       # 8-bit LoRA
#   bash run_pipeline.sh fp16       # full fp16
#   bash run_pipeline.sh all        # run all three and compare

set -e

QUANT="${1:-4bit}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCAL_MODEL_DIR="$SCRIPT_DIR/models/gemma-2b-it"

echo "============================================================"
echo "  Gemma 2B LoRA/QLoRA Pipeline  —  mode: $QUANT"
echo "============================================================"

# ── Step 0: Check / download model ─────────────────────────────
if [ -f "$LOCAL_MODEL_DIR/config.json" ]; then
    echo "[model] ✓  Local model found at: $LOCAL_MODEL_DIR"
    MODEL_PATH_ARG="--model_path $LOCAL_MODEL_DIR"
else
    echo "[model] Local model not found. Running download_model.py ..."
    python3 "$SCRIPT_DIR/download_model.py" --save_dir "$LOCAL_MODEL_DIR"
    MODEL_PATH_ARG="--model_path $LOCAL_MODEL_DIR"
fi

# ── Step 1: Data sanity check ──────────────────────────────────
echo ""
echo "[data] Running data.py sanity check..."
python3 "$SCRIPT_DIR/data.py"

# ── Step 2: Train ──────────────────────────────────────────────
if [ "$QUANT" = "all" ]; then
    for mode in 4bit 8bit fp16; do
        echo ""
        echo "[train] Starting mode: $mode"
        python3 "$SCRIPT_DIR/train.py" --quant "$mode" $MODEL_PATH_ARG
    done
else
    echo ""
    echo "[train] Starting mode: $QUANT"
    python3 "$SCRIPT_DIR/train.py" --quant "$QUANT" $MODEL_PATH_ARG
fi

# ── Step 3: Inference comparison ──────────────────────────────
echo ""
echo "[inference] Running before/after comparison..."
python3 "$SCRIPT_DIR/inference.py" \
    --adapter_path "$SCRIPT_DIR/lora_adapters/gemma2b_${QUANT}/adapter" \
    --quant "$QUANT" \
    $MODEL_PATH_ARG

# ── Step 4: Quantization benchmark (if all modes were trained) ─
if [ "$QUANT" = "all" ]; then
    echo ""
    echo "[compare] Running quantization benchmark..."
    python3 "$SCRIPT_DIR/compare_quant.py" \
        --modes 4bit 8bit fp16 \
        --adapter_base "$SCRIPT_DIR/lora_adapters" \
        $MODEL_PATH_ARG
fi

echo ""
echo "============================================================"
echo "  Pipeline complete."
echo "  Local model  : $LOCAL_MODEL_DIR"
echo "  Adapters     : $SCRIPT_DIR/lora_adapters/"
echo "============================================================"
