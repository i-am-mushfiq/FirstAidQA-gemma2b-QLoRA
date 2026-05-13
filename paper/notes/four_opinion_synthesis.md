# Four-LLM Second Opinion Synthesis
**Date:** May 2026  
**Prompt used:** `paper/notes/second_opinion_prompt.txt`  
**Judges:** 4 frontier LLMs (independent, identical prompt)

---

## What All Four Reports Agree On

**BM25 over dense retrieval.** All four recommend BM25 as the preferred retrieval
method, citing its strength with exact medical keywords ("tourniquet," "epinephrine,"
"cardiac arrest"). Dense/semantic retrieval risks returning "semantically close but
clinically wrong" answers.

**RAG will fail on the 5 protocol-gap questions.** Unanimous. For Q17, Q21, Q6, Q22,
and Q28, retrieval cannot fix what is not in the KB — it will surface the nearest
plausible but incorrect example. For Q21 (infant choking) and Q22 (embedded object)
this is actively dangerous, not just unhelpful.

**Prompt-level intervention alone cannot suppress Category B heuristics.** All four
explicitly state that the recovery-position-in-cardiac-arrest and CPR-on-breathing-
patient patterns are too deep in base model weights to be reliably suppressed by
system prompts alone. Shared language: "probabilistically effective, not
deterministically effective."

**Post-generation rule-based safety filter is essential.** Every report recommends
a deterministic keyword/phrase scanner running after generation to catch dangerous
outputs before they reach the user. Shared requirements: cheap, on-device, keyword-
based — not another model pass.

**Strict context/token budget discipline.** All converge on roughly 1 example under
~100–150 tokens for any retrieved content, citing quadratic attention cost on mobile
CPUs and the 20–30s latency ceiling.

**Greedy decoding (temperature=0) for SC categories.** All recommend it as a
supporting measure, not a primary fix.

---

## Consensus "Must Do" Stack (Priority Order)

1. **Category-conditional system prompt with explicit "NEVER" prohibitions** —
   specific per category, not generic safety language.

2. **BM25 RAG with hard 1-example limit**, gated to skip retrieval entirely for
   the 5 gap questions (especially Q21 and Q22 — retrieval would actively harm).

3. **Rule-based post-generation safety filter** — deterministic keyword matching
   with canned fallback responses for highest-risk categories.

4. **Greedy decoding (T2, temperature=0)** for all safety-critical queries.

---

## Points of Disagreement / Scepticism

**Logit biasing** — Reports 2 and 3 recommend it as a "surgical" tool. Reports 1
and 4 are dismissive: brittle, token-level, prone to word-salad, and the model will
find alternative phrasings. No consensus. Not implementing until further evidence.

**Number of RAG examples** — Report 2: 2 examples max (~300–400 tokens). Report 3:
strictly 1 example (<80 tokens). Report 4: 2 is safe ceiling with token-count guard.
Shared verdict: "very few." Starting with 1 to stay within latency budget.

**Keyword anchoring (T3)** — Reports 1 and 3 moderately positive for format-forcing.
Report 4 explicitly negative: will cause hallucinated step lists on gap questions;
recommends restricting to non-SC categories only. Report 2 does not prioritise.
Decision: restrict T3 to non-SC only for now.

**Curated "Gold KB" for retrieval** — Report 2 insists on replacing train.json with
100–200 clinically verified snippets. Other reports do not distinguish — they focus
on gating and filtering instead. Deferring Gold KB; implementing gap-question gating
first.

**Expected SC-mean ceiling** — Report 3 (optimistic): SC mean could reach 3.0–3.5
with inference-only fixes. Report 4 (pessimistic): reaching 3.0+ without retraining
is unlikely; protocol gaps are a hard ceiling. Others do not commit to numbers.
Using Report 4's framing for honest expectations.

---

## Implications for This Project

The consensus confirms and sharpens the steelman analysis:
- T5 RAG remains the highest-priority technique, but must be BM25-only and
  gap-question-gated.
- T1 system prompt remains high-priority but must be category-specific, not generic.
- A post-generation safety filter is a new requirement not in the original T1–T6
  framework — it is cheap and should be added.
- T3 keyword anchoring is limited to non-SC categories.
- T2 greedy stays on.
- T4 and T6 remain rejected.
- Logit biasing: parking unless T5+T1 proves insufficient.
