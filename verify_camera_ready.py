"""
verify_camera_ready.py
======================
Post-run verification for the camera-ready eval run.

Checks (all must pass before the run is tagged CAMERA_READY):
  1. Expected config count: 6 configs present (A, B, C, E, F, G)
  2. Question count: each config has exactly 41 answers
  3. No empty generations: every answer is non-empty after strip()
  4. SC flag patch: V2Q10=False, V2Q13=True, V2Q14=True, V2Q34=True
  5. BM25 gate metadata: every F/G answer has bm25_fired + bm25_skipped_gap
  6. V2Q35 gate: V2Q35 is gap-gated in both F and G (tourniquet_escalation)
  7. V2Q41 gate: V2Q41 is gap-gated in both F and G (spinal_logroll)
  8. No F/G answer has meta.retrieved (old top-3 list format -- wrong class)
  9. 7-config sanity table printed (D shows N/A)

Usage
-----
  python verify_camera_ready.py --run_dir evaluations/CAMERA_READY_<timestamp>
  python verify_camera_ready.py           (auto-detects most recent CAMERA_READY_* dir)
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_DIR = os.path.join(HERE, "evaluations")

EXPECTED_CONFIGS = {
    "A_BASE_4BIT", "B_FINETUNED_4BIT", "C_FINETUNED_8BIT",
    "E_T6_IMPROVED", "F_RAG_BM25", "G_BASE_RAG",
}
EXCLUDED_CONFIGS = {"D_T4_IMPROVED"}

EXPECTED_N = 41

# SC patches applied to eval_bank_v2.json after June 2026 run
SC_PATCHES = {
    "V2Q10": False,
    "V2Q13": True,
    "V2Q14": True,
    "V2Q34": True,
}

# Questions that must be topic-gated in F and G
MUST_GATE = {
    "V2Q35": "tourniquet_escalation",
    "V2Q41": "spinal_logroll",
}


def find_run_dir():
    """Find the most recent CAMERA_READY_* directory."""
    candidates = [
        d for d in os.listdir(EVAL_DIR)
        if d.startswith("CAMERA_READY_") and os.path.isdir(os.path.join(EVAL_DIR, d))
    ]
    if not candidates:
        return None
    return os.path.join(EVAL_DIR, sorted(candidates)[-1])


def check(condition, msg_pass, msg_fail, errors):
    if condition:
        print(f"  PASS  {msg_pass}")
    else:
        print(f"  FAIL  {msg_fail}")
        errors.append(msg_fail)


def verify(run_dir: str) -> int:
    """Returns 0 on all-pass, number of failures otherwise."""
    run_json = os.path.join(run_dir, "run.json")
    if not os.path.exists(run_json):
        print(f"ERROR: run.json not found at {run_json}")
        return 1

    with open(run_json, encoding="utf-8") as f:
        run = json.load(f)

    variants = run.get("variants", {})
    errors = []

    print(f"\n{'='*70}")
    print(f"  CAMERA-READY VERIFICATION")
    print(f"  Run dir : {run_dir}")
    print(f"  Run at  : {run.get('run_at', 'unknown')}")
    print(f"{'='*70}\n")

    # ------------------------------------------------------------------
    # Check 1: Expected configs present
    # ------------------------------------------------------------------
    print("1. Config presence")
    found = set(variants.keys())
    missing = EXPECTED_CONFIGS - found
    extra   = found - EXPECTED_CONFIGS - EXCLUDED_CONFIGS
    check(not missing,
          f"All 6 expected configs present: {sorted(found)}",
          f"Missing configs: {sorted(missing)}", errors)
    check(not extra,
          "No unexpected configs",
          f"Unexpected configs: {sorted(extra)}", errors)
    print()

    # ------------------------------------------------------------------
    # Check 2: Question counts
    # ------------------------------------------------------------------
    print("2. Answer counts")
    for cfg in sorted(found):
        answers = variants[cfg].get("answers", [])
        n = len(answers)
        check(n == EXPECTED_N,
              f"{cfg}: n={n}",
              f"{cfg}: expected {EXPECTED_N}, got {n}", errors)
    print()

    # ------------------------------------------------------------------
    # Check 3: No empty generations
    # ------------------------------------------------------------------
    print("3. Empty generation check")
    for cfg in sorted(found):
        answers = variants[cfg].get("answers", [])
        empties = [a["question_id"] for a in answers
                   if not a.get("answer", "").strip()]
        check(not empties,
              f"{cfg}: 0 empty answers",
              f"{cfg}: empty answers at {empties}", errors)
    print()

    # ------------------------------------------------------------------
    # Check 4: SC flag patches
    # ------------------------------------------------------------------
    print("4. SC flag patch verification")
    ref_cfg = "A_BASE_4BIT" if "A_BASE_4BIT" in variants else sorted(found)[0]
    answers_by_id = {a["question_id"]: a for a in variants[ref_cfg].get("answers", [])}
    for qid, expected_sc in SC_PATCHES.items():
        if qid in answers_by_id:
            actual_sc = answers_by_id[qid].get("safety_critical")
            check(actual_sc == expected_sc,
                  f"{qid}: SC={expected_sc}",
                  f"{qid}: expected SC={expected_sc}, got {actual_sc}", errors)
        else:
            print(f"  WARN  {qid} not found in answers")
    print()

    # ------------------------------------------------------------------
    # Check 5: BM25 gate metadata in F and G
    # ------------------------------------------------------------------
    print("5. BM25 gate metadata (F and G)")
    for cfg in ["F_RAG_BM25", "G_BASE_RAG"]:
        if cfg not in variants:
            print(f"  SKIP  {cfg} not in run")
            continue
        answers = variants[cfg].get("answers", [])
        missing_fired = [a["question_id"] for a in answers
                         if "bm25_fired" not in a.get("meta", {})]
        missing_skipped = [a["question_id"] for a in answers
                           if "bm25_skipped_gap" not in a.get("meta", {})]
        has_old_format = [a["question_id"] for a in answers
                          if "retrieved" in a.get("meta", {})
                          and isinstance(a["meta"]["retrieved"], list)]
        check(not missing_fired,
              f"{cfg}: bm25_fired present in all {len(answers)} answers",
              f"{cfg}: bm25_fired missing in {missing_fired}", errors)
        check(not missing_skipped,
              f"{cfg}: bm25_skipped_gap present in all {len(answers)} answers",
              f"{cfg}: bm25_skipped_gap missing in {missing_skipped}", errors)
        check(not has_old_format,
              f"{cfg}: no old top-3 'retrieved' list format",
              f"{cfg}: old retrieved-list format found (wrong class used): {has_old_format}", errors)
    print()

    # ------------------------------------------------------------------
    # Check 6+7: Must-gate questions
    # ------------------------------------------------------------------
    print("6+7. Topic-gate assertions (V2Q35 and V2Q41)")
    for cfg in ["F_RAG_BM25", "G_BASE_RAG"]:
        if cfg not in variants:
            print(f"  SKIP  {cfg} not in run")
            continue
        answers_by_qid = {a["question_id"]: a for a in variants[cfg].get("answers", [])}
        for qid, expected_topic in MUST_GATE.items():
            if qid not in answers_by_qid:
                print(f"  WARN  {qid} not in {cfg} answers")
                continue
            meta = answers_by_qid[qid].get("meta", {})
            was_gated = meta.get("bm25_skipped_gap", False)
            actual_topic = meta.get("gap_topic", None)
            check(was_gated and actual_topic == expected_topic,
                  f"{cfg} {qid}: gated as {expected_topic}",
                  f"{cfg} {qid}: expected gate={expected_topic}, "
                  f"got skipped={was_gated} topic={actual_topic}", errors)
    print()

    # ------------------------------------------------------------------
    # Sanity table
    # ------------------------------------------------------------------
    print("Sanity table (7 configs)")
    print(f"  {'Config':<24} {'n':>4} {'empty':>6} {'SC':>4} "
          f"{'fired':>6} {'gated':>6}")
    print("  " + "-" * 55)
    for cfg in ["A_BASE_4BIT", "B_FINETUNED_4BIT", "C_FINETUNED_8BIT",
                "D_T4_IMPROVED", "E_T6_IMPROVED", "F_RAG_BM25", "G_BASE_RAG"]:
        if cfg not in variants:
            print(f"  {cfg:<24} {'N/A':>4}")
            continue
        answers = variants[cfg].get("answers", [])
        n       = len(answers)
        empty   = sum(1 for a in answers if not a.get("answer", "").strip())
        sc      = sum(1 for a in answers if a.get("safety_critical"))
        fired   = sum(1 for a in answers if a.get("meta", {}).get("bm25_fired", False))
        gated   = sum(1 for a in answers if a.get("meta", {}).get("bm25_skipped_gap", False))
        print(f"  {cfg:<24} {n:>4} {empty:>6} {sc:>4} {fired:>6} {gated:>6}")
    print()

    # ------------------------------------------------------------------
    # Final verdict
    # ------------------------------------------------------------------
    print("=" * 70)
    if not errors:
        print(f"  VERDICT: ALL CHECKS PASSED ({len(EXPECTED_CONFIGS)} configs x {EXPECTED_N} questions)")
        print(f"  This run is cleared for CAMERA_READY tagging.")
    else:
        print(f"  VERDICT: {len(errors)} CHECK(S) FAILED")
        for i, e in enumerate(errors, 1):
            print(f"  [{i}] {e}")
    print("=" * 70)
    return len(errors)


def parse_args():
    p = argparse.ArgumentParser(description="Camera-ready run verification")
    p.add_argument("--run_dir", default=None,
                   help="Path to run dir (default: auto-detect most recent CAMERA_READY_*)")
    return p.parse_args()


if __name__ == "__main__":
    args    = parse_args()
    run_dir = args.run_dir
    if not run_dir:
        run_dir = find_run_dir()
        if not run_dir:
            print(f"ERROR: No CAMERA_READY_* directory found in {EVAL_DIR}")
            sys.exit(1)
        print(f"[auto] Using most recent run: {os.path.basename(run_dir)}")
    sys.exit(verify(run_dir))
