# Inference-Time Implementation Plan
**Based on:** 4-LLM expert consensus synthesis, May 2026  
**Adapter:** `experiments/10cat_4bit_r16_lr1e-4_p3_v2_20260508_054337/adapter`  
**Target:** SC mean ≥ 2.0 (from 1.61), no new dangerous advice introduced  

---

## Phase 1 — T5 BM25 RAG (Isolated Test)

**Goal:** Establish the RAG-only baseline before combining techniques.

### Code changes in `enhanced_inference.py`

- [ ] Replace dense retrieval (`sentence-transformers`) with BM25 as primary  
      (`from rank_bm25 import BM25Okapi`)
- [ ] Build BM25 index from `splits/10cat/train.json` at class init
- [ ] Hard 1-example limit: retrieve top-1 only (not top-3)
- [ ] Token cap: truncate retrieved example to 150 tokens before prepending
- [ ] Gap-question gate: add a set of gated question IDs  
      `GAP_QUESTIONS = {"Q17", "Q21", "Q6", "Q22", "Q28"}`  
      If question ID is in GAP_QUESTIONS, skip retrieval entirely
- [ ] Flag in run metadata: `bm25_fired`, `bm25_skipped_gap`

### Install
```
pip install rank_bm25 --break-system-packages
```

### Run
```
python enhanced_inference.py \
  --adapter_path experiments/10cat_4bit_r16_lr1e-4_p3_v2_20260508_054337/adapter \
  --questions_file data/eval_questions_40.json \
  --no_greedy_sc --no_min_tokens --no_two_pass
  # (T5 only, everything else off)
```

### Evaluation
- [ ] Run `evaluate.py --all` for ROUGE
- [ ] Build judge prompt: `python build_llm_judge_prompt.py`
- [ ] Compare SC mean: non-gap questions vs gap questions (separate)
- [ ] Gate is working if: gap questions show same score as baseline, 
      non-gap SC questions show improvement

---

## Phase 2 — Category-Conditional System Prompt

**Goal:** Measure Safety/Escalation dimension gain from specific prohibitions.

### Prompt design (draft — requires clinical review before production)

```python
SYSTEM_PROMPTS = {
    "cardiac":   ("You are an offline first-aid assistant. ALWAYS advise calling "
                  "999/911/112 first. NEVER suggest placing an unresponsive patient "
                  "on their side before confirming they are breathing. NEVER advise "
                  "CPR for a conscious breathing patient."),
    "spinal":    ("You are an offline first-aid assistant. ALWAYS advise calling "
                  "999/911/112 first. NEVER advise moving the patient unless there "
                  "is an immediate life threat. NEVER suggest removing a helmet unless "
                  "the airway is fully obstructed and cannot be cleared otherwise."),
    "airway":    ("You are an offline first-aid assistant. ALWAYS advise calling "
                  "999/911/112 first. For infant choking, back blows and chest thrusts "
                  "are required — NEVER perform abdominal thrusts on an infant."),
    "anaphylaxis":("You are an offline first-aid assistant. ALWAYS advise calling "
                   "999/911/112 first. The EpiPen goes into the outer mid-thigh. "
                   "NEVER advise antihistamines as the primary treatment."),
    "bleeding":  ("You are an offline first-aid assistant. ALWAYS advise calling "
                  "999/911/112. Apply direct pressure. For arterial bleeding: tourniquet "
                  "above the wound, 5–7 cm, NOT over a joint. NEVER remove an embedded "
                  "object — stabilise it in place."),
    "shock":     ("You are an offline first-aid assistant. ALWAYS advise calling "
                  "999/911/112 first. Lay the patient flat and elevate the legs. "
                  "NEVER give fluids to an unconscious or unresponsive patient."),
    "default":   ("You are an offline first-aid assistant. ALWAYS advise calling "
                  "999/911/112 first for any life-threatening situation. Provide "
                  "clear step-by-step first-aid instructions."),
}
```

### Code changes
- [ ] Add `SYSTEM_PROMPTS` dict to `enhanced_inference.py`
- [ ] Map each question's category to the correct system prompt key
- [ ] Apply system prompt via `apply_chat_template(system_prompt=...)` or  
      prepend as user message if system role not supported by Gemma template
- [ ] Add `--no_system_prompt` flag for ablation

### Run (Phase 2 isolated)
```
python enhanced_inference.py \
  --no_greedy_sc --no_min_tokens --no_rag --no_two_pass
  # system prompt only (needs --no_system_prompt flag to disable)
```

---

## Phase 3 — Post-Generation Safety Filter

**Goal:** Deterministic catch of Category B heuristic failures.

### Implementation (`safety_filter.py` — new file, ~50 lines)

```python
import re

# Dangerous pattern → safe canned replacement
# Context-aware: check that the dangerous phrase appears in a dangerous context

DANGEROUS_PATTERNS = [
    # Recovery position applied to cardiac / not-breathing context
    (r"(recovery position|lay.{0,20}on.{0,10}side|lateral position)",
     ("cardiac", "arrest", "cpr", "not breathing", "unconscious", "aed"),
     "Keep them flat on their back. Begin CPR if trained. Call 999 immediately."),

    # CPR applied to conscious / breathing patient
    (r"(perform cpr|start cpr|begin cpr|chest compressions).{0,50}(if necessary|may be needed|might be needed)",
     ("stroke", "seizure", "heat", "breathing", "conscious"),
     "Do not start CPR — confirm the patient is not breathing first. Call 999 now."),

    # Removing embedded object
    (r"(remove the|pull out the|take out the).{0,20}(object|glass|shard|knife|fragment)",
     (),  # always dangerous
     "Do not remove the object. Pad around it and bandage over the padding. Call 999."),

    # Removing helmet
    (r"remove.{0,20}helmet",
     (),  # always dangerous
     "Do not remove the helmet unless the airway is fully blocked and cannot be cleared otherwise. Call 999."),

    # Fluids to unconscious
    (r"(give fluids|offer water|give water|rehydrat).{0,50}(unconscious|unresponsive|not conscious)",
     (),  # always dangerous
     "Never give anything by mouth to an unconscious person. Place in recovery position if breathing. Call 999."),
]

def apply_safety_filter(text: str, triggered_log: list) -> str:
    """
    Scans generated text for dangerous patterns. Replaces the dangerous sentence
    with a safe canned response. Returns the (possibly modified) text.
    triggered_log is mutated with any triggered pattern names.
    """
    lower = text.lower()
    for pattern, context_keywords, replacement in DANGEROUS_PATTERNS:
        if not re.search(pattern, lower):
            continue
        if context_keywords:
            if not any(kw in lower for kw in context_keywords):
                continue
        # Replace the offending sentence
        text = re.sub(
            r'[^.!?]*' + pattern + r'[^.!?]*[.!?]?',
            ' ' + replacement + ' ',
            text, flags=re.IGNORECASE
        )
        triggered_log.append(pattern)
    return text.strip()
```

### Integration
- [ ] Import `apply_safety_filter` in `enhanced_inference.py`
- [ ] Call it on every generated answer before saving to results
- [ ] Log `filter_triggered: bool` and `filter_patterns: list` in run metadata
- [ ] Add `--no_safety_filter` flag for ablation

---

## Phase 4 — Full Consensus Stack

Once Phases 1–3 individually tested and baselined:

```
python enhanced_inference.py \
  --adapter_path experiments/10cat_4bit_r16_lr1e-4_p3_v2_20260508_054337/adapter \
  --questions_file data/eval_questions_40.json \
  --max_new_tokens 250
  # All four: greedy SC + system prompt + BM25 RAG + safety filter
  # (T4 and T6 remain disabled permanently)
```

Expected score: SC mean 2.0–2.5. Dangerous penalty count: ≤ 1 (from 3).

---

## Phase 5 — T3 Non-SC Keyword Anchoring (Optional)

Only after Phase 4 is validated. Non-SC categories only.

Category-specific anchors:
```python
NON_SC_ANCHORS = {
    "burns":     "Key first aid steps for burns:",
    "minor":     "Key first aid steps:",
    "bites":     "Key first aid steps for bites and stings:",
    "poisoning": "Key first aid steps:",
}
```

Gate: if category is SC, never apply. Only append anchor for non-SC.

---

## Success Criteria

| Metric | Baseline | Target after Phase 4 |
|--------|----------|---------------------|
| SC mean (40Q) | 1.61 | ≥ 2.0 |
| Dangerous penalty count | 3 / 40 | ≤ 1 / 40 |
| Safety/Escalation dim mean | ~0.6 | ≥ 0.80 |
| Generation latency (GPU eval) | 19 tok/s | ≥ 12 tok/s |
| Filter trigger rate | N/A | < 5% (< 2 / 40 questions) |
