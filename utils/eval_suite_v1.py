"""
eval_suite.py -- Persistent multi-model evaluation suite
=========================================================
Runs a fixed bank of 20 first-aid questions through every available model
variant (base, 4-bit, 8-bit, fp16) and saves results to a timestamped JSON
file under eval_results/.

Each run produces a NEW file -- results are never overwritten.
Designed for downstream evaluation with ROUGE-L, BERTScore, and LLM-judge.

Usage:
  python eval_suite.py
  python eval_suite.py --models base 4bit 8bit fp16   (all, default)
  python eval_suite.py --models 4bit fp16              (subset)
  python eval_suite.py --model_path ./models/gemma-2b-it
  python eval_suite.py --max_new_tokens 200
"""

import argparse
import gc
import json
import os
import time
from datetime import datetime

import torch
from peft import PeftModel
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

HERE        = os.path.dirname(__file__)
MODEL_ID    = "google/gemma-2b-it"
LOCAL_MODEL = os.path.join(HERE, "models", "gemma-2b-it")
RESULTS_DIR = os.path.join(HERE, "eval_results")

ADAPTER_PATHS = {
    "4bit": os.path.join(HERE, "lora_adapters", "gemma2b_4bit",  "adapter"),
    "8bit": os.path.join(HERE, "lora_adapters", "gemma2b_8bit",  "adapter"),
    "fp16": os.path.join(HERE, "lora_adapters", "gemma2b_fp16",  "adapter"),
    "4bit_old": os.path.join(HERE, "lora_adapters", "gemma2b_4bit",  "adapter"),
    "4bit_new": os.path.join(HERE, "lora_adapters", "google_gemma-2b-it_4bit", "adapter"),
}

SYSTEM_PROMPT = (
    "You are a first aid assistant. Provide accurate, step-by-step emergency "
    "guidance. For life-threatening situations, always advise calling emergency "
    "services immediately."
)

# ---------------------------------------------------------------------------
# Question bank  (20 questions, reference answers included)
# Categories mirror the 10-cat scheme for consistency.
# safety_critical=True marks AHA time-sensitive emergencies.
# ---------------------------------------------------------------------------

QUESTIONS = [
    # --- Cardiac & Resuscitation ---
    {
        "id": 1,
        "category": "Cardiac & Resuscitation",
        "safety_critical": True,
        "question": "What is the correct ratio of chest compressions to rescue breaths in CPR for an adult, and how deep should compressions be?",
        "reference": (
            "30 chest compressions to 2 rescue breaths (30:2). Compressions should "
            "be at least 5 cm (2 inches) deep at a rate of 100-120 per minute. "
            "Allow full chest recoil between compressions."
        ),
    },
    {
        "id": 2,
        "category": "Cardiac & Resuscitation",
        "safety_critical": True,
        "question": "A person collapses, is unresponsive, and not breathing normally. What are the first three steps you take?",
        "reference": (
            "1. Call emergency services (000 in Australia / 911 in US) immediately or "
            "send someone to call. 2. Begin CPR: 30 chest compressions followed by "
            "2 rescue breaths. 3. Use an AED as soon as one is available."
        ),
    },
    {
        "id": 3,
        "category": "Cardiac & Resuscitation",
        "safety_critical": True,
        "question": "What are the signs of a heart attack and what should you do immediately?",
        "reference": (
            "Signs: chest pain or pressure, pain radiating to arm or jaw, sweating, "
            "nausea, shortness of breath, pale or grey skin. Actions: call emergency "
            "services, have the person rest in a comfortable position, give aspirin "
            "300 mg if not allergic, be prepared to start CPR if they lose consciousness."
        ),
    },
    # --- Airway, Choking & Drowning ---
    {
        "id": 4,
        "category": "Airway, Choking & Drowning",
        "safety_critical": True,
        "question": "An adult is choking and cannot speak, cry, or breathe. What do you do?",
        "reference": (
            "Give up to 5 firm back blows between the shoulder blades with the heel "
            "of your hand. If unsuccessful, give up to 5 abdominal thrusts (Heimlich "
            "manoeuvre): stand behind the person, place a fist above the navel, and "
            "thrust inward and upward. Alternate back blows and abdominal thrusts "
            "until the object is cleared or the person loses consciousness. Call 000."
        ),
    },
    {
        "id": 5,
        "category": "Airway, Choking & Drowning",
        "safety_critical": True,
        "question": "You rescue someone from drowning. They are unconscious and not breathing. What do you do?",
        "reference": (
            "Call emergency services immediately. Give 5 initial rescue breaths before "
            "starting CPR. Then perform 30 compressions to 2 rescue breaths. Continue "
            "until the person starts breathing or help arrives. Place in recovery "
            "position once breathing resumes."
        ),
    },
    # --- Bleeding & Wounds ---
    {
        "id": 6,
        "category": "Bleeding & Wounds",
        "safety_critical": True,
        "question": "How do you control severe arterial bleeding from a limb when blood is spurting and direct pressure is not enough?",
        "reference": (
            "Apply a tourniquet 5-8 cm above the wound (not over a joint). Tighten "
            "until bleeding stops. Record the time of application. Do not remove the "
            "tourniquet. Treat for shock and call emergency services immediately. "
            "Pack the wound with clean dressings in addition to the tourniquet."
        ),
    },
    {
        "id": 7,
        "category": "Bleeding & Wounds",
        "safety_critical": False,
        "question": "What is the correct way to clean and dress a minor laceration?",
        "reference": (
            "Apply direct pressure with a clean cloth to stop bleeding. Clean the "
            "wound under running water for at least 5 minutes. Remove visible debris "
            "gently. Apply antiseptic if available. Cover with a sterile non-adherent "
            "dressing and secure with a bandage. Seek medical advice if wound is deep, "
            "gaping, or contaminated."
        ),
    },
    # --- Trauma & Musculoskeletal ---
    {
        "id": 8,
        "category": "Trauma & Musculoskeletal",
        "safety_critical": False,
        "question": "How do you immobilise a suspected fractured forearm before medical help arrives?",
        "reference": (
            "Support the arm in the position found. Apply padding around the fracture "
            "site. Use a splint (rigid object) along the length of the arm and secure "
            "with bandages above and below the fracture, not over it. Apply an arm "
            "sling to support the limb. Check circulation (colour, warmth, sensation) "
            "distal to the injury regularly."
        ),
    },
    {
        "id": 9,
        "category": "Trauma & Musculoskeletal",
        "safety_critical": False,
        "question": "A person has a severely sprained ankle. What is the correct immediate first aid?",
        "reference": (
            "Apply the RICE protocol: Rest the ankle, apply Ice (wrapped in cloth) "
            "for 20 minutes every 2 hours, apply Compression with a bandage to reduce "
            "swelling, and Elevate the limb above heart level. Do not apply ice "
            "directly to skin. Seek medical review to rule out fracture."
        ),
    },
    # --- Bites, Stings & Envenomation ---
    {
        "id": 10,
        "category": "Bites, Stings & Envenomation",
        "safety_critical": True,
        "question": "What is the correct first aid for a suspected snake bite in Australia?",
        "reference": (
            "Keep the person calm and still. Apply pressure immobilisation: wrap a "
            "broad pressure bandage firmly over the bite site, then wrap the entire "
            "limb from fingers/toes to the armpit/groin. Splint the limb. Do NOT "
            "wash the bite, cut it, or apply a tourniquet. Call 000 immediately. "
            "Do not allow the person to walk."
        ),
    },
    {
        "id": 11,
        "category": "Bites, Stings & Envenomation",
        "safety_critical": True,
        "question": "A person is stung by a bee and begins showing signs of anaphylaxis. What is the immediate treatment?",
        "reference": (
            "Administer epinephrine (EpiPen) immediately into the outer thigh. Call "
            "emergency services (000). Lay the person flat with legs elevated unless "
            "breathing is difficult (sit up). A second EpiPen can be given after "
            "5 minutes if no improvement. Be prepared to start CPR. Do NOT give "
            "antihistamine as sole treatment -- it is too slow."
        ),
    },
    # --- Poisoning, Overdose & Toxic Exposure ---
    {
        "id": 12,
        "category": "Poisoning, Overdose & Toxic Exposure",
        "safety_critical": True,
        "question": "You find someone who has suffered an electric shock. They are conscious. What do you do?",
        "reference": (
            "Do NOT touch the person while they are still in contact with the power "
            "source. Turn off the power at the source or switchboard. Once safe, "
            "call 000. Check for breathing and pulse. Treat burns at entry and exit "
            "points. Lay the person down and treat for shock. Monitor closely as "
            "cardiac arrhythmias can develop later."
        ),
    },
    {
        "id": 13,
        "category": "Poisoning, Overdose & Toxic Exposure",
        "safety_critical": False,
        "question": "A child has swallowed a household chemical. They are conscious and alert. What do you do?",
        "reference": (
            "Call Poisons Information Centre (13 11 26 in Australia) or 000 immediately. "
            "Do NOT induce vomiting unless specifically instructed. Do NOT give water or "
            "milk unless advised. Keep the container for identification. Monitor the "
            "child's breathing and consciousness. Follow the instructions of the "
            "poisons centre or emergency operator exactly."
        ),
    },
    # --- Burns & Environmental Emergencies ---
    {
        "id": 14,
        "category": "Burns & Environmental Emergencies",
        "safety_critical": False,
        "question": "How do you treat a minor superficial burn at home?",
        "reference": (
            "Cool the burn under cool (not cold or iced) running water for 20 minutes. "
            "Do not apply ice, butter, toothpaste, or any cream. Remove jewellery near "
            "the burn. Cover with a non-adherent sterile dressing. Seek medical advice "
            "for burns larger than a 20-cent coin, burns on face, hands, feet, or "
            "genitals, or any blistering."
        ),
    },
    {
        "id": 15,
        "category": "Burns & Environmental Emergencies",
        "safety_critical": True,
        "question": "What are the signs of heat stroke and how do you treat it?",
        "reference": (
            "Signs: hot, dry or minimally sweaty skin, temperature above 40C, "
            "confusion, agitation, loss of consciousness, rapid pulse. Treatment: "
            "call 000 immediately. Move to a cool area. Remove excess clothing. "
            "Cool rapidly with ice packs to armpits, neck, and groin, or immerse "
            "in cool water. Fan the person. Do not give fluids if unconscious."
        ),
    },
    # --- Neurological & Altered Consciousness ---
    {
        "id": 16,
        "category": "Neurological & Altered Consciousness",
        "safety_critical": True,
        "question": "A person is having a tonic-clonic seizure. What should you do and what must you avoid?",
        "reference": (
            "Do: protect the person from injury by clearing hard objects, cushion the "
            "head, time the seizure, place in recovery position after convulsions stop. "
            "Call 000 if seizure lasts more than 5 minutes, a second seizure occurs, "
            "or the person does not regain consciousness. "
            "Do NOT: restrain the person, put anything in their mouth, give water."
        ),
    },
    {
        "id": 17,
        "category": "Neurological & Altered Consciousness",
        "safety_critical": True,
        "question": "What position should you place a conscious casualty in when they show signs of shock?",
        "reference": (
            "Lay the person flat on their back. Elevate their legs approximately "
            "30 cm (unless there is a suspected spinal injury, leg fracture, or "
            "breathing difficulty). Keep the person warm with a blanket. Do not "
            "give food or water. Call 000. Monitor breathing and pulse continuously. "
            "Do not leave the person alone."
        ),
    },
    # --- Spinal Injuries & Patient Movement ---
    {
        "id": 18,
        "category": "Spinal Injuries & Patient Movement",
        "safety_critical": True,
        "question": "A person has fallen from height and you suspect a spinal injury. They are conscious. How do you manage them?",
        "reference": (
            "Tell the person to stay still. Do not move them unless there is immediate "
            "danger. Stabilise the head and neck in the position found. Call 000. "
            "If the person must be moved, use log-roll technique with enough rescuers "
            "to maintain spinal alignment. Keep the person warm. Monitor breathing "
            "continuously. Do not remove a helmet."
        ),
    },
    # --- Minor Injuries & General First Aid ---
    {
        "id": 19,
        "category": "Minor Injuries & General First Aid",
        "safety_critical": False,
        "question": "What are the warning signs of a concussion after a head injury and when should you seek emergency care?",
        "reference": (
            "Warning signs: headache, confusion, dizziness, memory loss, nausea, "
            "vomiting, blurred vision, slurred speech, unequal pupils. Seek emergency "
            "care immediately if: loss of consciousness occurred, repeated vomiting, "
            "seizure, worsening headache, one pupil is larger than the other, or "
            "any deterioration in condition. Do not leave alone for 24 hours."
        ),
    },
    {
        "id": 20,
        "category": "Minor Injuries & General First Aid",
        "safety_critical": False,
        "question": "How do you treat a chemical splash to the eye?",
        "reference": (
            "Immediately irrigate the eye with large amounts of clean water for at "
            "least 20 minutes. Hold the eyelid open and let water flow from the inner "
            "corner outward. Remove contact lenses if present. Do not rub the eye. "
            "Call Poisons Information Centre or go to emergency department. Bring the "
            "chemical container for identification."
        ),
    },
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_model(model_path: str = "") -> tuple:
    for candidate in [p for p in [model_path, LOCAL_MODEL] if p]:
        if os.path.isdir(candidate) and os.path.exists(
            os.path.join(candidate, "config.json")
        ):
            return os.path.abspath(candidate), True
    return MODEL_ID, False


def unload(model):
    del model
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()


def get_bnb_config(quant: str):
    if quant == "4bit":
        return BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16,
        )
    if quant == "8bit":
        return BitsAndBytesConfig(load_in_8bit=True)
    return None


def load_variant(source: str, is_local: bool, quant: str, adapter_path: str = ""):
    bnb = get_bnb_config(quant)
    tokenizer = AutoTokenizer.from_pretrained(
        source, trust_remote_code=True, local_files_only=is_local
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token    = tokenizer.eos_token
        tokenizer.pad_token_id = tokenizer.eos_token_id
    tokenizer.padding_side = "left"

    model = AutoModelForCausalLM.from_pretrained(
        source,
        quantization_config=bnb,
        torch_dtype=torch.float16,
        device_map="auto",
        trust_remote_code=True,
        local_files_only=is_local,
    )
    if adapter_path and os.path.exists(adapter_path):
        model = PeftModel.from_pretrained(model, adapter_path)
    model.eval()
    return model, tokenizer


def build_prompt(question: str) -> str:
    return (
        f"<start_of_turn>user\n{SYSTEM_PROMPT}\n\n"
        f"Question: {question}<end_of_turn>\n"
        f"<start_of_turn>model\n"
    )


def _get_stop_token_ids(tokenizer) -> list[int]:
    """
    Return all token IDs that should terminate generation.
    Includes eos_token plus Gemma's <end_of_turn> if present.
    """
    stop_ids = [tokenizer.eos_token_id]
    for candidate in ["<end_of_turn>", "<|im_end|>", "[/INST]"]:
        tid = tokenizer.convert_tokens_to_ids(candidate)
        if tid is not None and tid != tokenizer.unk_token_id:
            stop_ids.append(tid)
    return list(set(stop_ids))


def generate(model, tokenizer, question: str, max_new_tokens: int) -> dict:
    prompt  = build_prompt(question)
    inputs  = tokenizer(prompt, return_tensors="pt").to(model.device)
    in_len  = inputs["input_ids"].shape[-1]

    stop_ids = _get_stop_token_ids(tokenizer)

    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

    try:
        with torch.inference_mode():
            t0  = time.time()
            out = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                pad_token_id=tokenizer.pad_token_id,
                eos_token_id=stop_ids,
                repetition_penalty=1.15,
            )
            elapsed = time.time() - t0
        new_ids = out[0][in_len:]
        n_tok   = len(new_ids)
        answer  = tokenizer.decode(new_ids, skip_special_tokens=True).strip()
        peak_mb = (
            torch.cuda.max_memory_allocated() / 1e6
            if torch.cuda.is_available() else 0.0
        )
        return {
            "answer":           answer,
            "tokens_generated": n_tok,
            "tokens_per_sec":   round(n_tok / elapsed, 2) if elapsed > 0 else 0.0,
            "elapsed_s":        round(elapsed, 2),
            "peak_vram_mb":     round(peak_mb, 0),
            "error":            None,
        }
    except Exception as e:
        return {
            "answer":           "",
            "tokens_generated": 0,
            "tokens_per_sec":   0.0,
            "elapsed_s":        0.0,
            "peak_vram_mb":     0.0,
            "error":            str(e),
        }


# ---------------------------------------------------------------------------
# Core evaluation loop
# ---------------------------------------------------------------------------

def run_model(variant_key: str, source: str, is_local: bool,
              max_new_tokens: int) -> dict:
    """Load one model variant, run all 20 questions, unload. Returns result dict."""
    
    # 1. Determine quantization type from the key name
    if variant_key == "base":
        quant = "fp16"
    else:
        # Map keys like '4bit_old' or '4bit_new' to the '4bit' quant config
        quant = "4bit" if "4bit" in variant_key else ("8bit" if "8bit" in variant_key else "fp16")

    # 2. Get the specific folder path from the ADAPTER_PATHS dictionary
    adapter_path = "" if variant_key == "base" else ADAPTER_PATHS.get(variant_key, "")

    # 3. Create descriptive labels for the terminal output and results file
    labels = {
        "base": "Base model (no fine-tuning)",
        "4bit_old": "4-bit OLD (Aggressive/Overfit)",
        "4bit_new": "4-bit NEW (Optimized/Medical)",
        "8bit": "Fine-tuned LoRA 8-bit",
        "fp16": "Fine-tuned LoRA fp16",
    }
    label = labels.get(variant_key, variant_key)

    print(f"\n{'='*60}")
    print(f"  Model : {label}")
    print(f"  Quant : {quant}  |  Adapter: {adapter_path or 'none'}")
    print(f"{'='*60}")

    if adapter_path and not os.path.exists(adapter_path):
        print(f"  SKIP -- adapter not found: {adapter_path}")
        return {"variant": variant_key, "label": label, "skipped": True, "answers": []}

    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()

    print("  Loading...")
    model, tokenizer = load_variant(source, is_local, quant, adapter_path)
    mem_loaded = torch.cuda.memory_allocated() / 1e6 if torch.cuda.is_available() else 0.0
    print(f"  VRAM after load : {mem_loaded:.0f} MB")

    # warm-up pass (not saved)
    generate(model, tokenizer, "Hello", max_new_tokens=5)
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

    answers = []
    for q in QUESTIONS:
        print(f"  Q{q['id']:02d}/{len(QUESTIONS)}  {q['question'][:60]}...")
        result = generate(model, tokenizer, q["question"], max_new_tokens)
        answers.append({
            "question_id":    q["id"],
            "question":       q["question"],
            "reference":      q["reference"],
            "category":       q["category"],
            "safety_critical": q["safety_critical"],
            **result,
        })
        status = f"err: {result['error']}" if result["error"] else \
                 f"{result['tokens_per_sec']} tok/s  peak {result['peak_vram_mb']:.0f} MB"
        print(f"          {status}")

    unload(model)

    return {
        "variant":  variant_key,
        "label":    label,
        "quant":    quant,
        "skipped":  False,
        "answers":  answers,
    }

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def make_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def save_results(run_id: str, meta: dict, model_results: list):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, f"run_{run_id}.json")
    payload = {
        "run_id":    run_id,
        "timestamp": datetime.now().isoformat(),
        "meta":      meta,
        "results":   model_results,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"\n[eval] Results saved -> {path}")
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="Run 20-question eval suite across all model variants",
    )
    p.add_argument(
        "--models", nargs="+",
        default=["base", "4bit", "8bit", "fp16"],
        choices=["base", "4bit", "8bit", "fp16"],
        help="Which variants to run (default: all four)",
    )
    p.add_argument("--model_path", default="",
                   help="Local base model directory")
    p.add_argument("--max_new_tokens", type=int, default=250,
                   help="Max tokens per answer (default 250)")
    args = p.parse_args()

    run_id = make_run_id()
    source, is_local = resolve_model(args.model_path)

    meta = {
        "model_id":       MODEL_ID,
        "model_source":   source,
        "variants_run":   args.models,
        "max_new_tokens": args.max_new_tokens,
        "n_questions":    len(QUESTIONS),
        "system_prompt":  SYSTEM_PROMPT,
    }

    print("=" * 60)
    print("  Eval Suite -- 20-Question First Aid Benchmark")
    print("=" * 60)
    print(f"  Run ID     : {run_id}")
    print(f"  Variants   : {args.models}")
    print(f"  Questions  : {len(QUESTIONS)}")
    print(f"  Max tokens : {args.max_new_tokens}")
    print(f"  Output dir : {RESULTS_DIR}/")
    print("=" * 60)

    model_results = []
    for variant in args.models:
        result = run_model(variant, source, is_local, args.max_new_tokens)
        model_results.append(result)
        # Save incrementally after each model so a crash mid-run doesn't lose work
        save_results(run_id, meta, model_results)

    # --- Summary table ---
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    print(f"  {'Variant':<25} {'Answered':>8} {'Avg tok/s':>10} {'Errors':>7}")
    print("  " + "-" * 52)
    for r in model_results:
        if r.get("skipped"):
            print(f"  {r['label']:<25} {'SKIPPED':>8}")
            continue
        answered  = sum(1 for a in r["answers"] if not a["error"])
        avg_tps   = (
            sum(a["tokens_per_sec"] for a in r["answers"] if not a["error"]) / answered
            if answered else 0
        )
        errors    = sum(1 for a in r["answers"] if a["error"])
        print(f"  {r['label']:<25} {answered:>8} {avg_tps:>9.1f} {errors:>7}")

    out_path = os.path.join(RESULTS_DIR, f"run_{run_id}.json")
    print(f"\n  Full results : {out_path}")
    print("=" * 60)
