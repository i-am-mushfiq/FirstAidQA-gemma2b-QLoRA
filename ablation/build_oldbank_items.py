"""
Build a judging item set that pairs the OFFLINE run's answers with the OLD
(pre-compression) reference bank.

Purpose: isolate the effect of the reference revision. The offline judging round
changed three things at once -- the generation prompt, the reference bank, and
the rubric. This holds answers and rubric fixed and varies only the references,
so the difference in scores is attributable to the bank alone.

  OFFLINE_FINAL      offline answers + NEW refs (median 54 words) + rubric v5
  OLDBANK_ABLATION   offline answers + OLD refs (median 102 words) + rubric v5

assemble_items.py deliberately refuses this: check_bank_alignment() exits when
the run's embedded references differ from the live bank, which is exactly the
condition being created here. That guard is correct for the production lane and
is not modified; this script writes the ablation item set directly instead.

Usage (from repo root), with the OLD bank already copied over the live bank so
judge_deepseek's freshness check agrees:

    python ablation/build_oldbank_items.py

Restore afterwards with:

    git checkout evaluations/eval_bank_v2_40q/eval_bank_v2.json \
                 judging/items.jsonl judging/items_manifest.json
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "judging"))

from evaluation_protocol import question_bank_metadata          # noqa: E402
from assemble_items import make_blind_id                        # noqa: E402

RUN_DIR = REPO_ROOT / "evaluations" / "CAMERA_READY_OFFLINE_20260905_204533"
BANK_PATH = REPO_ROOT / "evaluations" / "eval_bank_v2_40q" / "eval_bank_v2.json"
ITEMS_PATH = REPO_ROOT / "judging" / "items.jsonl"
MANIFEST_PATH = REPO_ROOT / "judging" / "items_manifest.json"
EXPECTED_N = 41


def main() -> int:
    bank_list = json.loads(BANK_PATH.read_text(encoding="utf-8"))
    bank = {q["question_id"]: q for q in bank_list}

    import statistics
    median_words = statistics.median(
        len(q["reference"].split()) for q in bank_list
    )
    print(f"Live bank: {len(bank)} questions, median reference {median_words:.0f} words")
    if median_words < 80:
        print("REFUSING: the live bank looks like the NEW (compressed) one.\n"
              "  Copy the old bank over it first:\n"
              "    git show 5a6199b^:evaluations/eval_bank_v2_40q/eval_bank_v2.json \\\n"
              "        > evaluations/eval_bank_v2_40q/eval_bank_v2.json",
              file=sys.stderr)
        return 2

    run = json.loads((RUN_DIR / "run.json").read_text(encoding="utf-8"))
    variants = run["variants"]

    rows = []
    for config in sorted(variants):
        answers = variants[config]["answers"]
        if len(answers) != EXPECTED_N:
            print(f"REFUSING: {config} has {len(answers)} answers, expected {EXPECTED_N}",
                  file=sys.stderr)
            return 2
        blind_id = make_blind_id(config)
        for a in answers:
            qid = a["question_id"]
            q = bank[qid]
            text = (a.get("answer") or "").strip()
            if not text:
                print(f"REFUSING: empty answer {config}/{qid}", file=sys.stderr)
                return 2
            rows.append({
                "qid": qid,
                "question": q["question"],
                # THE ONLY FIELD THAT DIFFERS FROM OFFLINE_FINAL: taken from the
                # live (old) bank, not from the run's embedded reference.
                "reference": q["reference"],
                "sc_flag": q["safety_critical"],
                "category": q["category"],
                "config": config,
                "blind_id": blind_id,
                "answer": text,
            })

    with open(ITEMS_PATH, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_dir": str(RUN_DIR),
        "configs": sorted(variants),
        "real_item_count": len(rows),
        "ablation": "OLD reference bank paired with offline-run answers; "
                    "built by ablation/build_oldbank_items.py, NOT assemble_items.py. "
                    "Not a publishable offline evaluation on its own.",
        "bank": question_bank_metadata(bank_list, str(BANK_PATH)),
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"Wrote {len(rows)} items ({len(variants)} configs x {EXPECTED_N})")
    print(f"Bank sha256: {manifest['bank']['sha256'][:16]}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
