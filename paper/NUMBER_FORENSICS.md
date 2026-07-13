# Number Forensics — Task 3
## Reconciliation of three headline counts in the manuscript

**Date:** 9 July 2026  
**Verified against:** `data/firstaidqa_v1.json`, `data/firstaidqa_v1_enriched_10cat.json`,  
`splits/10cat/train.json`, `evaluations/eval_bank_v2_40q/eval_bank_v2.json`,  
`data/eval_questions_40.json`, `paper/SESSION_LOG_v2_to_v3.md`

---

## 1. The 68.3% / 28-of-41 EMS-first figure

**Finding: the 28/41 = 68.3% count is from the v2 evaluation bank's original reference answers, before the offline-deployment rewrite. It is not reproducible from any file currently on disk because the rewrite replaced those references.**

The figure is documented in two project files. `paper/SESSION_LOG_v2_to_v3.md` states: "28 of 41 original reference answers (68.3%) mentioned calling 000 or EMS as the leading or only action." `paper/PROJECT_HANDOFF_v3.md` repeats the same sentence. Neither the old 40-question bank (`data/eval_questions_40.json`, 40 entries) nor the current `eval_bank_v2.json` (rewritten references, 0/41 EMS-first hits under any regex) is the correct referent. The correct denominator is the 41-entry v2 bank as it existed before the offline-deployment reference rewrite, which transformed the answer structure from `[Call EMS] → [Steps]` to `[Immediate action] → [Contraindications/Don'ts] → [Escalation cues]`. The pre-rewrite version is not retained on disk; the SESSION_LOG is the audit trail.

**For the manuscript:** attribute as "28 of the 41 initial v2 bank reference answers (68.3%; `paper/SESSION_LOG_v2_to_v3.md`) led with a call-EMS instruction, making them unsuitable as evaluation targets for an offline-only deployment. All 41 references were rewritten before the camera-ready run; the revised answers appear in `evaluations/eval_bank_v2_40q/eval_bank_v2.json`."

**Which reference set produced 28 hits:** the 41-question v2 bank **before** the offline-deployment rewrite (October 2026 session). The old 40Q bank is ruled out because it has 40 entries, not 41; its own EMS-first count under a broad regex is 11/40 (27.5%), not 28.

---

## 2. 5,500 vs 5,550 — corpus record count

**Finding: `data/firstaidqa_v1.json` (5,550 records) is canonical for this project. The publicly released FirstAidQA dataset contains 5,500 records. The 50 extra records are a head/jaw-injury topic block appended at indices 5500–5549 during local data collection prior to this project.**

Programmatic diff of `data/firstaidqa_v1.json` confirms: the last 50 records (indices 5500–5549) cover head injury, jaw injury, skull fracture, and facial trauma — topics absent from the first 5,500 entries. None of the 50 overlap with earlier questions (0 shared question strings). They were appended as a supplemental block, not interspersed, and do not appear in the publicly released dataset.

Additionally, within the full 5,550-record file: 41 records are exact (question, answer) duplicates of earlier entries, all concentrated in an eye-injury sub-block at indices 2808–2848 (29 eye-injury records, 2 burn records, 10 other). A further 60 records repeat a question text with a different answer (variant answers for the same scenario). These are pre-existing dataset construction artefacts, not introduced by this project.

**Canonical count for all paper numbers: 5,550** (`data/firstaidqa_v1.json`). The comment in `data.py` line 350 that reads "5,500 samples" is an early-development approximation written before the final dataset size was established; it is not a count of any real file and should be corrected to 5,550 in any manuscript reference.

**Footnote text for the paper:** "The local corpus (`data/firstaidqa_v1.json`) contains 5,550 Q&A pairs. The publicly released FirstAidQA dataset contains 5,500 records; the 50 additional entries (indices 5500–5549) are a head/jaw-injury block collected locally and not part of the released version. Within the 5,550-record file, 41 records are exact duplicates of earlier entries (eye-injury block, indices 2808–2848) and 60 records repeat a question with a different answer; all downstream processing used the full 5,550-record file without deduplication, as duplicates constitute less than 1.1% of training data and were not expected to materially affect learning."

---

## 3. SC (safety-critical) definition reconciliation

**Three distinct SC rates appear across project documents. They are not errors relative to their own denominators and definitions, but they use three different definitions and denominators. Every paper number must state which definition it uses.**

**36.5% (2,028/5,550) — INCORRECT FIGURE in `paper/notes/second_opinion_prompt.txt`**

This figure appears in the second-opinion prompt submitted to external LLMs as supporting context. No data file contains exactly 2,028 SC records. The two candidate figures from actual files are 2,228 (40.1%) and 1,231 (22.2%). The 36.5% / 2,028 figure is a transcription error introduced when writing the second_opinion_prompt, likely computed from an intermediate or misread source. The submitted prompt is now archived and the error cannot be corrected retroactively in the judge responses. All paper tables and claims should use one of the two verified figures below and note that the second_opinion_prompt cited an erroneous intermediate count.

**40.1% (2,228/5,550) — Broad SC definition, full corpus, original 6-category designation**

Source: `data/firstaidqa_v1_enriched.json`. The original SC designation assigned SC=True to all records in six categories: CPR/Cardiac arrest (782), Shock/Unconsciousness (681), Choking/Airway (445), Spinal/Head injuries (203), Anaphylaxis (58), Severe bleeding (59). Total: 2,228/5,550 = 40.1%. This is the "full-dataset broad definition" using the pre-remapping 18-category schema. It is not the operative training definition.

**22.2% (1,231/5,550) — Remapped SC definition, 10-category schema, full corpus**

Source: `data/firstaidqa_v1_enriched_10cat.json` and `data/firstaidqa_v1_enriched_threshold020.json` (both identical SC counts). After remapping from 18 to 10 categories the SC designation was recalibrated: 1,231/5,550 = 22.2%. This is the rate used for the v2 evaluation bank stratified sampling design (DESIGN_RATIONALE.md: "22.2% (1,231/5,550)"). SC is distributed across all 10 categories at varying rates (Cardiac & Resuscitation highest at 48.6%; Trauma & Musculoskeletal lowest at 6.3%).

**22.4% (997/4,441) — Remapped SC definition, train split only**

Source: `splits/10cat/train.json`. The train split contains 997 SC records out of 4,441 total = 22.4%. This is the operationally relevant rate for class-balance reasoning during training and is the figure cited in PROJECT_HANDOFF_v2.md and PROJECT_HANDOFF_v3.md when discussing training exposure.

**Rule for the paper:** the 22.2% figure (full 10-cat corpus) is used for corpus description and evaluation bank design; the 22.4% figure (train split) is used for training methodology; the 40.1% figure (original 6-category broad SC) is used only when referencing the first-phase results that pre-date the remapping. The 36.5% / 2,028 figure should not appear in the final paper.

---

## Summary table

| Figure | Value | Denominator | Source file | Status |
|---|---|---|---|---|
| EMS-first in v2 bank pre-rewrite | 28/41 = 68.3% | 41 original v2 bank refs | `SESSION_LOG_v2_to_v3.md` (documented, not on disk) | Correct — attribute to pre-rewrite refs |
| Corpus record count | 5,550 | full local file | `data/firstaidqa_v1.json` | Canonical |
| Released dataset count | 5,500 | publicly released | FirstAidQA HuggingFace release | Use in footnote only |
| Extra records | 50 (indices 5500–5549) | head/jaw injury block | `data/firstaidqa_v1.json` | Local addition |
| SC broad (original 6-cat) | 2,228 / 5,550 = 40.1% | full corpus, 18-cat schema | `data/firstaidqa_v1_enriched.json` | Valid — early-phase use only |
| SC remapped (10-cat, corpus) | 1,231 / 5,550 = 22.2% | full corpus, 10-cat schema | `data/firstaidqa_v1_enriched_10cat.json` | **Primary corpus SC figure** |
| SC remapped (10-cat, train) | 997 / 4,441 = 22.4% | train split only | `splits/10cat/train.json` | **Primary training SC figure** |
| SC in second_opinion_prompt | 2,028 / 5,550 = 36.5% | — | `paper/notes/second_opinion_prompt.txt` | **INCORRECT** — transcription error |
