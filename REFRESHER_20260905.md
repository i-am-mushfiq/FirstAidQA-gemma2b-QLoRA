# Repo refresher — internal mechanics

**Written 2026-09-05 against `main` @ `7b63bad` (working tree carries the 2026-09-05 fix round: see Fixes applied, below)** Companion to `FINDINGS_20260905.md` (defects) and `DECISIONS.md` (choices). This reconstructs *how the machine works*, not what the paper says.

Evidence tags: **[Observed]** = directly visible in the repo · **[Inferred]** = strongly implied · **[Unknown]** = not establishable from the repo.

---

## 0. Orientation before you touch anything

- **[Observed]** This checkout is **code + JSON artifacts only**. `models/` does not exist, and `*.safetensors` / `tokenizer.json` / `tokenizer.model` are gitignored — `find . -name "*.safetensors"` returns nothing. **No adapter in `experiments/` has weights.** Every `adapter/` dir holds only `adapter_config.json`, `chat_template.jinja`, `tokenizer_config.json`, `README.md`.
- **[Observed]** Absolute paths baked into artifacts point at the original workstation: `C:\Personal_Endeavours\Fine_Tuning` (see `adapter_config.json:base_model_name_or_path`, `training_curve.json:adapter_path`, several `.ps1` headers).
- **[Observed]** No ML env here: `python camera_ready/pipeline.py check` reports `missing: numpy, torch, transformers, peft, rank_bm25` and `openai, scipy`. As of the 2026-09-05 fix round it also reports `model weights`, `model tokenizer vocab`, `4-bit adapter weights` and `4-bit adapter tokenizer vocab` as FAIL - previously the adapter showed PASS because only directory existence was tested.
- **[Observed]** `pipeline.py status` prints **`Latest run: none`**. No `CAMERA_READY_OFFLINE_*` run has ever been generated. All published numbers come from `evaluations/CAMERA_READY_20260708_180411` — a different prefix, therefore invisible to the façade.
- **[Observed]** `DECISIONS.md`: **retraining is frozen**; generation and API judging are allowed.

---

## 1. Repository mental model — six lanes, mostly independent

| Lane | Entry point | Core modules | Status |
|---|---|---|---|
| **T. Training** | `train_v2.py` (v1: `train.py`) | `data_v2.py` (v1: `data.py`) | Frozen by decision. v1 kept byte-stable for reproducibility. |
| **G. Camera-ready generation** | `camera_ready/pipeline.py` | `v2_comprehensive_eval.py`, `bm25_rag.py`, `evaluation_protocol.py`, `verify_camera_ready.py`, `camera_ready/{protocol.yaml,check.py}` | **Canonical.** Only supported operator interface. |
| **J1. Published judging** | `judging/judge_deepseek.py` | `assemble_items.py` → `make_controls.py` → `judge_deepseek.py` → `aggregate.py` | **Canonical for the paper.** 3 pinned judges, 45 planted controls. |
| **J2. Internal judging** | `internal_eval/judge_per_item.py` | `internal_eval/stats_v2.py` | Pre-run go/no-go only. Never paper-facing. Do not mix with J1 in one table. |
| **L. Legacy eval** | `eval_suite.py` → `evaluate.py` → `build_llm_judge_prompt.py` | 30/40-question banks, ROUGE + manual mega-prompt | Historical. Reproduces the old `evaluations/eval_*` dirs. |
| **X. Experimental inference** | `enhanced_inference.py`, `rag_inference.py`, `t4_t6_isolation_eval.py` | `bm25_rag.py` | **Broken / confounded** (`FINDINGS` 19, 20, 21, 33, 34, 40, 52). Their numbers back the T4/T6 rejection, which `FINDINGS` 1 says is unsupported. |

**Files that actually matter:** `evaluation_protocol.py` (the contract), `v2_comprehensive_eval.py` (all generation), `bm25_rag.py` (retrieval + gap gate), `camera_ready/protocol.yaml` (the one config file), `data_v2.py` + `train_v2.py`, and the four `judging/` scripts. Everything else is a runner, a diagnostic, or history.

---

## 2. End-to-end execution pipeline

```
data/firstaidqa_v1.json  (5,550 x {question, answer})
   |  utils/classify_10cat.py    <- [Inferred] what actually produced splits/10cat
   v
data/firstaidqa_v1_enriched_10cat.json
   (+category, question_type, safety_critical, *_confidence, template_idx)
   |  stratified_split(0.8/0.1/0.1, seed=42, strata = category|question_type)
   v
splits/10cat/{train,val,test}.json        4,441 / 556 / 553   [test LOCKED]
   |  data_v2.build_hf_dataset_v2(samples, tokenizer)   -> strings via apply_chat_template
   |  data_v2.tokenize_dataset_v2(ds, tok, max_length)  -> labels, -100 masking
   v
train_v2.train()  ->  experiments/<tag>_<quant>_r<r>_lr<lr>_p<pat>_v2_<ts>/
                        |-- adapter/            (weights + tokenizer)   <- ABSENT here
                        |-- checkpoint-*/       (every 200 steps, keep 2)
                        `-- training_curve.json (hyperparams + loss history + best_val_loss)
   v
camera_ready/pipeline.py generate
   -> check --strict  ->  v2_comprehensive_eval.py --camera_ready  ->  verify_camera_ready.py  [-> git commit]
   v
evaluations/CAMERA_READY_OFFLINE_<ts>/          IMMUTABLE
   |-- run.json      variants{config}.answers[], run_args._prompt / _question_bank /
   |                 _artifacts / _config_resolution / _code / _decoding / _versions
   |-- <CONFIG>.json one per config
   `-- metrics.json  in-script ROUGE-L
   v  sibling, never inside the run:  evaluations/CAMERA_READY_OFFLINE_<ts>_ANALYSIS/
   |
   |-- J2 internal: judge_per_item.py -> stats_v2.py -> *_ANALYSIS/{completion_matrix.csv, pairwise_F_vs_B.json, ...}
   `-- J1 published:
         assemble_items.py --run_dir <run>       -> judging/items.jsonl (246 rows) + blind_map.json
         make_controls.py                        -> +45 control rows (291 total) + controls_key.json
         judge_deepseek.py --model X --run_tag T -> judging/results/<model>/<tag>/{judgments.jsonl, manifest.json}
                                                    (291 items x 2 prompt types = 582 calls)
         aggregate.py --model X --run_tag T      -> scores_per_question.csv, config_summary.csv,
                                                    stats.csv, controls_report.md,
                                                    reliability_report.md, FINAL_REPORT.md
         -> 3/3 same-direction rule applied BY HAND across the three FINAL_REPORTs
```

**Legacy lane, for reproducing `evaluations/eval_*`:** `eval_suite.py` (auto-discovers every adapter under `experiments/`, skips `_v1_archive/`, reads `data/eval_questions_30.json`) → `evaluate.py` (ROUGE / BERTScore → `metrics.json`) → `build_llm_judge_prompt.py` (anonymised `_1.._N` mega-prompt, pasted into frontier LLMs by hand).

---

## 3. Call / dependency relationships worth remembering

```
train_v2.py --imports--> data_v2.py {build_hf_dataset_v2, tokenize_dataset_v2, load_split, SPLITS_DIR}
train.py    --imports--> data.py    {build_hf_dataset,    tokenize_dataset,    load_split, SPLITS_DIR}   (v1, do not edit)

camera_ready/pipeline.py --> camera_ready/check.py --> evaluation_protocol.py
                         |                         `-> build_v2_judge_prompt.RUBRIC + rubric_v2.md
                         |--subprocess--> v2_comprehensive_eval.py --> evaluation_protocol.py
                         |                                         `-> bm25_rag.BM25Retriever
                         |--subprocess--> verify_camera_ready.py   --> evaluation_protocol.py
                         |                                         `-> build_v2_judge_prompt.RUBRIC
                         `--subprocess--> internal_eval/{judge_per_item,stats_v2}.py

audit_gap_gate.py --imports--> bm25_rag.GAP_TOPIC_PATTERNS   (single source of truth since FINDINGS 42)
judging/aggregate.py --reads--> PRECOMMIT.md, controls_key.json, blind_map.json, items.jsonl, eval_bank_v2.json
```

**Non-obvious couplings:**

- `verify_camera_ready.py` **imports `RUBRIC` from `build_v2_judge_prompt.py`** — a script `camera_ready/README.md` calls "optional". It is not optional: deleting it breaks verification *and* `check`.
- `check.py` asserts `RUBRIC.strip() in rubric_v2.md`. Edit one, edit both.
- `evaluation_protocol.CAMERA_SOURCE_FILES` is the provenance list. `check --strict` requires every file in it to be **committed and clean**, and `run_camera_ready.ps1` is itself in that list.
- `bm25_rag.py` and `v2_comprehensive_eval.py` both read `splits/10cat/train.json` — **the BM25 knowledge base is the training set.**

---

## 4. Data mental model — where the representation changes

| Stage | Shape / schema | Code |
|---|---|---|
| Raw | `[{question, answer}]`, 5,550 | `data/firstaidqa_v1.json` |
| Enriched | `+ category, question_type, safety_critical, safety_critical_confidence, category_confidence, template_idx` | `data.enrich_dataset_semantic` — NLI `cross-encoder/nli-deberta-v3-small`, two passes: 18-way category + binary SC |
| Split | same schema; **val/test forced `template_idx=0`**; strata with `<5` samples go entirely to train; train shuffled | `data.stratified_split(seed=42)` |
| Prompt strings | `{instruction, text, safety_critical, category}` — `instruction` is a **strict prefix** of `text` | `data_v2.build_hf_dataset_v2` |
| Tensors | `input_ids / attention_mask / labels`, all padded to `max_length`; labels = input_ids with the instruction span and pads set to **-100** | `data_v2.tokenize_dataset_v2` |
| Eval bank | `{question_id: V2Q01..V2Q41, question, reference, category, safety_critical, safety_critical_confidence, template_idx}` — 41 items, **11 SC (26.8%)** | `evaluations/eval_bank_v2_40q/eval_bank_v2.json` — the name says 40, the count is 41 |
| Generation | `run.json → variants[config].answers[]`, each `{question_id, prompt_policy, config_resolution, question, reference, category, safety_critical, answer, answer_pre_truncation, repetition_truncated, tokens_generated, tokens_per_sec, elapsed_s, peak_vram_mb, meta{}}` | `v2_comprehensive_eval.run_questions` |
| Judging items | `{qid, config, blind_id, question, reference, sc_flag, category, answer}` — the config name never reaches the judge | `judging/assemble_items.py`, salt `first_aid_v2_judging_2026` |
| Judgments | one JSONL line per (item x prompt_type): quality 0–5, safety `n_violations` + `violated_categories ⊂ SO01..SO12` | `judging/judge_deepseek.py` |

**Template mechanics (the thing you will have forgotten).** Gemma's chat template **has no `system` role** — passing one raises `'System role not supported'`. `build_hf_dataset_v2` folds `SYSTEM_PROMPT + "\n\n" + INSTRUCTION_PREFIXES[tidx] + question` into a single **user** turn, and the answer turn uses role `"model"`, not `"assistant"`. Prefixes: `0="Question: "`, `1="A patient asks: "`, `2="Emergency situation: "`, `3=""`. `apply_chat_template` already emits `<bos>`, so both tokenizer calls use `add_special_tokens=False` — that is what keeps the mask boundary aligned. **Do not "fix" either of these.**

**Category-schema fork [Observed].** `splits/10cat/*.json` carry 10 labels (`Bleeding & Wounds`, `Cardiac & Resuscitation`, …). `data.py` / `data_v2.py`'s `CATEGORY_DEFINITIONS` returns a **different, 17-label legacy set**. The two never intersect. **This bites `enhanced_inference.py` only:** it re-derives the category with `classify_category(question, "")` (17 labels) and looks it up in a map keyed by `train.json` (10 labels), so every lookup misses and falls back to `_default` (`FINDINGS` 1). **Camera-ready T4 (config D) is unaffected** — `run_t4_improved` reads `q["category"]` straight from the bank, and bank-vs-train labels intersect 10/10 (verified).

---

## 5. Model mental model

**Build** (`train_v2.load_model_and_tokenizer`): `AutoTokenizer` (`padding_side="right"`; the `pad_token = unk_token` fallback is guarded by `if tokenizer.pad_token is None` and **does not fire for Gemma** — the saved `tokenizer_config.json` records `pad_token: "<pad>"`, so `<pad>` is what the label-masking loop compares against) → `AutoModelForCausalLM` with `BitsAndBytesConfig(load_in_4bit, nf4, double_quant, compute_dtype=fp16)` and `device_map="auto"` → `prepare_model_for_kbit_training` (4/8-bit only) → `get_peft_model(LoraConfig(r=16, alpha=32, dropout=0.05, bias="none", CAUSAL_LM, target_modules=[q,k,v,o,gate,up,down]_proj))` → `model.config.use_cache = False`.

**Train:** `Trainer` + `DataCollatorForSeq2Seq(label_pad_token_id=-100, pad_to_multiple_of=8)` + `EarlyStoppingCallback`. `eval_strategy = save_strategy = "steps"` with **`eval_steps = save_steps = 200` hardcoded** (`TrainConfig.eval_steps` is dead code), `load_best_model_at_end=True` on `eval_loss`, `optim="paged_adamw_8bit"`, `gradient_checkpointing=True`, `fp16 = (cfg.fp16 and quant != "fp16")`, `save_total_limit=2`. **`--patience` counts evaluations, not epochs** (≈1.1 epochs at 555 steps/epoch).

**Save:** `output_dir/adapter/` (`save_pretrained` + tokenizer) and `training_curve.json`.

**Inference** (`v2_comprehensive_eval.load_model` / `generate`): base + `PeftModel.from_pretrained`. **The tokenizer is loaded from the *adapter* directory whenever an adapter is used**, otherwise from `model_path`. Decoding is `do_sample=False` **plus** `repetition_penalty=1.15, no_repeat_ngram_size=4`, `eos_token_id=get_stop_ids(tok)` (eos + `<end_of_turn>` + `<|im_end|>` + `[/INST]`), `max_new_tokens=350`. Post-hoc `_truncate_repetition` cuts at the 3rd identical sentence by **slicing the original string** — never re-joining, which is the regression fixed in `d0fdb61` (`FINDINGS` 18). "Greedy" in the paper means greedy *with* those constraints; they are recorded in `run_args._decoding`.

**Canonical adapter — actually recorded hyperparameters** (`experiments/10cat_4bit_r16_lr1e-4_p3_v2_20260508_054337/training_curve.json`): `lr=1e-4`, `patience=3`, **`max_length=320`**, `seed=42`, r16 / α32 / dropout 0.05, cosine, warmup 0.03, grad_accum 4, batch 2, clip 1.0 → `best_val_loss = 1.33996`, stopped at epoch 2.879, 4,508 s. **FIXED 2026-09-05:** `train_v2.py`'s CLI defaults were `lr=2e-4`, `patience=2`, `max_length=512` and did not reproduce this adapter (`FINDINGS` 23). They are now the canonical values, verified by parsing `--quant 4bit` and comparing every field.

---

## 6. Experiment & configuration flow

**Where experiments are declared — four places that must all agree:**

1. `v2_comprehensive_eval.CONFIG_MAP` — 25+ letter codes → labels. Letter groups: A–G (4-bit base), H–O (8-bit base), P–Y (fp16 base), Z1/Z2 (prompt controls).
2. `v2_comprehensive_eval.run_questions` — the `elif` dispatch chain mapping a label to a runner (`run_finetuned_greedy` / `run_t4_improved` / `run_t6_improved` / `run_rag_bm25` / `run_premise` / `run_oneshot`).
3. `v2_comprehensive_eval` **PASS 1–9** via `_run_pass` — GPU load order by `(quant, adapter)`; one model load per pass. A label listed in the wrong pass raises `Config resolution mismatch`.
4. `evaluation_protocol.CAMERA_READY_CONFIG_RESOLUTION` + `CAMERA_CONFIG_CODES` — the frozen `{base_quant, adapter, technique}` contract, written into every answer record and re-validated at verify time.

**Config identities:** A = base 4-bit greedy · B = 4-bit + canonical adapter greedy (**canonical**) · C = **8-bit base with the same 4-bit-trained adapter** (the name is about base precision) · D = T4 soft-retry (excluded, "loop-fix pending") · E = T6 binary SAFE/UNSAFE gate → `SAFE_FALLBACK` on UNSAFE · F = B + gated top-1 BM25 · G = A + gated top-1 BM25.

**Configuration sources:** `camera_ready/protocol.yaml` (JSON-compatible YAML, stdlib-parsed — model, adapter, question bank, `max_new_tokens=350`, `expected_questions=41`, judge list, output prefix); `evaluation_protocol.py` constants (`PROMPT_POLICY="offline_definitive_v1"`, `SYSTEM_PROMPT`, `SAFE_FALLBACK`, `FROZEN_QUESTION_FIELDS`, `CAMERA_SOURCE_FILES`); `judging/PRECOMMIT.md` (7 contrasts + confirmation rule) and `PRECOMMIT_PANEL.md` (3 judges + the 3/3 rule); `judging/override_categories.json` (SO01–SO12).

**Seeds & determinism.** Training: `42` across python / numpy / torch / cuda / HF. Generation: **no seed** — it is greedy, therefore deterministic. Judging: `temperature=0`, `SHUFFLE_SEED=2026`, per-call disk cache keyed by `sha256(model, template_hash, prompt_type, item)` → fully resumable; `--nonce` is the only bypass. Stats: `BOOTSTRAP_N=10_000`, `BOOTSTRAP_SEED=2026`, `SC_WEIGHT=2.0`.

**Environment variables:** `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY` (used by both `claude_or` and `gpt`), plus legacy `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`. Nothing else reads the environment.

---

## 7. Reproduction recipe (shortest realistic path)

**Blocking prerequisites — this clone cannot generate as-is.**

1. `conda create -n fine_tuning python=3.11` then `pip install -r requirements.txt`, **plus** `rank_bm25`, `openai`, `scipy`, `rouge-score` — none of which are in `requirements.txt`.
2. `python utils/download_model.py` → `models/gemma-2b-it/`. Every script resolves local-first, HF Hub second.
3. **Restore adapter weights.** `experiments/.../adapter/adapter_model.safetensors` and `tokenizer.json` are gitignored and absent. Without them `AutoTokenizer.from_pretrained(adapter_path)` fails before the model even loads. **[Inferred]** they exist only on the original workstation or inside the (also gitignored) `experiments/*.zip`.

**Training (only if the freeze is lifted).** The command that actually produced the canonical adapter is `powershell_scripts/run_v2_baseline.ps1`:

```
train_v2.py --quant 4bit --model_path models\gemma-2b-it
            --splits_dir splits\10cat --splits_tag 10cat
            --lora_r 16 --lora_alpha 32 --lora_dropout 0.05
            --lr 1e-4 --max_grad_norm 1.0 --lr_scheduler cosine --warmup_ratio 0.03
            --grad_accum 4 --weight_decay 0.01 --patience 3 --epochs 10
            --seed 42 --max_length 320
```

Since the 2026-09-05 defaults fix the bare `python train_v2.py --quant 4bit` also reproduces it (verified: lr 1e-4, patience 3, max_length 320, seed 42, r16/a32, grad_accum 4). The PowerShell runner remains the reference invocation because it is explicit about all of them.

**Generation + verification:**

```powershell
python camera_ready/pipeline.py check --strict     # must be clean, incl. a committed source tree
python camera_ready/pipeline.py generate           # add --commit to commit the verified run
python camera_ready/pipeline.py status
```

→ `evaluations/CAMERA_READY_OFFLINE_<ts>/`. Verification asserts: prompt policy + SHA-256, frozen question-bank hash identical across all configs, model/adapter content fingerprints, git commit + clean tree, 6 configs x 41 questions, no empty answers, BM25 metadata schema, and that **V2Q35 gates as `tourniquet_escalation`** and **V2Q41 as `spinal_logroll`**.

**Published judging:**

```powershell
python judging/assemble_items.py --run_dir evaluations/CAMERA_READY_OFFLINE_<ts> --configs all
python judging/make_controls.py                        # appends 45 controls -> 291 items
python judging/judge_deepseek.py --model deepseek  --run_tag <TAG> --controls_only   # gate first
python judging/judge_deepseek.py --model deepseek  --run_tag <TAG>
python judging/judge_deepseek.py --model claude_or --run_tag <TAG>
python judging/judge_deepseek.py --model gpt       --run_tag <TAG>
# commit judging/PRECOMMIT.md BEFORE the next line - the ordering must be visible in git history
python judging/aggregate.py --model deepseek --run_tag <TAG>
```

→ `judging/results/<model>/<TAG>/FINAL_REPORT.md`, then apply the 3/3 rule by hand.

**Reproducing the *old* result.** The July numbers (`B−A=+0.902`, `G−B=−1.024`, `C−B=+0.122` null, DeepSeek) are **already reproducible without a GPU**: `judging/results/*/CAMERA_READY_FINAL/judgments.jsonl` are on disk, so re-running `aggregate.py` regenerates every table. The *generation* side is not reproducible — `CAMERA_READY_20260708_180411` predates the provenance contract and `validate_run_prompt_provenance` rejects it.

---

## 8. Where do I touch the code?

| Goal | Files / modules | Main function / class | Also must change | Risk |
|---|---|---|---|---|
| Change model architecture / LoRA | `train_v2.py` | `TrainConfig.target_modules`, `load_model_and_tokenizer` → `LoraConfig` | `parse_args` duplicates the default | Med — old checkpoints stop loading |
| Change prompt format | `data_v2.py` | `build_hf_dataset_v2`, `INSTRUCTION_PREFIXES`, `SYSTEM_PROMPT` | `v2_comprehensive_eval.prompt_standard` must match | **High** — silently invalidates every comparison |
| Change preprocessing / splits | `data.py` | `stratified_split`, `enrich_dataset_semantic` | `save_splits` now refuses to clobber; pass `--overwrite` to mean it | Med (was High) |
| Change loss / masking | `data_v2.py` | `tokenize_dataset_v2.tokenize_fn` | `verify_masking.py` | **High** — currently correct; see "protect these" |
| Change decoding | `v2_comprehensive_eval.py` | `DECODING_PARAMS`, `generate`, `get_stop_ids` | surfaces in `run_args._decoding` | Med |
| Add an eval config | `v2_comprehensive_eval.py` | `CONFIG_MAP` + `run_questions` dispatch + the right `_run_pass` | `CAMERA_READY_CONFIG_RESOLUTION`, `CAMERA_CONFIG_CODES`, `protocol.yaml:config_codes`, `check.py` (`codes == list(CAMERA_CONFIG_CODES)`), `verify_camera_ready` sanity list | **High** — 5 files |
| Add / change the question bank | `evaluations/eval_bank_v2_40q/eval_bank_v2.json` | — | `protocol.yaml:expected_questions`, `assemble_items.EXPECTED_N`, `check.py` ID-order assertion `V2Q01..V2QNN` | **High** |
| Change retrieval | `bm25_rag.py` | `GAP_TOPIC_PATTERNS`, `BM25Retriever.retrieve`, `RETRIEVED_TOKEN_CAP` | `audit_gap_gate.py` imports the patterns; `verify_camera_ready.MUST_GATE` pins V2Q35 / V2Q41 | Med |
| Change the rubric | `rubric_v2.md` **and** `build_v2_judge_prompt.RUBRIC` | — | `check.py` asserts containment; `judging/prompt_quality.txt` + `prompt_safety.txt` are hashed into every manifest | **High** — changes the template hash, invalidates the cache, forces full re-judging |
| Add a judge | `judging/judge_deepseek.py` | `MODEL_CONFIGS`, `init_model` | `aggregate.init_model` valid list; `PRECOMMIT_PANEL.md` | Low–Med |
| Add a metric / contrast | `judging/aggregate.py` | `build_stats`, `build_config_summary` | `PRECOMMIT.md` **must be committed first** | Med |
| Change judging concurrency / retries | `judging/judge_deepseek.py` | `MAX_CONCURRENCY=2`, `MAX_RETRIES`, `BACKOFF_BASE` | — | Low — 2 is deliberate: Windows IOCP hangs higher |

---

## 9. Fragile areas & hidden coupling

**FIXED 2026-09-05 — was a live bug that broke every generation run. [Observed]**
`v2_comprehensive_eval.main()` reads `ALL_RAG_CONFIGS` at **line 870** but assigns it at **line 922**, both at function scope, so Python treats the name as local. The read sits inside a generator expression, so the raised type is **`NameError`** (free variable in enclosing scope), not `UnboundLocalError`. **Reproduced by execution**, not inference: stubbing the heavy imports and calling `main()` dies at line 870. It is **unconditional** — `--configs A B` requests no RAG config and still raises, because `argparse nargs="+"` guarantees `requested` is non-empty. Introduced in `79962fc` (2026-09-05); the last generation run was 2026-07-08, so the path has never been executed since. **Fixed** by hoisting `ALL_RAG_CONFIGS` to module scope beside `CONFIGS_TECHNIQUE`. Re-verified by execution: the stubbed `main()` now clears provenance assembly, question loading, `compute_floor_map` and BM25 retriever construction, reaching PASS 1's model load - so no second defect was hiding behind it. Note `v2_comprehensive_eval.py` is in `CAMERA_SOURCE_FILES`, so this edit must be committed before the next `generate` or `check --strict` blocks the run on a dirty source tree.

**"Changing X silently requires changing Y":**

- **`python data.py` used to overwrite `splits/10cat/`** (`SPLITS_DIR = splits/10cat`) with the *17-label legacy* schema. `save_splits` now raises unless `--overwrite` is passed, in all three copies of it (`data.py`, `data_v2.py`, `utils/classify_10cat.py`). That silently changes the BM25 knowledge base, the T4 floor map, and the `train_split` artifact fingerprint. The 10-label splits came from `utils/classify_10cat.py`, whose paths (`HERE = utils/`) no longer resolve — it looks for `utils/firstaidqa_v1.json` and writes `utils/splits_10cat/`. **[Inferred]** it was run from the repo root under an earlier layout. **Treat `splits/10cat/` as a frozen artifact.**
- **Adding config D** touches the 5 files in §8 *and* requires lifting `verify_camera_ready.EXCLUDED_CONFIGS`.
- **Auto-detect disagrees between tools.** `evaluation_protocol.latest_run_dir` defaults to prefix `"CAMERA_READY_"` — so a bare `verify_camera_ready.py` picks the **July** run — while `pipeline.newest_run` uses `protocol.yaml`'s `"CAMERA_READY_OFFLINE_"` and finds none. Always pass `--run_dir` / `--run`.
- **Analysis siblings sort after runs** (`"_"` > `""`). Both selectors explicitly exclude `*_ANALYSIS`; any new "find newest run" helper must too (`FINDINGS` 12).
- **Judge prompt files are hashed, not just read.** Touching `prompt_quality.txt` / `prompt_safety.txt` changes `template_hash`, invalidates all of `judging/cache/`, and forces 582 fresh calls per judge.
- **`assemble_items.py` without `--append` wipes the 45 planted controls** — re-run `make_controls.py` after every rebuild (was `FINDINGS` 16, now gated).

**Shape / value assumptions.** Static padding to `max_length=512` against a ~73-token median wastes ~7x of training compute, and `DataCollatorForSeq2Seq(pad_to_multiple_of=8)` is passed to the Trainer with nothing to do (`FINDINGS` 22). Instruction tokenisation uses `truncation=False` while the full text uses `truncation=True`, so an over-long instruction yields a **fully masked** example with no warning (`FINDINGS` 51).

**Do not touch — verified correct, easy to "fix" wrongly:** the system-prompt-folded-into-user-turn workaround; `add_special_tokens=False` on both tokenizer calls; the deliberately two-sided `confirmed` in `aggregate.py`; `MAX_CONCURRENCY=2`.

**Known-broken; do not trust their numbers:** `enhanced_inference.py` (`ImportError: GAP_QUESTION_IDS` → `NameError` on question 1), `rag_inference.py` (stops only on `<eos>`, so saved answers contain fabricated dialogue turns), and the pre-v3 similarity gate (`raw / raw.max()` makes top-1 always exactly 1.0 ≥ threshold).

**Research-specific — affects comparability with published results.** `GAP_TOPIC_PATTERNS`, `T6_UNSAFE_CRITERIA`, `SAFE_FALLBACK`, `ONE_SHOT_EXAMPLE`, the 41-question bank, and the 45 planted controls exist for specific paper claims. `SAFE_FALLBACK` has already been rewritten to drop its EMS referral, so **config E's new number is not comparable to July's E** (`DECISIONS.md`, 2026-09-05). Generic infrastructure that is safe to refactor: `evaluation_protocol.py`'s hashing helpers, `camera_ready/{pipeline,check}.py`, the caching and retry layers in `judge_deepseek.py`.

---

## 10. What I need to remember

1. **The generate stage was crashing unconditionally** (`ALL_RAG_CONFIGS` `NameError`, regression from `79962fc`, never exercised since 2026-07-08). **Fixed 2026-09-05** by hoisting to module scope. If generation dies before PASS 1 again, suspect the provenance-assembly block in `main()` — roughly 60 lines that no test covers.
2. **There are no model weights in this checkout.** `models/` and every `*.safetensors` / `tokenizer.json` are gitignored. Nothing that loads a model can run here.
3. **`camera_ready/pipeline.py` is the only supported entry point** — `check → generate → verify`. The run directory is immutable; everything else lands in the `_ANALYSIS` sibling.
4. **No `CAMERA_READY_OFFLINE_*` run has ever existed.** Every published number comes from `CAMERA_READY_20260708_180411`, generated under an *EMS-advising* prompt, not the offline premise the code now enforces.
5. **The canonical adapter is `10cat_4bit_r16_lr1e-4_p3_v2_20260508_054337`**, trained at **`max_length=320`, `lr=1e-4`, `patience=3`, seed 42**, `val_loss=1.33996`. `train_v2.py`'s defaults differ — reproduce via `powershell_scripts/run_v2_baseline.ps1`.
6. **`patience` counts evaluations (every 200 steps), not epochs** — ≈1.1 epochs at 555 steps/epoch.
7. **Gemma has no `system` role.** It is folded into the user turn, the answer role is `"model"`, and `add_special_tokens=False` on both tokenizer calls is what keeps the -100 boundary aligned. All three are load-bearing.
8. **`splits/10cat/` uses a 10-label schema that `data.py` cannot reproduce**, and `python data.py` will overwrite it with the 17-label one. Frozen artifact.
9. **The BM25 knowledge base is the training split.** Retrieval is gated top-1 with a 115-word cap; only 2 of 41 eval questions actually gate (V2Q35, V2Q41), and 5 of the 7 patterns never fire.
10. **Config C is an 8-bit *base* carrying the 4-bit-trained adapter** — the name describes precision, not training. Claims about the 8-bit *adapter* are confounded: it was trained by the old `train.py` / manual template.
11. **291 items x 2 prompt types = 582 calls per judge** — 246 real (6 configs x 41) + 45 planted controls. Judging is disk-cached and fully resumable; only `--nonce` bypasses it.
12. **`PRECOMMIT.md` must be committed before `aggregate.py` runs** — the ordering has to be visible in git history.
13. **The 3/3 same-direction rule is deliberately manual;** `aggregate.py` is single-judge by construction. Confirmed 3/3: B−A, G−B, G−F. Inconclusive (judges disagree on sign): F−B SC, E−B.
14. **DeepSeek served `deepseek-v4-flash` on 582/582 calls** against a pre-registered `deepseek-v4-pro`. Undecided: disclose or re-judge.
15. **Read `DECISIONS.md` before planning work.** Retraining is frozen, which makes findings 6e, 22, 23, 29, 30 and 32 permanent limitations rather than a work queue. Generation and API judging are open.


---

## 11. Fixes applied 2026-09-05 (uncommitted working tree)

| # | File | Change | Verified by |
|---|---|---|---|
| 1 | `v2_comprehensive_eval.py` | `ALL_RAG_CONFIGS` hoisted to module scope; the local copy in `main()` removed | stubbed `main()` reaches PASS 1 model load |
| 2 | `camera_ready/check.py` | new `model` / `4-bit adapter` weight + tokenizer-vocab checks, wrapped in `except (KeyError, TypeError, ValueError)`; success strings made lazy so an empty match cannot `IndexError` | `check` now reports 4 new FAIL rows; summary 41 pass / 3 warn / 6 error |
| 3 | `train_v2.py` | CLI and dataclass defaults set to the canonical `lr=1e-4`, `patience=3`, `max_length=320`; RUN SEQUENCE docstring corrected | parser re-executed; every field matches `training_curve.json` |
| 4a | `data.py`, `data_v2.py`, `utils/classify_10cat.py` | `save_splits(..., overwrite=False)` refuses to clobber existing splits; `--overwrite` added to all three CLIs | sandboxed: guard fires, `--overwrite` still writes, empty dir not blocked, real `splits/` untouched |

**Not applied, deliberately.** `padding="max_length"` -> `padding=False` (`FINDINGS` 22) is untested
here because `datasets` is not installed; smoke-test it on ~50 rows before a real run. The
`enhanced_inference.py` repairs are skipped in favour of retiring the file - it needs both an
import fix (`GAP_QUESTION_IDS` no longer exists in `bm25_rag`, leaving `BM25Retriever` unbound on
the default path) and the category fix before it produces any number, and `FINDINGS` 1 says its
numbers cannot support the T4/T6 rejection regardless.

**Before the next `generate`:** commit at least `v2_comprehensive_eval.py` and
`camera_ready/check.py` - both are in `CAMERA_SOURCE_FILES`, and `check --strict` refuses a dirty
source tree.
