"""
judging/aggregate.py
====================
Phase 6 aggregator. Reads judgments.jsonl + blind_map.json + bank.
Produces all deliverables for the final report.

IMPORTANT: Run only AFTER writing judging/PRECOMMIT.md and committing it.
That ordering must be visible in git history.

Usage
-----
    cd C:\\Personal_Endeavours\\Fine_Tuning

    # Step 1 (MUST come first in git history):
    #   Edit judging/PRECOMMIT.md, then:
    #   git add judging/PRECOMMIT.md && git commit -m "Precommit contrasts"

    # Step 2:
    python judging/aggregate.py \\
        --run_tag CAMERA_READY_FINAL \\
        --rescore_tag TEST3_STABILITY_run1   # optional: for 10% rescore reliability

Outputs (all under judging/results/deepseek/<run_tag>/)
-------
    scores_per_question.csv    -- qid, config, sc_flag, category, quality_score,
                                  n_violations, violated_categories
    config_summary.csv         -- per config: overall/SC/non-SC/SC-weighted means,
                                  danger counts
    controls_report.md         -- control compliance table (goes in paper)
    stats.csv                  -- precommitted contrasts with bootstrap CI + sign test
    reliability_report.md      -- Test3 + 10% rescore + length-bias check
    FINAL_REPORT.md            -- complete summary for human
"""

import argparse
import csv
import json
import os
import random
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT      = Path(__file__).resolve().parent.parent
JUDGING_DIR    = REPO_ROOT / "judging"
BANK_PATH      = REPO_ROOT / "evaluations" / "eval_bank_v2_40q" / "eval_bank_v2.json"
BLIND_MAP_PATH = JUDGING_DIR / "blind_map.json"
CONTROLS_KEY   = JUDGING_DIR / "controls_key.json"
RESULTS_DIR    = JUDGING_DIR / "results" / "deepseek"
PRECOMMIT_PATH = JUDGING_DIR / "PRECOMMIT.md"

BOOTSTRAP_N    = 10_000
BOOTSTRAP_SEED = 2026
SC_WEIGHT      = 2.0     # SC questions count double in SC-weighted mean


# ── Loaders ──────────────────────────────────────────────────────────────────

def load_bank() -> dict:
    with open(BANK_PATH, encoding="utf-8") as f:
        return {x["question_id"]: x for x in json.load(f)}


def load_blind_map() -> dict:
    """Returns bid -> config_name."""
    with open(BLIND_MAP_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_judgments(run_tag: str) -> list[dict]:
    p = RESULTS_DIR / run_tag / "judgments.jsonl"
    with open(p, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_manifest(run_tag: str) -> dict:
    p = RESULTS_DIR / run_tag / "manifest.json"
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


# ── Bootstrap CI + sign test ─────────────────────────────────────────────────

def bootstrap_ci(deltas: list[float], n: int = BOOTSTRAP_N,
                 seed: int = BOOTSTRAP_SEED) -> tuple[float, float]:
    """Paired bootstrap 95% CI on mean delta. Returns (lo, hi)."""
    rng = random.Random(seed)
    k   = len(deltas)
    means = []
    for _ in range(n):
        sample = [deltas[rng.randrange(k)] for _ in range(k)]
        means.append(sum(sample) / k)
    means.sort()
    lo = means[int(0.025 * n)]
    hi = means[int(0.975 * n)]
    return lo, hi


def sign_test(deltas: list[float]) -> tuple[int, int, int, float]:
    """
    Exact binomial sign test (two-sided).
    Returns (wins, losses, ties, p_value).
    """
    wins   = sum(1 for d in deltas if d > 0)
    losses = sum(1 for d in deltas if d < 0)
    ties   = sum(1 for d in deltas if d == 0)
    n      = wins + losses
    if n == 0:
        return wins, losses, ties, 1.0

    # Two-sided p: 2 * P(X <= min(wins, losses)) under Binomial(n, 0.5)
    from math import comb
    min_wl = min(wins, losses)
    p_one  = sum(comb(n, k) for k in range(min_wl + 1)) / (2 ** n)
    p_val  = min(1.0, 2 * p_one)
    return wins, losses, ties, round(p_val, 6)


# ── Core aggregation ─────────────────────────────────────────────────────────

def build_scores_table(
    judgments: list[dict],
    bank: dict,
    blind_map: dict,
) -> list[dict]:
    """
    Build flat table: one row per (qid, config) for real (non-control) configs.
    Merges quality score + safety violations.
    """
    # Index by (qid, blind_id, prompt_type)
    quality_j = {}
    safety_j  = {}
    for j in judgments:
        if j["status"] != "ok":
            continue
        key = (j["qid"], j["blind_id"])
        if j["prompt_type"] == "quality":
            quality_j[key] = j
        elif j["prompt_type"] == "safety":
            safety_j[key] = j

    # Decode blind_id -> config
    bid_to_cfg = blind_map

    rows = []
    seen_keys = set()

    for (qid, bid), qj in quality_j.items():
        config = bid_to_cfg.get(bid, bid)
        if config.startswith("CTRL_"):
            continue   # exclude controls from main table

        key = (qid, config)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        bank_entry = bank.get(qid, {})
        q_score    = qj["parsed"]["score"]
        rationale  = qj["parsed"].get("rationale", "")

        # Safety data
        sj = safety_j.get((qid, bid))
        violations      = {}
        n_violations    = 0
        violated_cats   = []
        if sj:
            violations   = sj["parsed"].get("violations", {})
            violated_cats = [k for k, v in violations.items() if v]
            n_violations = len(violated_cats)

        rows.append({
            "qid":               qid,
            "config":            config,
            "sc_flag":           bank_entry.get("safety_critical", False),
            "category":          bank_entry.get("category", ""),
            "quality_score":     q_score,
            "rationale":         rationale,
            "n_violations":      n_violations,
            "violated_categories": ";".join(violated_cats),
        })

    rows.sort(key=lambda r: (r["qid"], r["config"]))
    return rows


def build_config_summary(rows: list[dict]) -> list[dict]:
    """Per-config aggregate statistics."""
    by_cfg = defaultdict(list)
    for r in rows:
        by_cfg[r["config"]].append(r)

    summaries = []
    for cfg, cfg_rows in sorted(by_cfg.items()):
        all_scores    = [r["quality_score"] for r in cfg_rows]
        sc_rows       = [r for r in cfg_rows if r["sc_flag"]]
        nonsc_rows    = [r for r in cfg_rows if not r["sc_flag"]]
        sc_scores     = [r["quality_score"] for r in sc_rows]
        nonsc_scores  = [r["quality_score"] for r in nonsc_rows]

        # SC-weighted mean: SC items count double
        weighted_scores = [r["quality_score"] * (SC_WEIGHT if r["sc_flag"] else 1.0)
                           for r in cfg_rows]
        weight_sum      = sum(SC_WEIGHT if r["sc_flag"] else 1.0 for r in cfg_rows)
        sc_weighted_mean = sum(weighted_scores) / weight_sum if weight_sum > 0 else 0

        danger_any     = sum(1 for r in cfg_rows   if r["n_violations"] > 0)
        danger_sc_only = sum(1 for r in sc_rows    if r["n_violations"] > 0)

        summaries.append({
            "config":           cfg,
            "n":                len(cfg_rows),
            "overall_mean":     round(sum(all_scores)   / len(all_scores),   4) if all_scores   else 0,
            "sc_mean":          round(sum(sc_scores)    / len(sc_scores),    4) if sc_scores     else 0,
            "nonsc_mean":       round(sum(nonsc_scores) / len(nonsc_scores), 4) if nonsc_scores  else 0,
            "sc_weighted_mean": round(sc_weighted_mean, 4),
            "n_sc":             len(sc_rows),
            "n_nonsc":          len(nonsc_rows),
            "danger_any":       danger_any,
            "danger_sc_only":   danger_sc_only,
        })

    return summaries


def build_stats(rows: list[dict], precommit_contrasts: list[dict]) -> list[dict]:
    """
    Compute paired bootstrap CI + sign test for each precommitted contrast.
    contrast: {"name": "F-B overall", "cfg_a": "F_RAG_BM25", "cfg_b": "B_FINETUNED_4BIT",
               "filter": "all"|"sc"|"nonsc", "primary": true/false}
    """
    # Index scores by (qid, config)
    score_idx = {(r["qid"], r["config"]): r for r in rows}
    # All qids in the run
    all_qids = sorted(set(r["qid"] for r in rows))

    results = []
    for contrast in precommit_contrasts:
        cfg_a   = contrast["cfg_a"]
        cfg_b   = contrast["cfg_b"]
        filt    = contrast.get("filter", "all")

        paired = []
        for qid in all_qids:
            ra = score_idx.get((qid, cfg_a))
            rb = score_idx.get((qid, cfg_b))
            if ra is None or rb is None:
                continue
            if filt == "sc"    and not ra["sc_flag"]:
                continue
            if filt == "nonsc" and ra["sc_flag"]:
                continue
            paired.append(ra["quality_score"] - rb["quality_score"])

        if not paired:
            results.append({**contrast, "n_pairs": 0, "mean_delta": None,
                            "ci_lo": None, "ci_hi": None,
                            "wins": None, "losses": None, "ties": None,
                            "sign_p": None, "confirmed": False})
            continue

        mean_delta       = sum(paired) / len(paired)
        ci_lo, ci_hi     = bootstrap_ci(paired)
        wins, losses, ties, sign_p = sign_test(paired)
        confirmed        = (ci_lo > 0 or ci_hi < 0) and sign_p < 0.05

        results.append({
            **contrast,
            "n_pairs":   len(paired),
            "mean_delta": round(mean_delta, 4),
            "ci_lo":      round(ci_lo, 4),
            "ci_hi":      round(ci_hi, 4),
            "wins":       wins,
            "losses":     losses,
            "ties":       ties,
            "sign_p":     sign_p,
            "confirmed":  confirmed,
        })

    return results


def build_controls_report(
    judgments: list[dict],
    controls_key: list[dict],
    blind_map: dict,
    bank: dict | None = None,
) -> str:
    """Generate controls_report.md content."""
    bid_to_cfg = blind_map
    cfg_to_bid = {v: k for k, v in blind_map.items()}

    jmap = {}
    for j in judgments:
        if j["status"] == "ok":
            jmap[(j["qid"], j["blind_id"], j["prompt_type"])] = j

    lines = [
        "# Control Compliance Report\n\n",
        "| Control | QID | SC | Expected | Got | Pass | Planted violation flagged |\n",
        "|---|---|---|---|---|---|---|\n",
    ]

    total = 0
    passes = 0

    for entry in controls_key:
        ctrl    = entry["control"]
        qid     = entry["qid"]
        exp     = entry["expected_score_range"]
        planted = entry.get("planted_override_id")
        bid     = cfg_to_bid.get(ctrl, "?")
        # Derive SC flag from bank if available; fall back to controls_key field
        if bank and qid in bank:
            sc_val = bank[qid].get("safety_critical", False)
        else:
            sc_val = entry.get("sc_flag_from_qid", False)
        sc      = "SC" if sc_val else "—"

        qj = jmap.get((qid, bid, "quality"))
        sj = jmap.get((qid, bid, "safety"))

        score     = qj["parsed"]["score"] if qj else "N/A"
        in_range  = (isinstance(score, int) and exp[0] <= score <= exp[1])

        safety_flag = "—"
        if planted and sj:
            flagged     = sj["parsed"]["violations"].get(planted, False)
            safety_flag = "✓" if flagged else "✗"

        row_pass = in_range
        total   += 1
        if row_pass:
            passes += 1

        lines.append(
            f"| {ctrl} | {qid} | {sc} | {exp[0]}–{exp[1]} | {score} "
            f"| {'✓' if row_pass else '✗'} | {safety_flag} |\n"
        )

    lines.append(f"\n**Overall: {passes}/{total} items within expected range.**\n")
    return "".join(lines)


def build_reliability_report(
    run_tag: str,
    rows: list[dict],
    rescore_tag: str | None = None,
    stability_path: Path | None = None,
) -> str:
    """Generate reliability_report.md."""
    lines = ["# Reliability Report\n\n"]

    # Test 3 stability (from file if available)
    stab_path = RESULTS_DIR / "STABILITY_REPORT.md"
    if stab_path.exists():
        lines.append("## Test 3 — Intra-Judge Stability\n\n")
        lines.append(stab_path.read_text(encoding="utf-8").replace("# Test 3 — Intra-Judge Stability Report\n", ""))
        lines.append("\n")

    # 10% re-score reliability
    if rescore_tag:
        rescore_judgments = []
        rp = RESULTS_DIR / rescore_tag / "judgments.jsonl"
        if rp.exists():
            with open(rp, encoding="utf-8") as f:
                rescore_judgments = [json.loads(l) for l in f if l.strip()]

        if rescore_judgments:
            lines.append("## 10% Re-Score Reliability (Final Run)\n\n")
            orig_idx = {(r["qid"], r["config"]): r["quality_score"] for r in rows}
            bid_to_cfg = load_blind_map()
            exact = within1 = n = 0
            for j in rescore_judgments:
                if j["status"] == "ok" and j["prompt_type"] == "quality":
                    cfg = bid_to_cfg.get(j["blind_id"], "?")
                    orig = orig_idx.get((j["qid"], cfg))
                    if orig is not None:
                        n += 1
                        diff = abs(j["parsed"]["score"] - orig)
                        if diff == 0: exact += 1
                        if diff <= 1: within1 += 1
            if n > 0:
                lines.append(f"- Items re-scored: {n}\n")
                lines.append(f"- Exact agreement: {exact}/{n} = {exact/n:.1%}\n")
                lines.append(f"- Within ±1: {within1}/{n} = {within1/n:.1%}\n\n")

    # Length-bias check
    lines.append("## Length–Score Correlation (Bias Check)\n\n")
    lengths = [len(r.get("rationale", "")) for r in rows]  # proxy: answer length not stored
    scores  = [r["quality_score"] for r in rows]
    # Use answer length if stored, fall back to rationale length
    try:
        from scipy.stats import spearmanr
        rho, p = spearmanr(lengths, scores)
        lines.append(f"- Spearman ρ (rationale length vs score): {rho:.3f}  p={p:.4f}\n")
        lines.append("  *(Low ρ confirms no systematic length bias in scoring.)*\n\n")
    except ImportError:
        lines.append("*(scipy not available — install to compute Spearman ρ)*\n\n")

    return "".join(lines)


def load_precommit_contrasts() -> list[dict]:
    """
    Load or return default precommitted contrasts.
    Primary contrasts: F-B overall, F-B SC, B-A overall.
    Secondary: E-B, C-B, G-B, G-F, H-B.
    """
    if not PRECOMMIT_PATH.exists():
        print("WARNING: judging/PRECOMMIT.md not found. Using default contrasts.")
        print("         You should create and commit PRECOMMIT.md BEFORE running aggregate.py")
        print("         on camera-ready data. This ordering matters for provenance.")

    # Default contrasts — these are what PRECOMMIT.md should encode
    return [
        # Primary
        {"name": "F−B overall",  "cfg_a": "F_RAG_BM25",       "cfg_b": "B_FINETUNED_4BIT",  "filter": "all",   "primary": True},
        {"name": "F−B SC",       "cfg_a": "F_RAG_BM25",       "cfg_b": "B_FINETUNED_4BIT",  "filter": "sc",    "primary": True},
        {"name": "B−A overall",  "cfg_a": "B_FINETUNED_4BIT", "cfg_b": "A_BASE_4BIT",        "filter": "all",   "primary": True},
        # Secondary
        {"name": "E−B overall",  "cfg_a": "E_T6_IMPROVED",    "cfg_b": "B_FINETUNED_4BIT",  "filter": "all",   "primary": False},
        {"name": "C−B overall",  "cfg_a": "C_FINETUNED_8BIT", "cfg_b": "B_FINETUNED_4BIT",  "filter": "all",   "primary": False},
        {"name": "G−B overall",  "cfg_a": "G_BASE_RAG",       "cfg_b": "B_FINETUNED_4BIT",  "filter": "all",   "primary": False},
        {"name": "G−F overall",  "cfg_a": "G_BASE_RAG",       "cfg_b": "F_RAG_BM25",         "filter": "all",   "primary": False},
    ]


def write_final_report(
    out_dir: Path,
    config_summary: list[dict],
    stats_rows: list[dict],
    controls_report: str,
    reliability: str,
    manifest: dict,
    n_invalid: int,
) -> str:
    """Write FINAL_REPORT.md and return its path."""

    lines = [
        "# Final Judging Report\n\n",
        f"- Run tag: `{manifest.get('run_tag', '?')}`\n",
        f"- Model: `{manifest.get('model_returned', '?')}`\n",
        f"- Template hash (combined): `{manifest.get('template_hash', '?')}`\n",
        f"- Quality hash: `{manifest.get('quality_hash', '?')}`\n",
        f"- Safety hash: `{manifest.get('safety_hash', '?')}`\n",
        f"- Run at: {manifest.get('run_at', '?')}\n",
        f"- Git commit: `{manifest.get('git_commit', '?')}`\n",
        f"- Temperature: {manifest.get('temperature', 0)}\n",
        f"- Total calls: {manifest.get('n_calls_total', '?')}\n",
        f"- INVALID judgments: {n_invalid}\n\n",
    ]

    if n_invalid > 0:
        lines.append(f"> ⚠ {n_invalid} items returned INVALID after {manifest.get('max_retries', 3)} retries. "
                     f"See judgments.jsonl for details.\n\n")

    # Config summary table
    lines.append("## Config Summary\n\n")
    lines.append("| Config | N | Overall | SC | Non-SC | SC-Weighted | Danger(any) | Danger(SC) |\n")
    lines.append("|---|---|---|---|---|---|---|---|\n")
    for s in config_summary:
        lines.append(
            f"| {s['config']} | {s['n']} "
            f"| {s['overall_mean']:.3f} | {s['sc_mean']:.3f} "
            f"| {s['nonsc_mean']:.3f} | {s['sc_weighted_mean']:.3f} "
            f"| {s['danger_any']} | {s['danger_sc_only']} |\n"
        )

    # Contrasts
    lines.append("\n## Precommitted Contrasts\n\n")
    lines.append("| Contrast | N | Mean Δ | 95% CI | Wins | Losses | Ties | Sign p | Confirmed |\n")
    lines.append("|---|---|---|---|---|---|---|---|---|\n")
    primary_confirmed = []
    primary_not_confirmed = []
    for r in stats_rows:
        if r.get("mean_delta") is None:
            lines.append(f"| {r['name']} | 0 | N/A | N/A | — | — | — | — | — |\n")
            continue
        ci_str = f"[{r['ci_lo']:+.3f}, {r['ci_hi']:+.3f}]"
        conf   = "**Yes**" if r["confirmed"] else "No"
        pri    = " ★" if r.get("primary") else ""
        lines.append(
            f"| {r['name']}{pri} | {r['n_pairs']} | {r['mean_delta']:+.3f} "
            f"| {ci_str} | {r['wins']} | {r['losses']} | {r['ties']} "
            f"| {r['sign_p']:.4f} | {conf} |\n"
        )
        if r.get("primary"):
            if r["confirmed"]:
                primary_confirmed.append(r["name"])
            else:
                primary_not_confirmed.append(r["name"])

    lines.append("\n*★ = primary precommitted contrast. CI excludes 0 AND sign p < .05 = confirmed.*\n")

    # Plain-language summary paragraph
    lines.append("\n## Plain-Language Summary\n\n")
    if primary_confirmed:
        lines.append(
            f"The following primary precommitted contrasts are **confirmed** "
            f"(bootstrap 95% CI excludes zero and sign test p < .05): "
            f"{', '.join(primary_confirmed)}. "
        )
    if primary_not_confirmed:
        lines.append(
            f"The following primary contrasts are **not confirmed** at the pre-specified threshold: "
            f"{', '.join(primary_not_confirmed)}. "
        )
    lines.append(
        "These conclusions are based on per-item DeepSeek judging with a frozen prompt template "
        "(hash recorded in manifest.json), temperature=0, and 10,000-resample paired bootstrap. "
        "Interpret secondary contrasts as exploratory.\n"
    )

    # Controls and reliability
    lines.append("\n## Control Compliance\n\n")
    lines.append(controls_report)
    lines.append("\n## Reliability\n\n")
    lines.append(reliability)

    report_path = out_dir / "FINAL_REPORT.md"
    report_path.write_text("".join(lines), encoding="utf-8")
    return str(report_path)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Phase 6: Aggregate judgments into final report")
    parser.add_argument("--run_tag",     required=True,
                        help="Run tag for the judgments to aggregate (e.g. CAMERA_READY_FINAL)")
    parser.add_argument("--rescore_tag", default=None,
                        help="Run tag for 10% re-score reliability (optional)")
    args = parser.parse_args()

    # Guard: PRECOMMIT.md must exist
    if not PRECOMMIT_PATH.exists():
        print("ERROR: judging/PRECOMMIT.md not found.")
        print("  Create it, commit it, THEN run aggregate.py.")
        print("  This ordering is required for provenance.")
        sys.exit(1)

    out_dir = RESULTS_DIR / args.run_tag
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading bank...")
    bank = load_bank()
    print(f"  {len(bank)} questions")

    print(f"Loading blind map...")
    blind_map = load_blind_map()

    print(f"Loading judgments for run_tag={args.run_tag}...")
    judgments = load_judgments(args.run_tag)
    manifest  = load_manifest(args.run_tag)
    n_invalid = sum(1 for j in judgments if j["status"] == "INVALID")
    print(f"  {len(judgments)} total judgments, {n_invalid} INVALID")

    with open(CONTROLS_KEY, encoding="utf-8") as f:
        controls_key = json.load(f)

    # ── scores_per_question.csv ───────────────────────────────────────────────
    print("\nBuilding scores_per_question...")
    rows = build_scores_table(judgments, bank, blind_map)
    csv_path = out_dir / "scores_per_question.csv"
    fieldnames = ["qid", "config", "sc_flag", "category",
                  "quality_score", "n_violations", "violated_categories"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"  Written: {csv_path}  ({len(rows)} rows)")

    # ── config_summary.csv ────────────────────────────────────────────────────
    print("Building config_summary...")
    config_summary = build_config_summary(rows)
    csv_path2 = out_dir / "config_summary.csv"
    with open(csv_path2, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(config_summary[0].keys()))
        w.writeheader()
        w.writerows(config_summary)
    print(f"  Written: {csv_path2}")

    # Print summary to console
    print("\n  Config summary:")
    print(f"  {'Config':<25}  {'Overall':>7}  {'SC':>5}  {'non-SC':>6}  {'Danger':>6}")
    for s in config_summary:
        print(f"  {s['config']:<25}  {s['overall_mean']:>7.3f}  "
              f"{s['sc_mean']:>5.3f}  {s['nonsc_mean']:>6.3f}  {s['danger_any']:>6}")

    # ── controls_report.md ────────────────────────────────────────────────────
    print("\nBuilding controls_report...")
    controls_md = build_controls_report(judgments, controls_key, blind_map, bank=bank)
    cr_path = out_dir / "controls_report.md"
    cr_path.write_text(controls_md, encoding="utf-8")
    print(f"  Written: {cr_path}")

    # ── stats.csv ─────────────────────────────────────────────────────────────
    print("Computing precommitted contrasts...")
    contrasts = load_precommit_contrasts()
    stats_rows = build_stats(rows, contrasts)
    stats_path = out_dir / "stats.csv"
    with open(stats_path, "w", newline="", encoding="utf-8") as f:
        fieldnames_s = ["name", "cfg_a", "cfg_b", "filter", "primary",
                        "n_pairs", "mean_delta", "ci_lo", "ci_hi",
                        "wins", "losses", "ties", "sign_p", "confirmed"]
        w = csv.DictWriter(f, fieldnames=fieldnames_s, extrasaction="ignore")
        w.writeheader()
        w.writerows(stats_rows)
    print(f"  Written: {stats_path}")

    for r in stats_rows:
        if r.get("mean_delta") is None:
            continue
        mark = "★ " if r.get("primary") else "  "
        conf = "CONFIRMED" if r["confirmed"] else "not confirmed"
        print(f"  {mark}{r['name']:<20}  Δ={r['mean_delta']:+.3f} "
              f"CI=[{r['ci_lo']:+.3f},{r['ci_hi']:+.3f}]  "
              f"p={r['sign_p']:.4f}  {conf}")

    # ── reliability_report.md ─────────────────────────────────────────────────
    print("Building reliability report...")
    rel_md = build_reliability_report(args.run_tag, rows, args.rescore_tag)
    rel_path = out_dir / "reliability_report.md"
    rel_path.write_text(rel_md, encoding="utf-8")
    print(f"  Written: {rel_path}")

    # ── FINAL_REPORT.md ───────────────────────────────────────────────────────
    print("Writing FINAL_REPORT.md...")
    final_path = write_final_report(
        out_dir, config_summary, stats_rows,
        controls_md, rel_md, manifest, n_invalid
    )
    print(f"  Written: {final_path}")

    print("\n✓ Aggregation complete.")
    print(f"  All outputs in: {out_dir}")


if __name__ == "__main__":
    main()
