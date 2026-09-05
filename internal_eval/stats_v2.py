"""
stats_v2.py
===========
Statistical analysis module for the camera-ready evaluation results.

Pre-commitment document: paper/PRECOMMIT_STATS_v2.md
Run AFTER that document is git-committed.

USAGE
-----
python stats_v2.py --run_dir evaluations/CAMERA_READY_OFFLINE_<timestamp>

Requires per-item judge scores in the sibling analysis directory:
  <run_dir>_ANALYSIS/judgments/<judge>/<config>/<qid>.json

OUTPUTS (all written to <run_dir>_ANALYSIS/stats/)
-------------------------------------------------
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
import sys

import numpy as np
from collections import defaultdict
from itertools import combinations
from pathlib import Path

# This module lives in internal_eval/ but reads and writes repo-root paths
# (evaluations/, the canonical rubric, the protocol contracts), so the root is
# both the import root and the base for every relative path below.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from build_v2_judge_prompt import RUBRIC  # noqa: E402
from evaluation_protocol import (  # noqa: E402
    CAMERA_READY_CONFIG_RESOLUTION,
    default_analysis_dir,
    frozen_questions_from_run,
    latest_run_dir,
    validate_camera_config_resolution,
    validate_run_prompt_provenance,
    validate_run_question_provenance,
)
# Imported by package path, not bare name, so this module and the test suite
# share one judge_per_item module object rather than loading it twice.
from internal_eval.judge_per_item import (  # noqa: E402
    JUDGES as JUDGE_CONFIGS,
    score_input_sha256,
)

# ---------------------------------------------------------------------------
# Constants (must match judge_per_item.py and eval bank)
# ---------------------------------------------------------------------------

JUDGES = list(JUDGE_CONFIGS)

JUDGE_LABELS = {
    "gpt4o": "GPT-4o",
    "claude": "Claude",
    "gemini": "Gemini",
    "grok": "Grok",
    "deepseek": "DeepSeek",
    "kimi": "Kimi K2",
}

CAMERA_READY_CONFIGS = list(CAMERA_READY_CONFIG_RESOLUTION)

CONFIG_LABELS = {
    "A_BASE_4BIT":      "A: Base 4-bit",
    "B_FINETUNED_4BIT": "B: Fine-tuned 4-bit",
    "C_FINETUNED_8BIT": "C: Base8 + FT4 adapter",
    "D_T4_IMPROVED":    "D: T4 length floor",
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

def load_scores(analysis_dir: Path, bank: list, run: dict,
                rubric_text: str, judges: list[str] | None = None) -> dict:
    """
    Load per-item scores from judgments/<judge>/<config>/<qid>.json.

    *judges* is the panel to require; it defaults to all six configured judges.
    Pass a subset to analyse a partial panel — the panel must still be complete
    for every judge in it, so a half-scored judge is still refused.

    Returns:
      scores[config][qid][judge_id] = int score (0-5)
    """
    panel = list(judges) if judges else list(JUDGES)
    judgment_dir = analysis_dir / "judgments"
    scores: dict = defaultdict(lambda: defaultdict(dict))
    errors = []
    q_by_id = {q["question_id"]: q for q in bank}
    variants = run.get("variants", {})

    for cfg in CAMERA_READY_CONFIGS:
        answers = {
            answer["question_id"]: answer.get("answer", "")
            for answer in variants.get(cfg, {}).get("answers", [])
        }
        for qid, question in q_by_id.items():
            for judge_id in panel:
                path = judgment_dir / judge_id / cfg / f"{qid}.json"
                if not path.exists():
                    errors.append(f"missing {judge_id}/{cfg}/{qid}")
                    continue
                try:
                    with open(path, encoding="utf-8") as handle:
                        data = json.load(handle)
                except (OSError, json.JSONDecodeError) as exc:
                    errors.append(f"invalid {judge_id}/{cfg}/{qid}: {exc}")
                    continue
                expected_hash = score_input_sha256(
                    judge_id, question, answers.get(qid, ""), rubric_text, cfg
                )
                if data.get("input_sha256") != expected_hash:
                    errors.append(f"stale {judge_id}/{cfg}/{qid}")
                    continue
                score = data.get("score")
                if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 5:
                    errors.append(f"bad score {judge_id}/{cfg}/{qid}: {score!r}")
                    continue
                if (data.get("judge_id"), data.get("config_label"), data.get("question_id")) != (judge_id, cfg, qid):
                    errors.append(f"identity mismatch {judge_id}/{cfg}/{qid}")
                    continue
                scores[cfg][qid][judge_id] = score

    if errors:
        preview = "; ".join(errors[:12])
        suffix = f"; ... and {len(errors)-12} more" if len(errors) > 12 else ""
        raise ValueError(
            f"Judgment panel is incomplete or stale ({len(errors)} problem(s)): "
            + preview + suffix
        )

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


# ---------------------------------------------------------------------------
# Bootstrap CI
# ---------------------------------------------------------------------------

def _percentile_indices(n_resamples: int, alpha: float) -> tuple[int, int]:
    """Clamped percentile indices into a sorted resample array."""
    lo = int(math.floor(alpha / 2 * n_resamples))
    hi = int(math.ceil((1 - alpha / 2) * n_resamples)) - 1
    return max(0, min(lo, n_resamples - 1)), max(0, min(hi, n_resamples - 1))


def bootstrap_mean_ci(values: list[float],
                      n_resamples: int = BOOTSTRAP_RESAMPLES,
                      seed: int = BOOTSTRAP_SEED,
                      alpha: float = ALPHA) -> tuple[float, float, float]:
    """
    Returns (point_estimate, ci_lo, ci_hi) from a percentile bootstrap.

    The seed is fixed for reproducibility, which means every config draws the
    same resample index pattern. Each interval is still valid marginally, but
    the intervals are not independent across configs: do not read overlap
    between two configs' CIs as a hypothesis test. Use bootstrap_delta_ci,
    which resamples the paired differences, for comparisons.
    """
    n = len(values)
    if n == 0:
        return (float("nan"),) * 3
    point = sum(values) / n
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, n, size=(n_resamples, n))
    resample_means = np.sort(np.asarray(values, dtype=float)[draws].mean(axis=1))
    lo_idx, hi_idx = _percentile_indices(n_resamples, alpha)
    return point, float(resample_means[lo_idx]), float(resample_means[hi_idx])


def bootstrap_delta_ci(vec_x: list[float], vec_y: list[float],
                       n_resamples: int = BOOTSTRAP_RESAMPLES,
                       seed: int = BOOTSTRAP_SEED,
                       alpha: float = ALPHA) -> tuple[float, float, float]:
    """
    Paired bootstrap for delta (x - y).
    vec_x and vec_y must be same length and aligned by question.
    Returns (delta, ci_lo, ci_hi).
    """
    n = min(len(vec_x), len(vec_y))
    if n == 0:
        return (float("nan"),) * 3
    diffs = np.asarray(vec_x[:n], dtype=float) - np.asarray(vec_y[:n], dtype=float)
    point = float(diffs.mean())
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, n, size=(n_resamples, n))
    deltas = np.sort(diffs[draws].mean(axis=1))
    lo_idx, hi_idx = _percentile_indices(n_resamples, alpha)
    return point, float(deltas[lo_idx]), float(deltas[hi_idx])

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


def _median(values: list[float]) -> float:
    """True median: the mean of the two central values when n is even."""
    if not values:
        return float("nan")
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def sign_test(vec_x: list[float], vec_y: list[float],
              expected_direction: str = "positive") -> dict:
    """
    Paired sign test on x - y.

    `significant` is the two-sided test as precommitted. `direction_match`
    records whether the observed sign agrees with *expected_direction*, so a
    result that is significant in the OPPOSITE direction cannot be read as
    support for a hypothesis labelled "X > Y". Pass "either" for exploratory
    comparisons that predict no direction.
    """
    wins = losses = ties = 0
    for x, y in zip(vec_x, vec_y):
        if x > y:   wins += 1
        elif x < y: losses += 1
        else:       ties += 1
    n_eff = wins + losses
    p = binomial_exact_twosided(wins, n_eff) if n_eff > 0 else float("nan")
    observed = "positive" if wins > losses else "negative" if losses > wins else "zero"
    return {"wins": wins, "ties": ties, "losses": losses,
            "n_effective": n_eff, "p_value": round(p, 4) if not math.isnan(p) else None,
            "significant": bool(not math.isnan(p) and p < ALPHA),
            "observed_direction": observed,
            "expected_direction": expected_direction,
            "direction_match": expected_direction == "either" or observed == expected_direction}

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
    mean_x = sum(rx) / n
    mean_y = sum(ry) / n
    numerator = sum((a - mean_x) * (b - mean_y) for a, b in zip(rx, ry))
    denom_x = sum((a - mean_x) ** 2 for a in rx)
    denom_y = sum((b - mean_y) ** 2 for b in ry)
    denominator = math.sqrt(denom_x * denom_y)
    return numerator / denominator if denominator > 0 else float("nan")


def compute_judge_agreement(scores: dict, bank: list,
                            panel: list[str] | None = None) -> list[dict]:
    """
    For each pair of judges:
      - Kendall's tau over the 6 configs' mean-score rankings
      - Spearman rho over per-question panel means
    Returns list of row dicts.
    """
    judges = list(panel) if panel else list(JUDGES)
    all_qids = [q["question_id"] for q in bank]

    # Per-judge config ranking (by mean over their questions)
    judge_config_means: dict = {}
    for j in judges:
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
    for j in judges:
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
    for j1, j2 in combinations(judges, 2):
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
    with open(run_json, encoding="utf-8") as f:
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

def run_analysis(run_dir: Path, analysis_dir: Path, out_dir: Path,
                 rubric_text: str, panel: list[str] | None = None) -> None:
    with open(run_dir / "run.json", encoding="utf-8") as f:
        run_meta = json.load(f)
    prompt_errors = validate_run_prompt_provenance(run_meta)
    if prompt_errors:
        raise ValueError(
            "Refusing offline statistical analysis: generation prompt is not "
            "aligned with the offline no-EMS rubric ("
            + "; ".join(prompt_errors)
            + "). Treat the July 2026 "
            "camera-ready run as a legacy-EMS baseline."
        )
    question_errors = validate_run_question_provenance(run_meta)
    if question_errors:
        raise ValueError(
            "Refusing statistical analysis: invalid frozen question snapshot ("
            + "; ".join(question_errors) + ")"
        )
    resolution_errors = validate_camera_config_resolution(run_meta)
    if resolution_errors:
        raise ValueError(
            "Refusing statistical analysis: invalid model/adapter mapping ("
            + "; ".join(resolution_errors) + ")"
        )
    bank = frozen_questions_from_run(run_meta)

    print(f"\nLoading per-item scores from {analysis_dir / 'judgments'} ...")
    panel = list(panel) if panel else list(JUDGES)
    scores = load_scores(analysis_dir, bank, run_meta, rubric_text, judges=panel)

    n_loaded = sum(
        len(scores[c][q]) for c in scores for q in scores[c]
    )
    print(f"  Loaded {n_loaded} individual judge scores")

    expected_loaded = len(panel) * len(CAMERA_READY_CONFIGS) * len(bank)
    if n_loaded != expected_loaded:
        raise ValueError(
            f"Expected {expected_loaded} valid scores "
            f"({len(panel)} judges x {len(CAMERA_READY_CONFIGS)} configs x {len(bank)} "
            f"questions), loaded {n_loaded}"
        )
    if len(panel) < len(JUDGES):
        print(f"  NOTE: analysing a {len(panel)}-judge panel ({', '.join(panel)}); "
              f"paper/PRECOMMIT_STATS_v2.md specifies all {len(JUDGES)}.")

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

        # Sample SD (n-1): these are 41 sampled questions, not a population.
        sd = (math.sqrt(sum((v - mean_all)**2 for v in all_vals) / (len(all_vals) - 1))
              if len(all_vals) > 1 else float("nan"))
        med = _median(all_vals)

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
            # Primary hypotheses are directional ("H1: F > B"); secondary
            # comparisons are exploratory and predict no direction.
            st = sign_test(vx, vy, "positive" if is_primary else "either")

            marker = "(PRIMARY)" if is_primary else "(secondary)"
            p_display = float("nan") if st["p_value"] is None else st["p_value"]
            verdict = ""
            if is_primary and st["significant"]:
                verdict = ("[SIGNIFICANT]" if st["direction_match"]
                           else "[SIGNIFICANT, OPPOSITE DIRECTION]")
            sig_str = f"p={p_display:.4f} {verdict}"
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
                "direction_match": st["direction_match"] if is_primary else "N/A",
                "supports_hypothesis": (st["significant"] and st["direction_match"])
                                       if is_primary else "N/A",
            }
            pairwise_rows.append(row)

    _write_csv(out_dir / "stats_v2_pairwise.csv", pairwise_rows)
    print(f"  Saved: stats_v2_pairwise.csv")

    # ── Judge agreement ───────────────────────────────────────────────────────
    print("\nJudge agreement:")
    agreement_rows = compute_judge_agreement(scores, bank, panel)
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
                 agreement_rows, flag_rows,
                 n_questions=len(all_qids), n_sc=len(sc_qids), panel=panel)
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
    with open(path, "w", newline="", encoding="utf-8") as f:
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
                 agreement: list, flags: list,
                 n_questions: int = 0, n_sc: int = 0,
                 panel: list[str] | None = None) -> None:
    # Counts are interpolated, never hardcoded: the safety-critical labels have
    # been repatched before (see verify_camera_ready.SC_PATCHES), and a caption
    # asserting "n=11" over a table computed from different data is a defect a
    # reader cannot see.
    panel = panel or []
    n_nsc = n_questions - n_sc
    lines = [
        "% Auto-generated by internal_eval/stats_v2.py -- DO NOT EDIT",
        "% Pre-commitment: paper/PRECOMMIT_STATS_v2.md",
        f"% Panel: {', '.join(panel) if panel else 'unspecified'}",
        "% Internal decision lane. Published results come from judging/.",
        "",
        "% Table: Per-config summary (Table 3 replacement)",
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Per-configuration mean panel score (0--5) with 95\% bootstrap CI "
        r"(" + f"{BOOTSTRAP_RESAMPLES:,}" + r" resamples, paired by question). "
        r"SC = safety-critical subset (n=" + str(n_sc) + r"). "
        r"Non-SC = remaining " + str(n_nsc) + r" questions.}",
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
                   help="Deprecated; questions and SC labels are read from the frozen run")
    p.add_argument("--analysis_dir", default=None,
                   help="Judgment artifact directory (default: sibling <run>_ANALYSIS)")
    p.add_argument("--rubric", default=None,
                   help="Rubric used for judging (default: canonical runtime rubric)")
    p.add_argument("--out_dir", default=None,
                   help="Statistics output dir (default: <analysis_dir>/stats/)")
    p.add_argument("--judges", nargs="+", default=None, choices=JUDGES,
                   help="Panel to analyse (default: all six, per "
                        "paper/PRECOMMIT_STATS_v2.md). A subset must still be "
                        "complete for every judge named.")
    return p.parse_args()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    args = parse_args()

    if args.run_dir is None:
        run_dir = latest_run_dir(ROOT / "evaluations")
        if run_dir is None:
            print("ERROR: No CAMERA_READY_* run directory found.")
            sys.exit(1)
        print(f"[auto] Using run: {run_dir.name}")
    else:
        run_dir = Path(args.run_dir)

    analysis_dir = Path(args.analysis_dir) if args.analysis_dir else default_analysis_dir(run_dir)
    rubric_text = Path(args.rubric).read_text(encoding="utf-8") if args.rubric else RUBRIC
    out_dir = Path(args.out_dir) if args.out_dir else analysis_dir / "stats"
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        run_analysis(run_dir, analysis_dir, out_dir, rubric_text, panel=args.judges)
    except ValueError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
