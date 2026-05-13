"""
train.py  --  Gemma 2B LoRA / QLoRA training pipeline
======================================================
Supports three quantization modes:
  - "4bit"   -> QLoRA  (BitsAndBytes NF4, most memory-efficient)
  - "8bit"   -> LoRA   (BitsAndBytes INT8)
  - "fp16"   -> LoRA   (native float16, highest precision)

Prerequisites:
  Run `python data.py` first to generate splits/ before training.

Usage:
  python train.py --quant 4bit
  python train.py --quant 4bit --seed 123
  python train.py --quant 4bit --model_path ./models/gemma-2b-it
"""

import argparse
import json
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import numpy as np
import torch
from peft import (
    LoraConfig,
    TaskType,
    get_peft_model,
    prepare_model_for_kbit_training,
)
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    DataCollatorForSeq2Seq,
    EarlyStoppingCallback,
    Trainer,
    TrainingArguments,
    set_seed,
)

from data import build_hf_dataset, load_split, tokenize_dataset

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODEL_ID = "google/gemma-2b-it"
DEFAULT_LOCAL_PATH = os.path.join(os.path.dirname(__file__), "models", "gemma-2b-it")
OUTPUT_BASE = os.path.join(os.path.dirname(__file__), "experiments")


def resolve_model_id(model_path: str = "", model_id: str = MODEL_ID):
    """
    Return (model_source, is_local).
    Priority: explicit --model_path -> ./models/gemma-2b-it -> HF Hub
    """
    for candidate in [p for p in [model_path, DEFAULT_LOCAL_PATH] if p]:
        if os.path.isdir(candidate) and os.path.exists(
            os.path.join(candidate, "config.json")
        ):
            print(f"[model] Using local model : {os.path.abspath(candidate)}")
            return os.path.abspath(candidate), True
    print(f"[model] Local model not found -- using HF Hub : {model_id}")
    print(f"        (Run download_model.py to cache locally)")
    return model_id, False


@dataclass
class TrainConfig:
    model_id: str = MODEL_ID
    model_path: str = ""
    quant: Literal["4bit", "8bit", "fp16"] = "4bit"
    output_dir: str = ""
    splits_dir: str = ""          # default resolved at runtime to data.SPLITS_DIR
    splits_tag: str = ""          # short label used in output dir name (e.g. "10cat")
    seed: int = 42
    max_length: int = 320           # covers 99th percentile of dataset (max ~230 tokens)
    # LoRA hyperparams
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    target_modules: list = field(
        default_factory=lambda: [
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ]
    )
    # Trainer hyperparams
    num_train_epochs: int = 10      # early stopping will cut this short
    per_device_train_batch_size: int = 2
    gradient_accumulation_steps: int = 4
    learning_rate: float = 2e-4
    warmup_ratio: float = 0.03
    lr_scheduler_type: str = "cosine"
    weight_decay: float = 0.01
    max_grad_norm: float = 1.0
    fp16: bool = True
    bf16: bool = False
    logging_steps: int = 10
    eval_steps: int = None          # None = evaluate once per epoch
    save_total_limit: int = 2
    early_stopping_patience: int = 2


# ---------------------------------------------------------------------------
# Seed control
# ---------------------------------------------------------------------------

def set_all_seeds(seed: int):
    """Set seeds across all libraries for reproducible training."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    set_seed(seed)  # HuggingFace transformers
    print(f"[train] Seeds set to {seed}")


# ---------------------------------------------------------------------------
# BitsAndBytes config
# ---------------------------------------------------------------------------

def get_bnb_config(quant: str):
    if quant == "4bit":
        return BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16,
        )
    elif quant == "8bit":
        return BitsAndBytesConfig(load_in_8bit=True)
    return None


# ---------------------------------------------------------------------------
# Model + tokenizer loader
# ---------------------------------------------------------------------------

def load_model_and_tokenizer(cfg: TrainConfig):
    model_source, is_local = resolve_model_id(cfg.model_path, cfg.model_id)
    print(f"\n[train] Loading model  : {model_source}")
    print(f"[train] Quantization   : {cfg.quant}")
    print(f"[train] Source         : {'local disk' if is_local else 'HuggingFace Hub'}")

    bnb_config = get_bnb_config(cfg.quant)

    tokenizer = AutoTokenizer.from_pretrained(
        model_source,
        trust_remote_code=True,
        local_files_only=is_local,
    )
    if tokenizer.pad_token is None:
        # Use unk_token rather than eos_token so padding and end-of-sequence
        # are distinct tokens. The attention mask protects padding from affecting
        # gradients, but keeping pad != eos is correct hygiene and avoids any
        # edge-case decoding ambiguity.
        tokenizer.pad_token = tokenizer.unk_token
        tokenizer.pad_token_id = tokenizer.unk_token_id
    tokenizer.padding_side = "right"  # required for causal LM training

    model = AutoModelForCausalLM.from_pretrained(
        model_source,
        quantization_config=bnb_config,
        device_map="auto",
        trust_remote_code=True,
        dtype=torch.float16,
        local_files_only=is_local,
    )

    if cfg.quant in ("4bit", "8bit"):
        model = prepare_model_for_kbit_training(model)

    lora_cfg = LoraConfig(
        r=cfg.lora_r,
        lora_alpha=cfg.lora_alpha,
        lora_dropout=cfg.lora_dropout,
        bias="none",
        task_type=TaskType.CAUSAL_LM,
        target_modules=cfg.target_modules,
    )
    model = get_peft_model(model, lora_cfg)
    # Disable KV-cache: incompatible with gradient checkpointing during training
    model.config.use_cache = False

    trainable, total = model.get_nb_trainable_parameters()
    print(
        f"[train] Trainable params : {trainable:,} "
        f"({100 * trainable / total:.2f}% of {total:,})"
    )
    return model, tokenizer


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train(cfg: TrainConfig):
    start = time.time()

    # --- Seeds ---
    set_all_seeds(cfg.seed)

    # --- Output dir ---
    if not cfg.output_dir:
        # Convention: <split>_<quant>_r<r>_lr<lr>_p<patience>_<YYYYMMDD>_<HHMMSS>
        from data import SPLITS_DIR as DEFAULT_SPLITS_DIR
        split_tag = cfg.splits_tag or os.path.basename(
            cfg.splits_dir or DEFAULT_SPLITS_DIR
        )
        lr_tag = f"lr{cfg.learning_rate:.0e}".replace("e-0", "e-").replace("e+0", "e")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = f"{split_tag}_{cfg.quant}_r{cfg.lora_r}_{lr_tag}_p{cfg.early_stopping_patience}_{ts}"
        cfg.output_dir = os.path.join(OUTPUT_BASE, folder)
    os.makedirs(cfg.output_dir, exist_ok=True)
    print(f"[train] Output dir     : {cfg.output_dir}")

    # --- Load splits from disk ---
    splits_dir = cfg.splits_dir or DEFAULT_SPLITS_DIR
    print(f"\n[train] Loading splits from  : {splits_dir}")
    train_samples = load_split("train", splits_dir)
    val_samples   = load_split("val",   splits_dir)
    print(f"[train] Train samples  : {len(train_samples):,}")
    print(f"[train] Val samples    : {len(val_samples):,}")

    # --- Model + tokenizer ---
    model, tokenizer = load_model_and_tokenizer(cfg)

    # --- Build and tokenize datasets ---
    print("\n[train] Tokenizing datasets...")
    train_hf = build_hf_dataset(train_samples)
    val_hf   = build_hf_dataset(val_samples)
    train_dataset = tokenize_dataset(train_hf, tokenizer, cfg.max_length)
    eval_dataset  = tokenize_dataset(val_hf,   tokenizer, cfg.max_length)
    print(f"[train] Tokenized -- train: {len(train_dataset)}, val: {len(eval_dataset)}")

    # --- TrainingArguments ---
    use_fp16 = cfg.fp16 and cfg.quant != "fp16"
    training_args = TrainingArguments(
        output_dir=cfg.output_dir,
        num_train_epochs=cfg.num_train_epochs,
        per_device_train_batch_size=cfg.per_device_train_batch_size,
        gradient_accumulation_steps=cfg.gradient_accumulation_steps,
        learning_rate=cfg.learning_rate,
        warmup_ratio=cfg.warmup_ratio,
        lr_scheduler_type=cfg.lr_scheduler_type,
        weight_decay=cfg.weight_decay,
        max_grad_norm=cfg.max_grad_norm,
        fp16=use_fp16,
        bf16=cfg.bf16,
        logging_steps=cfg.logging_steps,
        # Evaluation and checkpoint saving every 200 steps (finer-grained early stopping)
        eval_strategy="steps",
        eval_steps=200,
        save_strategy="steps",
        save_steps=200,
        # Load the best checkpoint (lowest val loss) at end of training
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        save_total_limit=cfg.save_total_limit,
        report_to="none",
        dataloader_num_workers=0,
        optim="paged_adamw_8bit" if cfg.quant in ("4bit", "8bit") else "adamw_torch",
        gradient_checkpointing=True,
        remove_unused_columns=False,
        seed=cfg.seed,
    )

    # --- Trainer ---
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        data_collator=DataCollatorForSeq2Seq(
            tokenizer,
            model=model,
            label_pad_token_id=-100,
            pad_to_multiple_of=8,
        ),
        callbacks=[
            EarlyStoppingCallback(
                early_stopping_patience=cfg.early_stopping_patience
            )
        ],
    )

    print(f"\n[train] Starting training  (quant={cfg.quant}, seed={cfg.seed}) ...")
    trainer.train()

    elapsed = time.time() - start
    print(f"[train] Training complete in {elapsed:.1f}s ({elapsed/60:.1f} min)")

    # --- Save adapter + tokenizer ---
    adapter_path = os.path.join(cfg.output_dir, "adapter")
    model.save_pretrained(adapter_path)
    tokenizer.save_pretrained(adapter_path)
    print(f"[train] LoRA adapter saved  -> {adapter_path}")

    # --- VRAM summary ---
    if torch.cuda.is_available():
        peak_mb = torch.cuda.max_memory_allocated() / 1e6
        print(f"[train] Peak VRAM used     : {peak_mb:.0f} MB")

    # --- Write run log ---
    _write_run_log(cfg, trainer, adapter_path, elapsed)

    return adapter_path


# ---------------------------------------------------------------------------
# Run logging
# ---------------------------------------------------------------------------

def _write_run_log(cfg: TrainConfig, trainer, adapter_path: str, elapsed: float):
    """Write training_curve.json inside the experiment folder for reproducibility."""
    log_path = os.path.join(cfg.output_dir, "training_curve.json")

    # Extract epoch-level losses from trainer history
    epoch_log = []
    for entry in trainer.state.log_history:
        if "epoch" in entry:
            epoch_log.append({
                "epoch": entry.get("epoch"),
                "train_loss": entry.get("loss"),
                "val_loss": entry.get("eval_loss"),
            })

    log = {
        "timestamp": datetime.now().isoformat(),
        "model_id": cfg.model_id,
        "model_path": cfg.model_path,
        "quant": cfg.quant,
        "seed": cfg.seed,
        "adapter_path": adapter_path,
        "training_time_s": round(elapsed, 1),
        "hyperparams": {
            "lora_r": cfg.lora_r,
            "lora_alpha": cfg.lora_alpha,
            "lora_dropout": cfg.lora_dropout,
            "max_length": cfg.max_length,
            "num_train_epochs_max": cfg.num_train_epochs,
            "per_device_train_batch_size": cfg.per_device_train_batch_size,
            "gradient_accumulation_steps": cfg.gradient_accumulation_steps,
            "learning_rate": cfg.learning_rate,
            "weight_decay": cfg.weight_decay,
            "max_grad_norm": cfg.max_grad_norm,
            "warmup_ratio": cfg.warmup_ratio,
            "lr_scheduler_type": cfg.lr_scheduler_type,
            "early_stopping_patience": cfg.early_stopping_patience,
        },
        "epoch_losses": epoch_log,
        "best_val_loss": trainer.state.best_metric,
        "stopped_epoch": trainer.state.epoch,
    }

    with open(log_path, "w") as f:
        json.dump(log, f, indent=2)
    print(f"[train] Training curve saved -> {log_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> TrainConfig:
    p = argparse.ArgumentParser(
        description="Fine-tune Gemma 2B (or any HF model) with LoRA/QLoRA",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--quant", default="4bit", choices=["4bit", "8bit", "fp16"])
    p.add_argument("--model_id", default=MODEL_ID)
    p.add_argument("--model_path", default="",
                   help="Local model directory (offline, skips HF download)")
    p.add_argument("--output_dir", default="")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--epochs", type=int, default=10,
                   help="Max epochs (early stopping will cut short, default 10)")
    p.add_argument("--batch_size", type=int, default=2)
    p.add_argument("--lr", type=float, default=2e-4)
    p.add_argument("--max_length", type=int, default=320)
    p.add_argument("--lora_r", type=int, default=16)
    p.add_argument("--lora_alpha", type=int, default=32)
    p.add_argument("--lora_dropout", type=float, default=0.05)
    p.add_argument("--patience", type=int, default=2,
                   help="Early stopping patience (eval checkpoints, default 2)")
    p.add_argument("--splits_dir", default="",
                   help="Path to splits directory (default: splits/10cat)")
    p.add_argument("--splits_tag", default="",
                   help="Short label for output dir name (e.g. '10cat'). "
                        "Inferred from splits_dir basename if omitted.")
    p.add_argument("--weight_decay", type=float, default=0.01,
                   help="AdamW weight decay (default 0.01)")
    p.add_argument("--target_modules", default="",
                   help="Comma-separated LoRA target modules. "
                        "Default: all 7 projection layers. "
                        "Use 'q_proj,k_proj,v_proj,o_proj' for attention-only.")
    p.add_argument("--max_grad_norm", type=float, default=1.0,
                   help="Gradient clipping max norm (default 1.0). "
                        "Both previous runs clipped 100%% of steps at mean norm ~3.7.")
    p.add_argument("--lr_scheduler", default="cosine",
                   choices=["cosine", "linear", "constant", "constant_with_warmup"],
                   help="LR scheduler type (default cosine)")
    p.add_argument("--warmup_ratio", type=float, default=0.03,
                   help="Fraction of steps used for LR warmup (default 0.03)")
    p.add_argument("--grad_accum", type=int, default=4,
                   help="Gradient accumulation steps. "
                        "Effective batch = batch_size * grad_accum (default 4 -> eff. batch 8)")
    args = p.parse_args()

    target_modules = (
        [m.strip() for m in args.target_modules.split(",") if m.strip()]
        if args.target_modules
        else ["q_proj", "k_proj", "v_proj", "o_proj",
              "gate_proj", "up_proj", "down_proj"]
    )

    return TrainConfig(
        model_id=args.model_id,
        model_path=args.model_path,
        quant=args.quant,
        output_dir=args.output_dir,
        splits_dir=args.splits_dir,
        splits_tag=args.splits_tag,
        seed=args.seed,
        max_length=args.max_length,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        warmup_ratio=args.warmup_ratio,
        lr_scheduler_type=args.lr_scheduler,
        lora_r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        early_stopping_patience=args.patience,
        weight_decay=args.weight_decay,
        max_grad_norm=args.max_grad_norm,
        target_modules=target_modules,
    )


if __name__ == "__main__":
    cfg = parse_args()
    print("=" * 60)
    print("  Gemma 2B -- LoRA/QLoRA Training")
    print("=" * 60)
    print(f"  Quant mode    : {cfg.quant}")
    print(f"  Model         : {cfg.model_path or cfg.model_id}")
    print(f"  Seed          : {cfg.seed}")
    print(f"  Max epochs    : {cfg.num_train_epochs} (early stop patience={cfg.early_stopping_patience})")
    print(f"  Max length    : {cfg.max_length}")
    print(f"  LR            : {cfg.learning_rate}")
    print(f"  LoRA r/alpha  : {cfg.lora_r}/{cfg.lora_alpha}  dropout={cfg.lora_dropout}")
    print(f"  Weight decay  : {cfg.weight_decay}   max_grad_norm={cfg.max_grad_norm}")
    print(f"  Scheduler     : {cfg.lr_scheduler_type}   warmup={cfg.warmup_ratio}")
    print(f"  Eff. batch    : {cfg.per_device_train_batch_size * cfg.gradient_accumulation_steps} "
          f"(batch={cfg.per_device_train_batch_size} x accum={cfg.gradient_accumulation_steps})")
    print(f"  Target modules: {', '.join(cfg.target_modules)}")
    print("=" * 60)

    adapter_path = train(cfg)

    print("\n" + "=" * 60)
    print(f"  Done! Adapter saved to: {adapter_path}")
    print(f"  Run inference with:")
    print(f"    python inference.py --adapter_path {adapter_path}")
    print("=" * 60)
