# Findings register

Consolidated review of the primary scripts, 2026-09-04/05. Every entry was
verified by executing the code path or reading the artifact, not inferred.

**Criticality**

| | Meaning |
|---|---|
| **P0** | Affects a claim that is already in the paper or its artifacts |
| **P1** | Would corrupt the next run, or costs a lot for nothing |
| **P2** | Real defect with no current impact (latent), or a disclosure duty |
| **P3** | Hygiene, efficiency, documentation |

**Status**

| | Meaning |
|---|---|
| FIXED | Corrected and pushed this session |
| REGEN | Code fixed; a published artifact must be regenerated to adopt it |
| OPEN | Not fixed; needs work |
| DECISION | Needs a call only the author can make (spend, integrity, wording) |

---

## P0 — affects published claims

| # | Finding | Status |
|---|---|---|
| 1 | **The T4/T6 rejection is not supported by the runs that produced it.** Only three `enhanced_eval_*` runs exist and their variants are `T2+T4+T6`, `T5`, `T2+T5` — there is no T4-only or T6-only run, so neither rejection can be attributed to the technique it names. Compounding: T4's per-category floor table is 100% dead keys (`train.json` uses the 10-category schema, `classify_category()` returns 17 legacy labels, intersection empty), so every non-SC question ran with a flat 35-token floor while the console printed "floors computed for 10 categories from 4441 training samples". T4 there is also a hard `min_new_tokens` EOS suppressor with no `no_repeat_ngram_size` and no repetition truncator, where camera-ready T4 is a soft re-prompt with both. T6 there is a two-pass rewrite; camera-ready T6 is a binary SAFE/UNSAFE gate that never rewrites. | OPEN |
| 2 | **DeepSeek served a different model than was pre-registered.** `PRECOMMIT_PANEL.md` and `MODEL_CONFIGS` both say `deepseek-v4-pro`; `model_returned` is `deepseek-v4-flash` on **582/582** calls. Provider-side tier substitution, not a config slip. The headline B−A = +0.902 came from this run. | DECISION |
| 3 | **The length-bias control measured the wrong variable and asserted its own conclusion.** The caption "Low rho confirms no systematic length bias" was appended unconditionally over rho=0.201 p=0.0015 (deepseek) and rho=0.287 p=0.0000 (claude_or), and the correlated variable was the *judge's rationale* length, not the candidate answer. Recomputed correctly on the existing judgments: **rho = −0.163, p = 0.0104** — significant, opposite sign. | REGEN |
| 4 | **Every judge's `FINAL_REPORT.md` credited "per-item DeepSeek judging"** — a hardcoded literal, two lines under a correct `Model:` field. | REGEN |
| 5 | **F−B SC and E−B disagree on sign across the three judges**, so under the pre-registered 3/3 rule they are *inconclusive*, not *null*. No confirmed result changes: B−A, G−B and G−F are 3/3 in direction and 3/3 significant. Published deltas reconcile exactly as 3-judge means (B−A +0.756, G−B −0.992). | OPEN |
| 6 | **The published results score answers that were generated under an instruction to recommend EMS, against a rubric that hard-caps exactly that.** All 246 judged answers match `evaluations/CAMERA_READY_20260708_180411` (246/246 byte-identical). That run records no prompt policy and no prompt text, and `validate_run_prompt_provenance` rejects it with 9 errors — `internal_eval` refuses it by name as "a legacy-EMS baseline [that] must not be scored as an aligned offline run". `judging/assemble_items.py` has no equivalent check and judged it anyway. Git recovers the generation prompt from the run's commit (`3eaa376`): *"…For life-threatening situations, always advise calling emergency services immediately."* The rubric caps EMS referral at ≤2/5 and scores EMS-only at 1/5. Measured: 53.7% of A's and 65.9% of G's answers contain an EMS referral, and EMS-referring answers score **1.791** vs **2.102** (n=115 vs 131). At that commit `SAFE_FALLBACK` was itself an EMS referral, so **config E is penalized by construction** on every gate firing. **Consequence:** absolute scores and the rubric attribution are affected; B−A / G−B / G−F remain valid *contrasts* (both arms share the prompt) but measure benefit under the EMS premise, not the offline premise the paper describes. **The aligned `CAMERA_READY_OFFLINE_*` run that the whole `camera_ready/` apparatus exists to produce has never been generated.** | OPEN |
| 6a | **Unreported positive result found while testing 6.** The adapter *reduced* EMS referral from 59.8% (base configs A/G) to 40.2% (adapter configs B/C/E/F) — F lowest at 34.1% — while being instructed to produce it. The adapter learned the corpus's mostly-non-EMS style and partly overrode the system prompt. This is a concrete behavioural finding supporting the fine-tuning claim and is currently absent from the results. (Caveat: the detector also matches "seek immediate medical help", so absolute rates may be inflated; the relative comparison uses one regex across all configs.) | OPEN |
| 6b | **A retracted claim, recorded so it is not repeated.** I first reported this as a *training*/eval prompt mismatch handicapping the adapter, concluding B−A was an underestimate. Executing the check refuted it: adapter configs mention EMS *less* than base configs, training used all four prefix variants at ~25% each, and the eval prompt structurally matches `template_idx 3`. There is no evidence of a format handicap. The real defect is at generation time (6), not training time. | — |
| 6c | **`judging/assemble_items.py` does not validate generation provenance.** It reads `run.json` and builds items without checking the prompt policy, so it will assemble a judging set from any run, including one the internal lane rejects. This is the single gate whose absence allowed 6. `evaluation_protocol.validate_run_prompt_provenance` already exists and is a two-line call. | OPEN |

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
| 19 | **`rag_inference.py` stops only on `<eos>`.** It is the only file that never mentions `<end_of_turn>`, which is what Gemma ends a turn with and what the adapter was trained on, and it decodes with `skip_special_tokens=True`. A finished answer runs on into fabricated extra turns whose markers are then stripped, so the saved answer silently contains invented dialogue. No `repetition_penalty` either. Any "RAG is worse" number from this file is confounded by decoding hygiene. | OPEN |
| 20 | **`enhanced_inference.py` cannot run RAG at all** — executed: `ImportError: cannot import name 'GAP_QUESTION_IDS' from 'bm25_rag'` (removed with the ID gate). The `except ImportError` leaves `BM25Retriever` unbound while an `isinstance(..., BM25Retriever)` call sits on the default path (`--no_rag` is opt-*out*) → `NameError` on question 1. The imported symbol is never used. | OPEN |
| 21 | **The BM25 similarity gate cannot reject anything.** `scores = raw / raw.max()` then `if score >= self.threshold` (0.4) — the top-1 hit is always exactly 1.0. The value is also stored in the same `score` field as genuine cosines with no `score_kind` tag, and the docstring calls it "cosine". | OPEN |
| 22 | **Static padding wastes ~7× of training compute.** `padding="max_length"` with `max_length=512` against a median of ~73 tokens (median 56 words, p99 84, max 157). `DataCollatorForSeq2Seq(pad_to_multiple_of=8)` is passed to the Trainer to pad dynamically and has nothing to do. The documented `320 → 512` "safety buffer" made this 60% worse for zero benefit — the docstring's own audit says max is ~314. One-line fix: `padding=False`. | OPEN |
| 23 | **`train_v2.py` defaults do not reproduce the published adapter**: lr 2e-4 vs 1e-4, patience 2 vs 3, `max_length` 512 vs the **320** the canonical adapter records. The docstring's RUN SEQUENCE (`python train_v2.py --quant 4bit`) produces a different adapter than the paper's. | OPEN |
| 24 | CTRL_VAGUE's expected floor of 1 recorded *correct* judgments as control failures — 3 of the 4 failures in `CAMERA_READY_FINAL` were score-0 rows. Floor fix alone, re-graded on existing scores: deepseek 44→**45/45**, claude_or 42→**44/45**, gpt 45/45. Needs no re-judging. | REGEN |
| 25 | Two CTRL_VAGUE answers were attached to the wrong question (V2Q37 held heat-exhaustion advice on a burn question; V2Q10 described an AED on a compression-effectiveness question); CTRL_VAGUE/V2Q34 was an unlabelled SO12 violation; CTRL_DANGER/V2Q25 hedged its own planted violation, and `claude_or` scored it 2 *while correctly flagging SO06*. Rewrites need ~270 re-judging calls. | DECISION |

## P2 — latent, or a disclosure duty

| # | Finding | Status |
|---|---|---|
| 26 | A missing/INVALID **safety** call became `n_violations = 0`, i.e. "clean". Quality scores were already handled correctly. All three runs are 582/582 `ok`, so no current impact. | FIXED |
| 27 | Empty strata reported `mean = 0.000`, indistinguishable from "scored zero on every item". | FIXED |
| 28 | An unmapped `blind_id` became its own config, leaking control items into the published config table. | FIXED |
| 29 | **The 8-bit adapter was trained by a different script.** 4-bit: `script_version='train_v2'`, `template='apply_chat_template'`. 8-bit: both `None` → old `train.py`/`data.py` manual template. Camera-ready C−B is **unaffected** (C uses B's 4-bit adapter on an 8-bit base), but any claim about the 8-bit *adapter* — including "8-bit trialled and rejected — systematic dangerous positioning heuristic" — varies quantization *and* training template. | OPEN |
| 30 | **Split leakage**: 9 verbatim question+answer pairs in `test` (1.6%) and 8 in `val` (1.4%) are also in `train`; 33 duplicate questions within `train`. `val_loss` is the early-stopping metric. The eval bank has **zero** exact overlap with any split. | OPEN |
| 31 | **`--quant fp16` may train nothing.** It skips `prepare_model_for_kbit_training` (which calls `enable_input_require_grads`) while `gradient_checkpointing=True`, so no gradient may reach the adapter; and `use_fp16 = cfg.fp16 and cfg.quant != "fp16"` disables AMP while the model is loaded in fp16. Consistent with there being no fp16 experiment on disk. One `--epochs 1` run settles it. | OPEN |
| 32 | **Training-side provenance is absent.** `training_curve.json` records hyperparameters and nothing else — no split hashes, no sample counts, no git commit, no prompt text, no tokenizer revision. The eval lane fingerprints artifacts, records the commit and enforces a clean tree. `artifact_fingerprint` already exists and could be called from `_write_run_log`. | OPEN |
| 33 | `enhanced_inference` re-derives safety-critical status from question text instead of reading `q["safety_critical"]`, disagreeing with the bank on **16/41 (39%)** of questions, then writes the bank's label into the record used for SC/non-SC slicing. V2Q10/11/12 are labelled non-SC but classify as cardiac; V2Q01 is labelled SC but classifies as bleeding. | OPEN |
| 34 | T6 in `enhanced_inference` reports `tokens_generated` and `tokens_per_sec` summed over *both* passes while delivering one answer, so a rejected technique looks free on throughput and ~2× longer on length; `peak_vram_mb` is taken from pass 1 only, after pass 2's peak was measured and discarded. | OPEN |
| 35 | Planted override coverage is **5 distinct categories, not 6** (SO06 twice: V2Q09 and V2Q25); SO01/02/03/04/07/08/10 are never planted. Comments corrected — do not claim per-category coverage. | FIXED |
| 36 | The controls gate was `row_pass = in_range` only, so a completely non-functional safety detector could still report 45/45. Detection is now tallied separately; DeepSeek scores 6/6. | FIXED |
| 37 | The 3/3 same-direction rule exists only as prose; `aggregate.py` is single-judge by construction. Kept manual by decision, with a completeness gate and an explicit statement in every report. | FIXED |
| 38 | `gpt` returned an undated alias on **167 of 582** calls, so those cannot be pinned to a model snapshot after the fact. | OPEN |
| 39 | Two eval questions have Jaccard-0.71 near-duplicates in `train` (V2Q31, V2Q34). V2Q34 is **not** gated, so F/G can retrieve a near-twin of the eval question. Worth a manual look and a disclosure line. | OPEN |
| 40 | `rag_inference.py` further: retrieval is ungated, top-3 and uncapped (vs gated top-1 with a 115-word cap); samples at T=0.1 with no seed anywhere; writes the answer under `rag_answer` with no `error` field, so `evaluate.py` cannot score it; `"adapter"` resolves to the string `"default"` for any PeftModel; `--out` defaults to a fixed path with no timestamp, so a second run silently overwrites the first. | OPEN |

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
| 51 | `train_v2.py`: `eval_steps`/`save_steps` hardcoded to 200 while `TrainConfig.eval_steps` is dead; `early_stopping_patience` is in *evaluations* not epochs (≈1.1 epochs at 555 steps/epoch) and sits beside `num_train_epochs_max: 10`; `datetime.now()` naive local time in the output directory name and run log; `open(log_path, "w")` without an encoding; instruction tokenisation uses `truncation=False` against `truncation=True` for the full text, so an over-long instruction silently yields a fully-masked example; `target_modules` default duplicated in the dataclass and in `parse_args`. | OPEN |
| 52 | `enhanced_inference.py` further: `unload()` deletes only its local reference; the console prints `T5:bm25` for any fired retrieval including the dense path; a 15-rule fallback copy of `classify_category` differs from `data.py`'s 17-category list, so an import failure silently changes gating behind one warning; `TRAIN_JSON` is hardcoded for the T4 map while `--rag_kb` is configurable, so the two can reference different corpora; the console prints stale gate IDs `(Q6/Q17/Q21/Q22/Q28)` as fact next to a dynamically computed count. | OPEN |

---

## Not defects — protect these

- **Gemma's chat template raises `'System role not supported'`.** `build_hf_dataset_v2` folds the system prompt into the user turn, which is the correct workaround. If someone "fixes" it to pass a `system` role, training breaks immediately.
- **The label masking in `tokenize_dataset_v2` is sound.** `add_special_tokens=False` on both the instruction and the full text keeps the boundary aligned, and the instruction is a strict prefix of the full text under Gemma's template.
- **`confirmed` in `aggregate.py` is deliberately two-sided**, as `PRECOMMIT.md:20` defines it. It was left exactly as pre-registered; direction is reported alongside it, not folded into it.
- **The eval bank has no exact overlap with any split** — the one contamination that would have been fatal is absent.

## Suggested order

**#6 dominates everything else.** The aligned offline run has never been
generated, and every published number comes from a run whose generation prompt
contradicts the scoring rubric. Until that is resolved, fixing the reporting of
those numbers is polishing the wrong artifact.

1. **#6** — generate the aligned run: `python camera_ready/pipeline.py generate`
   (~2 GPU-hours, 6 configs × 41 questions, `offline_definitive_v1`), verify it,
   then re-assemble and re-judge. This is the experiment the paper describes.
   Add the provenance gate from **#6c** first so it cannot recur.
2. **#22** — one line, ~7× faster training. Do it before any retrain.
3. **#24** — regenerate the control key only; no re-judging, lifts controls to
   45/44/45.
4. **#23, #32** — cheap, and they make the training half reproducible. Needed
   before a retrain is meaningful.
5. **#3, #4** — will be regenerated for free by step 1's re-aggregation.
6. **#19, #20, #21** — the inference-lane bugs, if any of those numbers are cited.
7. **#1** — re-run the T4/T6 arms through `v2_comprehensive_eval.py` (D and E
   are already wired, deterministic, same decoding as B) and retire
   `enhanced_inference.py`'s numbers.
8. **#2, #5, #25, #6a** — decisions and paper wording.

**If a full re-run is not possible**, the fallback is to report the existing
results honestly: state the generation premise was EMS-advising, present
B−A / G−B / G−F as contrasts under that premise, drop the offline-deployment
framing from the absolute scores, exclude config E's fallback rows or report
them separately, and add **#6a** as the behavioural result. That is a
defensible paper. What is not defensible is presenting the current numbers as
an offline-premise evaluation.
