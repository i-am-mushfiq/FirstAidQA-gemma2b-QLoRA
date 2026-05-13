# Inference-Time Improvement — Expert Consensus + Steelman Analysis

**Project:** Offline Medical First Aid, Gemma 2B QLoRA 4-bit  
**Baseline:** 2.18 / 5.00 mean (40Q), SC mean 1.61 / 5.00  
**Expert input:** 4-LLM synthesis (May 2026) — see `four_opinion_synthesis.md`  

---

## Confirmed Implementation Stack (All Four Reports Agree)

| Priority | Technique | Latency cost | Deterministic? |
|----------|-----------|-------------|----------------|
| 1 | Category-conditional system prompt ("NEVER" prohibitions) | ~0ms | No (probabilistic) |
| 2 | BM25 RAG, 1 example, gap-question gated | ~100ms + ~5–10s gen | No |
| 3 | Post-generation rule-based safety filter | ~0ms | **Yes** |
| 4 | T2 greedy decoding for SC queries | ~0ms | No |

---

## 1. Category-Conditional System Prompt  ★ HIGHEST PRIORITY

### The steelman case

Zero latency, zero KB lookup, zero code complexity. The system prompt is the
only intervention that fires on every single query including the gap questions.
For SC categories it suppresses the most consistent rubric failure: Safety &
Escalation under-scoring.

The key finding from all four reports: the prompt must be **category-specific
with explicit NEVER clauses**, not a generic "be safe" instruction. Generic
language is ignored. Specific prohibition is harder for the model to override.

Example structure (to be clinically reviewed per category):
```
[SC/Cardiac] You are an offline first-aid assistant. For cardiac emergencies:
ALWAYS tell the user to call 999/911/112 immediately as the FIRST step.
NEVER suggest placing an unresponsive patient on their side before confirming
the cause. NEVER advise CPR for a patient who is conscious and breathing.
Then provide clear step-by-step instructions.

[SC/Spinal] You are an offline first-aid assistant. For possible spinal injuries:
ALWAYS tell the user to call 999/911/112 immediately as the FIRST step.
NEVER advise the user to move the patient unless there is an immediate life
threat that cannot be managed in place. NEVER suggest removing a helmet unless
the airway is obstructed and cannot be cleared any other way.
Then provide clear step-by-step instructions.
```

**Best case:** Safety/Escalation dimension improves from ~0.6 → ~0.85+. Category
B heuristic bleed (recovery position, CPR on breathing patient) is reduced
probabilistically. All four reports: "will help, but not guarantee."

**Failure mode:** The model will occasionally generate the prohibited pattern
anyway — it is a probabilistic suppressor, not a hard block. This is why the
post-generation filter (Priority 3) is also mandatory.

**Mobile latency:** ~50–80 tokens of context overhead. Negligible.

---

## 2. BM25 RAG — 1 Example, Gap-Gated  ★ HIGH PRIORITY (but must be gated)

### The steelman case

BM25 is the right retrieval method. All four reports agree. Medical first-aid
queries are short, explicit, and keyword-rich — exact matching of "tourniquet",
"epinephrine", "cardiac arrest" outperforms semantic embedding similarity, which
risks "semantically close but clinically wrong" retrievals.

The KB is splits/10cat/train.json (4,441 Q&A pairs). For well-covered protocols
(CPR, anaphylaxis, severe bleeding with good examples), the top BM25 result
will contain correct procedural steps that the model can follow in-context.

**Hard constraints from expert consensus:**
- Maximum 1 example prepended (not 2–3 as originally designed)
- Hard token cap: ~100–150 tokens for the retrieved example
- Gap-question gate: retrieval must be **skipped entirely** for Q17, Q21, Q6,
  Q22, Q28. For Q21 (infant choking) and Q22 (embedded object), retrieval is
  actively dangerous — the nearest KB example is a plausible wrong answer.

**Best case:** SC mean improves from 1.61 → ~2.0–2.5 for non-gap SC questions.
CPR compression rate, EpiPen dosing, and bleeding control steps that were
previously omitted appear in retrieved context and are included in output.

**Failure mode for gap questions (if gate fails):** BM25 returns the most
keyword-similar training example. For infant choking, this is likely adult
Heimlich instructions — Heimlich in an infant is dangerous. The gate is not
optional.

**Implementation:** `pip install rank_bm25`. Build BM25 index from train.json
at startup. Map question to category. If category is in gap-question list,
skip retrieval. Otherwise retrieve top-1, truncate to 150 tokens, prepend.

**Mobile latency:** BM25 lookup ~10–50ms. Extra context ~5–10 seconds of
generation at 2–6 tok/s. Total: 25–40 seconds — tight but within budget.

---

## 3. Post-Generation Rule-Based Safety Filter  ★ ONLY DETERMINISTIC GUARANTEE

### The steelman case

This is the critical insight from the expert synthesis that was absent from the
original T1–T6 framework. All four reports recommended it. It is the only
technique that provides a **deterministic** safety guarantee — it does not depend
on the model cooperating.

After generation, scan the output text for known dangerous patterns. If matched,
replace with a safe canned response. The scanner runs in microseconds, requires
no GPU, no model, no retrieval.

**Patterns to detect (minimum viable set):**
```
Category B heuristics (active harm patterns):
  "recovery position" or "lay on their side" or "lateral position"
    → in context of: cardiac arrest, CPR, AED, not breathing
    → replace: "Keep them flat on their back for CPR. Call 999 now."

  "perform CPR" or "start chest compressions"
    → in context of: stroke, heat stroke, seizure (conscious patient)
    → replace: "Do not start CPR — the patient is breathing. Call 999 now."

  "remove the [helmet/object]" or "pull out the [object]"
    → replace: "Do not remove it. Stabilise it in place and call 999."

  "give fluids" or "offer water" or "rehydrate"
    → in context of: unconscious, unresponsive
    → replace: "Never give fluids to an unconscious person. Call 999 now."
```

**Implementation:** Regex matching on generated text. ~20 lines of code.
A lookup dict of pattern → safe_canned_response. No model calls. ~0ms.

**Failure mode:** False positives — a correct answer about CPR gets flagged
because it contains the word "CPR." Mitigation: use context-aware matching
(the word "CPR" is fine; "perform CPR if necessary" in a stroke answer is not).
The patterns need careful crafting but the effort is ~1–2 hours.

**Mobile latency:** ~0ms.

---

## 4. T2 — Greedy Decoding for SC Queries

All four reports agree. Temperature = 0 for SC categories. It is a supporting
measure, not a primary fix. For questions where the model knows the protocol,
greedy picks the highest-probability correct sequence. For gap questions, greedy
deterministically follows the wrong path — hence why it must be combined with
the gap gate and the post-generation filter.

T2 alone is neutral to marginally positive. T2 combined with T4 or T6 is
harmful (confirmed in earlier ablation). T2 stands alone.

---

## Deferred / Deprioritised Techniques

**Logit biasing:** Reports 2 and 3 recommend; Reports 1 and 4 dismiss as brittle.
No consensus. Risk of degenerate word-salad output. Deferring unless the agreed
stack proves insufficient.

**T3 keyword anchoring (non-SC only):** Reports 1 and 3 moderately positive.
Report 4 explicitly warns it will produce hallucinated step lists for gap
questions. Decision: restrict to non-SC categories only (burns, minor injuries,
bites/stings) where format-forcing is less dangerous.

**T4 min_new_tokens:** REJECTED. Unchanged from original analysis. SC categories
only.

**T6 two-pass self-critique:** REJECTED. Unchanged from original analysis.
Possible redesign as a defensive classification pass (not generation), but only
after Priority 1–3 are tested.

---

## Expected SC-Mean Ceiling

Report 4 (pessimistic, most credible): reaching 3.0+ SC mean without retraining
is unlikely. Protocol gaps are a hard floor. Inference-time work can address
Category B (heuristic bleed) and Category C (truncation) but cannot create
knowledge the model never learned.

Realistic target with full agreed stack implemented: SC mean 2.0–2.5 (from 1.61).
This is meaningful and safety-relevant, but not a complete solution.

---

## Recommended Test Order

1. BM25 RAG only (all other techniques off). Evaluate SC mean with and without
   gap questions. Quantify what RAG actually buys on non-gap questions.

2. System prompt only (RAG off). Measure Safety/Escalation dimension delta.
   Check for format rigidity / false escalation on non-SC questions.

3. System prompt + BM25 RAG + T2 greedy. Full consensus stack minus filter.

4. Add post-generation filter. Measure dangerous penalty count before/after.

5. Then: non-SC T3 keyword anchoring. Isolated test on non-SC questions only.
