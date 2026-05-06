"""
garbage_audit.py  --  Detect and score low-quality model outputs
================================================================
Reads an eval_suite result JSON and scores every answer for:
  - Non-ASCII character ratio
  - Repetitive n-gram ratio  (same trigram repeated)
  - Template artifact presence  (<start_of_turn>, model\n, etc.)
  - Suspiciously short answers  (<15 words)
  - Digit-corrupted words  (e.g. "0utline", "0utright")
  - Answer repeats the question verbatim

Outputs a per-question breakdown and a per-variant summary.

Usage:
  python garbage_audit.py
  python garbage_audit.py --run eval_results/run_20260505_143609.json
"""

import argparse
import json
import os
import re
import unicodedata
from pathlib import Path

HERE        = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(HERE, "eval_results")

# ---------------------------------------------------------------------------
# Individual signal detectors
# ---------------------------------------------------------------------------

def non_ascii_ratio(text: str) -> float:
    """Fraction of characters that are non-ASCII."""
    if not text:
        return 0.0
    non_ascii = sum(1 for c in text if ord(c) > 127)
    return round(non_ascii / len(text), 4)


def non_ascii_count(text: str) -> int:
    return sum(1 for c in text if ord(c) > 127)


def repetition_ratio(text: str, n: int = 3) -> float:
    """
    Fraction of n-grams that are duplicates.
    A value > 0.3 indicates noticeable repetition.
    """
    words = text.lower().split()
    if len(words) < n + 1:
        return 0.0
    grams = [tuple(words[i:i+n]) for i in range(len(words) - n + 1)]
    if not grams:
        return 0.0
    unique = len(set(grams))
    return round(1.0 - unique / len(grams), 4)


TEMPLATE_PATTERNS = [
    r"<start_of_turn>",
    r"<end_of_turn>",
    r"<bos>",
    r"<eos>",
    r"\bmodel\s*\n",
    r"user\s*\n",
    r"\[INST\]",
    r"\[\/INST\]",
    r"### (Human|Assistant|Response|Instruction)",
    r"<\|im_start\|>",
    r"<\|im_end\|>",
]
_template_re = re.compile("|".join(TEMPLATE_PATTERNS), re.IGNORECASE)

def has_template_artifact(text: str) -> bool:
    return bool(_template_re.search(text))


def word_count(text: str) -> int:
    return len(text.split())


DIGIT_CORRUPT_RE = re.compile(r"\b0[a-zA-Z]{2,}")  # "0utline", "0utright", "0ver"

def has_digit_corrupted_words(text: str) -> bool:
    return bool(DIGIT_CORRUPT_RE.search(text))


def question_echoed(question: str, answer: str) -> bool:
    """True if more than 60% of the question's words appear verbatim at the start of the answer."""
    q_words = set(question.lower().split())
    a_start  = " ".join(answer.lower().split()[:len(question.split()) + 5])
    a_words  = set(a_start.split())
    overlap  = len(q_words & a_words)
    return overlap / max(len(q_words), 1) > 0.6


def garbage_score(text: str, question: str) -> dict:
    """
    Compute all signals and return a composite garbage score (0–10).
    Higher = more garbage.
    """
    na_ratio  = non_ascii_ratio(text)
    na_count  = non_ascii_count(text)
    rep_ratio = repetition_ratio(text)
    template  = has_template_artifact(text)
    short     = word_count(text) < 15
    corrupt   = has_digit_corrupted_words(text)
    echoed    = question_echoed(question, text)

    # Weighted composite (max 10)
    score = 0.0
    score += min(na_ratio * 30, 3.0)   # up to 3 pts for non-ASCII density
    score += min(rep_ratio * 5,  2.0)  # up to 2 pts for repetition
    score += 2.0 if template else 0.0  # 2 pts flat for template bleed
    score += 1.0 if short    else 0.0  # 1 pt for suspiciously short
    score += 1.0 if corrupt  else 0.0  # 1 pt for digit-corrupted words
    score += 1.0 if echoed   else 0.0  # 1 pt for echoing the question

    return {
        "garbage_score":    round(min(score, 10.0), 2),
        "non_ascii_ratio":  na_ratio,
        "non_ascii_count":  na_count,
        "repetition_ratio": rep_ratio,
        "template_artifact":template,
        "too_short":        short,
        "digit_corrupted":  corrupt,
        "question_echoed":  echoed,
        "word_count":       word_count(text),
    }


# ---------------------------------------------------------------------------
# Audit a run file
# ---------------------------------------------------------------------------

SEVERITY_THRESHOLD = 2.0   # score >= this = flagged as garbage

def audit_run(run_path: str):
    with open(run_path, encoding="utf-8") as f:
        data = json.load(f)

    run_id = data.get("run_id", "unknown")
    print(f"\n{'='*70}")
    print(f"  GARBAGE AUDIT  —  Run {run_id}")
    print(f"{'='*70}")

    variant_summaries = []

    for result in data["results"]:
        label   = result["label"]
        answers = result["answers"]

        scored  = []
        flagged = []

        for a in answers:
            text     = a.get("answer", "") or ""
            question = a.get("question", "")
            signals  = garbage_score(text, question)
            signals["question_id"] = a["question_id"]
            signals["question"]    = question[:80]
            signals["answer_preview"] = text[:100].replace("\n", " ")
            scored.append(signals)
            if signals["garbage_score"] >= SEVERITY_THRESHOLD:
                flagged.append(signals)

        mean_gs   = sum(s["garbage_score"]    for s in scored) / len(scored)
        mean_rep  = sum(s["repetition_ratio"] for s in scored) / len(scored)
        mean_na   = sum(s["non_ascii_ratio"]  for s in scored) / len(scored)
        n_template = sum(1 for s in scored if s["template_artifact"])
        n_short    = sum(1 for s in scored if s["too_short"])
        n_corrupt  = sum(1 for s in scored if s["digit_corrupted"])
        n_flagged  = len(flagged)

        variant_summaries.append({
            "label":         label,
            "mean_garbage":  round(mean_gs, 2),
            "mean_rep":      round(mean_rep, 4),
            "mean_na_ratio": round(mean_na, 4),
            "n_template":    n_template,
            "n_short":       n_short,
            "n_corrupt":     n_corrupt,
            "n_flagged":     n_flagged,
            "flagged":       flagged,
            "all_scores":    scored,
        })

    # --- Summary table ---
    print(f"\n  {'Variant':<32}  {'GarbageScore':>12}  {'Repetition':>10}  {'Non-ASCII':>9}  {'Template':>8}  {'Short':>5}  {'Corrupt':>7}  {'Flagged':>7}")
    print("  " + "-"*100)
    for v in variant_summaries:
        print(
            f"  {v['label']:<32}  "
            f"{v['mean_garbage']:>12.2f}  "
            f"{v['mean_rep']:>10.4f}  "
            f"{v['mean_na_ratio']:>9.4f}  "
            f"{v['n_template']:>8}  "
            f"{v['n_short']:>5}  "
            f"{v['n_corrupt']:>7}  "
            f"{v['n_flagged']:>7}"
        )

    # --- Per-question flagged answers ---
    print(f"\n  ── Flagged answers (garbage score ≥ {SEVERITY_THRESHOLD}) ──\n")
    any_flagged = False
    for v in variant_summaries:
        if not v["flagged"]:
            continue
        any_flagged = True
        print(f"  [{v['label']}]")
        for f in sorted(v["flagged"], key=lambda x: -x["garbage_score"]):
            print(f"    Q{f['question_id']:>2}  score={f['garbage_score']:.1f}  "
                  f"na={f['non_ascii_ratio']:.3f}  rep={f['repetition_ratio']:.3f}  "
                  f"tmpl={int(f['template_artifact'])}  short={int(f['too_short'])}  "
                  f"corrupt={int(f['digit_corrupted'])}")
            print(f"         Q: {f['question'][:70]}")
            print(f"         A: {f['answer_preview'][:90]}")
        print()

    if not any_flagged:
        print("  No answers flagged.\n")

    # --- Ranking ---
    ranked = sorted(variant_summaries, key=lambda v: v["mean_garbage"], reverse=True)
    print("  ── Garbage ranking (worst → best) ──")
    for i, v in enumerate(ranked, 1):
        print(f"  {i}. {v['label']}  (mean score {v['mean_garbage']:.2f})")

    print(f"\n{'='*70}\n")
    return variant_summaries


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def find_latest_run() -> str | None:
    if not os.path.isdir(RESULTS_DIR):
        return None
    runs = sorted(p for p in Path(RESULTS_DIR).glob("run_*.json"))
    return str(runs[-1]) if runs else None


def main():
    global SEVERITY_THRESHOLD
    p = argparse.ArgumentParser(description="Detect garbage / artifacts in eval_suite outputs")
    p.add_argument("--run", default="", help="Path to run JSON (default: latest in eval_results/)")
    p.add_argument("--threshold", type=float, default=SEVERITY_THRESHOLD,
                   help=f"Garbage score threshold for flagging (default: {SEVERITY_THRESHOLD})")
    args = p.parse_args()

    SEVERITY_THRESHOLD = args.threshold

    run_path = args.run or find_latest_run()
    if not run_path:
        print("No run file found. Run eval_suite.py first.")
        return

    audit_run(run_path)


if __name__ == "__main__":
    main()
