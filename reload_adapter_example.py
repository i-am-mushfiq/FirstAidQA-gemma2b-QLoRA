"""
reload_adapter_example.py  —  Minimal snippet: reload saved adapter for inference
==================================================================================
Run this after training to verify the saved adapter loads and generates correctly.

Usage:
  python reload_adapter_example.py --adapter_path ./lora_adapters/gemma2b_4bit/adapter
"""

import argparse
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import PeftModel

def reload_and_generate(adapter_path: str, question: str, quant: str = "4bit"):
    """
    Reload a saved LoRA adapter and generate an answer.
    The tokenizer is loaded from the adapter_path (it was saved there by train.py).
    """
    print(f"[reload] Loading tokenizer from: {adapter_path}")
    tokenizer = AutoTokenizer.from_pretrained(adapter_path)

    print(f"[reload] Loading base model with {quant} quantization...")
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

    # Load base model (model_id embedded in adapter config)
    import json, os
    config_path = os.path.join(adapter_path, "adapter_config.json")
    with open(config_path) as f:
        adapter_cfg = json.load(f)
    base_model_id = adapter_cfg["base_model_name_or_path"]
    print(f"[reload] Base model: {base_model_id}")

    base_model = AutoModelForCausalLM.from_pretrained(
        base_model_id,
        quantization_config=bnb_config,
        device_map="auto",
        trust_remote_code=True,
        dtype=torch.float16,
    )

    print("[reload] Attaching LoRA adapter...")
    model = PeftModel.from_pretrained(base_model, adapter_path)
    model.eval()

    # Generate
    prompt = (
        f"<start_of_turn>user\n"
        f"Question: {question}<end_of_turn>\n"
        f"<start_of_turn>model\n"
        f"Answer:"
    )
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    with torch.inference_mode():
        outputs = model.generate(
            **inputs,
            max_new_tokens=200,
            do_sample=False,
            pad_token_id=tokenizer.pad_token_id,
        )
    new_tokens = outputs[0][inputs["input_ids"].shape[-1]:]
    answer = tokenizer.decode(new_tokens, skip_special_tokens=True).strip()

    print(f"\n[reload] Q: {question}")
    print(f"[reload] A: {answer}")
    return answer


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--adapter_path", required=True)
    p.add_argument("--quant", default="4bit", choices=["4bit", "8bit", "fp16"])
    p.add_argument("--question",
                   default="Can a human crutch be used for a person with a shoulder injury?")
    args = p.parse_args()
    reload_and_generate(args.adapter_path, args.question, args.quant)
