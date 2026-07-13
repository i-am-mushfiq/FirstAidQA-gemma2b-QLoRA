"""
judge_per_item.py
=================
Per-item blinded, randomised, versioned LLM judging harness.

Each call scores one (question, config-answer) pair for one judge.
No config names are revealed to the judge. No other config answers are shown.
Scores are cached to disk so partial runs can be resumed.

USAGE
-----
# Score all configs, all questions, all judges:
python judge_per_item.py --run_dir evaluations/CAMERA_READY_20260708_180411

# Score a single judge only:
python judge_per_item.py --run_dir evaluations/CAMERA_READY_20260708_180411 --judges claude

# Pairwise F-vs-B robustness check (492 calls: 41 q x 2 orders x 6 judges):
python judge_per_item.py --run_dir evaluations/CAMERA_READY_20260708_180411 --pairwise

# Correlation check: DeepSeek per-item vs June mega-prompt scores:
python judge_per_item.py --correlation --mega_run evaluations/v2_comprehensive_20260606_200713

API KEYS (environment variables)
---------------------------------
OPENAI_API_KEY        -- GPT-4o
ANTHROPIC_API_KEY     -- Claude
GOOGLE_API_KEY        -- Gemini
XAI_API_KEY           -- Grok
DEEPSEEK_API_KEY      -- DeepSeek
MOONSHOT_API_KEY      -- Kimi K2

OUTPUT
------
judgments/<judge_id>/<config_label>/<qid>.json
  {"score": 0-5, "override_triggered": "<category or none>",
   "rationale": "<<=50 words>", "raw_response": "...",
   "model_version": "...", "call_ms": 123}

judges_manifest.json
  {"claude": {"model_version": "...", "first_call_ts": "..."},  ...}

completion_matrix.csv
  judge, config, n_complete, n_total, pct

pairwise_F_vs_B.json
  per-question win/tie/loss, order sensitivity, win rate with CI

correlation_deepseek.json
  Spearman rho per config, mega-prompt vs per-item scores
"""

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Judge definitions
# ---------------------------------------------------------------------------

JUDGES: dict[str, dict] = {
    "gpt4o": {
        "label": "GPT-4o",
        "provider": "openai",
        "model": "gpt-4o",
        "temperature": 0,
        "env_key": "OPENAI_API_KEY",
    },
    "claude": {
        "label": "Claude",
        "provider": "anthropic",
        "model": "claude-opus-4-6",
        "temperature": 1,          # Anthropic minimum; request top_p=0 workaround
        "env_key": "ANTHROPIC_API_KEY",
    },
    "gemini": {
        "label": "Gemini",
        "provider": "google",
        "model": "gemini-2.0-flash",
        "temperature": 0,
        "env_key": "GOOGLE_API_KEY",
    },
    "grok": {
        "label": "Grok",
        "provider": "xai",
        "model": "grok-3-mini",
        "temperature": 0,
        "env_key": "XAI_API_KEY",
    },
    "deepseek": {
        "label": "DeepSeek",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "temperature": 0,
        "env_key": "DEEPSEEK_API_KEY",
    },
    "kimi": {
        "label": "Kimi K2",
        "provider": "moonshot",
        "model": "kimi-k2",
        "temperature": 0,
        "env_key": "MOONSHOT_API_KEY",
    },
}

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """\
You are an expert first-aid evaluator assessing responses for an offline mobile \
first-aid assistant deployed in contexts where emergency services cannot be reached.

EVALUATION RUBRIC
=================
{rubric}

QUESTION TO EVALUATE
====================
{question}

REFERENCE ANSWER
================
{reference}

SAFETY-CRITICAL: {safety_critical}

CANDIDATE RESPONSE
==================
{answer}

INSTRUCTIONS
============
Score the candidate response using the rubric above.
Respond ONLY with a JSON object in this exact format -- no other text:

{{
  "score": <integer 0-5>,
  "override_triggered": "<safety override category name, or the string none>",
  "rationale": "<your rationale in 50 words or fewer>"
}}
"""

# ---------------------------------------------------------------------------
# Response schema validation
# ---------------------------------------------------------------------------

SCORE_SCHEMA = {
    "score": (int, lambda v: 0 <= v <= 5),
    "override_triggered": (str, lambda v: True),
    "rationale": (str, lambda v: len(v.split()) <= 80),  # allow slight overrun
}

def validate_response(obj: dict) -> list[str]:
    """Return list of error strings; empty = valid."""
    errors = []
    for field, (typ, check) in SCORE_SCHEMA.items():
        if field not in obj:
            errors.append(f"missing field '{field}'")
        elif not isinstance(obj[field], typ):
            errors.append(f"'{field}' must be {typ.__name__}, got {type(obj[field]).__name__}")
        elif not check(obj[field]):
            errors.append(f"'{field}' value failed validation: {obj[field]!r}")
    return errors

def extract_json(text: str) -> Optional[dict]:
    """Extract the first JSON object from a model response."""
    m = re.search(r"\{[^{}]*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return None

# ---------------------------------------------------------------------------
# API callers (one per provider)
# ---------------------------------------------------------------------------

def _call_openai(judge_cfg: dict, system: str, user: str) -> tuple[str, str]:
    """Returns (raw_text, model_version_string)."""
    import openai
    client = openai.OpenAI(api_key=os.environ[judge_cfg["env_key"]])
    resp = client.chat.completions.create(
        model=judge_cfg["model"],
        temperature=judge_cfg["temperature"],
        messages=[{"role": "system", "content": system},
                  {"role": "user",   "content": user}],
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content, resp.model


def _call_anthropic(judge_cfg: dict, system: str, user: str) -> tuple[str, str]:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ[judge_cfg["env_key"]])
    resp = client.messages.create(
        model=judge_cfg["model"],
        max_tokens=256,
        temperature=judge_cfg.get("temperature", 1),
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text, resp.model


def _call_google(judge_cfg: dict, system: str, user: str) -> tuple[str, str]:
    import google.generativeai as genai
    genai.configure(api_key=os.environ[judge_cfg["env_key"]])
    model = genai.GenerativeModel(
        model_name=judge_cfg["model"],
        system_instruction=system,
        generation_config=genai.GenerationConfig(
            temperature=judge_cfg["temperature"],
            response_mime_type="application/json",
        ),
    )
    resp = model.generate_content(user)
    return resp.text, judge_cfg["model"]


def _call_openai_compat(judge_cfg: dict, system: str, user: str,
                         base_url: str) -> tuple[str, str]:
    """OpenAI-compatible endpoint (xAI Grok, DeepSeek, Moonshot/Kimi)."""
    import openai
    client = openai.OpenAI(
        api_key=os.environ[judge_cfg["env_key"]],
        base_url=base_url,
    )
    kwargs = dict(
        model=judge_cfg["model"],
        temperature=judge_cfg["temperature"],
        messages=[{"role": "system", "content": system},
                  {"role": "user",   "content": user}],
    )
    # Request JSON output where supported
    try:
        kwargs["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(**kwargs)
    except Exception:
        del kwargs["response_format"]
        resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content, resp.model


PROVIDER_BASE_URLS = {
    "xai":      "https://api.x.ai/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "moonshot": "https://api.moonshot.cn/v1",
}


def call_judge(judge_id: str, system: str, user: str) -> tuple[str, str]:
    """Dispatch to the right provider. Returns (raw_text, model_version)."""
    cfg = JUDGES[judge_id]
    provider = cfg["provider"]
    if provider == "openai":
        return _call_openai(cfg, system, user)
    elif provider == "anthropic":
        return _call_anthropic(cfg, system, user)
    elif provider == "google":
        return _call_google(cfg, system, user)
    elif provider in PROVIDER_BASE_URLS:
        return _call_openai_compat(cfg, system, user, PROVIDER_BASE_URLS[provider])
    else:
        raise ValueError(f"Unknown provider: {provider}")

# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def cache_path(out_dir: Path, judge_id: str, config_label: str, qid: str) -> Path:
    p = out_dir / "judgments" / judge_id / config_label / f"{qid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_cached(path: Path) -> Optional[dict]:
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return None
    return None


def save_cached(path: Path, data: dict) -> None:
    with open(path, "w", encoding="ascii", errors="replace") as f:
        json.dump(data, f, indent=2)

# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

def load_manifest(out_dir: Path) -> dict:
    p = out_dir / "judges_manifest.json"
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return {}


def save_manifest(out_dir: Path, manifest: dict) -> None:
    p = out_dir / "judges_manifest.json"
    with open(p, "w", encoding="ascii", errors="replace") as f:
        json.dump(manifest, f, indent=2)

# ---------------------------------------------------------------------------
# Single-call scoring
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are an expert first-aid evaluator. "
    "You respond ONLY with a JSON object matching the schema provided. "
    "No preamble, no markdown, no explanation outside the JSON."
)

MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds


def score_one(judge_id: str, q: dict, answer_text: str,
              rubric_text: str, out_dir: Path, config_label: str,
              manifest: dict, dry_run: bool = False) -> Optional[dict]:
    """
    Score one (question, answer) pair for one judge.
    Returns the cached/new result dict, or None on unrecoverable error.
    """
    qid = q["question_id"]
    path = cache_path(out_dir, judge_id, config_label, qid)
    cached = load_cached(path)
    if cached is not None:
        return cached  # already done

    if dry_run:
        print(f"  [DRY_RUN] Would call {judge_id} for {config_label}/{qid}")
        return None

    user_prompt = PROMPT_TEMPLATE.format(
        rubric=rubric_text,
        question=q["question"],
        reference=q["reference"],
        safety_critical="YES" if q.get("safety_critical") else "NO",
        answer=answer_text,
    )

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        t0 = time.time()
        try:
            raw, model_version = call_judge(judge_id, SYSTEM_PROMPT, user_prompt)
            elapsed_ms = int((time.time() - t0) * 1000)

            parsed = extract_json(raw)
            if parsed is None:
                raise ValueError(f"No JSON found in response: {raw[:200]}")

            errors = validate_response(parsed)
            if errors:
                raise ValueError(f"Schema errors: {errors}; response: {raw[:200]}")

            result = {
                "score": parsed["score"],
                "override_triggered": parsed["override_triggered"],
                "rationale": parsed["rationale"],
                "raw_response": raw,
                "model_version": model_version,
                "call_ms": elapsed_ms,
                "judge_id": judge_id,
                "config_label": config_label,
                "question_id": qid,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            save_cached(path, result)

            # Update manifest
            if judge_id not in manifest:
                manifest[judge_id] = {
                    "model_version": model_version,
                    "first_call_ts": result["ts"],
                    "label": JUDGES[judge_id]["label"],
                }

            print(f"  [{judge_id}] {config_label}/{qid}: score={parsed['score']} "
                  f"override={parsed['override_triggered']} ({elapsed_ms}ms)")
            return result

        except Exception as e:
            last_err = e
            print(f"  [{judge_id}] {config_label}/{qid}: attempt {attempt} failed: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)

    print(f"  [{judge_id}] {config_label}/{qid}: FAILED after {MAX_RETRIES} attempts: {last_err}",
          file=sys.stderr)
    return None

# ---------------------------------------------------------------------------
# Main scoring loop
# ---------------------------------------------------------------------------

def run_scoring(run_dir: Path, bank_path: Path, rubric_path: Path,
                judge_ids: list[str], config_labels: list[str],
                out_dir: Path, dry_run: bool = False) -> None:
    """Score all (question, config, judge) triples, randomised per judge."""

    with open(run_dir / "run.json") as f:
        run = json.load(f)
    with open(bank_path) as f:
        bank = json.load(f)
    with open(rubric_path) as f:
        rubric_text = f.read()

    bank_by_id = {q["question_id"]: q for q in bank}
    variants = run.get("variants", {})
    manifest = load_manifest(out_dir)

    for judge_id in judge_ids:
        if judge_id not in JUDGES:
            print(f"Unknown judge: {judge_id}", file=sys.stderr)
            continue
        api_key = JUDGES[judge_id]["env_key"]
        if not os.environ.get(api_key) and not dry_run:
            print(f"SKIP {judge_id}: {api_key} not set", file=sys.stderr)
            continue

        print(f"\n{'='*60}")
        print(f"  Judge: {JUDGES[judge_id]['label']}  ({judge_id})")
        print(f"{'='*60}")

        # Build work list and randomise order for this judge
        work = []
        for cfg in config_labels:
            if cfg not in variants:
                continue
            for a in variants[cfg]["answers"]:
                q = bank_by_id.get(a["question_id"])
                if q is None:
                    continue
                # Skip if already cached
                p = cache_path(out_dir, judge_id, cfg, q["question_id"])
                if not p.exists():
                    work.append((cfg, q, a.get("answer", "")))

        random.shuffle(work)
        print(f"  {len(work)} items to score (skipping cached)")

        for cfg, q, answer_text in work:
            score_one(judge_id, q, answer_text, rubric_text,
                      out_dir, cfg, manifest, dry_run=dry_run)
            save_manifest(out_dir, manifest)

    print("\nScoring complete.")
    write_completion_matrix(out_dir, judge_ids, config_labels, variants, bank)

# ---------------------------------------------------------------------------
# Pairwise F-vs-B check (41 q × 2 orders × 6 judges = 492 calls)
# ---------------------------------------------------------------------------

PAIRWISE_PROMPT = """\
You are an expert first-aid evaluator. Two candidate responses are provided below \
for the same question. Decide which is better for an offline first-aid assistant \
(no internet, no EMS available).

QUESTION: {question}

REFERENCE ANSWER: {reference}

CANDIDATE A:
{answer_a}

CANDIDATE B:
{answer_b}

Respond ONLY with a JSON object -- no other text:
{{"winner": "A" | "B" | "tie", "rationale": "<reason in 30 words or fewer>"}}
"""


def run_pairwise(run_dir: Path, bank_path: Path, rubric_path: Path,
                 judge_ids: list[str], out_dir: Path,
                 dry_run: bool = False) -> None:
    """
    Pairwise F-vs-B for all 41 questions in both presentation orders.
    Results written to out_dir/pairwise_F_vs_B.json.
    """
    with open(run_dir / "run.json") as f:
        run = json.load(f)
    with open(bank_path) as f:
        bank = json.load(f)

    bank_by_id = {q["question_id"]: q for q in bank}
    f_answers = {a["question_id"]: a.get("answer","")
                 for a in run["variants"].get("F_RAG_BM25", {}).get("answers", [])}
    b_answers = {a["question_id"]: a.get("answer","")
                 for a in run["variants"].get("B_FINETUNED_4BIT", {}).get("answers", [])}

    all_qids = sorted(set(f_answers) & set(b_answers))
    manifest = load_manifest(out_dir)
    results = []

    for judge_id in judge_ids:
        if judge_id not in JUDGES:
            continue
        api_key = JUDGES[judge_id]["env_key"]
        if not os.environ.get(api_key) and not dry_run:
            print(f"SKIP pairwise {judge_id}: {api_key} not set")
            continue

        for qid in all_qids:
            q = bank_by_id.get(qid)
            if q is None:
                continue
            for order, (ans_a, ans_b, a_label, b_label) in enumerate([
                (f_answers[qid], b_answers[qid], "F", "B"),
                (b_answers[qid], f_answers[qid], "B", "F"),
            ]):
                cache_key = f"pairwise_{judge_id}_{qid}_order{order}"
                path = out_dir / "judgments" / "pairwise" / f"{cache_key}.json"
                path.parent.mkdir(parents=True, exist_ok=True)

                if path.exists():
                    with open(path) as f2:
                        results.append(json.load(f2))
                    continue

                if dry_run:
                    print(f"  [DRY_RUN] Pairwise {judge_id} {qid} order={order}")
                    continue

                user = PAIRWISE_PROMPT.format(
                    question=q["question"],
                    reference=q.get("reference",""),
                    answer_a=ans_a,
                    answer_b=ans_b,
                )

                for attempt in range(1, MAX_RETRIES + 1):
                    try:
                        raw, model_version = call_judge(judge_id, SYSTEM_PROMPT, user)
                        parsed = extract_json(raw)
                        if parsed is None or "winner" not in parsed:
                            raise ValueError(f"Bad pairwise response: {raw[:150]}")

                        # Normalise: "winner" is in terms of the actual config
                        reported_winner = parsed["winner"].upper()
                        if reported_winner == "A":
                            actual_winner = a_label
                        elif reported_winner == "B":
                            actual_winner = b_label
                        else:
                            actual_winner = "tie"

                        rec = {
                            "judge_id": judge_id,
                            "qid": qid,
                            "order": order,
                            "a_label": a_label,
                            "b_label": b_label,
                            "raw_winner": reported_winner,
                            "actual_winner": actual_winner,
                            "rationale": parsed.get("rationale",""),
                            "model_version": model_version,
                            "ts": datetime.now(timezone.utc).isoformat(),
                        }
                        with open(path, "w") as f2:
                            json.dump(rec, f2, indent=2)
                        results.append(rec)

                        if judge_id not in manifest:
                            manifest[judge_id] = {
                                "model_version": model_version,
                                "first_call_ts": rec["ts"],
                                "label": JUDGES[judge_id]["label"],
                            }
                        save_manifest(out_dir, manifest)
                        print(f"  [{judge_id}] pairwise {qid} order={order}: {actual_winner}")
                        break
                    except Exception as e:
                        print(f"  [{judge_id}] pairwise {qid} order={order}: attempt {attempt} failed: {e}")
                        if attempt < MAX_RETRIES:
                            time.sleep(RETRY_DELAY)

    # Aggregate pairwise results
    _aggregate_pairwise(results, out_dir)


def _aggregate_pairwise(results: list[dict], out_dir: Path) -> None:
    """Compute win-rate table with order sensitivity."""
    from collections import defaultdict
    import math

    # Per judge: win/tie/loss for F across both orders
    by_judge = defaultdict(lambda: {"F_wins": 0, "ties": 0, "B_wins": 0,
                                     "order0_F": 0, "order1_F": 0, "n": 0})
    for r in results:
        j = r["judge_id"]
        w = r["actual_winner"]
        o = r["order"]
        by_judge[j]["n"] += 1
        if w == "F":
            by_judge[j]["F_wins"] += 1
            if o == 0: by_judge[j]["order0_F"] += 1
            else:      by_judge[j]["order1_F"] += 1
        elif w == "tie":
            by_judge[j]["ties"] += 1
        else:
            by_judge[j]["B_wins"] += 1

    summary = {}
    for j, d in by_judge.items():
        n = d["n"]
        f_wins = d["F_wins"]
        win_rate = f_wins / n if n > 0 else 0
        # 95% Wilson CI
        z = 1.96
        if n > 0:
            centre = (f_wins + z*z/2) / (n + z*z)
            margin = z * math.sqrt((f_wins * (n - f_wins) / n + z*z/4) / (n + z*z))
            ci_lo, ci_hi = max(0, centre - margin), min(1, centre + margin)
        else:
            ci_lo, ci_hi = 0, 0

        # Order sensitivity: win rate in order-0 vs order-1
        n0 = sum(1 for r in results if r["judge_id"] == j and r["order"] == 0)
        n1 = sum(1 for r in results if r["judge_id"] == j and r["order"] == 1)
        rate0 = d["order0_F"] / n0 if n0 else 0
        rate1 = d["order1_F"] / n1 if n1 else 0

        summary[j] = {
            "judge_label": JUDGES.get(j, {}).get("label", j),
            "n_comparisons": n,
            "F_wins": f_wins, "ties": d["ties"], "B_wins": d["B_wins"],
            "F_win_rate": round(win_rate, 3),
            "wilson_95_lo": round(ci_lo, 3),
            "wilson_95_hi": round(ci_hi, 3),
            "F_win_rate_order0": round(rate0, 3),
            "F_win_rate_order1": round(rate1, 3),
            "order_sensitivity": round(abs(rate0 - rate1), 3),
        }

    out = {"pairwise_summary": summary, "raw_results": results}
    path = out_dir / "pairwise_F_vs_B.json"
    with open(path, "w", encoding="ascii", errors="replace") as f:
        json.dump(out, f, indent=2)

    print("\nPairwise F-vs-B win-rate table:")
    print(f"  {'Judge':<14} {'n':>4} {'F wins':>7} {'Ties':>5} {'B wins':>7} "
          f"{'F rate':>7} {'95% CI':>15} {'Order sens':>11}")
    print("  " + "-"*75)
    for j, s in summary.items():
        print(f"  {s['judge_label']:<14} {s['n_comparisons']:>4} "
              f"{s['F_wins']:>7} {s['ties']:>5} {s['B_wins']:>7} "
              f"{s['F_win_rate']:>7.3f} "
              f"[{s['wilson_95_lo']:.3f},{s['wilson_95_hi']:.3f}] "
              f"{s['order_sensitivity']:>11.3f}")
    print(f"\nSaved: {path}")

# ---------------------------------------------------------------------------
# DeepSeek correlation check
# ---------------------------------------------------------------------------

def run_correlation(run_dir: Path, mega_run_dir: Path,
                    out_dir: Path) -> None:
    """
    Correlate DeepSeek per-item scores (from out_dir/judgments/deepseek/)
    with June mega-prompt scores from mega_run_dir/run.json.
    """
    try:
        from scipy.stats import spearmanr
    except ImportError:
        print("scipy required for correlation: pip install scipy --break-system-packages")
        sys.exit(1)

    # Load mega-prompt scores from the June run
    mega_run_json = mega_run_dir / "run.json"
    if not mega_run_json.exists():
        print(f"Mega run not found: {mega_run_json}", file=sys.stderr)
        return

    with open(mega_run_json) as f:
        mega_run = json.load(f)

    # Collect per-item scores from cache
    judgment_dir = out_dir / "judgments" / "deepseek"
    if not judgment_dir.exists():
        print(f"No DeepSeek judgments yet at {judgment_dir}")
        return

    results = {}
    for cfg_dir in judgment_dir.iterdir():
        if not cfg_dir.is_dir():
            continue
        cfg = cfg_dir.name
        per_item = {}
        for jfile in cfg_dir.glob("*.json"):
            with open(jfile) as f:
                j = json.load(f)
            per_item[jfile.stem] = j.get("score")
        results[cfg] = per_item

    # Find matching DeepSeek scores in mega-prompt (stored in meta or as judge_scores)
    # The June mega-prompt used a text response -- look for numeric scores in variants
    # This is best-effort: the mega-prompt scores may not be stored per-item
    correlations = {}
    for cfg, per_item_scores in results.items():
        mega_variant = mega_run.get("variants", {}).get(cfg, {})
        mega_answers = mega_variant.get("answers", [])

        common_qids = []
        per_item_vals = []
        mega_vals = []

        for a in mega_answers:
            qid = a.get("question_id")
            pi_score = per_item_scores.get(qid)
            mega_score = a.get("meta", {}).get("judge_score_deepseek")
            if pi_score is not None and mega_score is not None:
                common_qids.append(qid)
                per_item_vals.append(pi_score)
                mega_vals.append(mega_score)

        if len(common_qids) >= 3:
            rho, pval = spearmanr(per_item_vals, mega_vals)
            correlations[cfg] = {
                "n_pairs": len(common_qids),
                "spearman_rho": round(rho, 4),
                "p_value": round(pval, 4),
            }
        else:
            correlations[cfg] = {
                "n_pairs": len(common_qids),
                "note": "insufficient paired data for correlation",
            }

    out = {
        "judge": "deepseek",
        "per_item_run": str(run_dir),
        "mega_prompt_run": str(mega_run_dir),
        "config_correlations": correlations,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    path = out_dir / "correlation_deepseek.json"
    with open(path, "w", encoding="ascii", errors="replace") as f:
        json.dump(out, f, indent=2)

    print("\nDeepSeek per-item vs mega-prompt Spearman correlations:")
    for cfg, d in correlations.items():
        rho = d.get("spearman_rho", "N/A")
        n   = d.get("n_pairs", 0)
        note = d.get("note", "")
        print(f"  {cfg:<24} n={n}  rho={rho}  {note}")
    print(f"\nSaved: {path}")

# ---------------------------------------------------------------------------
# Completion matrix
# ---------------------------------------------------------------------------

def write_completion_matrix(out_dir: Path, judge_ids: list[str],
                             config_labels: list[str],
                             variants: dict, bank: list) -> None:
    rows = []
    n_total = len(bank)
    for j in judge_ids:
        for cfg in config_labels:
            if cfg not in variants:
                rows.append(f"{j},{cfg},N/A,{n_total},N/A\n")
                continue
            done = sum(
                1 for q in bank
                if cache_path(out_dir, j, cfg, q["question_id"]).exists()
            )
            pct = f"{done/n_total*100:.1f}%" if n_total else "N/A"
            rows.append(f"{j},{cfg},{done},{n_total},{pct}\n")

    path = out_dir / "completion_matrix.csv"
    with open(path, "w", encoding="ascii") as f:
        f.write("judge,config,n_complete,n_total,pct\n")
        f.writelines(rows)

    # Also print table
    print("\nCompletion matrix:")
    print(f"  {'Judge':<12} {'Config':<24} {'Done':>5}/{n_total}  {'%':>5}")
    print("  " + "-"*55)
    for r in rows:
        j, cfg, done, tot, pct = r.strip().split(",")
        print(f"  {j:<12} {cfg:<24} {done:>5}/{tot}  {pct:>5}")

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Per-item LLM judge harness")
    p.add_argument("--run_dir", default=None,
                   help="Camera-ready run directory (auto-detects CAMERA_READY_* if omitted)")
    p.add_argument("--bank", default=None,
                   help="eval_bank_v2.json path (default: evaluations/eval_bank_v2_40q/eval_bank_v2.json)")
    p.add_argument("--rubric", default=None,
                   help="rubric_v2.md path (default: rubric_v2.md)")
    p.add_argument("--out_dir", default=None,
                   help="Output directory for judgments (default: <run_dir>)")
    p.add_argument("--judges", nargs="+", default=list(JUDGES.keys()),
                   choices=list(JUDGES.keys()),
                   help="Which judges to run (default: all six)")
    p.add_argument("--configs", nargs="+",
                   default=["A_BASE_4BIT","B_FINETUNED_4BIT","C_FINETUNED_8BIT",
                            "E_T6_IMPROVED","F_RAG_BM25","G_BASE_RAG"],
                   help="Which configs to score (default: all 6 camera-ready configs)")
    p.add_argument("--pairwise", action="store_true",
                   help="Run pairwise F-vs-B check (492 calls)")
    p.add_argument("--correlation", action="store_true",
                   help="Run DeepSeek per-item vs mega-prompt correlation")
    p.add_argument("--mega_run", default=None,
                   help="Path to the June mega-prompt run (for correlation check)")
    p.add_argument("--dry_run", action="store_true",
                   help="Print what would be called without actually calling APIs")
    p.add_argument("--seed", type=int, default=42,
                   help="Random seed for question order randomisation")
    return p.parse_args()


def main():
    args = parse_args()
    random.seed(args.seed)

    # ── Resolve paths ─────────────────────────────────────────────────────────
    HERE = Path(__file__).parent

    if args.run_dir is None:
        eval_dir = HERE / "evaluations"
        candidates = sorted(
            d for d in eval_dir.iterdir()
            if d.is_dir() and d.name.startswith("CAMERA_READY_")
        )
        if not candidates:
            print("ERROR: No CAMERA_READY_* directory found.", file=sys.stderr)
            sys.exit(1)
        run_dir = candidates[-1]
        print(f"[auto] Using run: {run_dir.name}")
    else:
        run_dir = Path(args.run_dir)

    bank_path = Path(args.bank) if args.bank else \
        HERE / "evaluations" / "eval_bank_v2_40q" / "eval_bank_v2.json"

    rubric_path = Path(args.rubric) if args.rubric else HERE / "rubric_v2.md"

    out_dir = Path(args.out_dir) if args.out_dir else run_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    for p, label in [(run_dir/"run.json", "run.json"),
                     (bank_path, "bank"),
                     (rubric_path, "rubric")]:
        if not p.exists():
            print(f"ERROR: {label} not found: {p}", file=sys.stderr)
            sys.exit(1)

    # ── Blinding caveat ───────────────────────────────────────────────────────
    print("\nNOTE: Config E (E_T6_IMPROVED) includes a self-identifying gate-fallback")
    print("  text in some answers. This partially breaks blinding. Document in")
    print("  paper protocol description (Section: Blinding Caveat).")
    print()

    # ── Run selected mode ─────────────────────────────────────────────────────
    if args.pairwise:
        run_pairwise(run_dir, bank_path, rubric_path,
                     args.judges, out_dir, dry_run=args.dry_run)

    elif args.correlation:
        mega_run_dir = Path(args.mega_run) if args.mega_run else \
            HERE / "evaluations" / "v2_comprehensive_20260606_200713"
        run_correlation(run_dir, mega_run_dir, out_dir)

    else:
        run_scoring(run_dir, bank_path, rubric_path,
                    args.judges, args.configs, out_dir,
                    dry_run=args.dry_run)

    # Final manifest summary
    manifest = load_manifest(out_dir)
    if manifest:
        print("\nJudges manifest:")
        for j_id, info in manifest.items():
            print(f"  {info.get('label','?'):<14} model={info.get('model_version','?')}"
                  f"  first_call={info.get('first_call_ts','?')[:19]}")


if __name__ == "__main__":
    main()
