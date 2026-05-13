"""
evaluate.py  --  Automatic metrics for eval_suite.py output files
==================================================================
Computes ROUGE-1/2/L and BERTScore (PubMedBERT) for every model variant
in one or more eval_suite result files, then writes a metrics JSON and
prints a paper-ready summary table.

Prerequisites (install once):
  pip install rouge-score bert-score

Usage:
  # Evaluate the most recent run automatically
  python evaluate.py

  # Evaluate a specific run file
  python evaluate.py --run evaluations/eval_20250505_143022/run.json

  # Evaluate ALL run files and aggregate
  python evaluate.py --all

  # Skip BERTScore (much faster, ROUGE only)
  python evaluate.py --no-bert

  # Use a different BERTScore model (default: microsoft/BiomedNLP-PubMedBERT-base-uncased-abstract)
  python evaluate.py --bert-model allenai/scibert_scivocab_uncased

Output:
  evaluations/eval_<run_id>/metrics.json  -- full per-question metrics
  Terminal                              -- compact summary table
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Optional dependency check
# ---------------------------------------------------------------------------

def _check_deps(want_bert: bool):
    missing = []
    try:
        from rouge_score import rouge_scorer  # noqa: F401
    except Exception:
        missing.append("rouge-score")
    if want_bert:
        try:
            import bert_score  # noqa: F401
        except Exception:
            missing.append("bert-score")
    if missing:
        print("[evaluate] Missing packages:", ", ".join(missing), flush=True)
        print("           Install with:  pip install", " ".join(missing), flush=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

HERE        = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(HERE, "evaluations")

DEFAULT_BERT_MODEL = "allenai/scibert_scivocab_uncased"

# ---------------------------------------------------------------------------
# ROUGE helpers
# ---------------------------------------------------------------------------

def compute_rouge(predictions: list[str], references: list[str]) -> dict:
    """Return mean ROUGE-1, ROUGE-2, ROUGE-L F1 across the list."""
    from rouge_score import rouge_scorer as rs_module

    scorer = rs_module.RougeScorer(["rouge1", "rouge2", "rougeL"], use_stemmer=True)
    r1, r2, rl = [], [], []
    for pred, ref in zip(predictions, references):
        if not pred or not ref:
            continue
        scores = scorer.score(ref, pred)
        r1.append(scores["rouge1"].fmeasure)
        r2.append(scores["rouge2"].fmeasure)
        rl.append(scores["rougeL"].fmeasure)

    def _mean(lst):
        return round(sum(lst) / len(lst), 4) if lst else None

    return {
        "rouge1": _mean(r1),
        "rouge2": _mean(r2),
        "rougeL": _mean(rl),
        "n": len(r1),
    }


def compute_rouge_per_item(predictions: list[str], references: list[str]) -> list[dict]:
    """Return per-question ROUGE scores."""
    from rouge_score import rouge_scorer as rs_module

    scorer = rs_module.RougeScorer(["rouge1", "rouge2", "rougeL"], use_stemmer=True)
    out = []
    for pred, ref in zip(predictions, references):
        if not pred or not ref:
            out.append({"rouge1": None, "rouge2": None, "rougeL": None})
            continue
        s = scorer.score(ref, pred)
        out.append({
            "rouge1": round(s["rouge1"].fmeasure, 4),
            "rouge2": round(s["rouge2"].fmeasure, 4),
            "rougeL": round(s["rougeL"].fmeasure, 4),
        })
    return out


# ---------------------------------------------------------------------------
# BERTScore helpers
# ---------------------------------------------------------------------------

def _bert_num_layers(model_type: str) -> int | None:
    """
    Return the recommended num_layers for a model not in bert_score's registry.
    Falls back to None (let bert_score pick) if the model config can't be read.
    BERT-base = 9, BERT-large = 17 (bert_score conventions).
    """
    try:
        from bert_score.utils import model2layers
        if model_type in model2layers:
            return None  # already known, no override needed
    except Exception:
        pass
    # Try to read num_hidden_layers from the model config
    try:
        from transformers import AutoConfig
        cfg = AutoConfig.from_pretrained(model_type)
        n = getattr(cfg, "num_hidden_layers", 12)
        # bert_score uses layer 9 for 12-layer models, 17 for 24-layer models
        return 9 if n <= 12 else 17
    except Exception:
        return 9  # safe default for BERT-base sized models


def compute_bertscore(
    predictions: list[str],
    references:  list[str],
    model_type:  str,
    device:      str = "cpu",
    batch_size:  int = 8,
) -> dict:
    """Return mean BERTScore P/R/F1."""
    import bert_score

    # Filter empty
    pairs = [(p, r) for p, r in zip(predictions, references) if p and r]
    if not pairs:
        return {"bertscore_p": None, "bertscore_r": None, "bertscore_f1": None, "n": 0}

    preds, refs = zip(*pairs)
    num_layers = _bert_num_layers(model_type)
    kwargs = dict(model_type=model_type, device=device, batch_size=batch_size, verbose=False)
    if num_layers is not None:
        kwargs["num_layers"] = num_layers
    P, R, F1 = bert_score.score(list(preds), list(refs), **kwargs)

    def _m(t):
        return round(float(t.mean()), 4)

    return {
        "bertscore_p":  _m(P),
        "bertscore_r":  _m(R),
        "bertscore_f1": _m(F1),
        "n": len(pairs),
    }


def compute_bertscore_per_item(
    predictions: list[str],
    references:  list[str],
    model_type:  str,
    device:      str = "cpu",
    batch_size:  int = 8,
) -> list[dict]:
    """Return per-question BERTScore F1."""
    import bert_score

    results = []
    for pred, ref in zip(predictions, references):
        if not pred or not ref:
            results.append({"bertscore_f1": None})
            continue
        num_layers = _bert_num_layers(model_type)
        kwargs = dict(model_type=model_type, device=device, verbose=False)
        if num_layers is not None:
            kwargs["num_layers"] = num_layers
        _, _, F1 = bert_score.score([pred], [ref], **kwargs)
        results.append({"bertscore_f1": round(float(F1[0]), 4)})
    return results


# ---------------------------------------------------------------------------
# Subset grouping helpers
# ---------------------------------------------------------------------------

def _group_answers(answers: list[dict], key: str) -> dict[str, list[dict]]:
    """Partition answer dicts by a string key (e.g. 'category')."""
    groups: dict[str, list] = {}
    for a in answers:
        val = str(a.get(key, "unknown"))
        groups.setdefault(val, []).append(a)
    return groups


def _sc_split(answers: list[dict]) -> tuple[list, list]:
    """Returns (safety_critical_list, non_urgent_list)."""
    sc  = [a for a in answers if a.get("safety_critical") is True]
    non = [a for a in answers if a.get("safety_critical") is not True]
    return sc, non


# ---------------------------------------------------------------------------
# Per-variant evaluation
# ---------------------------------------------------------------------------

def _coerce_answer(a: dict) -> dict:
    """
    eval_suite.py serialises everything as strings (e.g. error="None",
    safety_critical="True", tokens_per_sec="18.98").  Normalise in place.
    """
    # error: "None" string → Python None
    err = a.get("error")
    if isinstance(err, str) and err.lower() in ("none", ""):
        a["error"] = None

    # safety_critical: "True"/"False" → bool
    sc = a.get("safety_critical")
    if isinstance(sc, str):
        a["safety_critical"] = sc.strip().lower() == "true"

    # numeric fields stored as strings
    for field in ("tokens_per_sec", "peak_vram_mb", "tokens_generated", "elapsed_s"):
        v = a.get(field)
        if isinstance(v, str):
            try:
                a[field] = float(v)
            except (ValueError, TypeError):
                a[field] = None

    # question_id: string "1" → int
    qid = a.get("question_id")
    if isinstance(qid, str):
        try:
            a["question_id"] = int(qid)
        except (ValueError, TypeError):
            pass

    return a


def evaluate_variant(
    variant_result: dict,
    want_bert:      bool,
    bert_model:     str,
    bert_device:    str,
) -> dict:
    """
    Given one variant entry from the run JSON, compute all metrics.
    Returns a metrics dict ready for JSON serialisation.
    """
    label   = variant_result.get("label", variant_result.get("variant"))
    answers = [_coerce_answer(a) for a in variant_result.get("answers", [])]

    # Only scored answers (no errors)
    valid = [a for a in answers if a.get("error") is None and a.get("answer")]

    preds = [a["answer"]    for a in valid]
    refs  = [a["reference"] for a in valid]

    # --- ROUGE ---
    if not valid:
        print(f"    WARNING: 0 valid answers found for '{label}' — skipping", flush=True)
    print(f"    ROUGE  ({len(valid)} answers)...", end=" ", flush=True)
    rouge_overall   = compute_rouge(preds, refs)
    rouge_per_item  = compute_rouge_per_item(preds, refs)
    print("done")

    # Attach per-item scores back to valid answers for downstream breakdown
    for a, rogue in zip(valid, rouge_per_item):
        a["_rouge"] = rogue

    # --- BERTScore ---
    bert_overall  = {}
    if want_bert and valid:
        print(f"    BERTScore ({bert_model})...", end=" ", flush=True)
        bert_overall = compute_bertscore(preds, refs, bert_model, bert_device)
        bert_per_item = compute_bertscore_per_item(preds, refs, bert_model, bert_device)
        for a, bs in zip(valid, bert_per_item):
            a["_bert"] = bs
        print(f"  F1={bert_overall['bertscore_f1']}")

    # --- Safety-critical split ---
    sc_answers, non_answers = _sc_split(valid)
    sc_preds  = [a["answer"]    for a in sc_answers]
    sc_refs   = [a["reference"] for a in sc_answers]
    non_preds = [a["answer"]    for a in non_answers]
    non_refs  = [a["reference"] for a in non_answers]

    sc_rouge  = compute_rouge(sc_preds,  sc_refs)
    non_rouge = compute_rouge(non_preds, non_refs)
    sc_bert   = compute_bertscore(sc_preds,  sc_refs,  bert_model, bert_device) if want_bert and sc_answers  else {}
    non_bert  = compute_bertscore(non_preds, non_refs, bert_model, bert_device) if want_bert and non_answers else {}

    # --- Per-category breakdown ---
    cat_groups   = _group_answers(valid, "category")
    cat_metrics  = {}
    for cat, items in sorted(cat_groups.items()):
        cp = [a["answer"]    for a in items]
        cr = [a["reference"] for a in items]
        entry = compute_rouge(cp, cr)
        if want_bert and items:
            entry.update(compute_bertscore(cp, cr, bert_model, bert_device))
        cat_metrics[cat] = entry

    # --- Speed / VRAM ---
    tps_vals  = [a["tokens_per_sec"] for a in valid if a.get("tokens_per_sec")]
    vram_vals = [a["peak_vram_mb"]   for a in valid if a.get("peak_vram_mb")]

    def _safe_mean(lst):
        return round(sum(lst) / len(lst), 1) if lst else None

    speed_stats = {
        "mean_tok_per_sec": _safe_mean(tps_vals),
        "mean_peak_vram_mb": _safe_mean(vram_vals),
    }

    # --- Per-question detail (for JSON output) ---
    per_question = []
    for a in valid:
        per_question.append({
            "id":               a.get("question_id"),
            "category":         a.get("category"),
            "safety_critical":  a.get("safety_critical"),
            "rouge1":           a.get("_rouge", {}).get("rouge1"),
            "rouge2":           a.get("_rouge", {}).get("rouge2"),
            "rougeL":           a.get("_rouge", {}).get("rougeL"),
            "bertscore_f1":     a.get("_bert",  {}).get("bertscore_f1"),
            "tokens_per_sec":   a.get("tokens_per_sec"),
            "peak_vram_mb":     a.get("peak_vram_mb"),
        })

    return {
        "variant": variant_result.get("variant"),
        "label":   label,
        "n_valid": len(valid),
        "n_error": len(answers) - len(valid),
        "overall": {**rouge_overall, **bert_overall},
        "safety_critical": {
            "n": len(sc_answers),
            **sc_rouge,
            **sc_bert,
        },
        "non_urgent": {
            "n": len(non_answers),
            **non_rouge,
            **non_bert,
        },
        "per_category": cat_metrics,
        "speed":         speed_stats,
        "per_question":  per_question,
    }


# ---------------------------------------------------------------------------
# Run-level evaluation
# ---------------------------------------------------------------------------

def evaluate_run(
    run_path:    str,
    want_bert:   bool,
    bert_model:  str,
    bert_device: str,
) -> dict:
    with open(run_path, encoding="utf-8") as f:
        run = json.load(f)

    run_id    = run.get("run_id", "unknown")
    timestamp = run.get("timestamp", "")
    meta      = run.get("meta", {})

    print(f"\n[evaluate] Run : {run_id}  ({timestamp})", flush=True)
    print(f"           File: {run_path}", flush=True)

    variant_metrics = []
    for vr in run.get("results", []):
        label = vr.get("label", vr.get("variant"))
        print(f"  -> Variant: {label}", flush=True)
        vm = evaluate_variant(vr, want_bert, bert_model, bert_device)
        variant_metrics.append(vm)

    return {
        "run_id":    run_id,
        "timestamp": timestamp,
        "source":    run_path,
        "meta":      meta,
        "variants":  variant_metrics,
        "evaluated_at": datetime.now().isoformat(),
        "bert_model": bert_model if want_bert else None,
    }


# ---------------------------------------------------------------------------
# Summary table printer
# ---------------------------------------------------------------------------

def print_summary(metrics: dict):
    want_bert = metrics.get("bert_model") is not None

    print("\n" + "=" * 80)
    print(f"  Run : {metrics['run_id']}   Evaluated: {metrics['evaluated_at'][:19]}")
    if want_bert:
        print(f"  BERTScore model: {metrics['bert_model']}")
    print("=" * 80)

    # Header
    if want_bert:
        hdr = f"  {'Variant':<18}  {'ROUGE-1':>8}  {'ROUGE-2':>8}  {'ROUGE-L':>8}  {'BERT-F1':>8}  {'tok/s':>7}  {'VRAM MB':>8}"
    else:
        hdr = f"  {'Variant':<18}  {'ROUGE-1':>8}  {'ROUGE-2':>8}  {'ROUGE-L':>8}  {'tok/s':>7}  {'VRAM MB':>8}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    for v in metrics["variants"]:
        ov = v["overall"]
        sp = v["speed"]
        r1 = f"{ov.get('rouge1', 0):.4f}" if ov.get("rouge1") is not None else "  n/a  "
        r2 = f"{ov.get('rouge2', 0):.4f}" if ov.get("rouge2") is not None else "  n/a  "
        rl = f"{ov.get('rougeL', 0):.4f}" if ov.get("rougeL") is not None else "  n/a  "
        bf = f"{ov.get('bertscore_f1', 0):.4f}" if ov.get("bertscore_f1") is not None else "  n/a  "
        tps  = f"{sp.get('mean_tok_per_sec', 0):.1f}"  if sp.get("mean_tok_per_sec")  else "  n/a "
        vram = f"{sp.get('mean_peak_vram_mb', 0):.0f}" if sp.get("mean_peak_vram_mb") else "  n/a "

        if want_bert:
            print(f"  {v['label']:<18}  {r1:>8}  {r2:>8}  {rl:>8}  {bf:>8}  {tps:>7}  {vram:>8}")
        else:
            print(f"  {v['label']:<18}  {r1:>8}  {r2:>8}  {rl:>8}  {tps:>7}  {vram:>8}")

    # SC vs non-urgent
    print("\n  -- Safety-critical vs Non-urgent ROUGE-L --")
    sc_hdr = f"  {'Variant':<18}  {'SC ROUGE-L':>12}  {'Non-SC ROUGE-L':>14}  {'SC BERT-F1':>12}  {'Non-SC BERT-F1':>14}"
    print(sc_hdr)
    print("  " + "-" * (len(sc_hdr) - 2))
    for v in metrics["variants"]:
        sc  = v["safety_critical"]
        non = v["non_urgent"]
        sc_rl   = f"{sc.get('rougeL',  0):.4f}" if sc.get("rougeL")  is not None else "  n/a    "
        non_rl  = f"{non.get('rougeL', 0):.4f}" if non.get("rougeL") is not None else "  n/a      "
        sc_bf   = f"{sc.get('bertscore_f1',  0):.4f}" if sc.get("bertscore_f1")  is not None else "  n/a    "
        non_bf  = f"{non.get('bertscore_f1', 0):.4f}" if non.get("bertscore_f1") is not None else "  n/a      "
        print(f"  {v['label']:<18}  {sc_rl:>12}  {non_rl:>14}  {sc_bf:>12}  {non_bf:>14}")

    # Per-category ROUGE-L (transpose: rows=categories, cols=variants)
    print("\n  -- Per-category ROUGE-L --")
    all_cats = sorted({
        cat
        for v in metrics["variants"]
        for cat in v.get("per_category", {})
    })
    variant_labels = [v["label"] for v in metrics["variants"]]
    col_w = max(len(c) for c in all_cats) if all_cats else 20

    cat_hdr = f"  {'Category':<{col_w}}" + "".join(f"  {lbl:>9}" for lbl in variant_labels)
    print(cat_hdr)
    print("  " + "-" * (len(cat_hdr) - 2))
    for cat in all_cats:
        row = f"  {cat:<{col_w}}"
        for v in metrics["variants"]:
            val = v.get("per_category", {}).get(cat, {}).get("rougeL")
            cell = f"{val:.4f}" if val is not None else "  n/a "
            row += f"  {cell:>9}"
        print(row)

    print("=" * 80 + "\n")


# ---------------------------------------------------------------------------
# Save metrics JSON
# ---------------------------------------------------------------------------

def save_metrics(metrics: dict, run_path: str) -> str:
    # Save metrics.json alongside run.json in the same eval_<id>/ folder
    eval_folder = os.path.dirname(os.path.abspath(run_path))
    out_path = os.path.join(eval_folder, "metrics.json")
    with open(out_path, "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"[evaluate] Metrics saved -> {out_path}")
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def find_latest_run() -> str | None:
    """Return path to run.json in the most recent evaluations/*eval_*/ folder."""
    if not os.path.isdir(RESULTS_DIR):
        return None
    runs = sorted(Path(RESULTS_DIR).glob("*eval_*/run.json"))
    return str(runs[-1]) if runs else None


def find_all_runs() -> list[str]:
    """Return paths to all run.json files across evaluations/*eval_*/ folders."""
    if not os.path.isdir(RESULTS_DIR):
        return []
    return sorted(str(p) for p in Path(RESULTS_DIR).glob("*eval_*/run.json"))


def parse_args():
    p = argparse.ArgumentParser(
        description="Compute ROUGE + BERTScore metrics for eval_suite.py results",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--run",
        default="",
        help="Path to a specific eval_suite run JSON. "
             "Defaults to run.json in the most recent evaluations/eval_*/ folder.",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="Evaluate ALL run.json files across evaluations/eval_*/ folders.",
    )
    p.add_argument(
        "--no-bert",
        action="store_true",
        help="Skip BERTScore (ROUGE only). This is now the DEFAULT — kept for backwards compatibility.",
    )
    p.add_argument(
        "--bert",
        action="store_true",
        help="Enable BERTScore (requires bert-score package and ~2 GB model download).",
    )
    p.add_argument(
        "--bert-model",
        default=DEFAULT_BERT_MODEL,
        help=f"HuggingFace model for BERTScore (default: {DEFAULT_BERT_MODEL}).",
    )
    p.add_argument(
        "--bert-device",
        default="cpu",
        choices=["cpu", "cuda", "mps"],
        help="Device for BERTScore inference (default: cpu). "
             "Use 'cuda' if you have a spare GPU after eval_suite.",
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Aggregate across multiple runs (for --all)
# ---------------------------------------------------------------------------

def aggregate_runs(all_metrics: list[dict]) -> dict:
    """
    Given metrics dicts from multiple runs, produce a single summary dict
    that shows mean ± std for each variant's key metrics.
    """
    import statistics

    # Collect values by variant label
    per_variant: dict[str, dict[str, list]] = {}
    for m in all_metrics:
        for v in m["variants"]:
            lbl = v["label"]
            per_variant.setdefault(lbl, {
                "rouge1": [], "rouge2": [], "rougeL": [],
                "bertscore_f1": [],
                "sc_rougeL": [], "non_rougeL": [],
                "mean_tok_per_sec": [],
            })
            ov = v["overall"]
            sc = v["safety_critical"]
            non = v["non_urgent"]
            sp = v["speed"]
            if ov.get("rouge1")       is not None: per_variant[lbl]["rouge1"].append(ov["rouge1"])
            if ov.get("rouge2")       is not None: per_variant[lbl]["rouge2"].append(ov["rouge2"])
            if ov.get("rougeL")       is not None: per_variant[lbl]["rougeL"].append(ov["rougeL"])
            if ov.get("bertscore_f1") is not None: per_variant[lbl]["bertscore_f1"].append(ov["bertscore_f1"])
            if sc.get("rougeL")       is not None: per_variant[lbl]["sc_rougeL"].append(sc["rougeL"])
            if non.get("rougeL")      is not None: per_variant[lbl]["non_rougeL"].append(non["rougeL"])
            if sp.get("mean_tok_per_sec") is not None: per_variant[lbl]["mean_tok_per_sec"].append(sp["mean_tok_per_sec"])

    def _stats(lst):
        if not lst:
            return {"mean": None, "std": None, "n": 0}
        return {
            "mean": round(sum(lst) / len(lst), 4),
            "std":  round(statistics.stdev(lst), 4) if len(lst) > 1 else 0.0,
            "n":    len(lst),
        }

    agg = {}
    for lbl, vals in per_variant.items():
        agg[lbl] = {k: _stats(v) for k, v in vals.items()}

    return {
        "aggregate_over_n_runs": len(all_metrics),
        "run_ids": [m["run_id"] for m in all_metrics],
        "variants": agg,
        "computed_at": datetime.now().isoformat(),
    }


def print_aggregate(agg: dict):
    print("\n" + "=" * 80)
    print(f"  AGGREGATE  ({agg['aggregate_over_n_runs']} runs: {', '.join(agg['run_ids'])})")
    print("=" * 80)
    hdr = f"  {'Variant':<18}  {'R-1 mean±std':>14}  {'R-L mean±std':>14}  {'BERT-F1 mean±std':>18}  {'tok/s mean':>10}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for lbl, stats in agg["variants"].items():
        def fmt(key):
            s = stats.get(key, {})
            m, sd = s.get("mean"), s.get("std")
            if m is None:
                return "    n/a       "
            return f"{m:.4f}±{sd:.4f}"
        tps_s = stats.get("mean_tok_per_sec", {})
        tps   = f"{tps_s['mean']:.1f}" if tps_s.get("mean") else "  n/a "
        print(f"  {lbl:<18}  {fmt('rouge1'):>14}  {fmt('rougeL'):>14}  {fmt('bertscore_f1'):>18}  {tps:>10}")
    print("=" * 80 + "\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    # BERT is opt-IN now (--bert flag). --no-bert kept for back-compat but has no effect.
    # Old default (want_bert=True) was crashing silently when bert_score wasn't properly
    # installed because non-ImportError exceptions weren't caught by the dep check.
    want_bert = args.bert and not args.no_bert

    print(f"[evaluate] Starting  (ROUGE only={not want_bert})", flush=True)
    _check_deps(want_bert)

    if args.all:
        run_paths = find_all_runs()
        if not run_paths:
            print("[evaluate] No run.json files found in", RESULTS_DIR)
            sys.exit(1)
        print(f"[evaluate] Found {len(run_paths)} run files.")
    elif args.run:
        run_paths = [args.run]
    else:
        latest = find_latest_run()
        if not latest:
            print("[evaluate] No run.json files found in", RESULTS_DIR)
            print("           Run eval_suite.py first to generate results.")
            sys.exit(1)
        run_paths = [latest]
        print(f"[evaluate] Auto-selected latest run: {latest}")

    all_metrics = []
    for rp in run_paths:
        metrics = evaluate_run(rp, want_bert, args.bert_model, args.bert_device)
        save_metrics(metrics, rp)
        print_summary(metrics)
        all_metrics.append(metrics)

    if args.all and len(all_metrics) > 1:
        agg = aggregate_runs(all_metrics)
        agg_path = os.path.join(RESULTS_DIR, "aggregate_metrics.json")
        with open(agg_path, "w") as f:
            json.dump(agg, f, indent=2)
        print(f"[evaluate] Aggregate saved -> {agg_path}")


if __name__ == "__main__":
    main()
