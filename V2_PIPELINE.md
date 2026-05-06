# Gemma 2B LoRA/QLoRA — V2 Final Training Pipeline
*Generated from evaluation of V1 fine-tuned adapters. Reference this before any training run.*

---

## Context

V1 models were trained on the raw `firstaidqa_v1.json` dataset (5,550 Q&A pairs) with no
category classification, no stratified splits, and an earlier version of train.py. The V1
evaluation (ROUGE + 4 LLM judges) identified five root causes of degradation that V2 must fix:

1. No category stratification in train/val/test splits
2. pad_token == eos_token in tokenizer (template bleed root cause at training time)
3. Template 3 omitted system prompt from 25% of training examples
4. Answer-prefix tokens ("Answer: ", "Response: ") created inference-training mismatch
5. Four critical protocols were under-represented or absent in the dataset

---

## Step 1 — Audit dataset for protocol gaps

Run this first. Do not skip.

```python
import json, re

data = json.load(open("firstaidqa_v1.json"))

protocol_checks = {
    "choking_back_blows":      r"back blow",
    "choking_heimlich_only":   r"heimlich|abdominal thrust",
    "shock_correct_position":  r"(supine|legs? (up|elevated|raised)|lay.*flat|elevate.*legs?).*shock|shock.*(supine|legs? (up|elevated|raised)|lay.*flat|elevate.*legs?)",
    "shock_contaminated":      r"shock.*(recovery position)|recovery position.*shock",
    "anaphylax_epipen_lead":   r"^.{0,80}(epipen|epinephrine|adrenaline)",
    "arterial_tourniquet":     r"(arterial|sever.*bleed).*(tourniquet)|(tourniquet).*(arterial|sever.*bleed)",
    "ring_pad":                r"ring pad",
}

for key, pat in protocol_checks.items():
    hits = [d for d in data if re.search(pat, d["question"]+" "+d["answer"], re.I)]
    print(f"{key:35}: {len(hits)}")
```

### V1 Audit Results (run 2026-05-05 against firstaidqa_v1.json, n=5550)

| Check | Count | Status |
|-------|-------|--------|
| choking — back blows in answer | 12 | ⚠️ LOW — and all hedged with "if trained" |
| choking — Heimlich only, no back blows | 41 | ⚠️ Wrong protocol priority |
| shock — correct position (supine+legs) | 40 | ⚠️ LOW — hedged with "if no spinal injury" |
| shock — CONTAMINATED (recovery pos + shock) | 3 | ❌ Contradictory labels |
| anaphylaxis — EpiPen leads answer (<80 chars) | 7 | ❌ CRITICALLY LOW |
| arterial bleeding + tourniquet together | 2 | ❌ ZERO usable — both examples DISCOURAGE tourniquet |
| ring pad mentions | 24 | ❌ More than arterial+tourniquet — explains 8-bit failure |

**Root cause confirmed:** The model outputs exactly what the data distribution predicts.
Arterial bleeding has 0 correct examples → model learned ring pad (24 examples).
EpiPen is buried in 22/29 anaphylaxis examples → 4-bit learned to omit it.

---

## Step 2 — Augment dataset for confirmed gaps

Write new Q&A pairs sourced from **ANZCOR 2024 / Australian Red Cross / St John Ambulance Australia**.
Keep exact same JSON format: `{"question": "...", "answer": "..."}`.
Save additions to `augmented_examples.json` (separate file for paper transparency).
Append to `firstaidqa_v1.json` before running data.py.

### Targets

| Protocol | Target count | Priority |
|----------|-------------|----------|
| Arterial bleeding → tourniquet (write from scratch) | 60–80 | CRITICAL |
| Choking: back blows FIRST, then abdominal thrusts, alternate | 50–60 | CRITICAL |
| Shock: supine flat, legs 30 cm — conscious patient only | 40–50 | HIGH |
| Anaphylaxis: EpiPen leads the answer, no hedging | 40–50 | HIGH |

### Choking examples must include:
- The alternating sequence (5 back blows → 5 abdominal thrusts → repeat)
- The severe scenario ("cannot speak, cannot breathe, cannot cough")
- The distinction from mild choking ("still able to cough → encourage coughing")
- Do NOT include "if trained" qualifier — assume rescuer is following instructions

### Shock examples must explicitly contrast:
- Conscious shock → supine flat, legs elevated ~30 cm
- Unconscious → THEN recovery position
- This contrast must appear in the answer, not just the correct answer alone

### Arterial bleeding examples must include:
- Tourniquet 5–8 cm above the wound
- Tighten until bleeding stops
- Record the time applied
- Do not remove the tourniquet
- Call 000
- Do NOT mention ring pad in these examples

### Anaphylaxis examples must:
- Lead with EpiPen/epinephrine in the first sentence
- Not bury it after signs/symptoms description
- Include: outer thigh injection, call 000, second EpiPen after 5 min if no improvement

---

## Step 3 — Fix data.py (3 changes)

### Change 1 — Add system prompt to Template 3 (line 197-199)
```python
# OLD:
else:  # template 3 -- minimal, no system prompt
    return (f"<start_of_turn>user\n{question}<end_of_turn>\n"
            f"<start_of_turn>model\n")

# NEW:
else:  # template 3 -- question-first, keeps system prompt
    return (f"<start_of_turn>user\n{s}\n\n"
            f"{question}<end_of_turn>\n"
            f"<start_of_turn>model\n")
```

### Change 2 — Remove answer-prefix tokens from templates 0-2
```python
# Template 0:
return (f"<start_of_turn>user\n{s}\n\nQuestion: {question}<end_of_turn>\n"
        f"<start_of_turn>model\n")

# Template 1:
return (f"<start_of_turn>user\n{s}\n\nA patient asks: {question}<end_of_turn>\n"
        f"<start_of_turn>model\n")

# Template 2:
return (f"<start_of_turn>user\n{s}\n\nEmergency: {question}<end_of_turn>\n"
        f"<start_of_turn>model\n")
```
All templates now end at `<start_of_turn>model\n`. Model learns to open its own response.

### Change 3 — Fix template_idx assignment (line 326 in enrich_dataset)
```python
# OLD:
"template_idx": i % 4,

# NEW:
"template_idx": rng.randint(0, 3),
```

---

## Step 4 — Fix train.py (4 changes)

### Change 1 — Pad token (MOST IMPORTANT, line 159-161)
```python
# OLD:
if tokenizer.pad_token is None:
    tokenizer.pad_token = tokenizer.eos_token
    tokenizer.pad_token_id = tokenizer.eos_token_id

# NEW:
if tokenizer.pad_token is None:
    tokenizer.pad_token = tokenizer.unk_token
    tokenizer.pad_token_id = tokenizer.unk_token_id
```

### Change 2 — Disable use_cache for fp16 (after line 184, get_peft_model)
```python
model = get_peft_model(model, lora_cfg)
model.config.use_cache = False   # incompatible with gradient checkpointing
```

### Change 3 — Fix fp16 training precision (line 232)
```python
# OLD:
use_fp16 = cfg.fp16 and cfg.quant != "fp16"

# NEW:
use_fp16 = True
```

### Change 4 — Step-based evaluation (lines 247-248 in TrainingArguments)
```python
# OLD:
eval_strategy="epoch",
save_strategy="epoch",

# NEW:
eval_strategy="steps",
eval_steps=200,
save_strategy="steps",
save_steps=200,
```

---

## Step 5 — Generate clean splits

```bash
python data.py --no-semantic --seed 42
```

Verify output: all 4 gap categories (Choking, Shock, Anaphylaxis, Severe bleeding) must
appear in both train and val splits. The category distribution table is printed automatically.

---

## Step 6 — Train all three variants

```bash
python train.py --quant 4bit --seed 42
python train.py --quant 8bit --seed 42
python train.py --quant fp16 --seed 42
```

- Same seed across all three — quantisation is the only variable
- If fp16 val loss diverges after initial decrease → retrain with `--lr 1e-4`
- If any variant stops at epoch 1 or 2 → bump `--patience 3`
- If any variant still improving at epoch 10 → increase `--epochs 15`

---

## Step 7 — Verify no template bleed

```bash
python eval_suite.py
python garbage_audit.py
```

Target: mean garbage score < 1.0 for all fine-tuned variants.
If artifacts persist → add `<end_of_turn>` to eos_token_id in eval_suite.py generate() call.

---

## Step 8 — Full evaluation

```bash
python eval_suite.py
python evaluate.py --no-bert
python garbage_audit.py
```

Then regenerate `llm_judge_prompt.txt` and submit to all 4 judges (same rubric as V1).

---

## Paper framing

"V2 fine-tuning addressed five sources of V1 degradation: unclassified training data,
pad/EOS token conflation, inconsistent instruction templates, missing system prompt in 25%
of training examples, and insufficient representation of four critical protocols.
V2 results are compared against the identical V1 evaluation setup."

V1 failures become named findings. Template bleed discovery is a transferable methodology
contribution. Quantisation-as-regularisation (fp16 underperforms quantised variants on
5,550 samples) connects to Dettmers et al. 2023 (QLoRA paper).

---
*Last updated: 2026-05-05 | Dataset: firstaidqa_v1.json (5,550 samples)*
