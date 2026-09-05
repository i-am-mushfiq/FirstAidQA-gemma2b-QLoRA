# Closed findings — moved out of the register 2026-09-06

Companion to [`FINDINGS_20260905.md`](FINDINGS_20260905.md). These 44 findings
are **closed**: corrected and verified. They were moved here on 2026-09-06 so the
register shows only outstanding work.

Wording, numbering and criticality band are unchanged from the original entries.
Findings 53-62 were raised and fixed on 2026-09-06; the rest were closed on or
before 2026-09-05. The pre-prune register is in git history at `c7f163f`.

**Why this file exists.** A findings register that lists only what is still broken
understates how much was examined. For a paper, the closed set is the evidence
that the pipeline was audited rather than assumed — it belongs somewhere citable,
just not in the list of open work.

| Status | Meaning |
|---|---|
| FIXED | Corrected and pushed; status as recorded on 2026-09-05 |
| CLOSED 2026-09-06 | Was OPEN / DECISION / REGEN in the register; closed by the 2026-09-06 session (see addendum A2) |

## P0 — affects published claims
| # | Finding | Status |
|---|---|---|
| 2 | **DeepSeek served a different model than was pre-registered.** `PRECOMMIT_PANEL.md` and `MODEL_CONFIGS` both say `deepseek-v4-pro`; `model_returned` is `deepseek-v4-flash` on **582/582** calls. Provider-side tier substitution, not a config slip. The headline B−A = +0.902 came from this run. | CLOSED 2026-09-06 |
| 6c | **`judging/assemble_items.py` does not validate generation provenance.** It reads `run.json` and builds items without checking the prompt policy, so it will assemble a judging set from any run, including one the internal lane rejects. This is the single gate whose absence allowed 6. `evaluation_protocol.validate_run_prompt_provenance` already exists and is a two-line call. | CLOSED 2026-09-06 |

## P1 — fix before the next run
| # | Finding | Status |
|---|---|---|
| 7 | Direction fields were computed by `build_stats` then dropped by `extrasaction="ignore"`, so a primary contrast significant in the *opposite* direction would publish as "confirmed". | FIXED |
| 8 | `call_api_sync` fell off its retry loop returning `None`, surfacing as `AttributeError` and killing the run. The same variable was already guarded 38 lines later. | FIXED |
| 9 | 5xx was detected as `"5" in str(e)[:3]`, which is always `"Err"` for SDK errors, so server errors never backed off. | FIXED |
| 10 | Contrast names contain U+2212 and the marker is U+2605; redirecting stdout on Windows died between `stats.csv` and `FINAL_REPORT.md`. | FIXED |
| 11 | A controls-only run produced a structurally complete `FINAL_REPORT.md` with every contrast `0 \| N/A`. Now gated, with an `item_set_sha256` so judges' item sets can be compared. | FIXED |
| 12 | Auto-detect selected the `_ANALYSIS` sibling (`"_"` sorts after `""`), breaking every no-arg invocation permanently after the first judge run. Three copies replaced by one helper. | FIXED |
| 13 | `extract_json`'s brace regex could not span a nested object: a reply with an extra field matched the innermost group and failed validation, burning the retry budget on a valid score. | FIXED |
| 14 | `stats_v2` hard-required all six judges, so it could not analyse the three-judge panel that was actually run. | FIXED |
| 15 | Scoped `--judges` runs rewrote `completion_matrix.csv` and `pairwise_F_vs_B.json` with one judge — and `pipeline.py status` reads the matrix as ground truth. | FIXED |
| 16 | `assemble_items.validate_items` never referenced `configs_present`, so a config contributing zero items printed "GATE PASSED"; outputs were written *before* the gate; a non-`--append` run silently wiped all 45 planted controls. | FIXED |
| 17 | Unscored judge items exited 0 and printed "Scoring complete." | FIXED |
| 18 | `_truncate_repetition` rejoined sentences with single spaces, reflowing every multi-line answer into one paragraph even with no repetition — destroying numbered-step structure in every stored answer — and making the new `repetition_truncated` flag fire on almost everything. | FIXED |
| 20 | **`enhanced_inference.py` cannot run RAG at all** — executed: `ImportError: cannot import name 'GAP_QUESTION_IDS' from 'bm25_rag'` (removed with the ID gate). The `except ImportError` leaves `BM25Retriever` unbound while an `isinstance(..., BM25Retriever)` call sits on the default path (`--no_rag` is opt-*out*) → `NameError` on question 1. The imported symbol is never used. | CLOSED 2026-09-06 |
| 23 | **`train_v2.py` defaults do not reproduce the published adapter**: lr 2e-4 vs 1e-4, patience 2 vs 3, `max_length` 512 vs the **320** the canonical adapter records. The docstring's RUN SEQUENCE (`python train_v2.py --quant 4bit`) produces a different adapter than the paper's. | CLOSED 2026-09-06 |
| 24 | CTRL_VAGUE's expected floor of 1 recorded *correct* judgments as control failures — 3 of the 4 failures in `CAMERA_READY_FINAL` were score-0 rows. Floor fix alone, re-graded on existing scores: deepseek 44→**45/45**, claude_or 42→**44/45**, gpt 45/45. Needs no re-judging. | CLOSED 2026-09-06 |
| 25 | Two CTRL_VAGUE answers were attached to the wrong question (V2Q37 held heat-exhaustion advice on a burn question; V2Q10 described an AED on a compression-effectiveness question); CTRL_VAGUE/V2Q34 was an unlabelled SO12 violation; CTRL_DANGER/V2Q25 hedged its own planted violation, and `claude_or` scored it 2 *while correctly flagging SO06*. Rewrites need ~270 re-judging calls. | CLOSED 2026-09-06 |

## P2 — latent, or a disclosure duty
| # | Finding | Status |
|---|---|---|
| 26 | A missing/INVALID **safety** call became `n_violations = 0`, i.e. "clean". Quality scores were already handled correctly. All three runs are 582/582 `ok`, so no current impact. | FIXED |
| 27 | Empty strata reported `mean = 0.000`, indistinguishable from "scored zero on every item". | FIXED |
| 28 | An unmapped `blind_id` became its own config, leaking control items into the published config table. | FIXED |
| 35 | Planted override coverage is **5 distinct categories, not 6** (SO06 twice: V2Q09 and V2Q25); SO01/02/03/04/07/08/10 are never planted. Comments corrected — do not claim per-category coverage. | FIXED |
| 36 | The controls gate was `row_pass = in_range` only, so a completely non-functional safety detector could still report 45/45. Detection is now tallied separately; DeepSeek scores 6/6. | FIXED |
| 37 | The 3/3 same-direction rule exists only as prose; `aggregate.py` is single-judge by construction. Kept manual by decision, with a completeness gate and an explicit statement in every report. | FIXED |

## P3 — hygiene
| # | Finding | Status |
|---|---|---|
| 41 | `run_camera_ready.ps1` carried 246 lines of unreachable script below its routing `exit`, including a stale source list missing every `camera_ready/*` entry — while the file is itself in `CAMERA_SOURCE_FILES`. | FIXED |
| 42 | `GAP_TOPIC_PATTERNS` existed as a second verbatim copy in `audit_gap_gate.py`, so the gate and its own forensic audit could desynchronise provenance-clean. Now imported. | FIXED |
| 43 | `audit_gap_gate.py` wrote its report *into* the run directory, contradicting the immutability the README promises. Now writes to the `_ANALYSIS` sibling. | FIXED |
| 44 | `verify_camera_ready.py` hardcoded `EXPECTED_N = 41` as a second source of truth beside `protocol.yaml`; the rubric file handle was never closed. | FIXED |
| 45 | `check.py --strict` probed only the generation dependencies, so it passed on a machine where judging could not run. | FIXED |
| 46 | Decoding parameters (`repetition_penalty=1.15`, `no_repeat_ngram_size=4`) were applied to every config but only `max_new_tokens` reached `run.json`, so "greedy" was not reproducible from the artifact. Now recorded in `run_args._decoding`. | FIXED |
| 47 | T4's `pass1_tokens` was recorded only when *no* retry happened — `r1` had already been reassigned, so the number was dropped in exactly the case it explained. | FIXED |
| 48 | `print_table` printed `(++0.0200 vs B)`: a manual `+` plus a `:+.4f` format. | FIXED |
| 49 | `bm25_rag` docstring claimed a normalised 0-1 score (it returns raw BM25); collections were not initialised before the missing-file early return; only the first matching gap topic was recorded; the smoke test printed a warning and exited 0, so a broken gate could not fail the pre-flight. | FIXED |
| 50 | `datetime.utcnow()` (deprecated, naive) in the eval lane. | FIXED |

## A4 · New findings raised 2026-09-06
| # | Finding | Criticality | Status |
|---|---|---|---|
| 53 | **`judging/prompt_quality.txt` was never updated after the rubric corrections landed.** The Config E fallback quote, the withheld-response cap wording, "all evaluated configs" and the reference note were corrected in `rubric_v2.md` Section 6 and `build_v2_judge_prompt.RUBRIC` at `7885e17`, but not in the file the per-item judges actually read, last touched at `37abda0`. The preflight compares `RUBRIC` to `rubric_v2.md` only, so it reported `PASS runtime rubric` while the judges read a stale marking scheme. Propagated, and two further defects fixed in all three copies: the SAFETY OVERRIDE band said "0 or 1" in the body and "score is 0" in the instruction footer (gpt returned 0 six times where deepseek returned 1 on the same six CTRL_DANGER items), and a TRUE_POSITIVE/FALSE_POSITIVE block asked judges to assess gate correctness from information they are never shown and for which the response schema has no field. | P0 | FIXED |
| 54 | **The choking protocol encoded AHA/ERC, not ANZCOR.** ANZCOR Guideline 4 uses back blows then **chest** thrusts and does not recommend abdominal thrusts at any age. The reference bank, `SO08`, `SO09` and the score-5 specificity anchor all specified abdominal thrusts. Present in the original references, not introduced by the compression. Corrected across the bank, `prompt_quality.txt`, `prompt_safety.txt`, `RUBRIC` and `rubric_v2.md`. `SO08` broadened from infants to any age, which **widens detection** — the training corpus is Heimlich-dominant (41 of 4441 answers mention abdominal thrusts or the Heimlich; 1 mentions chest thrusts), so this may raise flag rates on other runs. Zero answers in the current run mention abdominal thrusts, so no effect here. Templates re-frozen at iteration 5. | P0 | FIXED |
| 55 | **The judge cache key omitted the reference answer.** The key was `model \| template_hash \| prompt_type \| qid \| blind_id \| sha256(answer)`. Decoding is greedy, so a regenerated run yields byte-identical answers: had the reference bank changed while the prompt templates did not, every score would have been served from the July cache against the old gold standard, with `cache_hit: true` and no warning. It did not bite only because the ANZCOR correction happened to change both prompt files, moving `template_hash`. The key is now `sha256(rendered prompt)`, which subsumes template, question, reference, `sc_flag` and answer. | P1 | FIXED |
| 56 | **Re-running a judge into a used `run_tag` silently welds two evaluations together.** Resume is keyed on `(qid, blind_id, prompt_type)`, and `blind_id` is a stable salted hash of the config name — identical across runs. A re-run into `CAMERA_READY_FINAL` (the tag in `judge_deepseek.py`'s own usage examples and in `aggregate.py`'s docstring) would have loaded July's 582 judgments, marked them done, and judged only the newly added items, producing one `judgments.jsonl` mixing EMS-premise scores under the old rubric with offline scores under the new one. The cache fix does not cover this; the resume path never consults the cache. Resume now compares the prior manifest's `template_hash` and `bank_sha256` and refuses. Executed against all three real July directories: all refuse, on both grounds. | P1 | FIXED |
| 57 | **`items.jsonl` carried no provenance.** `judge_deepseek.py` read whatever was on disk; the committed copy was July's — 291 lines, six configs, pre-compression references — indistinguishable from a fresh one. `assemble_items.py` now writes `items_manifest.json` recording the run directory, configs and frozen bank hash; the judge verifies it before judging and in `--review_only`. | P1 | FIXED |
| 58 | **`assemble_items.py` took references from the live bank while `internal_eval` took them from `run.json`.** After a bank revision the two lanes grade against different gold standards — the same divergence class as the original premise mismatch. `check_bank_alignment()` now refuses a run whose embedded questions differ from the bank. Verified: assembling from the superseded `CAMERA_READY_OFFLINE_20260905_195908` exits 2 and names all 41 drifted references. | P1 | FIXED |
| 59 | **Config D was in scope with no registered contrast.** D entered the run after the repetition-loop fix, but neither `aggregate.py` nor `PRECOMMIT.md` listed a D contrast, so D would have been generated and judged (41 answers, ~246 panel calls) and received a `config_summary` row with no bootstrap CI and no sign test — the stated reason for including it unmet. `D−B` registered as secondary contrast 8 **before generation**, so no D data existed at the time of the amendment. | P1 | FIXED |
| 60 | **`TEMPLATE_FROZEN_HASH.txt` and the run manifests hashed the same files two different ways.** The freeze file hashed raw bytes; `judge_deepseek.file_sha256` hashes newline-normalised text. They agreed in July because the files were stored with LF; editing them on Windows introduced CRLF and produced two different "frozen" values for one artifact. The freeze file now uses the text method, matching the manifests, and says so. | P2 | FIXED |
| 61 | **`bitsandbytes` was absent from the preflight dependency list.** Every camera-ready config loads the base model in 4-bit or 8-bit, so generation dies at model load — but `check --strict` reported PASS. Same failure mode `79962fc` closed for the judging and stats lanes. Added. | P2 | FIXED |
| 62 | **`judge_deepseek --controls_only` produced judgments that nothing read back.** Evaluating the control battery required the full aggregator, so the gate before the 1,992-call panel was "go and look at a file". `judging/check_controls.py` (new) reads judgments already on disk, costs nothing, and prints pass/fail per judge against `controls_key.json`, additionally requiring the safety lane to have flagged each planted override. Validated against the July panel, where it reproduces the known result exactly: deepseek 45/45, claude_or 44/45, gpt 45/45, with the single failure identified as CTRL_DANGER/V2Q25 — finding 5 in `OPEN_FINDINGS.md`. | P2 | FIXED |

