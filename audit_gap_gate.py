"""
audit_gap_gate.py
=================
Forensic audit of the gap-question gate in Config F (F_RAG_BM25) of
evaluations/v2_comprehensive_20260606_200713/.

Three questions answered:
  1. Did the v2 comprehensive eval apply any gap gate?
  2. If a numeric ID gate had been applied (GAP_QUESTION_IDS = {6,17,21,22,28}),
     which v2 bank questions would have been affected, and were they the right ones?
  3. Which v2 bank questions cover the actual gap topics?

Output
------
  - Printed audit table (stdout)
  - Saved to <run_dir>/audit_gap_gate.txt

Usage
-----
  python audit_gap_gate.py
  python audit_gap_gate.py --run_dir evaluations/v2_comprehensive_20260606_200713
  python audit_gap_gate.py --run_dir <path> --bank <eval_bank_v2.json path>
"""

import argparse
import json
import os
import re
import sys

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_RUN_DIR = os.path.join(
    HERE, "evaluations", "v2_comprehensive_20260606_200713"
)
DEFAULT_BANK = os.path.join(
    HERE, "evaluations", "eval_bank_v2_40q", "eval_bank_v2.json"
)

# ---------------------------------------------------------------------------
# Old ID-based gate (what bm25_rag.py had before this audit)
# ---------------------------------------------------------------------------
OLD_GAP_IDS = frozenset({6, 17, 21, 22, 28})

OLD_GAP_DESCRIPTIONS = {
    6:  "Arterial bleeding / tourniquet placement",
    17: "Shock position (lay flat, elevate legs)",
    21: "Infant choking (back-blow / chest-thrust variant)",
    22: "Embedded object ('do not remove, stabilise')",
    28: "Helmet removal spinal immobilisation protocol",
}

# ---------------------------------------------------------------------------
# New topic patterns (from V2_PIPELINE corpus audit + T4/T6 synthesis)
# ---------------------------------------------------------------------------
GAP_TOPIC_PATTERNS = {
    "infant_choking":          re.compile(r"(infant|baby).{0,40}chok|chok.{0,40}(infant|baby)", re.I),
    "spinal_logroll":          re.compile(r"log.?roll|spinal.{0,30}(move|turn|transport|shift|roll)", re.I),
    "chest_seal":              re.compile(r"chest seal|sucking chest|open chest wound", re.I),
    "tourniquet_escalation":   re.compile(r"tourniquet", re.I),
    "naloxone_opioid":         re.compile(r"naloxone|opioid.{0,20}overdose|overdose.{0,20}opioid", re.I),
    "rescue_breaths_drowning": re.compile(r"rescue breath.{0,30}(child|drown|water)|drown.{0,30}(child|rescue)", re.I),
    "burn_cooling":            re.compile(r"burn.{0,40}cool|cool.{0,40}burn", re.I),
}


def check_topic_gate(question_text):
    """Return list of matching topic keys, or empty list."""
    hits = []
    for topic, pattern in GAP_TOPIC_PATTERNS.items():
        if pattern.search(question_text):
            hits.append(topic)
    return hits


# ---------------------------------------------------------------------------
# Main audit
# ---------------------------------------------------------------------------

def run_audit(run_dir: str, bank_path: str) -> str:
    """
    Returns the full audit report as a string.
    """
    lines = []

    def p(s=""):
        lines.append(s)

    # ------------------------------------------------------------------
    # Load sources
    # ------------------------------------------------------------------
    run_json_path = os.path.join(run_dir, "run.json")
    if not os.path.exists(run_json_path):
        p(f"ERROR: run.json not found at {run_json_path}")
        return "\n".join(lines)

    with open(run_json_path, encoding="utf-8") as f:
        run = json.load(f)

    if not os.path.exists(bank_path):
        p(f"ERROR: eval_bank_v2.json not found at {bank_path}")
        return "\n".join(lines)

    with open(bank_path, encoding="utf-8") as f:
        bank = json.load(f)

    bank_by_id = {q["question_id"]: q for q in bank}

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------
    p("=" * 80)
    p("  GAP-GATE FORENSIC AUDIT")
    p(f"  Run dir : {run_dir}")
    p(f"  Run at  : {run.get('run_at', 'unknown')}")
    p(f"  Config F: F_RAG_BM25")
    p("=" * 80)
    p()

    # ------------------------------------------------------------------
    # Section 1: Did the run apply any gap gate?
    # ------------------------------------------------------------------
    p("SECTION 1 -- GAP GATE PRESENCE IN run.json")
    p("-" * 80)

    f_config_key = "F_RAG_BM25"
    variants = run.get("variants", {})

    if f_config_key not in variants:
        p(f"  Config F ({f_config_key}) NOT FOUND in run.json variants.")
        p(f"  Available: {list(variants.keys())}")
        return "\n".join(lines)

    f_answers = variants[f_config_key]["answers"]
    p(f"  Config F has {len(f_answers)} answers.")
    p()

    # Check meta fields across all F answers
    has_bm25_fired      = sum(1 for a in f_answers if "bm25_fired"       in a.get("meta", {}))
    has_gap_skipped     = sum(1 for a in f_answers if "bm25_skipped_gap" in a.get("meta", {}))
    has_retrieved_field = sum(1 for a in f_answers if "retrieved"         in a.get("meta", {}))
    has_gap_topic       = sum(1 for a in f_answers if "gap_topic"         in a.get("meta", {}))

    p(f"  meta.bm25_fired present       : {has_bm25_fired:3d} / {len(f_answers)}")
    p(f"  meta.bm25_skipped_gap present  : {has_gap_skipped:3d} / {len(f_answers)}")
    p(f"  meta.gap_topic present         : {has_gap_topic:3d} / {len(f_answers)}")
    p(f"  meta.retrieved present         : {has_retrieved_field:3d} / {len(f_answers)}")
    p()

    if has_bm25_fired == 0 and has_gap_skipped == 0:
        p("  FINDING: No gap-gate metadata present in any Config F answer.")
        p("  The v2_comprehensive_eval.py inline BM25Retriever has no gap gate logic.")
        p("  Every question received retrieval regardless of topic.")
        p()
        gate_was_applied = False
    else:
        p("  FINDING: Gap gate WAS applied (metadata found).")
        gate_was_applied = True

    # ------------------------------------------------------------------
    # Section 2: Per-question table
    # ------------------------------------------------------------------
    p("SECTION 2 -- PER-QUESTION TABLE (Config F)")
    p("-" * 80)
    p(f"  {'QID':<8} {'SC':<4} {'OLD-ID-GATE?':<14} {'TOPIC-GATE?':<22} {'RETRIEVED?':<12} {'RETRIEVED_Q (80 chars)'}")
    p(f"  {'-'*7} {'-'*3} {'-'*13} {'-'*21} {'-'*11} {'-'*70}")

    old_id_would_gate_wrong   = []  # gated by old ID but not a gap topic
    old_id_would_miss          = []  # actual gap topic but old ID wouldn't gate
    topic_gate_should_fire     = []  # would fire under new topic gate

    for a in f_answers:
        qid_str  = a["question_id"]
        qid_num  = int(re.sub(r"\D", "", qid_str))
        sc       = "Y" if a["safety_critical"] else " "
        question = a["question"]

        # Old ID gate
        old_would_gate = qid_num in OLD_GAP_IDS
        old_desc = OLD_GAP_DESCRIPTIONS.get(qid_num, "") if old_would_gate else ""

        # New topic gate
        topic_hits = check_topic_gate(question)

        # Retrieval in this run
        meta      = a.get("meta", {})
        retrieved = meta.get("retrieved", [])
        if retrieved:
            ret_q = retrieved[0].get("question", "")[:70]
        elif meta.get("bm25_fired", False):
            ret_q = "(fired, question not stored)"
        else:
            ret_q = "(none)"

        fired_str = f"fired({len(retrieved)})" if retrieved else \
                    ("fired" if meta.get("bm25_fired") else \
                    ("GATED" if meta.get("bm25_skipped_gap") else "unknown"))

        old_gate_str  = "WOULD-GATE" if old_would_gate else "---"
        topic_str     = ",".join(topic_hits) if topic_hits else "---"

        p(f"  {qid_str:<8} {sc:<4} {old_gate_str:<14} {topic_str:<22} {fired_str:<12} {ret_q}")

        if old_would_gate and not topic_hits:
            old_id_would_gate_wrong.append((qid_str, question[:60]))
        if topic_hits and not old_would_gate:
            old_id_would_miss.append((qid_str, question[:60], topic_hits))
        if topic_hits:
            topic_gate_should_fire.append((qid_str, question[:60], topic_hits))

    p()

    # ------------------------------------------------------------------
    # Section 3: Misfire analysis
    # ------------------------------------------------------------------
    p("SECTION 3 -- MISFIRE ANALYSIS")
    p("-" * 80)
    p()

    p("  A. Old ID gate {6,17,21,22,28} cross-referenced against v2 bank:")
    p()
    for old_id in sorted(OLD_GAP_IDS):
        v2_qid  = f"V2Q{old_id:02d}"
        bank_q  = bank_by_id.get(v2_qid, {})
        q_text  = bank_q.get("question", "(not in bank)")[:70]
        topic   = check_topic_gate(bank_q.get("question", ""))
        topic_s = ",".join(topic) if topic else "(no gap topic match)"
        p(f"    Old Q{old_id:02d} -> {v2_qid}:  {q_text}")
        p(f"              Topic gate: {topic_s}")
        p()

    p("  B. Questions old ID gate would have gated INCORRECTLY (not a gap topic):")
    if old_id_would_gate_wrong:
        for qid, q in old_id_would_gate_wrong:
            p(f"    {qid}: {q}")
    else:
        p("    (none)")
    p()

    p("  C. Actual gap-topic questions the old ID gate would have MISSED:")
    if old_id_would_miss:
        for qid, q, topics in old_id_would_miss:
            p(f"    {qid}: {q}  [{', '.join(topics)}]")
    else:
        p("    (none)")
    p()

    p("  D. Questions that should be gated under new topic gate:")
    if topic_gate_should_fire:
        for qid, q, topics in topic_gate_should_fire:
            p(f"    {qid}: {q}  [{', '.join(topics)}]")
    else:
        p("    (none -- no gap topics in this bank)")
    p()

    # ------------------------------------------------------------------
    # Section 4: Verdict
    # ------------------------------------------------------------------
    p("SECTION 4 -- VERDICT")
    p("-" * 80)
    p()

    if not gate_was_applied:
        p("  VERDICT: MISFIRED")
        p()
        p("  Root cause: v2_comprehensive_eval.py defines its own inline BM25Retriever")
        p("  class that takes (query, top_k) with no question_id argument and no gap gate")
        p("  logic. The imported bm25_rag.py module was NOT used for this run. All 41")
        p("  questions in Config F received top-3 BM25 retrieval without any gap checking.")
        p()
        p("  Secondary finding: Even if the old ID gate had been applied, it would have")
        p(f"  gated {len(old_id_would_gate_wrong)} non-gap questions (V2Q06, V2Q17, V2Q21, V2Q22, V2Q28 -- all wrong)")
        p(f"  and missed {len(old_id_would_miss)} actual gap-topic questions in the v2 bank.")
        p()
        p("  Required fix: replace ID gate with topic gate in bm25_rag.py; update")
        p("  v2_comprehensive_eval.py to import and use bm25_rag.BM25Retriever instead")
        p("  of the inline class. Switch from top-3 to top-1 retrieval.")
    else:
        p("  VERDICT: GATE WAS APPLIED (see per-question table for correctness)")

    p()
    p("=" * 80)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Gap-gate forensic audit for v2 comprehensive run")
    p.add_argument("--run_dir", default=DEFAULT_RUN_DIR,
                   help=f"Path to run directory (default: {DEFAULT_RUN_DIR})")
    p.add_argument("--bank",    default=DEFAULT_BANK,
                   help=f"Path to eval_bank_v2.json (default: {DEFAULT_BANK})")
    p.add_argument("--out",     default=None,
                   help="Output file path (default: <run_dir>/audit_gap_gate.txt)")
    return p.parse_args()


if __name__ == "__main__":
    args     = parse_args()
    report   = run_audit(args.run_dir, args.bank)
    out_path = args.out or os.path.join(args.run_dir, "audit_gap_gate.txt")

    print(report)

    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="ascii", errors="replace") as f:
            f.write(report)
        print(f"\n[audit] Saved to {out_path}")
    except Exception as e:
        print(f"\n[audit] WARNING: could not save to {out_path}: {e}", file=sys.stderr)
