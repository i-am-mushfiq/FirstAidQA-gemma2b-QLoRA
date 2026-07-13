"""
stats_v2.py
===========
Statistical analysis module for the camera-ready evaluation results.

Pre-commitment document: paper/PRECOMMIT_STATS_v2.md
Run AFTER that document is git-committed.

USAGE
-----
python stats_v2.py --run_dir evaluations/CAMERA_READY_20260708_180411

Requires per-item judge scores in:
  <run_dir>/judgments/<judge>/<config>/<qid>.json

OUTPUTS (all written to <run_dir>/stats/)
------------------------------------------
  stats_v2_results.csv       -- per-config summary with bootstrap CIs
  stats_v2_pairwise.csv      -- per-pair deltas, CIs, sign test
  stats_v2_judge_agreement.csv -- Kendall tau + Spearman rho
  stats_v2_flags.csv         -- flag counts with binomial CIs
  stats_v2_latex.tex         -- LaTeX table macros for paper
  stats_v2_figure4.json      -- error-bar data for Figure 4
"""

import argparse
import csv
import json
import math
import os
import random
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants (must match judge_per_item.py and eval bank)
# ---------------------------------------------------------------------------

JUDGES = ["gpt4o", "claude", "gemini", "grok", "deepseek", "kimi"]

JUDGE_LABELS = {
    "gpt4o": "GPT-4o",
    "claude": "Claude",
    "gemini": "Gemini",
    "grok": "Grok",
    "deepseek": "DeepSeek",
    "kimi": "Kimi K2",
}

CAMERA_READY_CONFIGS = [
    "A_BASE_4BIT",
    "B_FINETUNED_4BIT",
    "C_FINETUNED_8BIT",
    "E_T6_IMPROVED",
    "F_RAG_BM25",
    "G_BASE_RAG",
]

CONFIG_LABELS = {
    "A_BASE_4BIT":      "A: Base 4-bit",
    "B_FINETUNED_4BIT": "B: Fine-tuned 4-bit",
    "C_FINETUNED_8BIT": "C: Fine-tuned 8-bit",
    "E_T6_IMPROVED":    "E: T6 gate",
    "F_RAG_BM25":       "F: RAG BM25",
    "G_BASE_RAG":       "G: Base + RAG",
}

# Primary hypotheses (precommit)
PRIMARY_PAIRS = [
    ("F_RAG_BM25",       "B_FINETUNED_4BIT", "H1: F > B overall",   "all"),
    ("F_RAG_BM25",       "B_FINETUNED_4BIT", "H2: F > B on SC",     "sc"),
    ("B_FINETUNED_4BIT", "A_BASE_4BIT",       "H3: B > A overall",   "all"),
]

# Secondary comparisons (report, no significance claims)
SECONDARY_PAIRS = [
    ("E_T6_IMPROVED",    "B_FINETUNED_4BIT", "E vs B", "all"),
    ("C_FINETUNED_8BIT", "B_FINETUNED_4BIT", "C vs B", "all"),
    ("G_BASE_RAG",       "B_FINETUNED_4BIT", "G vs B", "all"),
    ("G_BASE_RAG",       "F_RAG_BM25",       "G vs F", "all"),
]

BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 2026
ALPHA = 0.05

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_scores(run_dir: Path, bank: list) -> dict:
    """
    Load per-item scores from judgments/<judge>/<config>/<qid>.json.

    Returns:
      scores[config][qid][judge_id] = int score (0-5)
    """
    judgment_dir = run_dir / "judgments"
    scores: dict = defaultdict(lambda: defaultdict(dict))

    for judge_id in JUDGES:
        j_dir = judgment_dir / judge_id
        if not j_dir.exists():
            continue
        for cfg_dir in j_dir.iterdir():
            if not cfg_dir.is_dir():
                continue
            cfg = cfg_dir.name
            for jfile in cfg_dir.glob("*.json"):
                qid = jfile.stem
                try:
                    with open(jfile) as f:
                        d = json.load(f)
                    s = d.get("score")
                    if isinstance(s, (int, float)) and 0 <= s <= 5:
                        scores[cfg][qid][judge_id] = int(s)
                except Exception:
                    pass

    return scores


def panel_means(scores: dict, bank: list) -> dict:
    """
    Compute panel mean per (config, qid).

    Returns:
      pm[config][qid] = float  (mean over available judges)
    """
    pm: dict = defaultdict(dict)
    for cfg, qid_dict in scores.items():
        for qid, judge_dict in qid_dict.items():
            vals = list(judge_dict.values())
            if vals:
                pm[cfg][qid] = sum(vals) / len(vals)
    return pm


def config_vectors(pm: dict, bank: list,
                   sc_only: bool = False) -> dict:
    """
    Return dict[config] = list of panel-mean scores (one per question).
    Questions are ordered by bank; missing scores are omitted.
    """
    sc_set = {q["question_id"] for q in bank if q.get("safety_critical")}
    qids = [q["question_id"] for q in bank
            if (not sc_only or q["question_id"] in sc_set)]

    result = {}
    for cfg in CAMERA_READY_CONFIGS:
        cfg_pm = pm.get(cfg, {})
        vec = [cfg_pm[qid] for qid in qids if qid in cfg_pm]
        if vec:
            result[cfg] = (vec, qids[:len(vec)])
    return result

# ---------------------------------------------------------------------------
# Bootstrap CI
# ---------------------------------------------------------------------------

def bootstrap_mean_ci(values: list[float],
                      n_resamples: int = BOOTSTRAP_RESAMPLES,
                      seed: int = BOOTSTRAP_SEED,
                      alpha: float = ALPHA) -> tuple[float, float, float]:
    """Returns (point_estimate, ci_lo, ci_hi)."""
    rng = random.Random(seed)
    n = len(values)
    if n == 0:
        return (float("nan"),) * 3
    point = sum(values) / n
    resample_means = []
    for _ in range(n_resamples):
        sample = [rng.choice(values) for _ in range(n)]
        resample_means.append(sum(sample) / len(sample))
    resample_means.sort()
    lo_idx = int(math.floor(alpha / 2 * n_resamples))
    hi_idx = int(math.ceil((1 - alpha / 2) * n_resamples)) - 1
    return point, resample_means[lo_idx], resample_means[hi_idx]


def bootstrap_delta_ci(vec_x: list[float], vec_y: list[float],
                       n_resamples: int = BOOTSTRAP_RESAMPLES,
                       seed: int = BOOTSTRAP_SEED,
                       alpha: float = ALPHA) -> tuple[float, float, float]:
    """
    Paired bootstrap for delta (x - y).
    vec_x and vec_y must be same length and aligned by question.
    Returns (delta, ci_lo, ci_hi).
    """
    rng = random.Random(seed)
    n = min(len(vec_x), len(vec_y))
    if n == 0:
        return (float("nan"),) * 3
    pairs = list(zip(vec_x[:n], vec_y[:n]))
    point = sum(x - y for x, y in pairs) / n
    deltas = []
    for _ in range(n_resamples):
        sample = [rng.choice(pairs) for _ in range(n)]
        deltas.append(sum(x - y for x, y in sample) / n)
    deltas.sort()
    lo_idx = int(math.floor(alpha / 2 * n_resamples))
    hi_idx = int(math.ceil((1 - alpha / 2) * n_resamples)) - 1
    return point, deltas[lo_idx], deltas[hi_idx]

# ---------------------------------------------------------------------------
# Sign test (exact binomial)
# ---------------------------------------------------------------------------

def binomial_exact_twosided(n_wins: int, n: int,
                             p0: float = 0.5) -> float:
    """Exact two-sided binomial p-value using normal approximation for large n."""
    if n == 0:
        return float("nan")
    # Exact using math.comb for small n; normal approximation otherwise
    if n <= 100:
        from functools import reduce
        def binom_pmf(k, n, p):
            return math.comb(n, k) * (p ** k) * ((1 - p) ** (n - k))
        observed_p = binom_pmf(n_wins, n, p0)
        p_val = sum(binom_pmf(k, n, p0) for k in range(n + 1)
                    if binom_pmf(k, n, p0) <= observed_p + 1e-10)
        return min(p_val, 1.0)
    else:
        # Normal approximation
        z = (n_wins - n * p0) / math.sqrt(n * p0 * (1 - p0))
        # Two-sided p (standard normal CDF approximation)
        return 2 * _norm_sf(abs(z))


def _norm_sf(z: float) -> float:
    """Survival function (1-CDF) of standard normal at z (z >= 0)."""
    return 0.5 * math.erfc(z / math.sqrt(2))


def sign_test(vec_x: list[float], vec_y: list[float]) -> dict:
    """
    Paired sign test for x > y.
    Returns wins, ties, losses, n_effective, p_value.
    """
    wins = losses = ties = 0
    for x, y in zip(vec_x, vec_y):
        if x > y:   wins += 1
        elif x < y: losses += 1
        else:       ties += 1
    n_eff = wins + losses
    p = binomial_exact_twosided(wins, n_eff) if n_eff > 0 else float("nan")
    return {"wins": wins, "ties": ties, "losses": losses,
            "n_effective": n_eff, "p_value": round(p, 4) if not math.isnan(p) else None,
            "significant": bool(not math.isnan(p) and p < ALPHA)}

# ---------------------------------------------------------------------------
# Judge agreement
# ---------------------------------------------------------------------------

def kendalls_tau(rank_a: list, rank_b: list) -> float:
    """Kendall's tau-b for two ranking lists (config rankings by judge)."""
    n = len(rank_a)
    if n < 2:
        return float("nan")
    concordant = discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            d_a = rank_a[i] - rank_a[j]
            d_b = rank_b[i] - rank_b[j]
            if d_a * d_b > 0:
                concordant += 1
            elif d_a * d_b < 0:
                discordant += 1
    # tau-b (handles ties)
    t_a = sum(1 for i in range(n) for j in range(i+1,n)
              if rank_a[i] == rank_a[j])
    t_b = sum(1 for i in range(n) for j in range(i+1,n)
              if rank_b[i] == rank_b[j])
    denom = math.sqrt((n*(n-1)//2 - t_a) * (n*(n-1)//2 - t_b))
    return (concordant - discordant) / denom if denom > 0 else float("nan")


def spearman_rho(x: list[float], y: list[float]) -> float:
    """Spearman correlation of two equal-length numeric lists."""
    n = len(x)
    if n < 2:
        return float("nan")
    def _ranks(vals):
        sorted_vals = sorted(enumerate(vals), key=lambda kv: kv[1])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n - 1 and sorted_vals[j+1][1] == sorted_vals[j][1]:
                j += 1
            avg_rank = (i + j) / 2 + 1
            for k in range(i, j + 1):
                ranks[sorted_vals[k][0]] = avg_rank
            i = j + 1
        return ranks
    rx = _ranks(x)
    ry = _ranks(y)
    d2 = sum((a - b)**2 for a, b in zip(rx, ry))
    return 1 - 6 * d2 / (n * (n**2 - 1))


def compute_judge_agreement(scores: dict, bank: list) -> list[dict]:
    """
    For each pair of judges:
      - Kendall's tau over the 6 configs' mean-score rankings
      - Spearman rho over per-question panel means
    Returns list of row dicts.
    """
    pm = panel_means(scores, bank)
    all_qids = [q["question_id"] for q in bank]

    # Per-judge config ranking (by mean over their questions)
    judge_config_means: dict = {}
    for j in JUDGES:
        cfg_means = []
        for cfg in CAMERA_READY_CONFIGS:
            vals = [scores[cfg][qid].get(j)
                    for qid in all_qids
                    if qid in scores.get(cfg, {}) and j in scores[cfg][qid]]
            vals = [v for v in vals if v is not None]
            cfg_means.append(sum(vals) / len(vals) if vals else float("nan"))
        judge_config_means[j] = cfg_means

    # Per-judge per-question score vectors
    judge_q_vecs: dict = {}
    for j in JUDGES:
        vec = []
        for qid in all_qids:
            scores_for_q = []
            for cfg in CAMERA_READY_CONFIGS:
                s = scores.get(cfg, {}).get(qid, {}).get(j)
                if s is not None:
                    scores_for_q.append(s)
            # Use mean across configs as the judge's "difficulty" signal per question
            vec.append(sum(scores_for_q) / len(scores_for_q) if scores_for_q else float("nan"))
        judge_q_vecs[j] = vec

    rows = []
    for j1, j2 in combinations(JUDGES, 2):
        tau = kendalls_tau(judge_config_means[j1], judge_config_means[j2])

        # Spearman: filter to questions where both judges have a score
        x_vals = [judge_q_vecs[j1][i] for i in range(len(all_qids))
                  if not math.isnan(judge_q_vecs[j1][i])
                  and not math.isnan(judge_q_vecs[j2][i])]
        y_vals = [judge_q_vecs[j2][i] for i in range(len(all_qids))
                  if not math.isnan(judge_q_vecs[j1][i])
                  and not math.isnan(judge_q_vecs[j2][i])]
        rho = spearman_rho(x_vals, y_vals) if len(x_vals) >= 3 else float("nan")

        rows.append({
            "judge1": j1,
            "judge1_label": JUDGE_LABELS.get(j1, j1),
            "judge2": j2,
            "judge2_label": JUDGE_LABELS.get(j2, j2),
            "kendall_tau": round(tau, 4) if not math.isnan(tau) else None,
            "spearman_rho": round(rho, 4) if not math.isnan(rho) else None,
            "n_questions": len(x_vals),
        })

    return rows

# ---------------------------------------------------------------------------
# Flag counts
# ---------------------------------------------------------------------------

def exact_binomial_ci(k: int, n: int, alpha: float = ALPHA) -> tuple[float, float]:
    """
    Clopper-Pearson exact binomial CI.
    Uses the incomplete beta function approximation.
    """
    if n == 0:
        return (0.0, 1.0)
    try:
        # Python 3.10+ has math.comb; use scipy if available
        from scipy.stats import beta as beta_dist
        lo = beta_dist.ppf(alpha / 2, k, n - k + 1) if k > 0 else 0.0
        hi = beta_dist.ppf(1 - alpha / 2, k + 1, n - k) if k < n else 1.0
        return (round(lo, 4), round(hi, 4))
    except ImportError:
        # Fallback: Wilson interval
        z = 1.96
        p = k / n
        denom = 1 + z**2 / n
        centre = (p + z**2 / (2 * n)) / denom
        margin = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
        return (round(max(0, centre - margin), 4), round(min(1, centre + margin), 4))


def compute_flag_counts(run_dir: Path) -> list[dict]:
    """Load flag counts from run.json (Config E's T6 gate flags)."""
    run_json = run_dir / "run.json"
    if not run_json.exists():
        return []
    with open(run_json) as f:
        run = json.load(f)

    rows = []
    for cfg in CAMERA_READY_CONFIGS:
        variant = run.get("variants", {}).get(cfg, {})
        answers = variant.get("answers", [])
        n = len(answers)
        flagged = sum(1 for a in answers
                      if a.get("meta", {}).get("flagged_unsafe", False))
        ci_lo, ci_hi = exact_binomial_ci(flagged, n)
        rows.append({
            "config": cfg,
            "config_label": CONFIG_LABELS.get(cfg, cfg),
            "n_questions": n,
            "n_flagged": flagged,
            "flag_rate": round(flagged / n, 4) if n else None,
            "ci_95_lo": ci_lo,
            "ci_95_hi": ci_hi,
            "note": "No significance test applied (see PRECOMMIT_STATS_v2.md)",
        })
    return rows

# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def run_analysis(run_dir: Path, bank: list, out_dir: Path) -> None:
    print(f"\nLoading per-item scores from {run_dir / 'judgments'} ...")
    scores = load_scores(run_dir, bank)

    n_loaded = sum(
        len(scores[c][q]) for c in scores for q in scores[c]
    )
    print(f"  Loaded {n_loaded} individual judge scores")

    if n_loaded == 0:
        print("\nWARNING: No judgment files found. Run judge_per_item.py first.")
        print("Generating placeholder output files for structure verification.")

    pm = panel_means(scores, bank)
    all_qids = [q["question_id"] for q in bank]
    sc_qids  = {q["question_id"] for q in bank if q.get("safety_critical")}

    # ── Per-config summary ────────────────────────────────────────────────────
    print("\nPer-config summary:")
    results_rows = []
    for cfg in CAMERA_READY_CONFIGS:
        cfg_pm = pm.get(cfg, {})
        all_vals = [cfg_pm[q] for q in all_qids if q in cfg_pm]
        sc_vals  = [cfg_pm[q] for q in all_qids if q in cfg_pm and q in sc_qids]
        nsc_vals = [cfg_pm[q] for q in all_qids if q in cfg_pm and q not in sc_qids]

        mean_all, ci_lo_all, ci_hi_all = bootstrap_mean_ci(all_vals)
        mean_sc,  ci_lo_sc,  ci_hi_sc  = bootstrap_mean_ci(sc_vals)
        mean_nsc, ci_lo_nsc, ci_hi_nsc = bootstrap_mean_ci(nsc_vals)

        sd = (math.sqrt(sum((v - mean_all)**2 for v in all_vals) / len(all_vals))
              if len(all_vals) > 1 else float("nan"))
        med = sorted(all_vals)[len(all_vals)//2] if all_vals else float("nan")

        print(f"  {CONFIG_LABELS.get(cfg, cfg):<22} "
              f"mean={mean_all:.3f} [{ci_lo_all:.3f},{ci_hi_all:.3f}]  "
              f"SC={mean_sc:.3f}  non-SC={mean_nsc:.3f}  "
              f"n={len(all_vals)}")

        results_rows.append({
            "config": cfg,
            "config_label": CONFIG_LABELS.get(cfg, cfg),
            "n": len(all_vals),
            "mean": round(mean_all, 4) if not math.isnan(mean_all) else None,
            "median": round(med, 4)    if not math.isnan(med)     else None,
            "sd": round(sd, 4)         if not math.isnan(sd)      else None,
            "bootstrap_ci_lo": round(ci_lo_all, 4) if not math.isnan(ci_lo_all) else None,
            "bootstrap_ci_hi": round(ci_hi_all, 4) if not math.isnan(ci_hi_all) else None,
            "mean_sc": round(mean_sc, 4)   if not math.isnan(mean_sc)  else None,
            "ci_lo_sc": round(ci_lo_sc, 4) if not math.isnan(ci_lo_sc) else None,
            "ci_hi_sc": round(ci_hi_sc, 4) if not math.isnan(ci_hi_sc) else None,
            "mean_nsc": round(mean_nsc, 4) if not math.isnan(mean_nsc) else None,
            "ci_lo_nsc": round(ci_lo_nsc, 4) if not math.isnan(ci_lo_nsc) else None,
            "ci_hi_nsc": round(ci_hi_nsc, 4) if not math.isnan(ci_hi_nsc) else None,
        })

    _write_csv(out_dir / "stats_v2_results.csv", results_rows)
    print(f"  Saved: stats_v2_results.csv")

    # ── Pairwise comparisons ──────────────────────────────────────────────────
    print("\nPairwise comparisons:")
    pairwise_rows = []

    def _aligned_vecs(cfg_x, cfg_y, qids):
        """Return aligned vectors for qids present in both configs."""
        x_pm = pm.get(cfg_x, {})
        y_pm = pm.get(cfg_y, {})
        vx, vy = [], []
        for q in qids:
            if q in x_pm and q in y_pm:
                vx.append(x_pm[q])
                vy.append(y_pm[q])
        return vx, vy

    for pairs_list, is_primary in [(PRIMARY_PAIRS, True), (SECONDARY_PAIRS, False)]:
        for cfg_x, cfg_y, label, subset in pairs_list:
            if subset == "sc":
                qids_to_use = [q for q in all_qids if q in sc_qids]
            else:
                qids_to_use = all_qids

            vx, vy = _aligned_vecs(cfg_x, cfg_y, qids_to_use)
            if not vx:
                continue

            delta, ci_lo, ci_hi = bootstrap_delta_ci(vx, vy)
            st = sign_test(vx, vy)

            marker = "(PRIMARY)" if is_primary else "(secondary)"
            sig_str = "p={:.4f} {}".format(
                st["p_value"] or float("nan"),
                "[SIGNIFICANT]" if is_primary and st["significant"] else ""
            )
            print(f"  {label:<30} delta={delta:+.3f} [{ci_lo:+.3f},{ci_hi:+.3f}]  "
                  f"W/T/L={st['wins']}/{st['ties']}/{st['losses']}  "
                  f"{sig_str}  {marker}")

            row = {
                "comparison": label,
                "config_x": cfg_x,
                "config_y": cfg_y,
                "subset": subset,
                "primary": is_primary,
                "n_pairs": len(vx),
                "delta": round(delta, 4) if not math.isnan(delta) else None,
                "bootstrap_ci_lo": round(ci_lo, 4) if not math.isnan(ci_lo) else None,
                "bootstrap_ci_hi": round(ci_hi, 4) if not math.isnan(ci_hi) else None,
                "wins_x": st["wins"],
                "ties": st["ties"],
                "losses_x": st["losses"],
                "n_effective": st["n_effective"],
                "sign_test_p": st["p_value"],
                "significant_at_alpha_0.05": st["significant"] if is_primary else "N/A",
            }
            pairwise_rows.append(row)

    _write_csv(out_dir / "stats_v2_pairwise.csv", pairwise_rows)
    print(f"  Saved: stats_v2_pairwise.csv")

    # ── Judge agreement ───────────────────────────────────────────────────────
    print("\nJudge agreement:")
    agreement_rows = compute_judge_agreement(scores, bank)
    taus = [r["kendall_tau"] for r in agreement_rows if r["kendall_tau"] is not None]
    rhos = [r["spearman_rho"] for r in agreement_rows if r["spearman_rho"] is not None]

    if taus:
        print(f"  Mean Kendall tau  (config ranking agreement): "
              f"{sum(taus)/len(taus):.3f}  +/-  "
              f"{math.sqrt(sum((t-sum(taus)/len(taus))**2 for t in taus)/len(taus)):.3f}")
    if rhos:
        print(f"  Mean Spearman rho (per-question agreement):   "
              f"{sum(rhos)/len(rhos):.3f}  +/-  "
              f"{math.sqrt(sum((r-sum(rhos)/len(rhos))**2 for r in rhos)/len(rhos)):.3f}")

    _write_csv(out_dir / "stats_v2_judge_agreement.csv", agreement_rows)
    print(f"  Saved: stats_v2_judge_agreement.csv")

    # ── Flag counts ───────────────────────────────────────────────────────────
    flag_rows = compute_flag_counts(run_dir)
    _write_csv(out_dir / "stats_v2_flags.csv", flag_rows)
    print(f"\nFlag counts saved: stats_v2_flags.csv")

    # ── Figure 4 data ─────────────────────────────────────────────────────────
    fig4 = {"configs": []}
    for row in results_rows:
        fig4["configs"].append({
            "config": row["config"],
            "label": row["config_label"],
            "mean": row["mean"],
            "ci_lo": row["bootstrap_ci_lo"],
            "ci_hi": row["bootstrap_ci_hi"],
            "mean_sc": row["mean_sc"],
            "ci_lo_sc": row["ci_lo_sc"],
            "ci_hi_sc": row["ci_hi_sc"],
        })
    with open(out_dir / "stats_v2_figure4.json", "w") as f:
        json.dump(fig4, f, indent=2)
    print("  Saved: stats_v2_figure4.json")

    # ── LaTeX tables ──────────────────────────────────────────────────────────
    _write_latex(out_dir / "stats_v2_latex.tex", results_rows, pairwise_rows,
                 agreement_rows, flag_rows)
    print("  Saved: stats_v2_latex.tex")

    print("\nAnalysis complete.")
    _print_precommit_reminder()

# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="ascii", errors="replace") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _fmt(v, decimals=3):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "--"
    if isinstance(v, float):
        return f"{v:.{decimals}f}"
    return str(v)


def _write_latex(path: Path, results: list, pairwise: list,
                 agreement: list, flags: list) -> None:
    lines = [
        "% Auto-generated by stats_v2.py — DO NOT EDIT",
        "% Pre-commitment: paper/PRECOMMIT_STATS_v2.md",
        "",
        "% Table: Per-config summary (Table 3 replacement)",
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Per-configuration mean panel score (0--5) with 95\% bootstrap CI "
        r"(10,000 resamples, paired by question). SC = safety-critical subset (n=11). "
        r"Non-SC = remaining 30 questions.}",
        r"\label{tab:config_summary}",
        r"\begin{tabular}{lcccccc}",
        r"\toprule",
        r"Config & n & Mean & 95\% CI & SC Mean & SC CI & Non-SC Mean \\",
        r"\midrule",
    ]
    for r in results:
        ci = f"[{_fmt(r['bootstrap_ci_lo'])}, {_fmt(r['bootstrap_ci_hi'])}]"
        sc_ci = f"[{_fmt(r['ci_lo_sc'])}, {_fmt(r['ci_hi_sc'])}]"
        lines.append(
            f"{r['config_label']} & {r['n']} & {_fmt(r['mean'])} & {ci} & "
            f"{_fmt(r['mean_sc'])} & {sc_ci} & {_fmt(r['mean_nsc'])} \\\\"
        )
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}", ""]

    # Table: pairwise
    lines += [
        "% Table: Pairwise comparisons (Table 6 replacement)",
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Pairwise mean panel-score deltas with 95\% bootstrap CI and sign test. "
        r"Primary hypotheses were preregistered before analysis (paper/PRECOMMIT\_STATS\_v2.md). "
        r"Secondary comparisons are exploratory; no significance claims are made.}",
        r"\label{tab:pairwise}",
        r"\begin{tabular}{lcccccc}",
        r"\toprule",
        r"Comparison & Subset & $\Delta$ & 95\% CI & W/T/L & $p$ (sign) & Primary \\",
        r"\midrule",
    ]
    for r in pairwise:
        ci = f"[{_fmt(r['bootstrap_ci_lo'])}, {_fmt(r['bootstrap_ci_hi'])}]"
        wl = f"{r['wins_x']}/{r['ties']}/{r['losses_x']}"
        pval = _fmt(r['sign_test_p'], 4) if r["primary"] else "--"
        primary = r"\checkmark" if r["primary"] else ""
        lines.append(
            f"{r['comparison']} & {r['subset']} & {_fmt(r['delta'])} & {ci} & "
            f"{wl} & {pval} & {primary} \\\\"
        )
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}", ""]

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="ascii", errors="replace") as f:
        f.write("\n".join(lines))


def _print_precommit_reminder():
    print()
    print("=" * 60)
    print("  PRECOMMIT REMINDER")
    print("  Hypotheses: paper/PRECOMMIT_STATS_v2.md")
    print("  Bootstrap seed: 2026  |  Resamples: 10,000")
    print("  Alpha: 0.05  |  Test: exact binomial (sign test)")
    print("  Secondary comparisons carry NO significance claims.")
    print("  Flag delta comparisons carry NO significance claims.")
    print("=" * 60)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="stats_v2 — camera-ready statistical analysis")
    p.add_argument("--run_dir", default=None,
                   help="Camera-ready run dir (auto-detects CAMERA_READY_* if omitted)")
    p.add_argument("--bank", default=None,
                   help="eval_bank_v2.json path")
    p.add_argument("--out_dir", default=None,
                   help="Output dir (default: <run_dir>/stats/)")
    return p.parse_args()


def main():
    args = parse_args()
    HERE = Path(__file__).parent

    if args.run_dir is None:
        eval_dir = HERE / "evaluations"
        candidates = sorted(
            d for d in eval_dir.iterdir()
            if d.is_dir() and d.name.startswith("CAMERA_READY_")
        )
        if not candidates:
            print("ERROR: No CAMERA_READY_* directory found.")
            sys.exit(1)
        run_dir = candidates[-1]
        print(f"[auto] Using run: {run_dir.name}")
    else:
        run_dir = Path(args.run_dir)

    bank_path = Path(args.bank) if args.bank else \
        HERE / "evaluations" / "eval_bank_v2_40q" / "eval_bank_v2.json"

    if not bank_path.exists():
        print(f"ERROR: bank not found: {bank_path}")
        sys.exit(1)

    with open(bank_path) as f:
        bank = json.load(f)

    out_dir = Path(args.out_dir) if args.out_dir else run_dir / "stats"
    out_dir.mkdir(parents=True, exist_ok=True)

    run_analysis(run_dir, bank, out_dir)


if __name__ == "__main__":
    main()
