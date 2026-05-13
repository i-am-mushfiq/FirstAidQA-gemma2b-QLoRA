"""
data_v2.py  --  Dataset preparation, classification, splitting, and tokenization
=================================================================================
VERSION: v2  (original: data.py)

CHANGES FROM data.py
────────────────────
1. TEMPLATE ALIGNMENT (Fix #1)
   _build_instruction() and _build_full_text() are REPLACED by
   build_hf_dataset_v2(samples, tokenizer) which calls
   tokenizer.apply_chat_template() — the single officially supported
   formatting method for any HuggingFace instruct model.

   Why: apply_chat_template reads the template from the model's tokenizer_config,
   guaranteeing structural correctness. Manual string construction in data.py
   will silently diverge if the template ever changes across model revisions.

   NOTE: Run verify_template_v1.py first to confirm whether data.py templates
   were already correct. If PASS, the training output is identical; this change
   only adds robustness. If MISMATCH, this change is load-bearing.

2. BOS HANDLING (Fix #1 sub-fix)
   apply_chat_template embeds <bos> in the returned string.
   tokenize_dataset_v2() therefore uses add_special_tokens=False to
   prevent the tokenizer from adding a second BOS, which would shift the
   masking boundary by one token.

3. MAX_LENGTH DOCUMENTED
   Sequence length analysis (May 2026) across all 3 splits:
     99th pct: ~214 estimated tokens   Max: ~314 estimated tokens
   Current max_length=320 in train.py already covers 100% of examples.
   No truncation of safety escalations is occurring. max_length=512
   in train_v2.py adds a generous safety buffer for any future dataset growth.

4. LABEL MASKING — CONFIRMED CORRECT, NOT CHANGED
   data.py's tokenize_dataset() correctly masks instruction tokens to -100
   and computes loss only on answer tokens. verify_masking.py verifies this.
   The masking logic is preserved verbatim in tokenize_dataset_v2().

5. LORA TARGET MODULES — CONFIRMED CORRECT, NOT CHANGED
   train.py already targets all 7 projection layers including FFN:
   q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj.
   No change needed.

ALL OTHER FUNCTIONS (classification, splitting, loading, enrichment) are
copied verbatim from data.py. Both files can coexist safely.

Usage:
  Import data_v2 in place of data in train_v2.py.
  All public API is backward-compatible except build_hf_dataset_v2()
  requires a tokenizer argument.
"""

import json
import os
import random
from collections import defaultdict
from typing import Optional

# ML imports are lazy (inside functions) so `python data_v2.py` works without GPU deps

# ---------------------------------------------------------------------------
# Paths  (identical to data.py)
# ---------------------------------------------------------------------------

DEFAULT_DATASET_PATH = os.path.join(os.path.dirname(__file__), "data", "firstaidqa_v1.json")
ENRICHED_PATH = os.path.join(os.path.dirname(__file__), "data", "firstaidqa_v1_enriched.json")
SPLITS_DIR = os.path.join(os.path.dirname(__file__), "splits", "10cat")

# ---------------------------------------------------------------------------
# System prompt  (identical to data.py)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a first aid assistant. Provide accurate, step-by-step emergency "
    "guidance. For life-threatening situations, always advise calling emergency "
    "services immediately."
)

# ---------------------------------------------------------------------------
# Template prefix variants  (replaces _build_instruction template strings)
# Template 0 is canonical and used for val/test (consistent evaluation).
# The prefix is injected into the user message before apply_chat_template.
# ---------------------------------------------------------------------------

INSTRUCTION_PREFIXES = [
    "Question: ",           # template_idx 0  -- canonical
    "A patient asks: ",     # template_idx 1
    "Emergency situation: ",# template_idx 2
    "",                     # template_idx 3  -- direct question
]

# ---------------------------------------------------------------------------
# Category definitions  (verbatim from data.py)
# ---------------------------------------------------------------------------

CATEGORY_DEFINITIONS = [
    # --- Safety-critical (AHA time-sensitive) ---
    ("CPR / Cardiac arrest",
     ["cpr", "cardiac arrest", "chest compression", "defibrillat", "aed",
      "heart attack", "resuscitat"]),
    ("Choking / Airway",
     ["chok", "heimlich", "airway", "foreign object in throat",
      "foreign body in airway", "not breathing", "stopped breathing",
      "rescue breath", "mouth-to-mouth", "mouth to mouth"]),
    ("Anaphylaxis",
     ["anaphylax", "epipen", "epinephrine", "severe allergic reaction"]),
    ("Severe bleeding",
     ["bleed heavily", "bleeding heavily", "bleeding severely",
      "bleed severely", "uncontrolled bleed", "arterial bleed",
      "severe hemorrhage", "severe haemorrhage",
      "blood loss", "heavy bleeding", "severe bleeding"]),
    ("Shock / Unconsciousness",
     ["shock", "unconscious", "unresponsive", "faint", "collapse"]),
    ("Spinal / Head injuries",
     ["spinal", "neck injur", "head injur", "skull fracture",
      "cervical", "vertebra", "paralys"]),
    # --- Urgent ---
    ("Seizures / Neurological",
     ["seizure", "epilep", "convuls", "stroke"]),
    ("Poisoning / Overdose",
     ["poison", "overdose", "toxic substance", "ingested", "swallowed object"]),
    ("Bites / Stings / Envenomation",
     ["snake", "spider bite", "bee sting", "wasp sting", "venom",
      "funnel-web", "redback", "blue-ring", "marine sting", "stingray"]),
    ("Burns",
     ["burn", "scald"]),
    ("Fractures / Sprains",
     ["fracture", "broken bone", "sprain", "disloc", "splint"]),
    ("Diabetic emergencies",
     ["diabet", "hypoglyc", "hyperglycemi"]),
    ("Breathing / Respiratory",
     ["asthma", "inhaler", "breathing difficult", "respiratory distress"]),
    ("Heat / Cold emergencies",
     ["heat stroke", "heat exhaust", "hypotherm", "frostbite", "hypertherm"]),
    # --- Non-urgent ---
    ("Wounds / Bleeding",
     ["wound", "lacerat", "bleed", "bandage", "dressing", "cut"]),
    ("Moving / Transporting",
     ["drag", "move casualty", "transport casualty", "human crutch",
      "carry casualty", "lift casualty"]),
    ("Eye / Ear injuries",
     ["eye injur", "eye burn", "foreign object in eye", "ear injur"]),
]

SAFETY_CRITICAL_CATEGORIES = {
    "CPR / Cardiac arrest",
    "Choking / Airway",
    "Anaphylaxis",
    "Severe bleeding",
    "Shock / Unconsciousness",
    "Spinal / Head injuries",
}

SEMANTIC_CATEGORY_LABELS = {
    "CPR / Cardiac arrest":
        "performing CPR, responding to cardiac arrest, heart attack, using an AED "
        "or defibrillator, or resuscitating someone who has stopped breathing",
    "Choking / Airway":
        "choking, airway obstruction, Heimlich manoeuvre, foreign object blocking "
        "breathing, or someone who cannot breathe",
    "Anaphylaxis":
        "severe allergic reaction, anaphylaxis, using an EpiPen or epinephrine "
        "auto-injector",
    "Severe bleeding":
        "severe or uncontrolled bleeding, arterial hemorrhage, applying a tourniquet, "
        "or stopping heavy blood loss",
    "Shock / Unconsciousness":
        "shock, unconsciousness, unresponsive casualty, fainting, or collapsed person",
    "Spinal / Head injuries":
        "spinal injury, head injury, neck injury, skull fracture, or suspected "
        "paralysis from trauma",
    "Seizures / Neurological":
        "seizure, epileptic fit, convulsions, or stroke",
    "Poisoning / Overdose":
        "poisoning, drug overdose, toxic substance ingestion, or swallowing a "
        "dangerous substance",
    "Bites / Stings / Envenomation":
        "snake bite, spider bite, bee or wasp sting, marine sting, or venom "
        "envenomation",
    "Burns":
        "burn injury, scald, or thermal skin damage from heat or chemicals",
    "Fractures / Sprains":
        "bone fracture, broken bone, sprain, joint dislocation, or applying a splint",
    "Diabetic emergencies":
        "diabetic emergency, low blood sugar hypoglycemia, or high blood sugar "
        "hyperglycemia",
    "Breathing / Respiratory":
        "asthma attack, difficulty breathing, respiratory distress, or using an inhaler",
    "Heat / Cold emergencies":
        "heat stroke, heat exhaustion, hypothermia, frostbite, or a "
        "temperature-related emergency",
    "Wounds / Bleeding":
        "wound care, laceration, minor bleeding, bandaging, or dressing a cut",
    "Moving / Transporting":
        "moving or transporting an injured person, human crutch technique, or "
        "casualty evacuation",
    "Eye / Ear injuries":
        "eye injury, ear injury, foreign object in the eye, or chemical eye burn",
    "General first aid":
        "general first aid principles, basic emergency response, or miscellaneous "
        "first aid that does not fit a specific emergency category",
}

SAFETY_CRITICAL_NLI_LABELS = [
    "a life-threatening emergency requiring immediate first aid intervention",
    "a non-urgent first aid question or general medical information",
]

# ---------------------------------------------------------------------------
# 1. Classification helpers  (verbatim from data.py)
# ---------------------------------------------------------------------------

def extract_question_type(question: str) -> str:
    q = question.strip()
    first = q.split()[0].lower().rstrip("?") if q else ""
    if first == "what":
        return "What"
    elif first == "how":
        return "How"
    elif first == "why":
        return "Why"
    elif first == "when":
        return "When"
    elif first in ("can", "is", "should", "are", "do", "does", "would", "could"):
        return "Can/Is/Should"
    else:
        return "Other"


def classify_category(question: str, answer: str) -> str:
    text = (question + " " + answer).lower()
    for category, keywords in CATEGORY_DEFINITIONS:
        if any(kw in text for kw in keywords):
            return category
    return "General first aid"


def classify_category_semantic(question, answer, classifier, confidence_threshold=0.10):
    text = f"Question: {question} Answer: {answer}"
    label_keys = list(SEMANTIC_CATEGORY_LABELS.keys())
    label_descs = list(SEMANTIC_CATEGORY_LABELS.values())
    result = classifier(
        text,
        candidate_labels=label_descs,
        hypothesis_template="This text is about {}.",
        multi_label=False,
    )
    top_desc = result["labels"][0]
    top_confidence = result["scores"][0]
    top_idx = label_descs.index(top_desc)
    if top_confidence < confidence_threshold:
        category = classify_category(question, answer)
        return category, top_confidence
    return label_keys[top_idx], top_confidence


def classify_safety_critical_semantic(question, answer, classifier):
    text = f"Question: {question} Answer: {answer}"
    result = classifier(
        text,
        candidate_labels=SAFETY_CRITICAL_NLI_LABELS,
        hypothesis_template="This is {}.",
        multi_label=False,
    )
    is_critical = result["labels"][0] == SAFETY_CRITICAL_NLI_LABELS[0]
    confidence = result["scores"][0]
    return is_critical, confidence


# ---------------------------------------------------------------------------
# 2. Dataset enrichment  (verbatim from data.py)
# ---------------------------------------------------------------------------

def enrich_dataset(raw: list, seed: int = 42) -> list:
    rng = random.Random(seed)
    template_indices = [i % 4 for i in range(len(raw))]
    rng.shuffle(template_indices)
    enriched = []
    for i, sample in enumerate(raw):
        q = sample["question"]
        a = sample["answer"]
        category = classify_category(q, a)
        enriched.append({
            "question": q,
            "answer": a,
            "category": category,
            "question_type": extract_question_type(q),
            "safety_critical": category in SAFETY_CRITICAL_CATEGORIES,
            "template_idx": template_indices[i],
        })
    return enriched


def enrich_dataset_semantic(raw: list, seed: int = 42) -> list:
    from transformers import pipeline as hf_pipeline
    import torch
    device = 0 if torch.cuda.is_available() else -1
    device_label = "GPU" if device == 0 else "CPU"
    print(f"[data_v2] Loading NLI classifier on {device_label}...")
    classifier = hf_pipeline(
        "zero-shot-classification",
        model="cross-encoder/nli-deberta-v3-small",
        device=device,
    )
    print(f"[data_v2] Classifying {len(raw):,} samples...")
    rng = random.Random(seed)
    enriched = []
    fallback_count = 0
    for i, sample in enumerate(raw):
        if i % 500 == 0 and i > 0:
            print(f"[data_v2]   ... {i:,}/{len(raw):,} done")
        q = sample["question"]
        a = sample["answer"]
        category, cat_conf = classify_category_semantic(q, a, classifier)
        is_critical, sc_conf = classify_safety_critical_semantic(q, a, classifier)
        if cat_conf < 0.20:
            fallback_count += 1
        enriched.append({
            "question": q,
            "answer": a,
            "category": category,
            "question_type": extract_question_type(q),
            "safety_critical": is_critical,
            "safety_critical_confidence": round(sc_conf, 4),
            "category_confidence": round(cat_conf, 4),
            "template_idx": i % 4,
        })
    print(f"[data_v2] Done. Keyword fallbacks: {fallback_count}/{len(raw)}")
    return enriched


# ---------------------------------------------------------------------------
# 3. Stratified split  (verbatim from data.py)
# ---------------------------------------------------------------------------

def stratified_split(
    data: list,
    train_ratio: float = 0.80,
    val_ratio: float = 0.10,
    seed: int = 42,
) -> tuple:
    rng = random.Random(seed)
    strata = defaultdict(list)
    for i, sample in enumerate(data):
        key = f"{sample['category']}|{sample['question_type']}"
        strata[key].append(i)
    train_idx, val_idx, test_idx = [], [], []
    test_ratio = 1.0 - train_ratio - val_ratio
    for key, indices in strata.items():
        rng.shuffle(indices)
        n = len(indices)
        if n < 5:
            train_idx.extend(indices)
            continue
        n_test  = max(1, round(n * test_ratio))
        n_val   = max(1, round(n * val_ratio))
        n_train = n - n_test - n_val
        test_idx.extend(indices[:n_test])
        val_idx.extend(indices[n_test:n_test + n_val])
        train_idx.extend(indices[n_test + n_val:])
    train = [data[i] for i in train_idx]
    val   = [{**data[i], "template_idx": 0} for i in val_idx]
    test  = [{**data[i], "template_idx": 0} for i in test_idx]
    rng.shuffle(train)
    return train, val, test


# ---------------------------------------------------------------------------
# 4. Save / load splits  (verbatim from data.py)
# ---------------------------------------------------------------------------

def save_splits(train: list, val: list, test: list, splits_dir: str = SPLITS_DIR):
    os.makedirs(splits_dir, exist_ok=True)
    for name, data in [("train", train), ("val", val), ("test", test)]:
        path = os.path.join(splits_dir, f"{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"[data_v2] Saved {len(data):>4} samples -> {path}")


def load_split(name: str, splits_dir: str = SPLITS_DIR) -> list:
    path = os.path.join(splits_dir, f"{name}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Split file not found: {path}\n"
            f"Run `python data.py` first to generate the splits."
        )
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 5. HuggingFace Dataset construction  -- CHANGED from data.py
#    Now uses tokenizer.apply_chat_template() instead of manual strings.
#    Requires tokenizer argument so template is always model-authoritative.
# ---------------------------------------------------------------------------

def build_hf_dataset_v2(samples: list, tokenizer):
    """
    Build a HuggingFace Dataset using apply_chat_template for formatting.

    The 'instruction' field contains the formatted prompt up to <start_of_turn>model,
    used for computing the masking boundary in tokenize_dataset_v2().

    The 'text' field contains the complete training string (instruction + answer).

    IMPORTANT: The strings returned by apply_chat_template already contain <bos>.
    tokenize_dataset_v2() must use add_special_tokens=False to avoid adding it twice.
    """
    from datasets import Dataset

    records = []
    for s in samples:
        q    = s["question"]
        a    = s["answer"]
        tidx = s.get("template_idx", 0)

        prefix       = INSTRUCTION_PREFIXES[tidx % len(INSTRUCTION_PREFIXES)]
        user_content = SYSTEM_PROMPT + "\n\n" + prefix + q

        # Instruction-only (up to but not including the answer)
        instr_msgs = [{"role": "user", "content": user_content}]
        instruction = tokenizer.apply_chat_template(
            instr_msgs,
            tokenize=False,
            add_generation_prompt=True,   # appends <start_of_turn>model\n
        )

        # Full sequence (instruction + answer + end token)
        full_msgs = [
            {"role": "user",  "content": user_content},
            {"role": "model", "content": a},
        ]
        full_text = tokenizer.apply_chat_template(
            full_msgs,
            tokenize=False,
            add_generation_prompt=False,
        )

        records.append({
            "instruction":    instruction,
            "text":           full_text,
            "safety_critical": s.get("safety_critical", False),
            "category":       s.get("category", "General first aid"),
        })

    return Dataset.from_list(records)


# ---------------------------------------------------------------------------
# 6. Tokenization with answer-only loss masking  -- CHANGED from data.py
#    Uses add_special_tokens=False because apply_chat_template already
#    embeds <bos> in the string. Logic otherwise identical to data.py.
# ---------------------------------------------------------------------------

def tokenize_dataset_v2(
    dataset,
    tokenizer,
    max_length: int = 512,
):
    """
    Tokenize for causal LM training with answer-only loss masking.

    Identical semantics to data.tokenize_dataset() with one critical change:
    add_special_tokens=False is used throughout because apply_chat_template
    strings already contain the <bos> token. Using add_special_tokens=True
    would prepend a second <bos>, shifting the masking boundary by one token.

    Loss is computed ONLY on answer tokens. All instruction tokens (question,
    system prompt, turn markers) are masked to -100.

    Sequence length note (May 2026 audit):
      99th pct ~ 214 estimated tokens, max ~ 314 estimated tokens across
      all three splits. max_length=512 provides a 63% safety buffer.
    """

    def tokenize_fn(batch):
        # Tokenize full sequences (instruction + answer)
        # add_special_tokens=False: apply_chat_template already includes <bos>
        full_enc = tokenizer(
            batch["text"],
            truncation=True,
            padding="max_length",
            max_length=max_length,
            return_tensors=None,
            add_special_tokens=False,   # <-- KEY CHANGE from data.py
        )

        # Tokenize instruction-only to find the answer boundary.
        # add_special_tokens=False for the same reason.
        instr_enc = tokenizer(
            batch["instruction"],
            truncation=False,
            padding=False,
            return_tensors=None,
            add_special_tokens=False,   # <-- KEY CHANGE from data.py
        )

        labels = []
        for full_ids, instr_ids in zip(
            full_enc["input_ids"], instr_enc["input_ids"]
        ):
            label = list(full_ids)
            instr_len = len(instr_ids)

            # Mask all instruction tokens (including BOS) -- no loss signal
            for i in range(min(instr_len, len(label))):
                label[i] = -100

            # Mask padding tokens
            for i in range(len(label)):
                if full_ids[i] == tokenizer.pad_token_id:
                    label[i] = -100

            labels.append(label)

        full_enc["labels"] = labels
        return full_enc

    tokenized = dataset.map(
        tokenize_fn,
        batched=True,
        remove_columns=["text", "instruction", "safety_critical", "category"],
    )
    tokenized.set_format("torch")
    return tokenized


# ---------------------------------------------------------------------------
# 7. Fallback mock dataset  (verbatim from data.py)
# ---------------------------------------------------------------------------

MOCK_QA = [
    {
        "question": "Can a human crutch be used for a person with a shoulder injury?",
        "answer": (
            "No, the human crutch should not be used if the person has an injured "
            "arm, hand, or shoulder on the side you are supporting. Stand on the "
            "opposite side."
        ),
    },
    {
        "question": "What is the correct ratio of chest compressions to rescue breaths in CPR?",
        "answer": (
            "30 chest compressions to 2 rescue breaths (30:2), at 100-120 "
            "compressions per minute and at least 5 cm deep."
        ),
    },
    {
        "question": "How do you treat a minor burn at home?",
        "answer": (
            "Cool the burn under running water for 10-20 minutes. Do not apply "
            "ice, butter, or toothpaste. Cover with a sterile non-fluffy dressing."
        ),
    },
    {
        "question": "What are the signs of a stroke?",
        "answer": (
            "Use the FAST test: Face drooping, Arm weakness, Speech difficulty, "
            "Time to call emergency services. Keep the person calm until help arrives."
        ),
    },
    {
        "question": "How should you help someone who is choking?",
        "answer": (
            "Give up to 5 back blows between the shoulder blades. If unsuccessful, "
            "give up to 5 abdominal thrusts (Heimlich manoeuvre). Alternate until "
            "the object is cleared or the person loses consciousness."
        ),
    },
]


def load_qa_data(json_path: Optional[str] = None) -> list:
    if json_path and os.path.exists(json_path):
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        print(f"[data_v2] Loaded {len(data):,} samples from: {json_path}")
        return data
    if os.path.exists(DEFAULT_DATASET_PATH):
        with open(DEFAULT_DATASET_PATH, encoding="utf-8") as f:
            data = json.load(f)
        print(f"[data_v2] Loaded {len(data):,} samples from: {os.path.basename(DEFAULT_DATASET_PATH)}")
        return data
    print("[data_v2] WARNING: No dataset file found -- using built-in mock (5 samples).")
    return MOCK_QA


# ---------------------------------------------------------------------------
# 8. Standalone enrichment + split generation  (verbatim from data.py)
#    Run `python data_v2.py` to regenerate splits using data_v2 enrichment.
#    Splits are format-agnostic (plain JSON Q&A + metadata), so splits
#    generated by data.py are fully compatible with data_v2.py and vice versa.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import collections

    p = argparse.ArgumentParser(
        description="data_v2: Enrich dataset and generate stratified splits"
    )
    p.add_argument(
        "--no-semantic", action="store_true",
        help="Use keyword-based classifier instead of NLI",
    )
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    classifier_mode = ("keyword (--no-semantic)" if args.no_semantic
                       else "semantic NLI (cross-encoder/nli-deberta-v3-small)")

    print("=" * 65)
    print("  data_v2.py -- Dataset enrichment and split generation")
    print("  (apply_chat_template edition)")
    print("=" * 65)
    print(f"  Classifier : {classifier_mode}")
    print(f"  Seed       : {args.seed}")

    raw = load_qa_data()

    print(f"\n[data_v2] Classifying {len(raw):,} samples...")
    if args.no_semantic:
        enriched = enrich_dataset(raw, seed=args.seed)
    else:
        enriched = enrich_dataset_semantic(raw, seed=args.seed)

    with open(ENRICHED_PATH, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=2, ensure_ascii=False)
    print(f"[data_v2] Enriched dataset saved -> {ENRICHED_PATH}")

    print("\n[data_v2] Generating stratified splits (80 / 10 / 10)...")
    train, val, test = stratified_split(enriched)
    save_splits(train, val, test)

    print("\n" + "=" * 65)
    print("  Split summary")
    print("=" * 65)
    print(f"  Train : {len(train):>5}  ({100*len(train)/len(enriched):.1f}%)")
    print(f"  Val   : {len(val):>5}  ({100*len(val)/len(enriched):.1f}%)")
    print(f"  Test  : {len(test):>5}  ({100*len(test)/len(enriched):.1f}%)")
    print(f"  Total : {len(train)+len(val)+len(test):>5}")

    all_cats = sorted({s["category"] for s in enriched})
    print("\n  Category distribution:")
    header = f"  {'Category':<35} {'Train':>6} {'Val':>5} {'Test':>5} {'SC':>4}"
    print(header)
    print("  " + "-" * 55)
    for cat in all_cats:
        tr = sum(1 for s in train if s["category"] == cat)
        va = sum(1 for s in val   if s["category"] == cat)
        te = sum(1 for s in test  if s["category"] == cat)
        sc = "YES" if cat in SAFETY_CRITICAL_CATEGORIES else ""
        print(f"  {cat:<35} {tr:>6} {va:>5} {te:>5} {sc:>4}")

    sc_train = sum(1 for s in train if s["safety_critical"])
    sc_val   = sum(1 for s in val   if s["safety_critical"])
    sc_test  = sum(1 for s in test  if s["safety_critical"])
    print(f"\n  Safety-critical samples:")
    print(f"    Train: {sc_train}  Val: {sc_val}  Test: {sc_test}")

    tmpl_counts = collections.Counter(s["template_idx"] for s in train)
    print(f"\n  Training template distribution:")
    for t, c in sorted(tmpl_counts.items()):
        print(f"    Template {t} ('{INSTRUCTION_PREFIXES[t]}'): {c} samples")

    print("\n[data_v2] Done.\n")
