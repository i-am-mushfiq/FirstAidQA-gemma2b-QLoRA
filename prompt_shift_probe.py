"""
prompt_shift_probe.py
=====================
Measure what changes when the fine-tuned adapter is prompted with the OFFLINE
system prompt instead of the one it was TRAINED on.

Why this exists
---------------
FINDINGS_20260905.md #6d observes that the July camera-ready run generated under
a prompt byte-identical to `data_v2.SYSTEM_PROMPT`, so the model was asked at
test time exactly what it was asked during training. `offline_definitive_v1`
matches neither, so generating with it against the existing adapter introduces a
train/test prompt shift that July did not have. #6d therefore warns against
running the offline prompt without retraining.

That is an empirical question, so this measures it instead of arguing it: the
same adapter, same questions, same decoding, same seed-free greedy path -- only
the system prompt differs. The offline arm is read from the verified camera-ready
run; this script generates the training-prompt arm.

What it can and cannot tell you
-------------------------------
It compares the two arms on token count, word count, ROUGE-L against the
reference, EMS mention rate, and answer-to-answer similarity. If the two arms
are near-identical the prompt shift cannot be moving judge scores, and #6d is
dismissed with a number. If they diverge, this cannot say which is *better* --
only judging can -- but it sizes the effect.

Usage
-----
    python prompt_shift_probe.py --run_dir evaluations/CAMERA_READY_OFFLINE_20260905_204533
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import data_v2                                    # noqa: E402
import evaluation_protocol as ep                  # noqa: E402
import v2_comprehensive_eval as v2                # noqa: E402

TRAIN_PROMPT   = data_v2.SYSTEM_PROMPT            # what the adapter was trained on
OFFLINE_PROMPT = ep.SYSTEM_PROMPT                 # offline_definitive_v1

DEFAULT_MODEL   = str(ROOT / "models" / "gemma-2b-it")
DEFAULT_ADAPTER = str(ROOT / "experiments"
                      / "10cat_4bit_r16_lr1e-4_p3_v2_20260508_054337" / "adapter")


def prompt_with(system_prompt: str, question: str) -> str:
    """Same shape as v2.prompt_standard, with the system prompt swapped."""
    return (f"<start_of_turn>user\n{system_prompt}\n\n{question}<end_of_turn>\n"
            f"<start_of_turn>model\n")


def jaccard(a: str, b: str) -> float:
    sa, sb = set(a.lower().split()), set(b.lower().split())
    return len(sa & sb) / len(sa | sb) if (sa | sb) else 0.0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run_dir", required=True,
                   help="verified camera-ready run supplying the offline arm")
    p.add_argument("--config", default="B_FINETUNED_4BIT",
                   help="which config's answers form the offline arm")
    p.add_argument("--model_path", default=DEFAULT_MODEL)
    p.add_argument("--adapter", default=DEFAULT_ADAPTER)
    p.add_argument("--max_new_tokens", type=int, default=v2.MAX_NEW_TOKENS)
    p.add_argument("--out", default=str(ROOT / "evaluations" / "prompt_shift_probe.json"))
    args = p.parse_args()

    run = json.loads(Path(args.run_dir, "run.json").read_text(encoding="utf-8"))
    offline_answers = {a["question_id"]: a
                       for a in run["variants"][args.config]["answers"]}
    questions = [{k: a[k] for k in ("question_id", "question", "reference",
                                    "category", "safety_critical")}
                 for a in run["variants"][args.config]["answers"]]

    print(f"Probe: {args.config} under the TRAINING prompt vs the OFFLINE arm")
    print(f"  training prompt : {len(TRAIN_PROMPT)} chars")
    print(f"  offline prompt  : {len(OFFLINE_PROMPT)} chars")
    print(f"  questions       : {len(questions)}")
    print(f"  decoding        : greedy, max_new_tokens={args.max_new_tokens}, "
          f"{v2.DECODING_PARAMS}")

    model, tokenizer = v2.load_model(args.model_path, args.adapter, "4bit")
    stop_ids = v2.get_stop_ids(tokenizer)

    rows = []
    for i, q in enumerate(questions, 1):
        r = v2.generate(model, tokenizer,
                        prompt_with(TRAIN_PROMPT, q["question"]),
                        max_new_tokens=args.max_new_tokens, stop_ids=stop_ids)
        off = offline_answers[q["question_id"]]
        rows.append({
            "question_id":      q["question_id"],
            "reference":        q["reference"],
            "train_prompt_answer":   r["answer"],
            "offline_prompt_answer": off["answer"],
            "train_tokens":     r["tokens_generated"],
            "offline_tokens":   off["tokens_generated"],
        })
        print(f"  [{i:2d}/{len(questions)}] {q['question_id']}  "
              f"train={r['tokens_generated']:3d} tok  "
              f"offline={off['tokens_generated']:3d} tok", flush=True)
    v2.unload(model)

    ems = v2.re.compile(r"emergency service|ambulance|paramedic|\b999\b|\b911\b"
                        r"|\b000\b|\b112\b|emergency medical", v2.re.I)

    def summarise(key: str) -> dict:
        texts = [r[key] for r in rows]
        return {
            "mean_words":  round(statistics.mean(len(t.split()) for t in texts), 1),
            "mean_tokens": round(statistics.mean(
                r["train_tokens" if key.startswith("train") else "offline_tokens"]
                for r in rows), 1),
            "mean_rouge_l": round(statistics.mean(
                v2.rouge_l(r[key], r["reference"]) for r in rows), 4),
            "ems_mentions": sum(1 for t in texts if ems.search(t)),
        }

    train_s, off_s = summarise("train_prompt_answer"), summarise("offline_prompt_answer")
    sims = [jaccard(r["train_prompt_answer"], r["offline_prompt_answer"]) for r in rows]
    identical = sum(1 for r in rows
                    if r["train_prompt_answer"].strip() == r["offline_prompt_answer"].strip())

    print("\n" + "=" * 68)
    print(f"{'metric':22s} {'TRAIN prompt':>16s} {'OFFLINE prompt':>16s}")
    print("-" * 68)
    for label, k in [("mean words", "mean_words"), ("mean tokens", "mean_tokens"),
                     ("mean ROUGE-L vs ref", "mean_rouge_l"),
                     ("EMS mentions /41", "ems_mentions")]:
        print(f"{label:22s} {train_s[k]:>16} {off_s[k]:>16}")
    print("-" * 68)
    print(f"{'answer similarity':22s} mean Jaccard {statistics.mean(sims):.3f}  "
          f"median {statistics.median(sims):.3f}")
    print(f"{'byte-identical':22s} {identical}/{len(rows)}")
    print("=" * 68)

    delta = off_s["mean_rouge_l"] - train_s["mean_rouge_l"]
    print(f"\nROUGE-L delta (offline - train prompt): {delta:+.4f}")
    print("ROUGE-L is a weak proxy for judge score; it sizes the shift, it does "
          "not rank the arms.")

    out = Path(args.out)
    out.write_text(json.dumps({
        "probe": "prompt_shift",
        "config": args.config,
        "run_dir": args.run_dir,
        "train_prompt_sha256": ep.canonical_json_sha256(TRAIN_PROMPT),
        "offline_prompt_sha256": ep.canonical_json_sha256(OFFLINE_PROMPT),
        "summary": {"train_prompt": train_s, "offline_prompt": off_s,
                    "mean_jaccard": round(statistics.mean(sims), 4),
                    "median_jaccard": round(statistics.median(sims), 4),
                    "byte_identical": identical, "n": len(rows)},
        "rows": rows,
    }, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nWritten: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
