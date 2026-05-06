"""
classify_10cat.py -- 10-category semantic classification for firstaidqa_v1.json
===============================================================================
Produces a parallel set of enriched data and splits alongside the original
18-category pipeline. Nothing in data.py or splits/ is touched.

Outputs:
  firstaidqa_v1_enriched_10cat.json   -- labelled dataset (10-category scheme)
  splits_10cat/train.json             -- 80% stratified training split
  splits_10cat/val.json               -- 10% validation split
  splits_10cat/test.json              -- 10% test split (LOCKED)

Usage:
  python classify_10cat.py
  python classify_10cat.py --no-semantic   # keyword fallback only (fast)
  python classify_10cat.py --seed 123

To train on the new splits:
  python train.py --quant 4bit --splits_dir splits_10cat
"""

import argparse
import json
import os
import random
from collections import Counter, defaultdict
from typing import Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

HERE               = os.path.dirname(__file__)
RAW_PATH           = os.path.join(HERE, "firstaidqa_v1.json")
ENRICHED_10_PATH   = os.path.join(HERE, "firstaidqa_v1_enriched_10cat.json")
SPLITS_10_DIR      = os.path.join(HERE, "splits_10cat")

# ---------------------------------------------------------------------------
# 10 Category definitions
# ---------------------------------------------------------------------------
# Rich natural-language descriptions tuned for NLI entailment scoring.
# Australian envenomation terminology is explicit (funnel-web, redback,
# cone shell, bluebottle) because the dataset is AU-centric and generic
# descriptions would miss ~600 samples.

CATEGORY_LABELS_10 = {
    # DESIGN PRINCIPLE: each description is written to be EXCLUSIVE, not just
    # inclusive. It emphasises what uniquely identifies this category and
    # explicitly distinguishes it from neighbouring ones. This prevents any
    # single label acting as a semantic superset and collapsing the softmax.

    "Cardiac & Resuscitation":
        "performing CPR or responding to a cardiac arrest or heart attack — "
        "chest compression technique, AED defibrillation, mouth-to-mouth "
        "rescue breathing, or heart failure management where the heart has "
        "stopped or is critically failing and the person needs resuscitation",

    "Airway, Choking & Drowning":
        "a physically obstructed or blocked airway — choking on a foreign "
        "object, Heimlich manoeuvre technique, asthma attack, respiratory "
        "distress caused by obstruction, or rescuing a drowning person from "
        "water — where breathing is impossible due to blockage or submersion, "
        "not because of a heart problem or unconsciousness",

    "Bleeding & Wounds":
        "cleaning or dressing a minor surface cut or skin laceration, "
        "applying a bandage or dressing to a skin-level wound, nosebleed "
        "management, or wound infection prevention — injuries confined to "
        "skin damage only, with no broken bones, no venom or poison, no "
        "burns, and no life-threatening haemorrhage requiring a tourniquet",

    "Trauma & Musculoskeletal":
        "a broken bone, fracture, joint dislocation, sprain, ligament injury, "
        "muscle strain or cramp, applying a sling or splint to an injured limb, "
        "fish hook removal, or knocked-out tooth — mechanical damage to the "
        "skeleton, joints, or muscles where bones may be cracked or displaced",

    "Bites, Stings & Envenomation":
        "venom or poison introduced into the body by a living creature — "
        "snake bite requiring pressure immobilisation, funnel-web or redback "
        "spider bite, blue-ringed octopus or cone shell envenomation, box "
        "jellyfish or bluebottle sting, stonefish, tick paralysis, bee or "
        "wasp sting, animal bite, or anaphylaxis triggered by creature venom",

    "Poisoning, Overdose & Toxic Exposure":
        "a toxic chemical or substance ingested, inhaled, or externally "
        "absorbed — drug or medication overdose, household chemical poisoning, "
        "swallowing a dangerous object, carbon monoxide inhalation, electric "
        "shock or electrocution — where the hazard is a chemical or electrical "
        "source, not a creature's venom and not a temperature extreme",

    "Burns & Environmental Emergencies":
        "tissue damage caused strictly by heat, cold, or radiation — flame or "
        "hot liquid burn, scald, sunburn, heat stroke or heat exhaustion from "
        "overheating, hypothermia or frostbite from cold exposure — injuries "
        "where temperature or radiation is the sole cause, with no venom, no "
        "fracture, and no ingested chemical substance involved",

    "Neurological & Altered Consciousness":
        "a condition directly disrupting brain or nervous system function — "
        "seizure or epileptic fit, stroke, concussion from a blow to the head, "
        "unexplained fainting or sudden collapse, diabetic emergency causing "
        "altered awareness, or an unresponsive person whose loss of consciousness "
        "is neurological or metabolic, not from blood loss or airway blockage",

    "Spinal Injuries & Patient Movement":
        "a suspected spinal cord or neck injury, or the correct technique for "
        "moving an injured casualty without causing paralysis — how to drag, "
        "carry, or support with a human crutch, managing a road traffic "
        "accident scene, or transporting a casualty with a possible back, "
        "neck, or vertebral injury where movement risks permanent cord damage",

    "Minor Injuries & General First Aid":
        "routine non-emergency first aid or general assessment knowledge — "
        "splinter removal, minor eye irritation or foreign object in the ear, "
        "checking pulse and vital signs during patient assessment, managing "
        "vomiting or diarrhoea, dental first aid, first aid kit contents, "
        "measuring body temperature, or skin conditions like blisters",
}

# Safety-critical subset for independent binary NLI classification
SAFETY_CRITICAL_NLI_LABELS = [
    "a life-threatening emergency requiring immediate first aid intervention",
    "a non-urgent first aid question or general medical information",
]

# Keyword fallback (used when NLI confidence < threshold)
KEYWORD_FALLBACK_10 = [
    ("Cardiac & Resuscitation",
     ["cpr", "cardiac arrest", "chest compression", "defibrillat", "aed",
      "heart attack", "resuscitat", "heart failure", "mouth-to-mouth"]),
    ("Airway, Choking & Drowning",
     ["chok", "heimlich", "asthma", "drown", "airway",
      "not breathing", "stopped breathing", "rescue breath"]),
    ("Bleeding & Wounds",
     ["bleed", "wound", "lacerat", "bandage", "dressing",
      "nosebleed", "haemorrhage", "hemorrhage"]),
    ("Trauma & Musculoskeletal",
     ["fracture", "broken bone", "sprain", "disloc", "splint",
      "sling", "cramp", "fish hook", "dental", "knocked-out tooth"]),
    ("Bites, Stings & Envenomation",
     ["snake", "spider", "funnel-web", "redback", "jellyfish", "bluebottle",
      "stonefish", "cone shell", "blue-ring", "marine sting", "venom",
      "envenomat", "pressure immobili", "anaphylax", "epipen",
      "epinephrine", "bee sting", "wasp sting", "tick"]),
    ("Poisoning, Overdose & Toxic Exposure",
     ["poison", "overdose", "toxic", "electric shock", "electrocut",
      "swallowed", "carbon monoxide", "ingested", "chemical burn"]),
    ("Burns & Environmental Emergencies",
     ["burn", "scald", "sunburn", "heat stroke", "heat exhaust",
      "hypotherm", "frostbite"]),
    ("Neurological & Altered Consciousness",
     ["seizure", "epilep", "convuls", "stroke", "concussion",
      "faint", "unconscious", "unresponsive", "shock",
      "diabet", "hypoglyc", "hyperglycemi"]),
    ("Spinal Injuries & Patient Movement",
     ["spinal", "neck injur", "cervical", "paralys", "vertebra",
      "drag", "human crutch",
      # phrase variants the original keywords missed
      "moving a casualty", "moving the casualty", "move an injured",
      "road traffic accident", "accident site", "accident scene",
      "overturned vehicle", "transport a casualty", "carry a casualty",
      "carry the casualty", "lift a casualty", "lift the casualty"]),
]

# Spinal Injuries & Patient Movement is intentionally excluded from this set.
# The category mixes true spinal emergencies with technique questions
# (human crutch, dragging method) which are informational, not life-threatening.
# Safety-critical labelling for the 10-cat scheme relies on the independent
# NLI binary pass (safety_critical_confidence field) rather than category
# membership — use that field when filtering for safety-critical evaluation.
SAFETY_CRITICAL_CATEGORIES_10 = {
    "Cardiac & Resuscitation",
    "Airway, Choking & Drowning",
    "Neurological & Altered Consciousness",
}


# ---------------------------------------------------------------------------
# Keyword fallback classifier
# ---------------------------------------------------------------------------

def keyword_classify_10(question: str, answer: str) -> str:
    text = (question + " " + answer).lower()
    for category, keywords in KEYWORD_FALLBACK_10:
        if any(kw in text for kw in keywords):
            return category
    return "Minor Injuries & General First Aid"


# ---------------------------------------------------------------------------
# NLI semantic classifiers
# ---------------------------------------------------------------------------

def semantic_classify_category(
    question: str,
    answer: str,
    classifier,
    confidence_threshold: float = 0.10,
) -> tuple:
    """
    Classify into one of 10 categories via zero-shot NLI.
    Returns (category_str, confidence_float).
    Falls back to keyword classifier if confidence < threshold.
    """
    text = f"Question: {question} Answer: {answer}"
    label_keys  = list(CATEGORY_LABELS_10.keys())
    label_descs = list(CATEGORY_LABELS_10.values())

    result = classifier(
        text,
        candidate_labels=label_descs,
        hypothesis_template="This text is about {}.",
        multi_label=False,
    )
    top_desc       = result["labels"][0]
    top_confidence = result["scores"][0]
    top_idx        = label_descs.index(top_desc)

    if top_confidence < confidence_threshold:
        return keyword_classify_10(question, answer), top_confidence

    return label_keys[top_idx], top_confidence


def semantic_classify_safety_critical(
    question: str,
    answer: str,
    classifier,
) -> tuple:
    """
    Independent binary NLI pass for safety-critical flag.
    Returns (is_critical: bool, confidence: float).
    """
    text = f"Question: {question} Answer: {answer}"
    result = classifier(
        text,
        candidate_labels=SAFETY_CRITICAL_NLI_LABELS,
        hypothesis_template="This is {}.",
        multi_label=False,
    )
    is_critical = result["labels"][0] == SAFETY_CRITICAL_NLI_LABELS[0]
    return is_critical, result["scores"][0]


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

def extract_question_type(question: str) -> str:
    q     = question.strip()
    first = q.split()[0].lower().rstrip("?") if q else ""
    if first == "what":    return "What"
    if first == "how":     return "How"
    if first == "why":     return "Why"
    if first == "when":    return "When"
    if first in ("can", "is", "should", "are", "do", "does", "would", "could"):
        return "Can/Is/Should"
    return "Other"


def enrich_semantic(raw: list, seed: int = 42) -> list:
    """
    Classify all samples using zero-shot NLI (10-category scheme).
    Two independent passes per sample: category + safety-critical.
    """
    from transformers import pipeline as hf_pipeline
    import torch

    device       = 0 if torch.cuda.is_available() else -1
    device_label = "GPU" if device == 0 else "CPU"
    print(f"[10cat] Loading NLI model on {device_label}...")

    classifier = hf_pipeline(
        "zero-shot-classification",
        model="cross-encoder/nli-deberta-v3-small",
        device=device,
    )
    print(f"[10cat] Classifier ready. Processing {len(raw):,} samples...")

    enriched       = []
    fallback_count = 0

    for i, sample in enumerate(raw):
        if i % 500 == 0 and i > 0:
            print(f"[10cat]   ... {i:,}/{len(raw):,} done "
                  f"({fallback_count} keyword fallbacks so far)")

        q, a = sample["question"], sample["answer"]

        category, cat_conf = semantic_classify_category(q, a, classifier)
        is_critical, sc_conf = semantic_classify_safety_critical(q, a, classifier)

        if cat_conf < 0.10:
            fallback_count += 1

        enriched.append({
            "question":                    q,
            "answer":                      a,
            "category":                    category,
            "question_type":               extract_question_type(q),
            "safety_critical":             is_critical,
            "safety_critical_confidence":  round(sc_conf, 4),
            "category_confidence":         round(cat_conf, 4),
            "template_idx":                i % 4,
        })

    pct = 100 * fallback_count / len(raw)
    print(f"[10cat] Done. Keyword fallbacks: {fallback_count}/{len(raw)} ({pct:.1f}%)")
    return enriched


def enrich_keyword(raw: list, seed: int = 42) -> list:
    """Keyword-only enrichment (fast, no model needed)."""
    enriched = []
    for i, sample in enumerate(raw):
        q, a     = sample["question"], sample["answer"]
        category = keyword_classify_10(q, a)
        enriched.append({
            "question":        q,
            "answer":          a,
            "category":        category,
            "question_type":   extract_question_type(q),
            "safety_critical": category in SAFETY_CRITICAL_CATEGORIES_10,
            "template_idx":    i % 4,
        })
    return enriched


def enrich_keyword_with_sc_scores(raw: list, seed: int = 42) -> list:
    """
    Hybrid enrichment: keywords assign category (deterministic, reliable),
    NLI binary pass assigns safety_critical + confidence score (principled).

    This is the recommended mode for paper use:
      - Category labels are stable and reproducible (no model needed to verify)
      - Safety-critical flag is independent of category, based on actual
        text semantics, and comes with a confidence score you can report
      - No category_confidence field (keywords are deterministic -- confidence
        is implicitly 1.0 for matched keywords, 0.0 for the fallback)

    Usage: python classify_10cat.py --no-semantic --add-sc-scores
    """
    from transformers import pipeline as hf_pipeline
    import torch

    device       = 0 if torch.cuda.is_available() else -1
    device_label = "GPU" if device == 0 else "CPU"
    print(f"[10cat] Step 1 of 2: keyword category classification...")

    # Pass 1 -- keywords (instant)
    keyword_results = []
    for i, sample in enumerate(raw):
        q, a = sample["question"], sample["answer"]
        keyword_results.append((q, a, keyword_classify_10(q, a)))
    print(f"[10cat] Keyword classification done for {len(raw):,} samples.")

    # Pass 2 -- NLI safety-critical scoring
    print(f"[10cat] Step 2 of 2: loading NLI model for SC scoring on {device_label}...")
    classifier = hf_pipeline(
        "zero-shot-classification",
        model="cross-encoder/nli-deberta-v3-small",
        device=device,
    )
    print(f"[10cat] NLI model ready. Running binary SC pass...")

    enriched = []
    for i, (q, a, category) in enumerate(keyword_results):
        if i % 500 == 0 and i > 0:
            print(f"[10cat]   ... {i:,}/{len(raw):,} SC scores computed")

        is_critical, sc_conf = semantic_classify_safety_critical(q, a, classifier)

        enriched.append({
            "question":                   q,
            "answer":                     a,
            "category":                   category,
            "question_type":              extract_question_type(q),
            "safety_critical":            is_critical,
            "safety_critical_confidence": round(sc_conf, 4),
            "template_idx":               i % 4,
        })

    print(f"[10cat] SC scoring complete.")
    sc_count = sum(1 for s in enriched if s["safety_critical"])
    mean_conf = sum(s["safety_critical_confidence"] for s in enriched) / len(enriched)
    print(f"[10cat] Safety-critical samples : {sc_count}/{len(enriched)} "
          f"({100*sc_count/len(enriched):.1f}%)")
    print(f"[10cat] Mean SC confidence      : {mean_conf:.3f}")
    return enriched


# ---------------------------------------------------------------------------
# Stratified split (identical logic to data.py)
# ---------------------------------------------------------------------------

def stratified_split(data: list, train_r=0.80, val_r=0.10, seed=42):
    rng    = random.Random(seed)
    strata = defaultdict(list)
    for i, s in enumerate(data):
        strata[f"{s['category']}|{s['question_type']}"].append(i)

    train_idx, val_idx, test_idx = [], [], []
    test_r = 1.0 - train_r - val_r

    for indices in strata.values():
        rng.shuffle(indices)
        n = len(indices)
        if n < 5:
            train_idx.extend(indices)
            continue
        n_test  = max(1, round(n * test_r))
        n_val   = max(1, round(n * val_r))
        test_idx.extend(indices[:n_test])
        val_idx.extend(indices[n_test:n_test + n_val])
        train_idx.extend(indices[n_test + n_val:])

    train = [data[i] for i in train_idx]
    val   = [{**data[i], "template_idx": 0} for i in val_idx]
    test  = [{**data[i], "template_idx": 0} for i in test_idx]
    rng.shuffle(train)
    return train, val, test


def save_splits(train, val, test, splits_dir=SPLITS_10_DIR):
    os.makedirs(splits_dir, exist_ok=True)
    for name, data in [("train", train), ("val", val), ("test", test)]:
        path = os.path.join(splits_dir, f"{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"[10cat] Saved {len(data):>5} samples -> {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="10-category classification for firstaidqa_v1.json"
    )
    p.add_argument("--no-semantic", action="store_true",
                   help="Use keyword classifier only for categories (fast)")
    p.add_argument("--add-sc-scores", action="store_true",
                   help="Add NLI SC confidence scores on top of keyword "
                        "categories. Use with --no-semantic. Recommended.")
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    if args.add_sc_scores and not args.no_semantic:
        print("[10cat] WARNING: --add-sc-scores requires --no-semantic. "
              "Adding --no-semantic automatically.")
        args.no_semantic = True

    if args.no_semantic and args.add_sc_scores:
        mode = "keyword (category) + NLI (safety-critical score)  [recommended]"
    elif args.no_semantic:
        mode = "keyword only"
    else:
        mode = "full semantic NLI (cross-encoder/nli-deberta-v3-small)"

    print("=" * 65)
    print("  classify_10cat.py -- 10-category enrichment")
    print("=" * 65)
    print(f"  Classifier : {mode}")
    print(f"  Seed       : {args.seed}")

    # Load raw data
    with open(RAW_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    print(f"[10cat] Loaded {len(raw):,} samples from {os.path.basename(RAW_PATH)}")

    # Classify
    print(f"\n[10cat] Classifying {len(raw):,} samples into 10 categories...")
    if args.no_semantic and args.add_sc_scores:
        enriched = enrich_keyword_with_sc_scores(raw, seed=args.seed)
    elif args.no_semantic:
        enriched = enrich_keyword(raw, seed=args.seed)
    else:
        enriched = enrich_semantic(raw, seed=args.seed)

    # Save enriched dataset
    with open(ENRICHED_10_PATH, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=2, ensure_ascii=False)
    print(f"[10cat] Enriched dataset saved -> {ENRICHED_10_PATH}")

    # Stratified splits
    print("\n[10cat] Generating stratified splits (80 / 10 / 10)...")
    train, val, test = stratified_split(enriched, seed=args.seed)
    save_splits(train, val, test)

    # --- Report ---
    print("\n" + "=" * 65)
    print("  Split summary")
    print("=" * 65)
    total = len(enriched)
    print(f"  Train : {len(train):>5}  ({100*len(train)/total:.1f}%)")
    print(f"  Val   : {len(val):>5}  ({100*len(val)/total:.1f}%)")
    print(f"  Test  : {len(test):>5}  ({100*len(test)/total:.1f}%)")

    print("\n  Category distribution:")
    print(f"  {'Category':<42} {'Train':>6} {'Val':>5} {'Test':>5} {'SC':>4}")
    print("  " + "-" * 62)
    all_cats = sorted(CATEGORY_LABELS_10.keys())
    for cat in all_cats:
        tr = sum(1 for s in train if s["category"] == cat)
        va = sum(1 for s in val   if s["category"] == cat)
        te = sum(1 for s in test  if s["category"] == cat)
        sc = "YES" if cat in SAFETY_CRITICAL_CATEGORIES_10 else ""
        print(f"  {cat:<42} {tr:>6} {va:>5} {te:>5} {sc:>4}")

    sc_train = sum(1 for s in train if s["safety_critical"])
    sc_val   = sum(1 for s in val   if s["safety_critical"])
    sc_test  = sum(1 for s in test  if s["safety_critical"])
    print(f"\n  Safety-critical samples:")
    print(f"    Train: {sc_train}  Val: {sc_val}  Test: {sc_test}")

    if "safety_critical_confidence" in enriched[0]:
        sc_confs = [s["safety_critical_confidence"] for s in enriched]
        print(f"\n  Safety-critical NLI confidence:")
        print(f"    Mean  : {sum(sc_confs)/len(sc_confs):.3f}")
        print(f"    Min   : {min(sc_confs):.3f}")
        print(f"    Max   : {max(sc_confs):.3f}")

    if "category_confidence" in enriched[0]:
        cat_confs = [s["category_confidence"] for s in enriched]
        low_conf  = sum(1 for c in cat_confs if c < 0.10)
        print(f"\n  Category NLI confidence (n={len(cat_confs):,}):")
        print(f"    Mean  : {sum(cat_confs)/len(cat_confs):.3f}")
        print(f"    Min   : {min(cat_confs):.3f}")
        print(f"    <0.10 (keyword fallback): {low_conf} samples")

    # Compare with 18-cat if available
    enriched_18_path = os.path.join(HERE, "firstaidqa_v1_enriched.json")
    if os.path.exists(enriched_18_path):
        with open(enriched_18_path, encoding="utf-8") as f:
            enriched_18 = json.load(f)
        sc_18 = sum(1 for s in enriched_18 if s["safety_critical"])
        sc_10 = sum(1 for s in enriched if s["safety_critical"])
        print(f"\n  Safety-critical count comparison:")
        print(f"    18-category scheme : {sc_18}")
        print(f"    10-category scheme : {sc_10}")

    print()
    print("[10cat] Done.")
    print(f"  To train on these splits:")
    print(f"    python train.py --quant 4bit --splits_dir splits_10cat")
    print("=" * 65)
