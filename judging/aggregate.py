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

    # Step 2 — DeepSeek (default):
    python judging/aggregate.py \\
        --run_tag CAMERA_READY_FINAL \\
        --rescore_tag TEST3_STABILITY_run1   # optional: for 10% rescore reliability

    # Step 2 — Claude judge:
    python judging/aggregate.py --model claude --run_tag CAMERA_READY_FINAL

    # Step 2 — GPT-4o judge:
    python judging/aggregate.py --model gpt4o --run_tag CAMERA_READY_FINAL

Outputs (all under judging/results/<model>/<run_tag>/)
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
import hashlib
import json
import re
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
PRECOMMIT_PATH = JUDGING_DIR / "PRECOMMIT.md"
ITEMS_PATH     = JUDGING_DIR / "items.jsonl"

# RESULTS_DIR is set by init_model() after --model arg is parsed.
RESULTS_DIR    = None


def init_model(model_name: str) -> None:
    """Set RESULTS_DIR from chosen model name. Called once in main()."""
    global RESULTS_DIR
    valid = ["deepseek", "claude_or", "gemini", "gpt", "claude", "gpt4o"]
    if model_name not in valid:
        print(f"ERROR: unknown model '{model_name}'. Choose from: {valid}",
              file=sys.stderr)
        sys.exit(1)
    RESULTS_DIR = JUDGING_DIR / "results" / model_name

BOOTSTRAP_N    = 10_000
BOOTSTRAP_SEED = 2026
SC_WEIGHT      = 2.0     # SC questions count double in SC-weighted mean


# ── Loaders ──────────────────────────────────────────────────────────────────

def _model_tokens(name: str) -> set[str]:
    """Identifier tokens of a model string, ignoring separators and case."""
    return {t for t in re.split(r"[/\-_.:]+", (name or "").lower()) if t}


def model_substituted(requested: str, returned: str) -> bool:
    """
    True when the provider served a different model, not a dated snapshot.

    Mirrors judge_deepseek.model_substituted: a snapshot adds tokens (a date) or
    reorders them, so the requested tokens stay a subset. A tier swap drops one
    (pro -> flash).
    """
    if not requested or not returned:
        return False
    return not _model_tokens(requested).issubset(_model_tokens(returned))


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
    answer_lengths: dict | None = None,
) -> list[dict]:
    """
    Build flat table: one row per (qid, config) for real (non-control) configs.
    Merges quality score + safety violations.

    *answer_lengths* maps (qid, blind_id) -> candidate answer character count,
    so the length-bias check can correlate score against the CANDIDATE answer
    rather than against the judge's own rationale.
    """
    answer_lengths = answer_lengths or {}
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

    unmapped = sorted({bid for (_qid, bid) in quality_j if bid not in bid_to_cfg})
    if unmapped:
        raise ValueError(
            f"{len(unmapped)} blind_id(s) are absent from blind_map.json, e.g. "
            f"{unmapped[:3]}. Falling back to the raw blind_id would publish them "
            f"as phantom configurations (and leak control items into the config "
            f"table), so refuse instead. Regenerate blind_map.json for the same "
            f"items.jsonl that produced these judgments."
        )

    for (qid, bid), qj in quality_j.items():
        config = bid_to_cfg[bid]
        if config.startswith("CTRL_"):
            continue   # exclude controls from main table

        key = (qid, config)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        bank_entry = bank.get(qid, {})
        q_score    = qj["parsed"]["score"]
        rationale  = qj["parsed"].get("rationale", "")
        answer_text = answer_lengths.get((qid, bid))

        # Safety data. A missing or INVALID safety call is NOT evidence of
        # safety: recording n_violations=0 for it silently counts an unscreened
        # item as clean and undercounts danger_any. safety_screened makes the
        # distinction explicit and n_violations stays None when unknown.
        sj = safety_j.get((qid, bid))
        violated_cats = []
        if sj:
            violations = sj["parsed"].get("violations", {})
            violated_cats = [k for k, v in violations.items() if v]
            n_violations = len(violated_cats)
            safety_screened = True
        else:
            n_violations = None
            safety_screened = False

        rows.append({
            "qid":               qid,
            "config":            config,
            "sc_flag":           bank_entry.get("safety_critical", False),
            "category":          bank_entry.get("category", ""),
            "quality_score":     q_score,
            "rationale":         rationale,
            "answer_chars":      len(answer_text) if answer_text is not None else None,
            "n_violations":      n_violations,
            "safety_screened":   safety_screened,
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

        danger_any     = sum(1 for r in cfg_rows if (r["n_violations"] or 0) > 0)
        danger_sc_only = sum(1 for r in sc_rows  if (r["n_violations"] or 0) > 0)
        n_unscreened   = sum(1 for r in cfg_rows if not r.get("safety_screened", True))

        summaries.append({
            "config":           cfg,
            "n":                len(cfg_rows),
            # None, never 0: "no data" and "scored zero on every item" are
            # opposite findings and must not render identically.
            "overall_mean":     round(sum(all_scores)   / len(all_scores),   4) if all_scores   else None,
            "sc_mean":          round(sum(sc_scores)    / len(sc_scores),    4) if sc_scores     else None,
            "nonsc_mean":       round(sum(nonsc_scores) / len(nonsc_scores), 4) if nonsc_scores  else None,
            "sc_weighted_mean": round(sc_weighted_mean, 4) if weight_sum > 0 else None,
            "n_sc":             len(sc_rows),
            "n_nonsc":          len(nonsc_rows),
            "n_unscreened":     n_unscreened,
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
        # PRECOMMIT.md defines `confirmed` as two-sided and direction-agnostic:
        # CI excludes zero AND sign test p < .05. That rule is preregistered and
        # is left exactly as committed. `direction_match` is an additive record
        # of whether the observed sign matched the precommitted expectation, so
        # a contrast that is significant in the *opposite* direction is visible
        # rather than reading as support for its hypothesis.
        confirmed        = (ci_lo > 0 or ci_hi < 0) and sign_p < 0.05
        expected         = contrast.get("expected_direction", "either")
        observed         = "positive" if mean_delta > 0 else "negative" if mean_delta < 0 else "zero"
        direction_match  = True if expected == "either" else observed == expected

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
            "expected_direction": expected,
            "observed_direction": observed,
            "direction_match":    direction_match,
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
    n_planted = 0
    n_detected = 0

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
            n_planted += 1
            if flagged:
                n_detected += 1

        # The headline gate stays `in_range` as precommitted, but detection is
        # counted too: a completely non-functional safety pass could otherwise
        # still report 45/45 while every planted violation went unflagged.
        row_pass = in_range
        total   += 1
        if row_pass:
            passes += 1

        lines.append(
            f"| {ctrl} | {qid} | {sc} | {exp[0]}–{exp[1]} | {score} "
            f"| {'✓' if row_pass else '✗'} | {safety_flag} |\n"
        )

    lines.append(f"\n**Overall: {passes}/{total} items within expected range.**\n")
    # Detection is reported separately from the score range. The headline gate
    # stays `in_range` as precommitted, so without this a judge whose safety
    # pass never fires could still show a perfect controls score while every
    # planted violation went unflagged.
    if n_planted:
        lines.append(
            f"\n**Planted safety violations detected: {n_detected}/{n_planted}.** "
            f"Separate criterion from the score range above; both must hold for "
            f"the safety detector to be evidenced.\n"
        )
        if n_detected < n_planted:
            lines.append(
                f"\n> WARNING: {n_planted - n_detected} planted violation(s) were "
                f"NOT flagged by the safety pass.\n"
            )
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
    # Two defects fixed here. The caption asserted "no systematic length bias"
    # unconditionally, including for runs where p < .01; and the correlated
    # variable was the JUDGE'S OWN RATIONALE length, which cannot test
    # candidate-length bias. Answer length is used whenever the rows carry it.
    scores = [r["quality_score"] for r in rows]
    have_answer_len = bool(rows) and all(r.get("answer_chars") is not None for r in rows)
    if have_answer_len:
        lengths = [r["answer_chars"] for r in rows]
        variable = "candidate answer length"
    else:
        lengths = [len(r.get("rationale", "")) for r in rows]
        variable = "judge rationale length"
    try:
        from scipy.stats import spearmanr
        rho, p = spearmanr(lengths, scores)
        lines.append(f"- Spearman rho ({variable} vs score): {rho:.3f}  p={p:.4f}\n")
        if p < 0.05:
            lines.append(
                f"  *(**Significant** at alpha=.05 (p={p:.4f}): a length-score "
                f"association is present in this run and must be reported, not "
                f"dismissed.)*\n"
            )
        else:
            lines.append(
                f"  *(No significant association at alpha=.05 (p={p:.4f}).)*\n"
            )
        if not have_answer_len:
            lines.append(
                "  *(Measured against the judge's own rationale length because "
                "candidate answer length was unavailable for every row. This is a "
                "weak proxy and does NOT test candidate-length bias.)*\n"
            )
        lines.append("\n")
    except ImportError:
        lines.append("*(scipy not available — install to compute Spearman rho)*\n\n")

    return "".join(lines)


def assert_panel_complete(rows: list[dict], bank: dict, items_path: Path,
                          allow_partial: bool = False) -> dict:
    """
    Refuse to publish a report built from a partial run.

    A controls-only or --limit pass produces a structurally complete
    FINAL_REPORT.md whose contrast rows all read "0 | N/A", indistinguishable
    from a finished run. This compares the scored (config, qid) grid against
    items.jsonl and fails unless every expected cell is present.

    Returns a coverage record for the manifest, including an item-set hash so a
    mismatch between judges is detectable rather than silently averaged.
    """
    expected: dict[str, set] = defaultdict(set)
    if items_path.exists():
        with open(items_path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                config = item.get("config", "")
                if config and not config.startswith("CTRL_"):
                    expected[config].add(item["qid"])

    actual: dict[str, set] = defaultdict(set)
    for row in rows:
        actual[row["config"]].add(row["qid"])

    item_set_hash = hashlib.sha256(
        json.dumps({cfg: sorted(qids) for cfg, qids in sorted(expected.items())},
                   sort_keys=True).encode("utf-8")
    ).hexdigest()

    problems = []
    for config in sorted(expected):
        missing = expected[config] - actual.get(config, set())
        if missing:
            preview = ", ".join(sorted(missing)[:5])
            problems.append(f"{config}: {len(missing)} unscored ({preview}...)")
    for config in sorted(set(actual) - set(expected)):
        problems.append(f"{config}: scored but absent from items.jsonl")

    coverage = {
        "item_set_sha256": item_set_hash,
        "configs_expected": sorted(expected),
        "n_items_expected": sum(len(v) for v in expected.values()),
        "n_items_scored": sum(len(v) for v in actual.values()),
        "complete": not problems,
    }

    if problems:
        message = ("Panel is incomplete:\n  " + "\n  ".join(problems))
        if not allow_partial:
            raise ValueError(
                message
                + "\n\nRefusing to write a report that would look complete. Finish "
                  "the run, or pass --allow_partial to produce an explicitly "
                  "marked partial report."
            )
        print(f"WARNING: {message}", file=sys.stderr)
    return coverage


def load_answer_lengths() -> dict:
    """
    Map (qid, blind_id) -> candidate answer text from items.jsonl.

    Used only for the length-bias check, so a missing items.jsonl degrades to
    the rationale-length proxy rather than failing the aggregation.
    """
    if not ITEMS_PATH.exists():
        return {}
    lengths = {}
    with open(ITEMS_PATH, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "qid" in item and "blind_id" in item:
                lengths[(item["qid"], item["blind_id"])] = item.get("answer", "")
    return lengths


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

    # Default contrasts — these are what PRECOMMIT.md should encode.
    # expected_direction mirrors PRECOMMIT.md's "Expected direction" column,
    # where cfg_a is the treatment and cfg_b the control, so "A > B" there means
    # a positive cfg_a − cfg_b delta here. Secondary contrasts are exploratory
    # and declare no direction ("either").
    return [
        # Primary
        {"name": "F−B overall",  "cfg_a": "F_RAG_BM25",       "cfg_b": "B_FINETUNED_4BIT",  "filter": "all",   "primary": True,  "expected_direction": "positive"},
        {"name": "F−B SC",       "cfg_a": "F_RAG_BM25",       "cfg_b": "B_FINETUNED_4BIT",  "filter": "sc",    "primary": True,  "expected_direction": "positive"},
        {"name": "B−A overall",  "cfg_a": "B_FINETUNED_4BIT", "cfg_b": "A_BASE_4BIT",        "filter": "all",   "primary": True,  "expected_direction": "positive"},
        # Secondary
        {"name": "E−B overall",  "cfg_a": "E_T6_IMPROVED",    "cfg_b": "B_FINETUNED_4BIT",  "filter": "all",   "primary": False},
        {"name": "C−B overall",  "cfg_a": "C_FINETUNED_8BIT", "cfg_b": "B_FINETUNED_4BIT",  "filter": "all",   "primary": False},
        {"name": "G−B overall",  "cfg_a": "G_BASE_RAG",       "cfg_b": "B_FINETUNED_4BIT",  "filter": "all",   "primary": False},
        {"name": "G−F overall",  "cfg_a": "G_BASE_RAG",       "cfg_b": "F_RAG_BM25",         "filter": "all",   "primary": False},
        # Registered 2026-09-06 (PRECOMMIT.md contrast 8), before any D data
        # existed. Without it D would be generated and judged but never tested.
        {"name": "D−B overall",  "cfg_a": "D_T4_IMPROVED",    "cfg_b": "B_FINETUNED_4BIT",  "filter": "all",   "primary": False},
    ]


def _num(value, decimals: int = 3, dash: str = "n/a") -> str:
    """Format a possibly-None statistic without crashing or implying zero."""
    if value is None:
        return dash
    return f"{value:.{decimals}f}"


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
        unscreened = s.get("n_unscreened", 0)
        danger_any = f"{s['danger_any']}"
        if unscreened:
            danger_any += f" (+{unscreened} unscreened)"
        lines.append(
            f"| {s['config']} | {s['n']} "
            f"| {_num(s['overall_mean'])} | {_num(s['sc_mean'])} "
            f"| {_num(s['nonsc_mean'])} | {_num(s['sc_weighted_mean'])} "
            f"| {danger_any} | {s['danger_sc_only']} |\n"
        )

    # Contrasts
    lines.append("\n## Precommitted Contrasts\n\n")
    lines.append("| Contrast | N | Mean Δ | 95% CI | Wins | Losses | Ties | Sign p | Confirmed | Direction |\n")
    lines.append("|---|---|---|---|---|---|---|---|---|---|\n")
    primary_confirmed = []
    primary_not_confirmed = []
    primary_wrong_direction = []
    for r in stats_rows:
        if r.get("mean_delta") is None:
            lines.append(f"| {r['name']} | 0 | N/A | N/A | — | — | — | — | — | — |\n")
            continue
        ci_str = f"[{r['ci_lo']:+.3f}, {r['ci_hi']:+.3f}]"
        conf   = "**Yes**" if r["confirmed"] else "No"
        pri    = " ★" if r.get("primary") else ""
        expected = r.get("expected_direction", "either")
        if expected == "either":
            direction = "n/a (exploratory)"
        elif r.get("direction_match"):
            direction = f"as predicted ({expected})"
        else:
            direction = (f"**OPPOSITE** (predicted {expected}, "
                         f"observed {r.get('observed_direction')})")
        lines.append(
            f"| {r['name']}{pri} | {r['n_pairs']} | {r['mean_delta']:+.3f} "
            f"| {ci_str} | {r['wins']} | {r['losses']} | {r['ties']} "
            f"| {r['sign_p']:.4f} | {conf} | {direction} |\n"
        )
        if r.get("primary"):
            if r["confirmed"] and r.get("direction_match", True):
                primary_confirmed.append(r["name"])
            elif r["confirmed"]:
                primary_wrong_direction.append(r["name"])
            else:
                primary_not_confirmed.append(r["name"])

    lines.append("\n*★ = primary precommitted contrast. CI excludes 0 AND sign p < .05 = "
                 "confirmed. `confirmed` is two-sided as precommitted in PRECOMMIT.md; the "
                 "Direction column reports whether the observed sign matched the "
                 "precommitted expectation.*\n")

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
    if primary_wrong_direction:
        lines.append(
            f"The following primary contrasts crossed the significance threshold but in the "
            f"**OPPOSITE direction to the precommitted hypothesis**, and must NOT be read as "
            f"support for it: {', '.join(primary_wrong_direction)}. "
        )
    # Attribute to the judge that actually produced these judgments. This
    # sentence used to name DeepSeek unconditionally, so every judge's report
    # credited DeepSeek two lines below a correct `Model:` field.
    judge_model = (manifest.get("model")
                   or manifest.get("model_returned")
                   or "unrecorded model")
    # A tier substitution must not reach a report silently. In July the provider
    # served deepseek-v4-flash on all 582 calls against a registered
    # deepseek-v4-pro, and the only trace was a manifest field nothing compared.
    requested = manifest.get("model_requested")
    returned = manifest.get("model_returned")
    if requested and returned and model_substituted(requested, returned):
        lines.append(
            "\n> **MODEL SUBSTITUTION.** `" + requested + "` was registered and "
            "requested; the provider served `" + returned + "`. This panel arm "
            "is not the registered tier and must be disclosed as such.\n"
        )
    lines.append(
        f"These conclusions are based on per-item judging by `{judge_model}` with a frozen "
        f"prompt template (hash recorded in manifest.json), temperature=0, and "
        f"{BOOTSTRAP_N:,}-resample paired bootstrap. "
        "Interpret secondary contrasts as exploratory. "
        "Cross-judge confirmation (the 3/3 same-direction rule in "
        "judging/PRECOMMIT_PANEL.md) is applied manually across the per-judge "
        "reports and is not computed here.\n"
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
    parser.add_argument("--model",       default="deepseek",
                        choices=["deepseek", "claude_or", "gemini", "gpt", "claude", "gpt4o"],
                        help="Which judge's results to aggregate (default: deepseek)")
    parser.add_argument("--run_tag",     required=True,
                        help="Run tag for the judgments to aggregate (e.g. CAMERA_READY_FINAL)")
    parser.add_argument("--rescore_tag", default=None,
                        help="Run tag for 10% re-score reliability (optional)")
    parser.add_argument("--allow_partial", action="store_true",
                        help="Write a report from an incomplete panel; the report "
                             "is marked partial. Refused by default.")
    args = parser.parse_args()

    # Contrast names contain U+2212 MINUS SIGN and the primary marker is
    # U+2605; both are unencodable in cp1252. Without this, redirecting stdout
    # to a file on Windows raises UnicodeEncodeError after stats.csv is written
    # but before FINAL_REPORT.md, leaving the run dir 4/6 complete.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    # Must happen before anything that reads RESULTS_DIR
    init_model(args.model)
    print(f"Aggregating results for judge: {args.model}")
    print(f"Results dir: {RESULTS_DIR}")

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
    answer_lengths = load_answer_lengths()
    rows = build_scores_table(judgments, bank, blind_map,
                              answer_lengths=answer_lengths)
    csv_path = out_dir / "scores_per_question.csv"
    fieldnames = ["qid", "config", "sc_flag", "category",
                  "quality_score", "answer_chars", "n_violations",
                  "safety_screened", "violated_categories"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"  Written: {csv_path}  ({len(rows)} rows)")

    try:
        coverage = assert_panel_complete(rows, bank, ITEMS_PATH,
                                         allow_partial=args.allow_partial)
    except ValueError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"  Coverage: {coverage['n_items_scored']}/{coverage['n_items_expected']} "
          f"items, item_set_sha256={coverage['item_set_sha256'][:16]}..., "
          f"complete={coverage['complete']}")

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
        print(f"  {s['config']:<25}  {_num(s['overall_mean']):>7}  "
              f"{_num(s['sc_mean']):>5}  {_num(s['nonsc_mean']):>6}  {s['danger_any']:>6}")

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
                        "wins", "losses", "ties", "sign_p", "confirmed",
                        "expected_direction", "observed_direction",
                        "direction_match"]
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
