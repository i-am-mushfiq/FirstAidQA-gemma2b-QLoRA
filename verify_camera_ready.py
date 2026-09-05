"""
verify_camera_ready.py
======================
Post-run verification for the camera-ready eval run.

Checks include the prompt policy, frozen question-bank hash, model/adapter content
fingerprints and mapping, committed code revision, exact 6-config/41-question
matrix, non-empty answers, SC patches, and the current gated top-1 BM25 schema.

Usage
-----
  python verify_camera_ready.py --run_dir evaluations/CAMERA_READY_OFFLINE_<timestamp>
  python verify_camera_ready.py           (auto-detects most recent CAMERA_READY_* dir)
"""

import argparse
import json
import os
import re
import sys

from evaluation_protocol import (
    CAMERA_READY_CONFIG_RESOLUTION,
    CAMERA_SOURCE_FILES,
    PROMPT_POLICY,
    artifact_fingerprint,
    latest_run_dir,
    validate_camera_config_resolution,
    validate_run_prompt_provenance,
    validate_run_question_provenance,
)
from build_v2_judge_prompt import RUBRIC

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_DIR = os.path.join(HERE, "evaluations")

EXPECTED_CONFIGS = set(CAMERA_READY_CONFIG_RESOLUTION)
# D_T4_IMPROVED was excluded from the July run and is now in scope: the
# length-floor technique had never been isolated, so it was an untested claim.
# Empty rather than deleted -- the set is still the mechanism for tolerating a
# config that appears in a run but is not part of the protocol.
EXCLUDED_CONFIGS: set[str] = set()

def _expected_n_from_manifest(default: int = 41) -> int:
    """
    Read the expected question count from the camera-ready manifest.

    The count lives in camera_ready/protocol.yaml as the single source of
    truth; hardcoding it here meant the verifier and the manifest could
    disagree without anything noticing.
    """
    manifest = os.path.join(HERE, "camera_ready", "protocol.yaml")
    try:
        with open(manifest, encoding="utf-8") as handle:
            value = json.load(handle)["verification"]["expected_questions"]
        return int(value)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        print(f"  WARN  could not read expected_questions from {manifest}; "
              f"falling back to {default}")
        return default


EXPECTED_N = _expected_n_from_manifest()
EXPECTED_PROMPT_POLICY = PROMPT_POLICY

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
    """Find the most recent CAMERA_READY_* run directory (never an _ANALYSIS sibling)."""
    run_dir = latest_run_dir(EVAL_DIR)
    return str(run_dir) if run_dir is not None else None


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
    # Check 1: Prompt/rubric premise alignment and provenance
    # ------------------------------------------------------------------
    print("1. Prompt policy and provenance")
    prompt_errors = validate_run_prompt_provenance(run)
    check(not prompt_errors,
          f"Prompt text, policy, and SHA-256 match {EXPECTED_PROMPT_POLICY}",
          "; ".join(prompt_errors), errors)
    print()

    print("2. Frozen question bank and model/config provenance")
    question_errors = validate_run_question_provenance(run)
    check(not question_errors,
          "Question text, references, categories, and SC labels are frozen consistently",
          "; ".join(question_errors), errors)
    resolution_errors = validate_camera_config_resolution(run)
    check(not resolution_errors,
          "Model/adapter/technique mapping matches the canonical camera configuration",
          "; ".join(resolution_errors), errors)
    for name, recorded in sorted(run.get("run_args", {}).get("_artifacts", {}).items()):
        try:
            current = artifact_fingerprint(recorded["path"])
            matches = current["sha256"] == recorded.get("sha256")
            check(matches,
                  f"{name}: content fingerprint verified",
                  f"{name}: content differs from recorded fingerprint", errors)
        except (KeyError, OSError, ValueError) as exc:
            check(False, "", f"{name}: cannot verify artifact: {exc}", errors)
    code_meta = run.get("run_args", {}).get("_code", {})
    code_ok = (
        bool(code_meta.get("git_commit"))
        and code_meta.get("source_tree_clean") is True
        and code_meta.get("source_files") == CAMERA_SOURCE_FILES
    )
    check(code_ok,
          f"Generation code was committed and clean at {code_meta.get('git_commit', '')[:12]}",
          "Generation code commit is missing or camera-ready sources were not clean", errors)
    rubric_path = os.path.join(HERE, "rubric_v2.md")
    with open(rubric_path, encoding="utf-8") as handle:
        rubric_doc = handle.read()
    check(RUBRIC.strip() in rubric_doc,
          "Documented final rubric exactly contains the runtime rubric",
          "rubric_v2.md final rubric differs from the runtime judge rubric", errors)
    print()

    # ------------------------------------------------------------------
    # Check 2: Expected configs present
    # ------------------------------------------------------------------
    print("3. Config presence")
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
    # Check 3: Question counts and identity
    # ------------------------------------------------------------------
    print("4. Answer counts and identity")
    expected_ids = {f"V2Q{i:02d}" for i in range(1, EXPECTED_N + 1)}
    for cfg in sorted(found):
        answers = variants[cfg].get("answers", [])
        n = len(answers)
        ids = [a.get("question_id") for a in answers]
        check(n == EXPECTED_N,
              f"{cfg}: n={n}",
              f"{cfg}: expected {EXPECTED_N}, got {n}", errors)
        check(set(ids) == expected_ids and len(ids) == len(set(ids)),
              f"{cfg}: all expected question IDs present exactly once",
              f"{cfg}: missing, extra, or duplicate question IDs", errors)
    print()

    # ------------------------------------------------------------------
    # Check 4: No empty generations
    # ------------------------------------------------------------------
    print("5. Empty generation check")
    for cfg in sorted(found):
        answers = variants[cfg].get("answers", [])
        empties = [a["question_id"] for a in answers
                   if not a.get("answer", "").strip()]
        check(not empties,
              f"{cfg}: 0 empty answers",
              f"{cfg}: empty answers at {empties}", errors)
    print()

    # ------------------------------------------------------------------
    # Check 5: SC flag patches
    # ------------------------------------------------------------------
    print("6. SC flag patch verification")
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
    # Check 6: BM25 gate metadata in F and G
    # ------------------------------------------------------------------
    print("7. BM25 gate metadata (F and G)")
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
        bad_scores = [a["question_id"] for a in answers
                      if a.get("meta", {}).get("bm25_fired")
                      and (a.get("meta", {}).get("retrieved_score_kind") != "bm25_raw"
                           or a.get("meta", {}).get("retrieved_score", 0) <= 0)]
        check(not missing_fired,
              f"{cfg}: bm25_fired present in all {len(answers)} answers",
              f"{cfg}: bm25_fired missing in {missing_fired}", errors)
        check(not missing_skipped,
              f"{cfg}: bm25_skipped_gap present in all {len(answers)} answers",
              f"{cfg}: bm25_skipped_gap missing in {missing_skipped}", errors)
        check(not has_old_format,
              f"{cfg}: no old top-3 'retrieved' list format",
              f"{cfg}: old retrieved-list format found (wrong class used): {has_old_format}", errors)
        check(not bad_scores,
              f"{cfg}: fired retrievals have positive raw BM25 scores",
              f"{cfg}: invalid retrieval score metadata at {bad_scores}", errors)
    print()

    # ------------------------------------------------------------------
    # Check 7: Must-gate questions
    # ------------------------------------------------------------------
    print("8. Topic-gate assertions (V2Q35 and V2Q41)")
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
    print("Sanity table (6 camera-ready configs; D shown as excluded)")
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
