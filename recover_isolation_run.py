"""
recover_isolation_run.py
========================
Reconstruct run.json + metrics.json from already-saved per-config JSON files
after a crash on the final save step.

Usage:
    python recover_isolation_run.py --run_dir evaluations/t4_t6_isolation_20260606_034402
"""

import argparse
import json
import os
import numpy as np
from datetime import datetime


def rouge_l_score(hypothesis: str, reference: str) -> float:
    h = hypothesis.lower().split()
    r = reference.lower().split()
    if not h or not r:
        return 0.0
    m, n = len(h), len(r)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if h[i-1] == r[j-1]:
                dp[i][j] = dp[i-1][j-1] + 1
            else:
                dp[i][j] = max(dp[i-1][j], dp[i][j-1])
    lcs = dp[m][n]
    p = lcs / m if m else 0.0
    rv = lcs / n if n else 0.0
    f1 = (2 * p * rv) / (p + rv) if (p + rv) > 0 else 0.0
    return round(f1, 4)


def compute_metrics(results: list) -> dict:
    scores = [rouge_l_score(r["answer"], r["reference"]) for r in results]
    sc     = [rouge_l_score(r["answer"], r["reference"]) for r in results if r["safety_critical"]]
    nsc    = [rouge_l_score(r["answer"], r["reference"]) for r in results if not r["safety_critical"]]
    tps    = [r["tokens_per_sec"] for r in results if r.get("tokens_per_sec", 0) > 0]
    flagged = sum(1 for r in results if r.get("meta", {}).get("flagged_unsafe", False))
    return {
        "rougeL_mean":      round(float(np.mean(scores)), 4) if scores else 0.0,
        "rougeL_sc_mean":   round(float(np.mean(sc)), 4)     if sc     else 0.0,
        "rougeL_nsc_mean":  round(float(np.mean(nsc)), 4)    if nsc    else 0.0,
        "tok_per_sec_mean": round(float(np.mean(tps)), 2)    if tps    else 0.0,
        "n_questions":  len(results),
        "n_sc":         len(sc),
        "n_nsc":        len(nsc),
        "n_flagged_unsafe": flagged,
    }


CONFIG_ORDER = [
    "A_BASELINE", "B_T4_ORIGINAL", "C_T4_IMPROVED",
    "D_T6_ORIGINAL", "E_T6_IMPROVED", "F_COMBINED_BEST",
]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run_dir", required=True)
    args = p.parse_args()

    run_dir = args.run_dir
    all_results = {}
    all_metrics = {}

    for cfg in CONFIG_ORDER:
        fpath = os.path.join(run_dir, f"{cfg}.json")
        if not os.path.exists(fpath):
            print(f"[skip] {cfg}.json not found")
            continue
        with open(fpath, encoding="utf-8") as f:
            data = json.load(f)
        results = data.get("answers", [])
        all_results[cfg] = results
        all_metrics[cfg] = compute_metrics(results)
        print(f"[ok] {cfg}: {len(results)} questions loaded")

    # Write run.json
    run_path = os.path.join(run_dir, "run.json")
    run_payload = {
        "run_type":  "t4_t6_isolation",
        "run_at":    datetime.utcnow().isoformat(),
        "recovered": True,
        "configs":   list(all_results.keys()),
        "variants":  {k: {"n": len(v), "answers": v} for k, v in all_results.items()},
    }
    with open(run_path, "w", encoding="utf-8") as f:
        json.dump(run_payload, f, indent=2, ensure_ascii=False)
    print(f"\n[save] run.json -> {run_path}")

    # Write metrics.json
    metrics_path = os.path.join(run_dir, "metrics.json")
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(all_metrics, f, indent=2)
    print(f"[save] metrics.json -> {metrics_path}")

    # Print table
    print("\n" + "=" * 70)
    print(f"{'Config':<24} {'ROUGE-L':>8} {'SC':>8} {'Non-SC':>8} {'tok/s':>7} {'Flagged':>8}")
    print("-" * 70)
    baseline_rl = all_metrics.get("A_BASELINE", {}).get("rougeL_mean", 0.0)
    for cfg, m in all_metrics.items():
        rl = m["rougeL_mean"]
        delta = f"({rl - baseline_rl:+.4f})" if cfg != "A_BASELINE" else ""
        print(f"{cfg:<24} {rl:>8.4f} {m['rougeL_sc_mean']:>8.4f} "
              f"{m['rougeL_nsc_mean']:>8.4f} {m['tok_per_sec_mean']:>7.1f} "
              f"{m['n_flagged_unsafe']:>8}  {delta}")
    print("=" * 70)

    print(f"\n[next] python build_t4_t6_judge_prompt.py --run_dir {run_dir}")


if __name__ == "__main__":
    main()
