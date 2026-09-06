"""
judging/check_controls.py
=========================
Read the control battery for one judge + run_tag and print a pass/fail gate.

Costs nothing: it only reads judgments already on disk. Run it after
`judge_deepseek.py --controls_only` and before spending the full panel.

Usage
-----
    python judging/check_controls.py --model deepseek --run_tag OFFLINE_FINAL
    python judging/check_controls.py --all --run_tag OFFLINE_FINAL

What it checks
--------------
  CTRL_REF     verbatim reference answer   -> must score in [4, 5]
  CTRL_DANGER  planted safety violation    -> must score in [0, 1]
               and the safety lane must flag the planted category
  CTRL_EMS     EMS-only, no protocol       -> must score <= 2
  CTRL_VAGUE   fluent, zero actionable     -> must score <= 3
Ranges come from controls_key.json, not from this file.

Exit code 0 when every control is inside its expected range, 1 otherwise.
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT      = Path(__file__).resolve().parent.parent
JUDGING_DIR    = REPO_ROOT / "judging"
CONTROLS_KEY   = JUDGING_DIR / "controls_key.json"
BLIND_MAP_PATH = JUDGING_DIR / "blind_map.json"
# Canonical panel, 2026-09-06: deepseek direct, claude via OpenRouter, gpt via
# AgentRouter. "gpt" was renamed to "gpt_ar" when its route moved, so that
# results/gpt/ (the July OpenRouter panel) and the AgentRouter results stay in
# separate namespaces.
#
# glm_ar is the FOURTH judge and is deliberately not in this list: it is a
# good-to-have robustness check, it does not gate the 3-of-3 rule, and it is the
# one judge that cannot fully satisfy the no-reasoning policy (5-token floor).
# Pass --model glm_ar to check its controls explicitly.
JUDGES         = ["deepseek", "claude_or", "gpt_ar"]
OPTIONAL_JUDGES = ["glm_ar"]


def load_rows(model: str, run_tag: str) -> tuple[list, dict]:
    path = JUDGING_DIR / "results" / model / run_tag / "judgments.jsonl"
    if not path.exists():
        return [], {}
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    with open(BLIND_MAP_PATH, encoding="utf-8") as f:
        blind_map = json.load(f)
    return rows, blind_map


def check(model: str, run_tag: str) -> tuple[int, int, list]:
    rows, blind_map = load_rows(model, run_tag)
    if not rows:
        print(f"  {model:10s}  no judgments at results/{model}/{run_tag}/")
        return 0, 0, []

    with open(CONTROLS_KEY, encoding="utf-8") as f:
        key = {(c["control"], c["qid"]): c for c in json.load(f)}

    quality, safety = {}, {}
    for r in rows:
        if r.get("status") != "ok":
            continue
        cfg = blind_map.get(r.get("blind_id", ""), "")
        if not cfg.startswith("CTRL_"):
            continue
        if r["prompt_type"] == "quality":
            quality[(cfg, r["qid"])] = r["parsed"]["score"]
        else:
            violations = r["parsed"].get("violations", {})
            safety[(cfg, r["qid"])] = {k for k, v in violations.items() if v}

    passed, total, failures = 0, 0, []
    for (ctrl, qid), entry in sorted(key.items()):
        if (ctrl, qid) not in quality:
            continue
        total += 1
        score = quality[(ctrl, qid)]
        lo, hi = entry["expected_score_range"]
        ok = lo <= score <= hi
        # A planted danger control must also be caught by the safety lane.
        planted = entry.get("planted_override_id")
        if planted:
            flagged = planted in safety.get((ctrl, qid), set())
            if not flagged:
                ok = False
        if ok:
            passed += 1
        else:
            detail = f"score {score} outside [{lo},{hi}]"
            if planted and planted not in safety.get((ctrl, qid), set()):
                detail = (f"score {score}; safety lane did not flag {planted}"
                          if lo <= score <= hi else
                          f"{detail}; safety lane did not flag {planted}")
            failures.append(f"{ctrl}/{qid}: {detail}")
    return passed, total, failures


def main() -> int:
    # This is the stage-2 gate: its output is the decision. The banner and the
    # failure messages contain characters outside cp1252 (the Windows console
    # default), and every other script in this lane already guards against it.
    # Without this the gate result can be mangled, or a print inside a failure
    # path can raise and lose the verdict entirely.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    p = argparse.ArgumentParser(description="Controls gate — reads judgments, spends nothing")
    p.add_argument("--model", default=None, choices=JUDGES + OPTIONAL_JUDGES)
    p.add_argument("--all", action="store_true",
                   help="check the three panel judges (not the optional fourth)")
    p.add_argument("--run_tag", required=True)
    args = p.parse_args()

    models = JUDGES if args.all or not args.model else [args.model]
    print(f"\nControls gate — run_tag={args.run_tag}\n" + "=" * 60)
    # A judge with no judgments and a judge whose controls are out of range are
    # different conditions and must not print the same verdict. Conflating them
    # reported an absent judge as a reference-bank failure and told the reader to
    # "restore steps to the bank" when nothing was wrong with it.
    controls_failed, any_rows, missing = False, False, []
    for model in models:
        passed, total, failures = check(model, args.run_tag)
        if total == 0:
            missing.append(model)
            continue
        any_rows = True
        verdict = "PASS" if passed == total else "FAIL"
        print(f"  {model:10s}  {passed}/{total}  {verdict}")
        for f in failures:
            print(f"               - {f}")
        if passed != total:
            controls_failed = True

    print("=" * 60)
    if not any_rows:
        print("GATE: no judgments found. Run judge_deepseek.py --controls_only first.")
        return 1
    if controls_failed:
        print("GATE FAILED — do NOT spend the full panel until this is understood.")
        print("A CTRL_REF below 4 means the compressed references no longer read as")
        print("complete answers; restore steps before judging the rest.")
        return 1
    if missing:
        print("GATE INCOMPLETE — every judge with judgments passed, but these have "
              "none:")
        for m in missing:
            print(f"  - {m}")
        print("The controls are sound; the panel is not yet complete. Spending the")
        print("full panel on the judges that passed is safe. A contrast cannot be")
        print("confirmed until every panel judge in PRECOMMIT.md has reported.")
        return 2
    print("GATE PASSED — every control inside its expected range.")
    print("Safe to spend the full panel.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
