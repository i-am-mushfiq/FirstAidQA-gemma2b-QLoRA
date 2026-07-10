# Phase 0 — Reconnaissance Notes

Generated: 2026-07-11  
No files were modified during this phase.

---

## 1. eval_bank_v2.json Schema

**Path:** `evaluations/eval_bank_v2_40q/eval_bank_v2.json`  
**Type:** JSON array, 41 elements

### Field names (exact spellings)

| Field | Type | Notes |
|---|---|---|
| `question_id` | string | Format: `V2Q01`–`V2Q41`. Primary join key. |
| `question` | string | Full question text |
| `reference` | string | Gold-standard reference answer (offline deployment version) |
| `category` | string | One of 10 values (see below) |
| `safety_critical` | boolean | `true` for 11/41 questions |
| `safety_critical_confidence` | float | 0.78–0.99 for SC items |
| `template_idx` | integer | Template index used during generation |

### Categories (10 total, with SC counts)

| Category | N | SC |
|---|---|---|
| Airway, Choking & Drowning | 4 | 1 |
| Bites, Stings & Envenomation | 3 | 2 |
| Bleeding & Wounds | 7 | 1 |
| Burns & Environmental Emergencies | 3 | 1 |
| Cardiac & Resuscitation | 6 | 3 |
| Minor Injuries & General First Aid | 6 | 1 |
| Neurological & Altered Consciousness | 4 | 1 |
| Poisoning, Overdose & Toxic Exposure | 2 | 1 |
| Spinal Injuries & Patient Movement | 1 | 0 |
| Trauma & Musculoskeletal | 5 | 0 |

### SC question IDs
`V2Q01, V2Q08, V2Q09, V2Q13, V2Q14, V2Q25, V2Q29, V2Q33, V2Q34, V2Q36, V2Q39`

### Example record (V2Q01)
```json
{
  "question_id": "V2Q01",
  "question": "You are applying direct pressure to a wound on someone's forearm...",
  "reference": "Escalate to a tourniquet immediately...",
  "category": "Bleeding & Wounds",
  "safety_critical": true,
  "safety_critical_confidence": 0.97,
  "template_idx": 0
}
```

---

## 2. Run Directory Schema

### June run: `evaluations/v2_comprehensive_20260606_200713/`
### Camera-ready run: `evaluations/CAMERA_READY_20260708_180411/`

Both have identical structure.

### Files present
- `run.json` — master file containing all configs and answers
- `<CONFIG_NAME>.json` — per-config file (same answers, one config per file)
- `metrics.json` — aggregate ROUGE/BLEU metrics
- `llm_judge_v2_prompt.txt` — mega-prompt (camera-ready only)

### `run.json` structure
```
{
  "run_type": "v2_comprehensive",
  "run_at": "<ISO timestamp>",
  "run_args": { ... },
  "configs": ["A_BASE_4BIT", "G_BASE_RAG", ...],
  "variants": {
    "<CONFIG_NAME>": {
      "n": 41,
      "answers": [ <41 answer objects> ]
    },
    ...
  }
}
```

### Per-config JSON (`<CONFIG>.json`) structure
```
{
  "config": "<CONFIG_NAME>",
  "run_args": { ... },
  "run_at": "<ISO timestamp>",
  "n": 41,
  "answers": [ <41 answer objects> ]
}
```

### Answer object fields (exact spellings)

| Field | Type | Notes |
|---|---|---|
| `question_id` | string | Join key → `eval_bank_v2.json` |
| `question` | string | Question text (copied from bank at eval time) |
| `reference` | string | Reference answer (copied from bank at eval time) |
| `category` | string | Category (copied from bank) |
| `safety_critical` | boolean | SC flag (copied from bank) |
| `safety_critical_confidence` | float | From bank |
| `template_idx` | integer | From bank |
| `answer` | string | **Generated model answer — primary field** |
| `tokens_generated` | integer | Token count of generated answer |
| `tokens_per_sec` | float | Generation speed |
| `elapsed_s` | float | Wall time for this item |
| `peak_vram_mb` | float | Peak VRAM during generation |
| `config` | string | Config name (redundant with parent key) |
| `meta` | dict | Extra metadata (e.g. `bm25_fired`, `gap_topic` for RAG configs) |

### Join integrity (verified)
- All 41 `question_id` values in run.json match bank exactly (no orphans either side)
- All `reference` values in run.json match current bank exactly (bank not patched since run)
- **Assembler must use bank as authoritative source** for `reference`, `sc_flag`, `category`
  (per brief; this is also verified as identical)

### Camera-ready configs
```
A_BASE_4BIT, B_FINETUNED_4BIT, C_FINETUNED_8BIT,
E_T6_IMPROVED, F_RAG_BM25, G_BASE_RAG
```
Note: D_T4_IMPROVED is **absent** from the camera-ready run (loop-fix pending).

---

## 3. rubric_v2.md — Structure

**Path:** `rubric_v2.md`

The file has 6 sections. The **operative rubric for judging is Section 6** ("Final Definitive Rubric — Offline Deployment Context"). Sections 1–5 are analysis and candidate rubrics; they must NOT be embedded in judge prompts.

### Section 6 rubric text
- Scoring scale: 0–5 with explicit descriptors
- Safety overrides: 12 items (see `override_categories.json`)
- T6 fallback scoring policy: TRUE_POSITIVE → 2/5, FALSE_POSITIVE → 1/5
- EMS policy: EMS-only responses capped at 2/5
- Length policy: no penalty for brevity (training median 43 words)
- Drug dose policy: naloxone/EpiPen/aspirin/glucose are credit-positive

### 12 Safety Override Categories (SO01–SO12)
Extracted to `judging/override_categories.json`. Machine-readable list with fields:
`id`, `key`, `name`, `description`.

**Human confirmation requested:** Please verify the 12-category extraction in
`override_categories.json` is complete and the descriptions are accurate before
Phase 3 (the SAFETY prompt embeds these verbatim).

---

## 4. Assembler join logic (from `build_v2_judge_prompt.py`)

The existing `load_eval_bank()` function joins on `question_id`. The assembler
will replicate this: for each answer in `run.json variants.<config>.answers`,
look up the corresponding bank entry by `question_id` and take:
- `reference` from bank (authoritative)
- `safety_critical` from bank (authoritative)
- `category` from bank (authoritative)
- `answer` from run.json (the generated text)

---

## 5. STOP condition check

**No STOP condition fired.** The (qid, config, answer_text) mapping is
unambiguous:
- qid: `answer['question_id']`
- config: the variants dict key (e.g. `"A_BASE_4BIT"`)
- answer_text: `answer['answer']`

All 41 × 6 = 246 (qid, config) pairs are present and non-empty in the
camera-ready run.
