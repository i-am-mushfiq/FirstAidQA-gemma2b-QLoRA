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
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evaluation_protocol import (          # noqa: E402  (needs REPO_ROOT on sys.path)
    PROMPT_POLICY,
    validate_run_prompt_provenance,
)

BANK_PATH  = REPO_ROOT / "evaluations" / "eval_bank_v2_40q" / "eval_bank_v2.json"
JUDGING_DIR = REPO_ROOT / "judging"
ITEMS_PATH  = JUDGING_DIR / "items.jsonl"

#: Answers each config must contribute. Matches the v2 eval bank and
#: verify_camera_ready.EXPECTED_N.
EXPECTED_N = 41
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


def check_prompt_provenance(run_dir: Path, *, allow_unaligned: bool) -> None:
    """
    Refuse to build a judging set from a run not generated under the offline premise.

    This is the check that was missing. The July 2026 camera-ready run was
    generated with the EMS-advising system prompt, then judged against offline
    references and the offline rubric, and nothing in this lane noticed --
    because nothing in this lane ever looked at which prompt produced the
    answers. That run.json records no prompt policy at all.

    validate_run_prompt_provenance() checks the run-level identity (policy name,
    exact text, SHA-256) and the per-answer prompt_policy marker. Empty list
    means aligned.
    """
    run_json = run_dir / "run.json"
    if not run_json.exists():
        # Per-config fallback runs carry no run-level provenance to check, so
        # the premise is unverifiable -- which is a failure, not a pass.
        errors = [f"{run_json} not found; prompt provenance is unverifiable"]
    else:
        with open(run_json, encoding="utf-8") as f:
            errors = validate_run_prompt_provenance(json.load(f))

    if not errors:
        print(f"  Prompt provenance: OK ({PROMPT_POLICY})")
        return

    print("", file=sys.stderr)
    print("-- Prompt provenance: FAIL ------------------------------", file=sys.stderr)
    print(f"  Run: {run_dir}", file=sys.stderr)
    print(f"  Required policy: {PROMPT_POLICY}", file=sys.stderr)
    for err in errors:
        print(f"    - {err}", file=sys.stderr)

    if allow_unaligned:
        print("", file=sys.stderr)
        print("  WARNING: --allow_unaligned_prompt was given. Building items from a",
              file=sys.stderr)
        print("  run whose generation premise does not match the rubric. Scores from",
              file=sys.stderr)
        print("  this item set MUST NOT be published as an offline evaluation.",
              file=sys.stderr)
        print("", file=sys.stderr)
        return

    print("", file=sys.stderr)
    print("  Refusing to assemble. Regenerate under the canonical offline prompt", file=sys.stderr)
    print("  (python camera_ready/pipeline.py generate), or pass", file=sys.stderr)
    print("  --allow_unaligned_prompt to build a knowingly mismatched item set.", file=sys.stderr)
    print("", file=sys.stderr)
    sys.exit(2)


def validate_items(items: list, bank: dict, configs_present: list) -> bool:
    """
    Gate check: every expected config must have exactly 41 answers, none empty.
    Prints config × count table. Returns True if all pass.

    *configs_present* is the list of configs the run was supposed to contribute.
    It is iterated explicitly: the count table is built only from items that
    exist, so a config that produced ZERO items has no key and, before this,
    could not fail the gate at all. An empty variants["F_RAG_BM25"] printed
    "GATE PASSED" over a five-config items.jsonl.
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
    # Union, so a config that is expected-but-absent and one that is
    # present-but-unexpected are both visible and both fail.
    for cfg in sorted(set(counts) | set(configs_present or [])):
        n       = counts.get(cfg, 0)
        emp     = empties.get(cfg, 0)
        bad     = bad_qids.get(cfg, [])
        ok      = (n == EXPECTED_N and emp == 0 and not bad)
        status  = "OK" if ok else "FAIL"
        if not ok:
            all_ok = False
        note = ""
        if n == 0:
            note = "  <-- expected but contributed NO items"
        elif cfg not in (configs_present or []):
            note = "  <-- present but not in the expected config list"
        print(f"  {cfg:<30}  {n:>4}  {emp:>5}  {status}{note}")
        if bad:
            print(f"    BAD QIDs: {bad}")

    print(f"\n  Total items: {len(items)}")
    return all_ok


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

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
        "--force", action="store_true",
        help="Allow an overwrite that discards planted CTRL_* items "
             "(re-run make_controls.py afterwards to re-plant them)"
    )
    parser.add_argument(
        "--allow_unaligned_prompt", action="store_true",
        help="Build items from a run whose generation prompt does not match the "
             "canonical offline policy. Emits a loud warning; scores from such an "
             "item set must not be published as an offline evaluation."
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

    # ── Prompt provenance gate (before any other work) ───────────────────────
    print(f"Checking prompt provenance for {run_dir}")
    check_prompt_provenance(run_dir, allow_unaligned=args.allow_unaligned_prompt)

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

    # ── Validate BEFORE writing anything ─────────────────────────────────────
    # Outputs used to be written first and the gate checked afterwards, which
    # left a truncated blind_map.json and items.jsonl on disk after a failed
    # gate, indistinguishable from good ones to the next stage.
    ok = validate_items(items, bank, selected_configs)
    if not ok:
        print("\nGATE FAILED: not every expected config has "
              f"{EXPECTED_N} valid answers. Nothing was written.")
        sys.exit(1)

    # ── Refuse to silently discard the planted control items ─────────────────
    # make_controls.py always appends, so a default (non-append) run truncates
    # items.jsonl and removes all CTRL_* rows while controls_key.json keeps its
    # entries. aggregate.py then reports "0/45 controls within expected range".
    if not args.append and out_path.exists():
        existing_controls = 0
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    if json.loads(line).get("config", "").startswith("CTRL_"):
                        existing_controls += 1
                except json.JSONDecodeError:
                    continue
        if existing_controls and not args.force:
            print(f"\nREFUSING to overwrite {out_path}: it holds "
                  f"{existing_controls} planted control item(s) that this run "
                  f"does not regenerate.\n"
                  f"  Controls come from make_controls.py, which appends.\n"
                  f"  Either re-run this with --force and then re-run "
                  f"make_controls.py to re-plant them,\n"
                  f"  or pass --append to add to the existing file.")
            sys.exit(1)
        if existing_controls:
            print(f"\nWARNING: --force given; dropping {existing_controls} "
                  f"control item(s). Re-run make_controls.py before judging.")

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

    print(f"\nGATE PASSED  All expected configs have {EXPECTED_N} valid answers, "
          f"all qids join to bank.")


if __name__ == "__main__":
    main()
