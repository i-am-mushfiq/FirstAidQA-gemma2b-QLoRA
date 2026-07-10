"""
judging/assemble_items.py
=========================
Phase 1 assembler: produces judging/items.jsonl

One line per (qid, config) with fields:
  qid, config, blind_id, question, reference, sc_flag, category, answer

Usage
-----
    python judging/assemble_items.py \\
        --run_dir evaluations/CAMERA_READY_20260708_180411 \\
        --configs all

    python judging/assemble_items.py \\
        --run_dir evaluations/v2_comprehensive_20260606_200713 \\
        --configs A_BASE_4BIT B_FINETUNED_4BIT

    # Append (for control items written by make_controls.py):
    python judging/assemble_items.py --run_dir ... --append

Output
------
    judging/items.jsonl          -- one JSON object per line
    judging/blind_map.json       -- mapping blind_id -> config name (kept locally,
                                    NEVER included in released artifacts)
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
REPO_ROOT  = Path(__file__).resolve().parent.parent
BANK_PATH  = REPO_ROOT / "evaluations" / "eval_bank_v2_40q" / "eval_bank_v2.json"
JUDGING_DIR = REPO_ROOT / "judging"
ITEMS_PATH  = JUDGING_DIR / "items.jsonl"
BLIND_MAP_PATH = JUDGING_DIR / "blind_map.json"

# Salt for blind IDs — stable across runs, not a secret.
# Purpose: prevent config names appearing in judge prompts by accident.
BLIND_SALT = "first_aid_v2_judging_2026"


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_blind_id(config: str) -> str:
    """Stable opaque ID for a config name (salted SHA256, first 12 hex chars)."""
    raw = f"{BLIND_SALT}:{config}"
    return "BID_" + hashlib.sha256(raw.encode()).hexdigest()[:12].upper()


def load_bank() -> dict:
    """Load eval bank; return dict keyed by question_id."""
    with open(BANK_PATH, encoding="utf-8") as f:
        bank = json.load(f)
    return {item["question_id"]: item for item in bank}


def load_run(run_dir: Path) -> dict:
    """
    Load answers from a run directory.
    Returns dict: config_name -> list of answer dicts.
    Prefers run.json (single file); falls back to per-config JSONs.
    """
    run_json = run_dir / "run.json"
    if run_json.exists():
        with open(run_json, encoding="utf-8") as f:
            run = json.load(f)
        variants = run.get("variants", {})
        return {cfg: v["answers"] for cfg, v in variants.items()}

    # Fallback: scan per-config JSON files
    result = {}
    for p in sorted(run_dir.glob("*.json")):
        if p.stem in ("run", "metrics"):
            continue
        with open(p, encoding="utf-8") as f:
            cfg_data = json.load(f)
        config = cfg_data.get("config", p.stem)
        result[config] = cfg_data.get("answers", [])
    return result


def validate_items(items: list, bank: dict, configs_present: list) -> bool:
    """
    Gate check: every config must have exactly 41 answers, no empty strings.
    Prints config × count table. Returns True if all pass.
    """
    from collections import defaultdict
    counts   = defaultdict(int)
    empties  = defaultdict(int)
    bad_qids = defaultdict(list)

    for item in items:
        cfg = item["config"]
        counts[cfg] += 1
        if not item["answer"].strip():
            empties[cfg] += 1
        if item["qid"] not in bank:
            bad_qids[cfg].append(item["qid"])

    print("\n── Config × count table ─────────────────────────────────")
    print(f"  {'Config':<30}  {'N':>4}  {'Empty':>5}  {'Status'}")
    print(f"  {'-'*30}  {'-'*4}  {'-'*5}  {'-'*6}")

    all_ok = True
    for cfg in sorted(counts.keys()):
        n       = counts[cfg]
        emp     = empties.get(cfg, 0)
        bad     = bad_qids.get(cfg, [])
        ok      = (n == 41 and emp == 0 and not bad)
        status  = "OK" if ok else "FAIL"
        if not ok:
            all_ok = False
        print(f"  {cfg:<30}  {n:>4}  {emp:>5}  {status}")
        if bad:
            print(f"    BAD QIDs: {bad}")

    print(f"\n  Total items: {len(items)}")
    return all_ok


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Assemble (qid, config) items for per-item judging"
    )
    parser.add_argument(
        "--run_dir", required=True,
        help="Path to evaluation run directory (absolute or relative to repo root)"
    )
    parser.add_argument(
        "--configs", nargs="+", default=["all"],
        help="Config names to include, or 'all' (default). E.g. A_BASE_4BIT B_FINETUNED_4BIT"
    )
    parser.add_argument(
        "--append", action="store_true",
        help="Append to existing items.jsonl instead of overwriting"
    )
    parser.add_argument(
        "--out", default=str(ITEMS_PATH),
        help=f"Output path for items.jsonl (default: {ITEMS_PATH})"
    )
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    if not run_dir.is_absolute():
        run_dir = REPO_ROOT / run_dir
    if not run_dir.exists():
        print(f"ERROR: run_dir not found: {run_dir}", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.out)

    # ── Load bank ────────────────────────────────────────────────────────────
    print(f"Loading eval bank from {BANK_PATH}")
    bank = load_bank()
    print(f"  {len(bank)} questions")

    # ── Load run ─────────────────────────────────────────────────────────────
    print(f"Loading run from {run_dir}")
    all_variants = load_run(run_dir)
    available_configs = sorted(all_variants.keys())
    print(f"  Configs found: {available_configs}")

    # ── Filter configs ───────────────────────────────────────────────────────
    if args.configs == ["all"] or args.configs == ["ALL"]:
        selected_configs = available_configs
    else:
        selected_configs = []
        for c in args.configs:
            if c in all_variants:
                selected_configs.append(c)
            else:
                print(f"  WARNING: config '{c}' not found in run dir (skipping)")
    print(f"  Selected configs: {selected_configs}")

    # ── Load existing blind map (or create fresh) ─────────────────────────────
    blind_map: dict = {}
    if BLIND_MAP_PATH.exists():
        with open(BLIND_MAP_PATH, encoding="utf-8") as f:
            blind_map = json.load(f)
        # blind_map stores bid -> config; build reverse for lookup
        cfg_to_bid = {v: k for k, v in blind_map.items()}
    else:
        cfg_to_bid = {}

    # ── Assemble items ───────────────────────────────────────────────────────
    items = []
    missing_bank = []

    for config in selected_configs:
        answers = all_variants[config]

        # Ensure stable blind ID for this config
        if config not in cfg_to_bid:
            bid = make_blind_id(config)
            cfg_to_bid[config] = bid
            blind_map[bid] = config

        bid = cfg_to_bid[config]

        for ans in answers:
            qid = ans["question_id"]
            if qid not in bank:
                missing_bank.append((config, qid))
                continue

            bank_entry = bank[qid]
            item = {
                "qid":       qid,
                "config":    config,
                "blind_id":  bid,
                "question":  bank_entry["question"],
                "reference": bank_entry["reference"],
                "sc_flag":   bank_entry["safety_critical"],
                "category":  bank_entry["category"],
                "answer":    ans["answer"],
            }
            items.append(item)

    if missing_bank:
        print(f"\nWARNING: {len(missing_bank)} answers had no matching bank entry:")
        for cfg, qid in missing_bank[:10]:
            print(f"  {cfg} / {qid}")

    # ── Validate ─────────────────────────────────────────────────────────────
    ok = validate_items(items, bank, selected_configs)

    # ── Write blind map ───────────────────────────────────────────────────────
    JUDGING_DIR.mkdir(exist_ok=True)
    with open(BLIND_MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(blind_map, f, indent=2)
    print(f"\nBlind map written: {BLIND_MAP_PATH}  ({len(blind_map)} entries)")

    # ── Write items.jsonl ─────────────────────────────────────────────────────
    mode = "a" if args.append else "w"
    with open(out_path, mode, encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    action = "Appended" if args.append else "Wrote"
    print(f"{action} {len(items)} items to {out_path}")

    if not ok:
        print("\nGATE FAILED: not all configs have 41 valid answers. Fix before proceeding.")
        sys.exit(1)

    print("\nGATE PASSED ✓  All configs have 41 valid answers, all qids join to bank.")


if __name__ == "__main__":
    main()
