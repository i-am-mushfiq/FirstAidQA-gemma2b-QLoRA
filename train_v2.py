"""
train_v2.py  --  Gemma 2B LoRA / QLoRA training pipeline
=========================================================
VERSION: v2  (original: train.py)

CHANGES FROM train.py
─────────────────────
1. TEMPLATE ALIGNMENT (Fix #1)
   Imports build_hf_dataset_v2 and tokenize_dataset_v2 from data_v2.py.
   These use tokenizer.apply_chat_template() instead of manual strings,
   guaranteeing structural correctness against the model's tokenizer config.

   Tokenizer is now loaded BEFORE dataset construction and passed into
   build_hf_dataset_v2(), which requires it to call apply_chat_template.

2. BOS HANDLING (Fix #1 sub-fix, handled in data_v2.py)
   tokenize_dataset_v2() uses add_special_tokens=False throughout,
   preventing double-BOS injection from apply_chat_template strings.

3. MAX LENGTH: 320 -> 512 (future-proofing)
   Sequence length audit (May 2026): max across all splits is ~314 estimated
   tokens, so the old 320 covered 100% of data. Setting 512 adds a 63% buffer
   for any future dataset growth without meaningful VRAM cost at 2B scale.

4. WHAT IS NOT CHANGED (confirmed correct by audit)
   - LoRA target modules: already includes all 7 projections (q/k/v/o + FFN)
     gate_proj, up_proj, down_proj were already in train.py defaults.
   - Label masking: data.py's tokenize_dataset() correctly masks instruction
     tokens. data_v2.tokenize_dataset_v2() preserves this logic.
   - All hyperparameters: lr, r, alpha, dropout, scheduler, grad_norm unchanged.
   - DataCollatorForSeq2Seq, EarlyStoppingCallback, TrainingArguments: unchanged.
   - _write_run_log, parse_args, CLI output: unchanged.

RUN SEQUENCE:
  Step 1 (recommended): python verify_template_v1.py
  Step 2 (verify masking): python verify_masking.py
  Step 3 (train):  python train_v2.py --quant 4bit
  Step 4 (train):  python train_v2.py --quant 8bit
  Step 5 (train):  python train_v2.py --quant fp16

Usage:
  python train_v2.py --quant 4bit
  python train_v2.py --quant 8bit  --lr 1e-4 --lora_r 16 --lora_alpha 32
  python train_v2.py --quant fp16  --lr 1e-4 --lora_r 16 --lora_alpha 32
  python train_v2.py --quant 4bit  --seed 123 --model_path ./models/gemma-2b-it
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

# v2: import from data_v2 (apply_chat_template edition)
from data_v2 import build_hf_dataset_v2, load_split, tokenize_dataset_v2

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODEL_ID = "google/gemma-2b-it"
DEFAULT_LOCAL_PATH = os.path.join(os.path.dirname(__file__), "models", "gemma-2b-it")
OUTPUT_BASE = os.path.join(os.path.dirname(__file__), "experiments")


def resolve_model_id(model_path: str = "", model_id: str = MODEL_ID):
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
    splits_dir: str = ""
    splits_tag: str = ""
    seed: int = 42
    # v2: bumped 320 -> 512 for future-proofing
    # Audit (May 2026): dataset max ~314 estimated tokens; 512 gives 63% buffer
    max_length: int = 512
    # LoRA hyperparams -- unchanged from train.py
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    # All 7 projections (attention + FFN) -- was already correct in train.py
    target_modules: list = field(
        default_factory=lambda: [
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ]
    )
    # Trainer hyperparams -- unchanged from train.py
    num_train_epochs: int = 10
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
    eval_steps: int = None
    save_total_limit: int = 2
    early_stopping_patience: int = 2


# ---------------------------------------------------------------------------
# Seed control  (verbatim from train.py)
# ---------------------------------------------------------------------------

def set_all_seeds(seed: int):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    set_seed(seed)
    print(f"[train_v2] Seeds set to {seed}")


# ---------------------------------------------------------------------------
# BitsAndBytes config  (verbatim from train.py)
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
# Model + tokenizer loader  (CHANGED: tokenizer loaded here and returned)
# ---------------------------------------------------------------------------

def load_model_and_tokenizer(cfg: TrainConfig):
    """
    Load model and tokenizer.

    v2 change: tokenizer is returned alongside the model so it can be passed
    to build_hf_dataset_v2() for apply_chat_template formatting. In train.py,
    build_hf_dataset() did not require the tokenizer.
    """
    model_source, is_local = resolve_model_id(cfg.model_path, cfg.model_id)
    print(f"\n[train_v2] Loading model  : {model_source}")
    print(f"[train_v2] Quantization   : {cfg.quant}")
    print(f"[train_v2] Source         : {'local disk' if is_local else 'HuggingFace Hub'}")
    print(f"[train_v2] Template method: apply_chat_template (data_v2)")

    bnb_config = get_bnb_config(cfg.quant)

    tokenizer = AutoTokenizer.from_pretrained(
        model_source,
        trust_remote_code=True,
        local_files_only=is_local,
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.unk_token
        tokenizer.pad_token_id = tokenizer.unk_token_id
    tokenizer.padding_side = "right"

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
    model.config.use_cache = False

    trainable, total = model.get_nb_trainable_parameters()
    print(
        f"[train_v2] Trainable params : {trainable:,} "
        f"({100 * trainable / total:.2f}% of {total:,})"
    )
    return model, tokenizer


# ---------------------------------------------------------------------------
# Training  (CHANGED: tokenizer passed to build_hf_dataset_v2)
# ---------------------------------------------------------------------------

def train(cfg: TrainConfig):
    start = time.time()

    set_all_seeds(cfg.seed)

    # --- Output dir ---
    if not cfg.output_dir:
        from data_v2 import SPLITS_DIR as DEFAULT_SPLITS_DIR
        split_tag = cfg.splits_tag or os.path.basename(
            cfg.splits_dir or DEFAULT_SPLITS_DIR
        )
        lr_tag = f"lr{cfg.learning_rate:.0e}".replace("e-0", "e-").replace("e+0", "e")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = (f"{split_tag}_{cfg.quant}_r{cfg.lora_r}_{lr_tag}"
                  f"_p{cfg.early_stopping_patience}_v2_{ts}")
        cfg.output_dir = os.path.join(OUTPUT_BASE, folder)
    os.makedirs(cfg.output_dir, exist_ok=True)
    print(f"[train_v2] Output dir     : {cfg.output_dir}")

    # --- Load splits ---
    from data_v2 import SPLITS_DIR as DEFAULT_SPLITS_DIR
    splits_dir = cfg.splits_dir or DEFAULT_SPLITS_DIR
    print(f"\n[train_v2] Loading splits from  : {splits_dir}")
    train_samples = load_split("train", splits_dir)
    val_samples   = load_split("val",   splits_dir)
    print(f"[train_v2] Train samples  : {len(train_samples):,}")
    print(f"[train_v2] Val samples    : {len(val_samples):,}")

    # --- Model + tokenizer ---
    # v2: tokenizer returned here for use in dataset construction
    model, tokenizer = load_model_and_tokenizer(cfg)

    # --- Build and tokenize datasets  (v2: pass tokenizer) ---
    print("\n[train_v2] Building datasets with apply_chat_template...")
    train_hf = build_hf_dataset_v2(train_samples, tokenizer)
    val_hf   = build_hf_dataset_v2(val_samples,   tokenizer)

    print("[train_v2] Tokenizing datasets...")
    train_dataset = tokenize_dataset_v2(train_hf, tokenizer, cfg.max_length)
    eval_dataset  = tokenize_dataset_v2(val_hf,   tokenizer, cfg.max_length)
    print(f"[train_v2] Tokenized -- train: {len(train_dataset)}, val: {len(eval_dataset)}")

    # Sequence length report
    if hasattr(train_dataset, "features"):
        sample_ids = train_dataset[0]["input_ids"]
        non_pad = sum(1 for t in sample_ids if t != tokenizer.pad_token_id)
        print(f"[train_v2] Sample[0] non-pad tokens : {non_pad} / {len(sample_ids)} "
              f"(max_length={cfg.max_length})")

    # --- TrainingArguments  (verbatim from train.py) ---
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
        eval_strategy="steps",
        eval_steps=200,
        save_strategy="steps",
        save_steps=200,
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

    # --- Trainer  (verbatim from train.py) ---
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

    print(f"\n[train_v2] Starting training  (quant={cfg.quant}, seed={cfg.seed}) ...")
    trainer.train()

    elapsed = time.time() - start
    print(f"[train_v2] Training complete in {elapsed:.1f}s ({elapsed/60:.1f} min)")

    # --- Save adapter + tokenizer ---
    adapter_path = os.path.join(cfg.output_dir, "adapter")
    model.save_pretrained(adapter_path)
    tokenizer.save_pretrained(adapter_path)
    print(f"[train_v2] LoRA adapter saved  -> {adapter_path}")

    # --- VRAM summary ---
    if torch.cuda.is_available():
        peak_mb = torch.cuda.max_memory_allocated() / 1e6
        print(f"[train_v2] Peak VRAM used     : {peak_mb:.0f} MB")

    _write_run_log(cfg, trainer, adapter_path, elapsed)

    return adapter_path


# ---------------------------------------------------------------------------
# Run logging  (verbatim from train.py, version tag added)
# ---------------------------------------------------------------------------

def _write_run_log(cfg: TrainConfig, trainer, adapter_path: str, elapsed: float):
    log_path = os.path.join(cfg.output_dir, "training_curve.json")

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
        "script_version": "train_v2",
        "template_method": "apply_chat_template (data_v2)",
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
            "target_modules": cfg.target_modules,
        },
        "epoch_losses": epoch_log,
        "best_val_loss": trainer.state.best_metric,
        "stopped_epoch": trainer.state.epoch,
    }

    with open(log_path, "w") as f:
        json.dump(log, f, indent=2)
    print(f"[train_v2] Training curve saved -> {log_path}")


# ---------------------------------------------------------------------------
# CLI  (verbatim from train.py)
# ---------------------------------------------------------------------------

def parse_args() -> TrainConfig:
    p = argparse.ArgumentParser(
        description="train_v2: Fine-tune Gemma 2B with LoRA/QLoRA (apply_chat_template edition)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--quant", default="4bit", choices=["4bit", "8bit", "fp16"])
    p.add_argument("--model_id", default=MODEL_ID)
    p.add_argument("--model_path", default="")
    p.add_argument("--output_dir", default="")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--epochs", type=int, default=10)
    p.add_argument("--batch_size", type=int, default=2)
    p.add_argument("--lr", type=float, default=2e-4)
    p.add_argument("--max_length", type=int, default=512,
                   help="Max token length per example. Default 512 (audit: dataset max ~314).")
    p.add_argument("--lora_r", type=int, default=16)
    p.add_argument("--lora_alpha", type=int, default=32)
    p.add_argument("--lora_dropout", type=float, default=0.05)
    p.add_argument("--patience", type=int, default=2)
    p.add_argument("--splits_dir", default="")
    p.add_argument("--splits_tag", default="")
    p.add_argument("--weight_decay", type=float, default=0.01)
    p.add_argument("--target_modules", default="",
                   help="Comma-separated LoRA target modules. "
                        "Default: all 7 projections (attn + FFN).")
    p.add_argument("--max_grad_norm", type=float, default=1.0)
    p.add_argument("--lr_scheduler", default="cosine",
                   choices=["cosine", "linear", "constant", "constant_with_warmup"])
    p.add_argument("--warmup_ratio", type=float, default=0.03)
    p.add_argument("--grad_accum", type=int, default=4)
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
    print("=" * 65)
    print("  Gemma 2B -- LoRA/QLoRA Training  [train_v2 / data_v2]")
    print("=" * 65)
    print(f"  Quant mode    : {cfg.quant}")
    print(f"  Model         : {cfg.model_path or cfg.model_id}")
    print(f"  Seed          : {cfg.seed}")
    print(f"  Max epochs    : {cfg.num_train_epochs} (early stop patience={cfg.early_stopping_patience})")
    print(f"  Max length    : {cfg.max_length}  [v2: 320->512, dataset max ~314 tokens]")
    print(f"  LR            : {cfg.learning_rate}")
    print(f"  LoRA r/alpha  : {cfg.lora_r}/{cfg.lora_alpha}  dropout={cfg.lora_dropout}")
    print(f"  Weight decay  : {cfg.weight_decay}   max_grad_norm={cfg.max_grad_norm}")
    print(f"  Scheduler     : {cfg.lr_scheduler_type}   warmup={cfg.warmup_ratio}")
    print(f"  Eff. batch    : {cfg.per_device_train_batch_size * cfg.gradient_accumulation_steps} "
          f"(batch={cfg.per_device_train_batch_size} x accum={cfg.gradient_accumulation_steps})")
    print(f"  Target modules: {', '.join(cfg.target_modules)}")
    print(f"  Template      : apply_chat_template (data_v2.py)")
    print("=" * 65)

    adapter_path = train(cfg)

    print("\n" + "=" * 65)
    print(f"  Done! Adapter saved to: {adapter_path}")
    print(f"  Run inference with:")
    print(f"    python inference.py --adapter_path {adapter_path}")
    print("=" * 65)
