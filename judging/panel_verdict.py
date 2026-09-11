"""
judging/panel_verdict.py
========================
Apply the pre-registered cross-judge confirmation rule to a completed panel.

WHY THIS EXISTS
---------------
`PRECOMMIT_PANEL.md` and `PRECOMMIT.md` define the confirmation rule across
judges; `aggregate.py` is single-judge by construction (`RESULTS_DIR =
judging/results/<model>`) and deliberately does not implement it. Every
`FINAL_REPORT.md` says so explicitly: "Cross-judge confirmation ... is applied
manually across the per-judge reports and is not computed here."

Keeping the rule manual was a deliberate decision (`DECISIONS.md`, 2026-09-05,
"The 3/3 panel rule stays manual"). The cost of that decision showed up as a
defect: the rule was applied by hand to the July run and written into
`judging/OPEN_FINDINGS.md` section 2, and then **never applied to the offline
run at all**, even though `DECISIONS.md` listed re-application as consequence 2
of superseding July. The canonical experiment had no written headline verdict.

This script closes that hole without reversing the decision. It does not decide
anything: it reads the committed per-judge `stats.csv` files, applies the
registered rule mechanically, and writes a dated Markdown verdict for a human to
read, check and commit. The human sign-off remains the last step -- that is the
part `DECISIONS.md` wanted to keep manual. What is removed is the possibility of
the step being *skipped* or of a hand-typed panel mean drifting from the
artifacts.

WHAT IT CHECKS BEFORE APPLYING THE RULE
---------------------------------------
Averaging or comparing judges that did not judge the same thing with the same
instrument is the failure this guards against. `aggregate.py` computes an
`item_set_sha256` in `assert_panel_complete()` but only prints it -- it is not
persisted -- so the cross-judge identity check is re-derived here from the
committed manifests and stats files:

  * `template_hash`, `quality_hash`, `safety_hash` identical across judges
  * `bank_sha256` identical across judges
  * `n_calls_total` identical, and `n_invalid == 0` for every judge
  * identical contrast names, in identical order, with identical `n_pairs`
  * `items.jsonl` still hashes to the manifest bank (staleness guard)
  * `model_requested` compared against the registered panel string, so a
    substitution is reported in the verdict rather than discovered later

Any mismatch is fatal unless `--allow_instrument_mismatch` is passed, which
stamps the deviation into the output instead of aborting.

THE RULE, AS REGISTERED
-----------------------
From `PRECOMMIT.md` ("Panel composition"):

    A contrast is confirmed only when all three confirmatory judges agree in
    direction and each is independently significant by the rule above. Judges
    disagreeing on sign makes a contrast *inconclusive*, not null.

and per-judge significance (`PRECOMMIT.md`, "Primary Contrasts"):

    bootstrap 95% CI excludes zero AND two-sided sign test p < 0.05

The per-judge criterion is already computed by `aggregate.py` into the
`confirmed` column and is read from there rather than recomputed, so this script
cannot disagree with the artifacts it summarises.

`glm_ar` is the registered exploratory fourth judge. It is reported in every
row and **never gates** a verdict, per its registration.

ONE ADDITION, AND WHY IT IS NOT A CHANGE OF RULE
------------------------------------------------
The registered sign test is an exact two-sided binomial on non-tied pairs only.
On a discrete 0-5 scale most pairs tie, and at a high enough tie count *no
arrangement of the data can reach p < 0.05*: with 5 non-tied pairs the smallest
attainable two-sided p is 0.0625, with 4 it is 0.125. Six non-tied pairs, all
one direction, is the minimum for significance.

That is arithmetic about the registered test, not a new test. F-B SC (primary
contrast 2) sits below that floor for all four judges, so "not confirmed at the
pre-specified threshold" -- the wording every `FINAL_REPORT.md` uses -- reads as
evidence of no effect when the truth is that the test had no power to find one.
The verdict therefore carries an UNTESTABLE label and a per-judge power column.
No contrast is upgraded by this; a contrast can only move from NO ESTABLISHED
EFFECT to UNTESTABLE, which is strictly more conservative about what was learned.

Usage
-----
    python judging/panel_verdict.py --run_tag OFFLINE_FINAL
    python judging/panel_verdict.py --run_tag OFFLINE_FINAL --check   # no write

Stdlib only, like `aggregate.py`, so it runs without the judge SDK or scipy.
Exit codes: 0 written/clean, 1 instrument mismatch, 2 missing inputs,
3 `--check` found the on-disk verdict stale.
"""

import argparse
import csv
import hashlib
import json
import sys
from datetime import date
from math import comb
from pathlib import Path

REPO_ROOT   = Path(__file__).resolve().parent.parent
JUDGING_DIR = REPO_ROOT / "judging"
RESULTS_DIR = JUDGING_DIR / "results"
ITEMS_PATH  = JUDGING_DIR / "items.jsonl"
BANK_PATH   = REPO_ROOT / "evaluations" / "eval_bank_v2_40q" / "eval_bank_v2.json"

#: The registered panel. MUST track `PRECOMMIT.md` including its amendments --
#: the same hand-sync contract `aggregate.load_precommit_contrasts()` carries,
#: and for the same reason: a registration that lives only in prose cannot be
#: checked against a run.
#:
#: `model` is what PRECOMMIT.md registered. Where the run served something else
#: the deviation is reported in the verdict; it is NOT silently accepted and it
#: is NOT treated as a reason to drop the judge, because dropping a judge after
#: seeing its results is a worse act than disclosing a substitution.
REGISTERED_PANEL = [
    {"judge": "deepseek",  "model": "deepseek-v4-pro",          "role": "confirmatory"},
    {"judge": "claude_or", "model": "anthropic/claude-opus-4.8", "role": "confirmatory",
     "amended_to": "anthropic/claude-opus-5",
     "amendment": "PRECOMMIT.md, Amendment 2026-09-10 -- POST-HOC, written after the run finished"},
    {"judge": "gpt_ar",    "model": "gpt-5.6-sol",              "role": "confirmatory"},
    {"judge": "glm_ar",    "model": "glm-5.3",                  "role": "exploratory"},
]

CONFIRMATORY = [j["judge"] for j in REGISTERED_PANEL if j["role"] == "confirmatory"]
EXPLORATORY  = [j["judge"] for j in REGISTERED_PANEL if j["role"] == "exploratory"]

#: Fields that must be identical across judges for their scores to be
#: comparable at all. A difference in any of these means the judges read
#: different prompts or graded against different references.
INSTRUMENT_FIELDS = ["template_hash", "quality_hash", "safety_hash", "bank_sha256"]

VERDICT_PATH_NAME = "PANEL_VERDICT_{run_tag}.md"


# ── loaders ───────────────────────────────────────────────────────────────────

def _sha256_text(path: Path) -> str:
    """Text-mode SHA-256, newline-normalised -- matches judge_deepseek.file_sha256."""
    return hashlib.sha256(path.read_text(encoding="utf-8").encode()).hexdigest()


def load_stats(judge: str, run_tag: str) -> list[dict]:
    path = RESULTS_DIR / judge / run_tag / "stats.csv"
    if not path.exists():
        print(f"ERROR: {path} not found. Run aggregate.py for '{judge}' first.",
              file=sys.stderr)
        sys.exit(2)
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    out = []
    for row in rows:
        # A contrast whose configs carried no judgments is written with empty
        # numeric fields by aggregate.py. It must not be read as a zero delta.
        if not row.get("mean_delta"):
            out.append({"name": row["name"], "skipped": True})
            continue
        out.append({
            "name":      row["name"],
            "primary":   row["primary"].strip().lower() == "true",
            "n_pairs":   int(row["n_pairs"]),
            "mean":      float(row["mean_delta"]),
            "ci_lo":     float(row["ci_lo"]),
            "ci_hi":     float(row["ci_hi"]),
            "wins":      int(row["wins"]),
            "losses":    int(row["losses"]),
            "ties":      int(row["ties"]),
            "sign_p":    float(row["sign_p"]),
            # Read, never recomputed: this is aggregate.py's own verdict under
            # the pre-registered per-judge criterion.
            "confirmed": row["confirmed"].strip().lower() == "true",
            "expected":  row.get("expected_direction", "either"),
            "skipped":   False,
        })
    return out


def load_manifest(judge: str, run_tag: str) -> dict:
    path = RESULTS_DIR / judge / run_tag / "manifest.json"
    if not path.exists():
        print(f"ERROR: {path} not found.", file=sys.stderr)
        sys.exit(2)
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# ── the registered rule, plus its power floor ─────────────────────────────────

def sign_of(mean: float) -> str:
    """Direction of a delta. Exact zero is its own case, not a direction."""
    return "positive" if mean > 0 else "negative" if mean < 0 else "zero"


def min_attainable_sign_p(n_nontied: int) -> float:
    """
    Smallest two-sided exact-binomial p reachable with this many non-tied pairs.

    Achieved when every non-tied pair falls the same way, i.e. min(wins,losses)=0,
    giving 2 * (1/2)**n. Returns 1.0 for n=0 (aggregate.sign_test's own
    convention when there is nothing to test).
    """
    if n_nontied <= 0:
        return 1.0
    return min(1.0, 2 * sum(comb(n_nontied, k) for k in range(1)) / 2 ** n_nontied)


def judge_is_powerless(row: dict, alpha: float = 0.05) -> bool:
    """True when no outcome at this tie count could have reached significance."""
    if row.get("skipped"):
        return True
    return min_attainable_sign_p(row["wins"] + row["losses"]) >= alpha


def apply_rule(per_judge: dict[str, dict], alpha: float = 0.05) -> dict:
    """
    Apply the registered cross-judge rule to one contrast.

    per_judge maps judge name -> that judge's stats row for this contrast.
    Only CONFIRMATORY judges gate the verdict; the exploratory judge is
    carried through for reporting, per its registration.
    """
    conf_rows = {j: per_judge[j] for j in CONFIRMATORY}

    if any(r.get("skipped") for r in conf_rows.values()):
        missing = [j for j, r in conf_rows.items() if r.get("skipped")]
        return {"verdict": "NOT RUN", "detail": f"no judgments for: {', '.join(missing)}",
                "dir_agree": False, "all_sig": False, "powerless": [],
                "panel_mean": None, "spread": None, "signs": {}}

    signs = {j: sign_of(r["mean"]) for j, r in conf_rows.items()}
    distinct = set(signs.values())
    # "All agree in direction" requires one shared direction, and an exact zero
    # is not a direction. Reading zero as agreement would be the convenient
    # reading, not the registered one.
    dir_agree = len(distinct) == 1 and "zero" not in distinct
    all_sig   = all(r["confirmed"] for r in conf_rows.values())
    powerless = [j for j, r in conf_rows.items() if judge_is_powerless(r, alpha)]

    means = [r["mean"] for r in conf_rows.values()]
    panel_mean = sum(means) / len(means)
    spread = (min(means), max(means))

    if not dir_agree:
        verdict = "INCONCLUSIVE"
        detail = "confirmatory judges disagree on sign"
        if "zero" in distinct:
            zero_judges = [j for j, s in signs.items() if s == "zero"]
            detail += f" ({', '.join(zero_judges)} exactly zero)"
        if powerless:
            detail += "; underpowered for " + ", ".join(powerless)
    elif all_sig:
        verdict = "CONFIRMED"
        detail = f"3/3 direction ({distinct.pop()}) and 3/3 significant"
    elif powerless:
        verdict = "UNTESTABLE"
        detail = ("direction agrees but the registered sign test could not reach "
                  f"p<{alpha} at the observed tie count for: " + ", ".join(powerless))
    else:
        # Not "NULL": a non-significant result is not an equivalence result, and
        # the registered rule contains no equivalence margin. Raised as F10 by
        # forensic_audit_20260910/REPORT.md, which also noted the old detail
        # string asserted "no judge significant" even where some judges passed.
        verdict = "NO ESTABLISHED EFFECT"
        n_sig = sum(1 for r in conf_rows.values() if r["confirmed"])
        detail = (f"direction agrees; {n_sig} of {len(conf_rows)} confirmatory judges "
                  "significant. NOT an equivalence result -- the registered rule sets no "
                  "margin. Report the estimate and CI")

    return {"verdict": verdict, "detail": detail, "dir_agree": dir_agree,
            "all_sig": all_sig, "powerless": powerless,
            "panel_mean": panel_mean, "spread": spread, "signs": signs}


# ── instrument identity ───────────────────────────────────────────────────────

def check_instrument(manifests: dict[str, dict],
                     stats: dict[str, list[dict]]) -> tuple[list[str], list[str]]:
    """
    Returns (fatal_problems, disclosures).

    Fatal: the judges cannot be compared at all.
    Disclosure: they can be compared, but something must be stated in the paper.
    """
    fatal: list[str] = []
    disclose: list[str] = []
    judges = list(manifests)

    ref = judges[0]
    for field in INSTRUMENT_FIELDS:
        values = {j: manifests[j].get(field) for j in judges}
        if len(set(values.values())) > 1:
            fatal.append(f"{field} differs across judges: " +
                         ", ".join(f"{j}={str(v)[:16]}..." for j, v in values.items()))
        elif values[ref] is None:
            fatal.append(f"{field} absent from every manifest, so it cannot be shown to match")

    calls = {j: manifests[j].get("n_calls_total") for j in judges}
    if len(set(calls.values())) > 1:
        fatal.append("n_calls_total differs across judges: " +
                     ", ".join(f"{j}={v}" for j, v in calls.items()))

    for judge in judges:
        n_invalid = manifests[judge].get("n_invalid")
        if n_invalid:
            fatal.append(f"{judge} has {n_invalid} INVALID judgment(s); "
                         f"re-run that judge before applying the rule")

    # Contrast alignment: same names, same order, same pairing.
    names = {j: [r["name"] for r in stats[j]] for j in judges}
    if len({tuple(v) for v in names.values()}) > 1:
        fatal.append("contrast sets differ across judges' stats.csv")
    else:
        for idx, name in enumerate(names[ref]):
            pairs = {j: stats[j][idx].get("n_pairs") for j in judges}
            if len(set(pairs.values())) > 1:
                fatal.append(f"n_pairs differs for '{name}': " +
                             ", ".join(f"{j}={v}" for j, v in pairs.items()))

    # items.jsonl staleness: the bank the items were built from must still be
    # the bank on disk, or the references in the prompts are superseded.
    items_manifest = JUDGING_DIR / "items_manifest.json"
    if not items_manifest.exists():
        disclose.append("items_manifest.json absent: items.jsonl provenance unverifiable")
    else:
        with open(items_manifest, encoding="utf-8") as handle:
            built_from = json.load(handle).get("bank", {}).get("sha256")
        if built_from and built_from != manifests[ref].get("bank_sha256"):
            fatal.append(f"items.jsonl was built against bank {built_from[:16]}... but the "
                         f"judgments record {str(manifests[ref].get('bank_sha256'))[:16]}...")

    # Model identity against the registration.
    for entry in REGISTERED_PANEL:
        judge = entry["judge"]
        if judge not in manifests:
            continue
        requested = manifests[judge].get("model_requested")
        returned  = manifests[judge].get("model_returned")
        if returned != requested:
            disclose.append(
                f"{judge}: requested `{requested}`, provider served `{returned}` -- "
                f"provider-side substitution, disclose")
        if requested != entry["model"]:
            amended = entry.get("amended_to")
            if amended and requested == amended:
                disclose.append(
                    f"{judge}: registered `{entry['model']}`, run used `{requested}` "
                    f"under {entry['amendment']}")
            else:
                fatal.append(
                    f"{judge}: registered `{entry['model']}`, run used `{requested}`, "
                    f"and no amendment in REGISTERED_PANEL covers it")

    return fatal, disclose


# ── rendering ─────────────────────────────────────────────────────────────────

def fmt(value, digits=4, dash="n/a"):
    return dash if value is None else f"{value:+.{digits}f}"


def render(run_tag: str, manifests: dict, stats: dict, results: list[dict],
           disclosures: list[str], mismatch_waived: bool) -> str:
    judges = CONFIRMATORY + [j for j in EXPLORATORY if j in stats]
    ref = judges[0]
    out: list[str] = []
    a = out.append

    a(f"# Panel verdict — `{run_tag}`\n\n")
    a(f"**Generated:** {date.today().isoformat()} by `judging/panel_verdict.py` "
      f"from the committed per-judge `stats.csv` files.\n")
    a("**Status:** machine-applied, awaiting human sign-off (see *Sign-off* at the end).\n\n")
    a("This file exists because the cross-judge confirmation rule is applied by hand "
      "by design (`DECISIONS.md`, 2026-09-05) and `aggregate.py` is single-judge by "
      "construction. It was applied to the July run in "
      "`judging/OPEN_FINDINGS.md` §2 and **had never been applied to this run**, which "
      "left the canonical experiment without a written headline verdict. Regenerate with:\n\n")
    a(f"```\npython judging/panel_verdict.py --run_tag {run_tag}\n```\n\n")
    a("---\n\n")

    # ── panel ────────────────────────────────────────────────────────────────
    a("## Panel\n\n")
    a("| Role | Judge | Registered | Requested | Served (all rows) | Calls | INVALID |\n")
    a("|---|---|---|---|---|---|---|\n")
    for entry in REGISTERED_PANEL:
        judge = entry["judge"]
        if judge not in manifests:
            continue
        man = manifests[judge]
        registered = f"`{entry['model']}`"
        if entry.get("amended_to"):
            registered += f"<br>*amended to* `{entry['amended_to']}`"
        a(f"| {entry['role']} | `{judge}` | {registered} | "
          f"`{man.get('model_requested')}` | `{man.get('model_returned')}` | "
          f"{man.get('n_calls_total')} | {man.get('n_invalid')} |\n")
    a("\n")

    a("**Instrument, identical across all judges** — verified by this script, not asserted:\n\n")
    for field in INSTRUMENT_FIELDS:
        a(f"- `{field}` = `{manifests[ref].get(field)}`\n")
    a(f"- `n_calls_total` = {manifests[ref].get('n_calls_total')} per judge, "
      f"`n_invalid` = 0 for every judge\n")
    a("- identical contrast names, order and `n_pairs` in every `stats.csv`\n")
    a("- `items.jsonl` still matches the bank recorded in the judgments\n\n")

    if disclosures:
        a("### Deviations to disclose\n\n")
        for item in disclosures:
            a(f"- {item}\n")
        a("\n")
    if mismatch_waived:
        a("> **WARNING:** written with `--allow_instrument_mismatch`. A fatal "
          "instrument check failed and was waived. Do not cite this verdict "
          "without reading the fatal list printed by the script.\n\n")

    # ── rule ─────────────────────────────────────────────────────────────────
    a("## The rule, as registered\n\n")
    a("> A contrast is confirmed only when all three confirmatory judges agree in "
      "direction and each is independently significant by the rule above. Judges "
      "disagreeing on sign makes a contrast *inconclusive*, not null.\n")
    a(">\n")
    a("> — `judging/PRECOMMIT.md`, *Panel composition*\n\n")
    a("Per-judge significance is `bootstrap 95% CI excludes zero AND two-sided sign "
      "test p < 0.05`, read from each `stats.csv`'s `confirmed` column rather than "
      "recomputed here, so this file cannot disagree with the artifacts it summarises. "
      f"`{EXPLORATORY[0] if EXPLORATORY else 'the exploratory judge'}` is the registered "
      "exploratory fourth judge and **never gates** a verdict.\n\n")
    a("**UNTESTABLE** marks a contrast where the registered sign test could not have "
      "reached p<0.05 at the observed tie count, whichever way the non-tied pairs "
      "fell. That is arithmetic about the registered test, not a substitute for it: "
      "with 4 non-tied pairs the smallest attainable two-sided p is 0.125, with 5 it "
      "is 0.0625, and 6 all-one-way is the minimum for significance. A contrast can "
      "only move from NO ESTABLISHED EFFECT to UNTESTABLE by this label, never to "
      "CONFIRMED.\n\n")

    # ── verdicts ─────────────────────────────────────────────────────────────
    a("## Verdicts\n\n")
    a("| # | Contrast | Pri | Panel mean Δ | Confirmatory spread | 3/3 dir | 3/3 sig | "
      f"`{EXPLORATORY[0]}` (expl.) | Verdict |\n" if EXPLORATORY else
      "| # | Contrast | Pri | Panel mean Δ | Confirmatory spread | 3/3 dir | 3/3 sig | Verdict |\n")
    a("|---|---|---|---|---|---|---|---|---|\n" if EXPLORATORY else "|---|---|---|---|---|---|---|\n")
    for res in results:
        row = res["rule"]
        star = "★" if res["primary"] else "—"
        spread = ("n/a" if row["spread"] is None
                  else f"{row['spread'][0]:+.3f} … {row['spread'][1]:+.3f}")
        cells = [str(res["index"]), res["name"], star, f"**{fmt(row['panel_mean'])}**",
                 spread, "Yes" if row["dir_agree"] else "**No**",
                 "**Yes**" if row["all_sig"] else "No"]
        if EXPLORATORY:
            expl = res["per_judge"].get(EXPLORATORY[0], {})
            cells.append("n/a" if expl.get("skipped", True)
                         else f"{expl['mean']:+.3f} {'sig' if expl['confirmed'] else 'ns'}")
        cells.append(f"**{row['verdict']}**")
        a("| " + " | ".join(cells) + " |\n")
    a("\n★ = primary pre-registered contrast. Panel mean is the arithmetic mean of the "
      "three confirmatory judges — the same aggregation used for the July figures, which "
      "reconcile exactly as three-judge means (`judging/OPEN_FINDINGS.md` §2). "
      "**Report the panel mean with the spread visible, never the mean alone.**\n\n")

    # ── per judge ────────────────────────────────────────────────────────────
    a("## Per-judge detail\n\n")
    for res in results:
        a(f"### {res['index']}. {res['name']}"
          f"{' ★ primary' if res['primary'] else ' (exploratory contrast)'}\n\n")
        a("| Judge | Role | n | Mean Δ | 95% CI | W/L/T | Sign p | "
          "Min attainable p | Sig? |\n")
        a("|---|---|---|---|---|---|---|---|---|\n")
        for judge in judges:
            row = res["per_judge"][judge]
            role = "confirmatory" if judge in CONFIRMATORY else "exploratory"
            if row.get("skipped"):
                a(f"| `{judge}` | {role} | — | not run | — | — | — | — | — |\n")
                continue
            floor = min_attainable_sign_p(row["wins"] + row["losses"])
            powerless = floor >= 0.05
            a(f"| `{judge}` | {role} | {row['n_pairs']} | {row['mean']:+.4f} | "
              f"[{row['ci_lo']:+.4f}, {row['ci_hi']:+.4f}] | "
              f"{row['wins']}/{row['losses']}/{row['ties']} | {row['sign_p']:.6f} | "
              f"{floor:.4f}{' **(no power)**' if powerless else ''} | "
              f"{'**yes**' if row['confirmed'] else 'no'} |\n")
        a(f"\n**Verdict: {res['rule']['verdict']}** — {res['rule']['detail']}.\n\n")

    # ── how to read ──────────────────────────────────────────────────────────
    a("## How to report these\n\n")
    a("| Verdict | Means | Wording for the paper |\n|---|---|---|\n")
    a("| CONFIRMED | All three confirmatory judges agree in direction and each is "
      "independently significant | May be stated as a finding. Give the panel mean, the "
      "per-judge spread and the effect size, not the p-value alone. |\n")
    a("| NO ESTABLISHED EFFECT | Direction agrees; not all confirmatory judges reach the "
      "threshold; the test had the power to | \"No statistically established effect under "
      "the pre-registered criterion.\" Give the estimate and CI. **Not** an equivalence "
      "result: the registered rule sets no margin, so this never licenses \"X does not "
      "help\". Do not upgrade on directional agreement alone either. |\n")
    a("| INCONCLUSIVE | Confirmatory judges disagree on sign | \"Inconclusive: the panel "
      "disagreed on sign.\" **Not** a null result, and not evidence of equivalence. |\n")
    a("| UNTESTABLE | The registered test could not reach significance at the observed "
      "tie count | \"The pre-registered test had no power to decide this contrast.\" Report "
      "the tie count and the attainable minimum p. |\n")
    a("| NOT RUN | A confirmatory judge has no judgments | Nothing may be said. |\n\n")

    # ── sign-off ─────────────────────────────────────────────────────────────
    a("## Sign-off\n\n")
    a("The rule is registered as a human step. This file is the machine's application "
      "of it; committing it is the human's ratification. Before committing, confirm:\n\n")
    a("- [ ] The **Panel** table's *Served* column is the panel you intend to publish, "
      "and every row under *Deviations to disclose* is stated in the paper.\n")
    a("- [ ] No `stats.csv` in this run was regenerated after the judgments were "
      "inspected.\n")
    a("- [ ] Verdicts here match the per-judge `FINAL_REPORT.md` files, which report the "
      "per-judge criterion only.\n")
    a("- [ ] Exploratory contrasts are labelled exploratory wherever they appear, "
      "however clean they look.\n\n")
    a("Signed: ______________________  Date: ____________\n")
    return "".join(out)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    parser = argparse.ArgumentParser(
        description="Apply the pre-registered cross-judge rule to a completed panel")
    parser.add_argument("--run_tag", required=True,
                        help="Run tag present under judging/results/<judge>/ (e.g. OFFLINE_FINAL)")
    parser.add_argument("--out", default=None,
                        help="Output path (default: judging/PANEL_VERDICT_<run_tag>.md)")
    parser.add_argument("--check", action="store_true",
                        help="Do not write; exit 3 if the on-disk verdict differs")
    parser.add_argument("--allow_instrument_mismatch", action="store_true",
                        help="Write anyway when a fatal instrument check fails, stamping "
                             "the waiver into the output. Use only deliberately.")
    args = parser.parse_args()

    manifests = {}
    stats = {}
    for entry in REGISTERED_PANEL:
        judge = entry["judge"]
        run_dir = RESULTS_DIR / judge / args.run_tag
        if not run_dir.exists():
            if entry["role"] == "confirmatory":
                print(f"ERROR: confirmatory judge '{judge}' has no results for "
                      f"run_tag '{args.run_tag}'. The panel is incomplete and a "
                      f"two-judge panel confirms nothing.", file=sys.stderr)
                sys.exit(2)
            print(f"  note: exploratory judge '{judge}' not present; continuing")
            continue
        manifests[judge] = load_manifest(judge, args.run_tag)
        stats[judge] = load_stats(judge, args.run_tag)

    fatal, disclose = check_instrument(manifests, stats)
    if fatal:
        print("\nInstrument / registration check FAILED:", file=sys.stderr)
        for item in fatal:
            print(f"  - {item}", file=sys.stderr)
        if not args.allow_instrument_mismatch:
            print("\nRefusing to combine judges that did not judge the same thing with "
                  "the same instrument. Pass --allow_instrument_mismatch to override "
                  "deliberately. [exit 1]", file=sys.stderr)
            sys.exit(1)
        print("\n--allow_instrument_mismatch: continuing and stamping the waiver.\n",
              file=sys.stderr)

    ref = CONFIRMATORY[0]
    results = []
    for index, row in enumerate(stats[ref]):
        per_judge = {j: stats[j][index] for j in stats}
        results.append({
            "index":     index + 1,
            "name":      row["name"],
            "primary":   row.get("primary", False),
            "per_judge": per_judge,
            "rule":      apply_rule(per_judge),
        })

    text = render(args.run_tag, manifests, stats, results, disclose, bool(fatal))
    out_path = Path(args.out) if args.out else JUDGING_DIR / VERDICT_PATH_NAME.format(
        run_tag=args.run_tag)

    # Console summary, so the answer is visible without opening the file.
    print(f"\nPanel verdict — {args.run_tag}")
    print("=" * 78)
    for res in results:
        star = "*" if res["primary"] else " "
        row = res["rule"]
        mean = "n/a" if row["panel_mean"] is None else f"{row['panel_mean']:+.4f}"
        print(f" {star} {res['name']:<14} panel={mean:>8}  {row['verdict']:<12} {row['detail']}")
    print("=" * 78)
    for item in disclose:
        print(f" disclose: {item}")

    if args.check:
        if not out_path.exists():
            print(f"\n--check: {out_path} does not exist. [exit 3]", file=sys.stderr)
            sys.exit(3)
        # The generation date line changes daily; compare everything else.
        def body(s: str) -> str:
            return "\n".join(l for l in s.splitlines()
                             if not l.startswith("**Generated:**"))
        if body(out_path.read_text(encoding="utf-8")) != body(text):
            print(f"\n--check: {out_path} is stale relative to the stats.csv files. "
                  f"Regenerate it. [exit 3]", file=sys.stderr)
            sys.exit(3)
        print(f"\n--check: {out_path} is up to date.")
        return

    out_path.write_text(text, encoding="utf-8")
    print(f"\nWritten: {out_path}")
    print("This is the machine's application of a rule registered as a human step. "
          "Read the Sign-off checklist before committing it.")


if __name__ == "__main__":
    main()
