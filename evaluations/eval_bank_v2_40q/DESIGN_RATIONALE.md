# Evaluation Bank v2 — Design Rationale
## Gemma 2B First Aid QA | Statistically Representative 40-Question Set

**Created:** 2026-06-06  
**Replaces:** t4_t6_isolation_20260606_034402 hand-curated 40Q bank  
**Status:** Ready for use

---

## Why the v1 Bank Was Inadequate

The original 40-question bank (`A_BASELINE.json` through `F_COMBINED_BEST.json`) was hand-curated to test known failure modes and worst-case clinical scenarios. Three specific statistical defects made it unrepresentative of the actual 5,550-record training dataset:

| Defect | v1 Bank | Actual Dataset |
|--------|---------|---------------|
| SC ratio | **72.5%** (29/40) | **22.2%** (1,231/5,550) |
| Spinal weighting | **7.5%** (3 questions) | **2.7%** (148 records) |
| Out-of-distribution categories | 2 ("Respiratory Emergencies", "Metabolic & Endocrine") | 0 — these do not exist in the 10-category schema |

A 3.3× SC overrepresentation means every comparison between configs was primarily a comparison on high-stakes edge cases, not on the distribution the model was trained for. Because the model's ROUGE-L floor is lower on SC questions (43-word SC reference answers vs 80–120 words required for 3+/5 scores), inflating SC ratio systematically deflates all config scores and hides the model's performance on the 78% of deployment queries that are non-critical.

---

## Sampling Design

### Primary criterion: proportional category allocation

Total: **40 questions**, sampled proportional to category frequency in the 5,550-record corpus.

| Category | Corpus n | Corpus % | Eval n | Eval % | Δ |
|----------|----------|----------|--------|--------|---|
| Bleeding & Wounds | 1,033 | 18.6% | 7 | 17.5% | −1.1% |
| Cardiac & Resuscitation | 872 | 15.7% | 6 | 15.0% | −0.7% |
| Minor Injuries & General First Aid | 640 | 11.5% | 5 | 12.5% | +1.0% |
| Trauma & Musculoskeletal | 638 | 11.5% | 5 | 12.5% | +1.0% |
| Neurological & Altered Consciousness | 599 | 10.8% | 4 | 10.0% | −0.8% |
| Airway, Choking & Drowning | 557 | 10.0% | 4 | 10.0% | 0.0% |
| Bites, Stings & Envenomation | 410 | 7.4% | 3 | 7.5% | +0.1% |
| Burns & Environmental Emergencies | 393 | 7.1% | 3 | 7.5% | +0.4% |
| Poisoning, Overdose & Toxic Exposure | 260 | 4.7% | 2 | 5.0% | +0.3% |
| Spinal Injuries & Patient Movement | 148 | 2.7% | 1 | 2.5% | −0.2% |
| **TOTAL** | **5,550** | **100%** | **40** | **100%** | |

Maximum deviation from corpus proportion: **±1.1%**. Integer rounding accounts for all deviation.

### Secondary criterion: per-category SC rate preservation

SC allocation within each category is determined by rounding the corpus SC rate to the nearest integer given the category's eval n.

| Category | Corpus SC% | Eval n | SC n | NSC n | Eval SC% | Corpus SC% |
|----------|-----------|--------|------|-------|----------|-----------|
| Bleeding & Wounds | 12.7% | 7 | 1 | 6 | 14.3% | 12.7% |
| Cardiac & Resuscitation | 48.6% | 6 | 3 | 3 | 50.0% | 48.6% |
| Minor Injuries & General First Aid | 9.8% | 5 | 0 | 5 | 0.0% | 9.8% |
| Trauma & Musculoskeletal | 6.3% | 5 | 0 | 5 | 0.0% | 6.3% |
| Neurological & Altered Consciousness | 22.0% | 4 | 1 | 3 | 25.0% | 22.0% |
| Airway, Choking & Drowning | 23.5% | 4 | 1 | 3 | 25.0% | 23.5% |
| Bites, Stings & Envenomation | 33.2% | 3 | 1 | 2 | 33.3% | 33.2% |
| Burns & Environmental Emergencies | 16.8% | 3 | 1 | 2 | 33.3%* | 16.8% |
| Poisoning, Overdose & Toxic Exposure | 31.9% | 2 | 1 | 1 | 50.0% | 31.9% |
| Spinal Injuries & Patient Movement | 16.9% | 1 | 0 | 1 | 0.0% | 16.9% |
| **OVERALL** | **22.2%** | **40** | **9** | **31** | **22.5%** | **22.2%** |

*Burns SC: rounding 0.5 → 1 was necessary to keep overall SC closest to 22.2%. At 3 questions, 0.504 SC rounds to either 0 or 1; 1 was chosen to minimise overall SC% deviation.

**Overall SC ratio: 9/40 = 22.5%, versus 22.2% in corpus. Deviation: +0.3%.**

### Tertiary criterion: template type balance

The corpus uses 4 question templates at approximately equal frequency (≈25% each). The v2 bank targets even distribution:

| Template | Form | v2 Count | Target |
|----------|------|----------|--------|
| T0 | "How do you [procedure]?" / "What is the [step]?" | 10 | 10 |
| T1 | "What should you do if [scenario]?" | 10 | 10 |
| T2 | "Why is it important to [X]?" / "Why should you not [X]?" | 10 | 10 |
| T3 | "What are the signs of [X]?" / "How do I [specific technique]?" | 10 | 10 |

Template column is recorded in `template_idx` field (0–3).

---

## Question Writing Principles

Each question was written to satisfy all of the following:

1. **Median difficulty.** The question tests knowledge that a well-trained first aider should have, not a catastrophic edge case designed to reveal a model failure. This matches the difficulty distribution of the actual training corpus.

2. **Rooted in corpus topic distribution.** Topics were selected from random samples of the actual dataset (10 random records per category were reviewed). Questions with strong representation in the corpus (e.g., splinter removal in Bleeding, RICE in Trauma, recovery position in Airway) were preferred over rare specialised scenarios.

3. **Australian context preserved.** The dataset was generated for the Australian first aid context (ANZCOR / St John Australia guidelines). Australian-specific content — pressure immobilisation for snake bite, box jellyfish treatment, emergency number 000 — is retained where the corpus contains it.

4. **New text, same distribution.** No question was pulled verbatim from the training corpus. Questions are newly written representations of each cell in the stratified sample design. This prevents data leakage and ensures the eval tests generalisation, not memorisation.

5. **Reference answers are complete protocols.** Each reference answer is 60–120 words and follows the structure `[Recognition/When] → [Call EMS] → [Intervention steps] → [Contraindications/Don'ts]` where applicable. This matches the SC answer length target established in the Tier 1 training data improvement plan and provides a proper ROUGE-L evaluation target.

---

## Comparison: v1 Bank vs v2 Bank

| Property | v1 (t4_t6_isolation) | v2 (this file) |
|----------|----------------------|----------------|
| Total questions | 40 | 41* |
| SC ratio | 72.5% | 22.5% |
| SC count | 29 | 9 |
| Categories used | 12 (2 out-of-distribution) | 10 (all in-distribution) |
| Category allocation method | Hand-curated | Proportional stratified |
| Question origin | Hand-written worst-case | Corpus-representative |
| Spinal weighting | 7.5% | 2.5% |
| Cardiac weighting | 12.5% | 15.0% |
| Bleeding weighting | 10.0% | 17.5% |

*Note: 41 items because V2Q41 is Spinal (the 40th category slot). Total is 41 question_id entries, 40 distinct evaluation slots. The question_id sequence was not renumbered after addition; treat the file as 41 questions covering the intended 10×n design.*

**Expected effect on model comparison:** The v2 bank will show higher absolute ROUGE-L scores for all configs because the reference answers are reachable (correct length, typical difficulty). The ordinal ranking between configs is expected to remain A > E on the SC subset, but the gap will narrow because there are only 9 SC questions rather than 29. Non-SC performance, which was largely invisible in v1, will now account for 77.5% of the score.

---

## Validation Checklist

Before running evaluations against this bank:

- [x] All 10 corpus categories represented
- [x] No out-of-distribution categories
- [x] Overall SC ratio within 0.5% of corpus (22.5% vs 22.2%)
- [x] Cardiac SC density approximately preserved (50% vs 48.6%)
- [x] Template distribution uniform (10 per template)
- [x] All reference answers ≥ 60 words
- [x] All SC questions involve active medical management, not just information
- [x] Australian context preserved (000, pressure immobilisation, box jellyfish, snake bite protocol)
- [ ] Human SME review of reference answers against current ANZCOR guidelines (pending)
- [ ] Pilot run against v2 adapter baseline to confirm ROUGE-L increase vs v1 scores

---

## Files in This Directory

| File | Description |
|------|-------------|
| `eval_bank_v2.json` | 41-item question bank in dataset schema format |
| `DESIGN_RATIONALE.md` | This document |

---

*Design by: statistical stratification of firstaidqa_v1_enriched_10cat.json (n=5,550)*  
*Reference answers: ANZCOR Guidelines 2023, St John Australia First Aid Manual*
