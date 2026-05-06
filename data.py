"""
data.py -- Dataset preparation, classification, splitting, and tokenization
===========================================================================
Run this script ONCE before training to enrich the dataset and create splits:

    python data.py

This will produce:
    firstaidqa_v1_enriched.json   -- original samples + category/type/safety labels
    splits/train.json             -- 80% stratified training split
    splits/val.json               -- 10% stratified validation split
    splits/test.json              -- 10% stratified test split (LOCKED -- never train on this)

Splits are saved to disk so every training run uses identical data.

HOW TO USE YOUR OWN DATASET
------------------------------------------------------------
Drop a JSON file with this structure into this folder:
    [{"question": "...", "answer": "..."}, ...]
Then update DEFAULT_DATASET_PATH below and re-run:
    python data.py
------------------------------------------------------------
"""

import json
import os
import random
from collections import defaultdict
from typing import Optional

# ML imports are lazy (inside functions) so `python data.py` works without GPU deps

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DEFAULT_DATASET_PATH = os.path.join(os.path.dirname(__file__), "data", "firstaidqa_v1.json")
ENRICHED_PATH = os.path.join(os.path.dirname(__file__), "data", "firstaidqa_v1_enriched.json")
SPLITS_DIR = os.path.join(os.path.dirname(__file__), "splits", "10cat")

# ---------------------------------------------------------------------------
# System prompt -- kept short for edge deployment (fewer tokens = less latency)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a first aid assistant. Provide accurate, step-by-step emergency "
    "guidance. For life-threatening situations, always advise calling emergency "
    "services immediately."
)

# ---------------------------------------------------------------------------
# Category definitions -- priority ordered, safety-critical categories first.
# Each sample is assigned the FIRST matching category (priority wins).
# Anchored to AHA time-sensitive emergency classifications.
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

# ---------------------------------------------------------------------------
# Semantic labels for zero-shot NLI classification
# Rich natural-language descriptions work far better than bare category names
# because the NLI model scores entailment of "This text is about <label>."
# ---------------------------------------------------------------------------

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

# For independent binary safety-critical NLI (separate from category assignment)
SAFETY_CRITICAL_NLI_LABELS = [
    "a life-threatening emergency requiring immediate first aid intervention",
    "a non-urgent first aid question or general medical information",
]

# ---------------------------------------------------------------------------
# Instruction templates -- 4 framings for training diversity.
# Template 0 is canonical and used for val/test (consistent evaluation).
# ---------------------------------------------------------------------------

def _build_instruction(question: str, template_idx: int) -> str:
    """Return the prompt text up to (but not including) the answer content.

    All templates now:
      - include the system prompt (fixes 25% of V1 examples having no safety framing)
      - end at <start_of_turn>model\\n with no answer prefix (fixes Answer:/Response:/
        Guidance: mismatch; model learns to open its own response freely)
      - use varied question framings for diversity without prefix inconsistency
    """
    s = SYSTEM_PROMPT
    if template_idx == 0:
        return (f"<start_of_turn>user\n{s}\n\n"
                f"Question: {question}<end_of_turn>\n"
                f"<start_of_turn>model\n")
    elif template_idx == 1:
        return (f"<start_of_turn>user\n{s}\n\n"
                f"A patient asks: {question}<end_of_turn>\n"
                f"<start_of_turn>model\n")
    elif template_idx == 2:
        return (f"<start_of_turn>user\n{s}\n\n"
                f"Emergency situation: {question}<end_of_turn>\n"
                f"<start_of_turn>model\n")
    else:  # template 3 -- direct question, system prompt retained
        return (f"<start_of_turn>user\n{s}\n\n"
                f"{question}<end_of_turn>\n"
                f"<start_of_turn>model\n")


def _build_full_text(question: str, answer: str, template_idx: int) -> str:
    """Return the complete training string (instruction + answer + end token)."""
    return _build_instruction(question, template_idx) + answer + "<end_of_turn>"


# ---------------------------------------------------------------------------
# 1. Classification helpers
# ---------------------------------------------------------------------------

def extract_question_type(question: str) -> str:
    """Classify question by its opening word -- deterministic, no model needed."""
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
    """
    Assign the highest-priority matching category (keyword fallback).
    Returns 'General first aid' if no keywords match.
    """
    text = (question + " " + answer).lower()
    for category, keywords in CATEGORY_DEFINITIONS:
        if any(kw in text for kw in keywords):
            return category
    return "General first aid"


def classify_category_semantic(
    question: str,
    answer: str,
    classifier,
    confidence_threshold: float = 0.10,
) -> tuple:
    """
    Classify category using zero-shot NLI against rich semantic descriptions.
    Returns (category_str, confidence_float).
    Falls back to keyword classifier when top NLI confidence < threshold,
    logging the sample so you can audit edge cases later.
    """
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
        # Keyword fallback for low-confidence NLI predictions
        category = classify_category(question, answer)
        return category, top_confidence

    return label_keys[top_idx], top_confidence


def classify_safety_critical_semantic(
    question: str,
    answer: str,
    classifier,
) -> tuple:
    """
    Independently classify whether a Q&A pair is safety-critical using NLI.
    This is separate from category assignment -- a question can be about a
    safety-critical topic (e.g., "What is CPR?") without being a live emergency.
    Returns (is_safety_critical: bool, confidence: float).
    """
    text = f"Question: {question} Answer: {answer}"
    result = classifier(
        text,
        candidate_labels=SAFETY_CRITICAL_NLI_LABELS,
        hypothesis_template="This is {}.",
        multi_label=False,
    )
    # First label wins only if it is the life-threatening one
    is_critical = result["labels"][0] == SAFETY_CRITICAL_NLI_LABELS[0]
    confidence = result["scores"][0]
    return is_critical, confidence


# ---------------------------------------------------------------------------
# 2. Dataset enrichment
# ---------------------------------------------------------------------------

def enrich_dataset(raw: list, seed: int = 42) -> list:
    """
    Add classification labels and template indices to each sample.

    Fields added:
      category       -- clinical topic category (string)
      question_type  -- What / How / Can/Is/Should / Why / When / Other
      safety_critical -- bool, True if AHA time-sensitive emergency
      template_idx   -- 0-3, training samples rotate through all 4;
                        val/test samples always use template 0
    """
    rng = random.Random(seed)
    # Build a shuffled index list so templates are balanced (exactly n//4 each)
    # and not correlated with position in the raw JSON. This prevents augmented
    # examples appended at the end from clustering into specific template indices.
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
            "template_idx": template_indices[i],   # overwritten for val/test in split step
        })
    return enriched


def enrich_dataset_semantic(raw: list, seed: int = 42) -> list:
    """
    Enrich dataset using zero-shot NLI semantic classification.

    Uses cross-encoder/nli-deberta-v3-small -- ~180M params, fast enough on
    CPU (~4 min for 5,500 samples), much faster on GPU.

    Two independent NLI passes per sample:
      1. Multi-class category assignment (18 candidate descriptions)
      2. Binary safety-critical check (life-threatening vs. non-urgent)

    Stores category_confidence and safety_critical_confidence for paper
    transparency and post-hoc audit.  Low-confidence (<0.20) category
    predictions fall back to keyword classifier.
    """
    from transformers import pipeline as hf_pipeline
    import torch

    device = 0 if torch.cuda.is_available() else -1
    device_label = "GPU" if device == 0 else "CPU"
    print(f"[data] Loading NLI classifier (cross-encoder/nli-deberta-v3-small) "
          f"on {device_label}...")

    classifier = hf_pipeline(
        "zero-shot-classification",
        model="cross-encoder/nli-deberta-v3-small",
        device=device,
    )
    print(f"[data] Classifier ready. Classifying {len(raw):,} samples...")

    rng = random.Random(seed)
    enriched = []
    fallback_count = 0

    for i, sample in enumerate(raw):
        if i % 500 == 0 and i > 0:
            print(f"[data]   ... {i:,}/{len(raw):,} done "
                  f"({fallback_count} keyword fallbacks so far)")

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
            "template_idx": i % 4,   # overwritten for val/test in split step
        })

    print(f"[data] Semantic classification complete. "
          f"Keyword fallbacks: {fallback_count}/{len(raw)} "
          f"({100*fallback_count/len(raw):.1f}%)")
    return enriched


# ---------------------------------------------------------------------------
# 3. Stratified split
# ---------------------------------------------------------------------------

def stratified_split(
    data: list,
    train_ratio: float = 0.80,
    val_ratio: float = 0.10,
    seed: int = 42,
) -> tuple:
    """
    Multi-axis stratified split by (category, question_type).
    Val and test samples are assigned template_idx=0 for consistent evaluation.
    Returns (train_list, val_list, test_list).
    """
    rng = random.Random(seed)

    # Group indices by stratum
    strata = defaultdict(list)
    for i, sample in enumerate(data):
        key = f"{sample['category']}|{sample['question_type']}"
        strata[key].append(i)

    train_idx, val_idx, test_idx = [], [], []
    test_ratio = 1.0 - train_ratio - val_ratio

    for key, indices in strata.items():
        rng.shuffle(indices)
        n = len(indices)

        # Strata with fewer than 5 samples go entirely to training
        if n < 5:
            train_idx.extend(indices)
            continue

        n_test = max(1, round(n * test_ratio))
        n_val = max(1, round(n * val_ratio))
        n_train = n - n_test - n_val

        test_idx.extend(indices[:n_test])
        val_idx.extend(indices[n_test:n_test + n_val])
        train_idx.extend(indices[n_test + n_val:])

    # Build split lists; fix template_idx for val/test
    train = [data[i] for i in train_idx]
    val = [{**data[i], "template_idx": 0} for i in val_idx]
    test = [{**data[i], "template_idx": 0} for i in test_idx]

    # Shuffle training set
    rng.shuffle(train)

    return train, val, test


# ---------------------------------------------------------------------------
# 4. Save / load splits
# ---------------------------------------------------------------------------

def save_splits(train: list, val: list, test: list, splits_dir: str = SPLITS_DIR):
    os.makedirs(splits_dir, exist_ok=True)
    for name, data in [("train", train), ("val", val), ("test", test)]:
        path = os.path.join(splits_dir, f"{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"[data] Saved {len(data):>4} samples -> {path}")


def load_split(name: str, splits_dir: str = SPLITS_DIR) -> list:
    """Load a named split ('train', 'val', or 'test') from disk."""
    path = os.path.join(splits_dir, f"{name}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Split file not found: {path}\n"
            f"Run `python data.py` first to generate the splits."
        )
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 5. HuggingFace Dataset construction
# ---------------------------------------------------------------------------

def build_hf_dataset(samples: list):
    from datasets import Dataset  # lazy import -- not needed for data.py enrichment step
    """
    Build a HuggingFace Dataset with both 'text' (full) and 'instruction'
    (prompt-only) fields. The instruction field is used for answer-only
    loss masking during tokenization.
    """
    records = []
    for s in samples:
        q, a, tidx = s["question"], s["answer"], s["template_idx"]
        records.append({
            "instruction": _build_instruction(q, tidx),
            "text": _build_full_text(q, a, tidx),
            "safety_critical": s.get("safety_critical", False),
            "category": s.get("category", "General first aid"),
        })
    return Dataset.from_list(records)


# ---------------------------------------------------------------------------
# 6. Tokenization with answer-only loss masking
# ---------------------------------------------------------------------------

def tokenize_dataset(
    dataset,
    tokenizer,
    max_length: int = 320,
):
    """
    Tokenize for causal LM training with answer-only loss masking.

    The loss is computed ONLY on answer tokens. All instruction tokens
    (question, system prompt, chat template) are masked to -100 so they
    contribute no gradient signal. This focuses the adapter entirely on
    learning to generate correct medical answers.
    """

    def tokenize_fn(batch):
        # Tokenize full sequences (instruction + answer)
        full_enc = tokenizer(
            batch["text"],
            truncation=True,
            padding="max_length",
            max_length=max_length,
            return_tensors=None,
        )
        # Tokenize instruction-only to find the answer boundary.
        # add_special_tokens=True so the BOS token is included in the
        # length count, matching the full sequence tokenization.
        instr_enc = tokenizer(
            batch["instruction"],
            truncation=False,
            padding=False,
            return_tensors=None,
            add_special_tokens=True,
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
# 7. Fallback mock dataset (used only when no JSON file is found)
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
    """
    Load raw Q&A pairs. Used only for initial enrichment (python data.py).
    For training, use load_split() instead.
    """
    if json_path and os.path.exists(json_path):
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        print(f"[data] Loaded {len(data):,} samples from: {json_path}")
        return data
    if os.path.exists(DEFAULT_DATASET_PATH):
        with open(DEFAULT_DATASET_PATH, encoding="utf-8") as f:
            data = json.load(f)
        print(f"[data] Loaded {len(data):,} samples from: {os.path.basename(DEFAULT_DATASET_PATH)}")
        return data
    print("[data] WARNING: No dataset file found -- using built-in mock (5 samples).")
    return MOCK_QA


# ---------------------------------------------------------------------------
# 8. Standalone enrichment + split generation
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import collections

    p = argparse.ArgumentParser(
        description="Enrich dataset and generate stratified splits"
    )
    p.add_argument(
        "--no-semantic", action="store_true",
        help="Use keyword-based classifier instead of NLI (faster, less accurate)",
    )
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    classifier_mode = "keyword (--no-semantic)" if args.no_semantic else "semantic NLI (cross-encoder/nli-deberta-v3-small)"

    print("=" * 65)
    print("  data.py -- Dataset enrichment and split generation")
    print("=" * 65)
    print(f"  Classifier : {classifier_mode}")
    print(f"  Seed       : {args.seed}")

    # Load raw data
    raw = load_qa_data()

    # Enrich with classification labels
    print(f"\n[data] Classifying {len(raw):,} samples...")
    if args.no_semantic:
        enriched = enrich_dataset(raw, seed=args.seed)
    else:
        enriched = enrich_dataset_semantic(raw, seed=args.seed)

    # Save enriched dataset
    with open(ENRICHED_PATH, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=2, ensure_ascii=False)
    print(f"[data] Enriched dataset saved -> {ENRICHED_PATH}")

    # Generate stratified splits
    print("\n[data] Generating stratified splits (80 / 10 / 10)...")
    train, val, test = stratified_split(enriched)
    save_splits(train, val, test)

    # --- Verification report ---
    print("\n" + "=" * 65)
    print("  Split summary")
    print("=" * 65)
    print(f"  Train : {len(train):>5}  ({100*len(train)/len(enriched):.1f}%)")
    print(f"  Val   : {len(val):>5}  ({100*len(val)/len(enriched):.1f}%)")
    print(f"  Test  : {len(test):>5}  ({100*len(test)/len(enriched):.1f}%)")
    print(f"  Total : {len(train)+len(val)+len(test):>5}")

    # Category distribution across splits
    print("\n  Category distribution:")
    header = f"  {'Category':<35} {'Train':>6} {'Val':>5} {'Test':>5} {'SC':>4}"
    print(header)
    print("  " + "-" * 55)
    all_cats = sorted({s["category"] for s in enriched})
    for cat in all_cats:
        tr = sum(1 for s in train if s["category"] == cat)
        va = sum(1 for s in val   if s["category"] == cat)
        te = sum(1 for s in test  if s["category"] == cat)
        sc = "YES" if cat in SAFETY_CRITICAL_CATEGORIES else ""
        print(f"  {cat:<35} {tr:>6} {va:>5} {te:>5} {sc:>4}")

    # Safety-critical summary
    sc_train = sum(1 for s in train if s["safety_critical"])
    sc_val   = sum(1 for s in val   if s["safety_critical"])
    sc_test  = sum(1 for s in test  if s["safety_critical"])
    print(f"\n  Safety-critical samples:")
    print(f"    Train: {sc_train}  Val: {sc_val}  Test: {sc_test}")

    # Template distribution in training set
    tmpl_counts = collections.Counter(s["template_idx"] for s in train)
    print(f"\n  Training template distribution:")
    for t, c in sorted(tmpl_counts.items()):
        print(f"    Template {t}: {c} samples")

    # Confidence stats (only present when semantic mode was used)
    if not args.no_semantic and "category_confidence" in enriched[0]:
        cat_confs = [s["category_confidence"] for s in enriched]
        sc_confs  = [s["safety_critical_confidence"] for s in enriched]
        low_conf  = sum(1 for c in cat_confs if c < 0.20)
        print(f"\n  Category NLI confidence (n={len(cat_confs):,}, low-conf (<0.20): {low_conf})")
        print(f"  Safety-critical confidence   : mean={sum(sc_confs)/len(sc_confs):.3f}")

    print("\n[data] Done.\n")
