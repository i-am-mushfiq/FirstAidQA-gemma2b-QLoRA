# Second Opinion Request — Pre-Training Script Changes for Gemma 2B LoRA Fine-Tune

## What I am building

Fine-tuning Google Gemma 2B Instruct (google/gemma-2b-it, 2.51B parameters) for offline
first-aid emergency guidance on Android devices. Three quantisation variants: 4-bit QLoRA
(BitsAndBytes NF4), 8-bit LoRA (BitsAndBytes INT8), fp16 LoRA. Dataset: 5,550 Q&A pairs.

LoRA config: r=16, alpha=32, dropout=0.05, target modules = q/k/v/o/gate/up/down projections.
Training: lr=2e-4, cosine schedule, 3% warmup, effective batch size 8 (2 per device × 4
gradient accumulation), max 10 epochs with early stopping patience=2, weight_decay=0.01.

Before training the final paper-ready model I want to apply a set of fixes to the training
scripts. I have been advised to make 7 specific changes. I need a second opinion on whether
each change is correct, whether the reasoning is sound, and whether anything important is
being missed.

---

## Relevant code — exactly as it exists right now

### data.py — instruction template builder

```python
SYSTEM_PROMPT = (
    "You are a first aid assistant. Provide accurate, step-by-step emergency "
    "guidance. For life-threatening situations, always advise calling emergency "
    "services immediately."
)

def _build_instruction(question: str, template_idx: int) -> str:
    """Return the prompt text up to (but not including) the answer content."""
    s = SYSTEM_PROMPT
    if template_idx == 0:
        return (f"<start_of_turn>user\n{s}\n\n"
                f"Question: {question}<end_of_turn>\n"
                f"<start_of_turn>model\nAnswer: ")
    elif template_idx == 1:
        return (f"<start_of_turn>user\n{s}\n\n"
                f"A patient asks: {question}<end_of_turn>\n"
                f"<start_of_turn>model\nResponse: ")
    elif template_idx == 2:
        return (f"<start_of_turn>user\n{s}\n\n"
                f"Emergency: {question}<end_of_turn>\n"
                f"<start_of_turn>model\nGuidance: ")
    else:  # template 3 -- minimal, no system prompt
        return (f"<start_of_turn>user\n{question}<end_of_turn>\n"
                f"<start_of_turn>model\n")

def _build_full_text(question: str, answer: str, template_idx: int) -> str:
    """Return the complete training string (instruction + answer + end token)."""
    return _build_instruction(question, template_idx) + answer + "<end_of_turn>"
```

### data.py — template_idx assignment in enrich_dataset

```python
def enrich_dataset(raw: list, seed: int = 42) -> list:
    rng = random.Random(seed)
    enriched = []
    for i, sample in enumerate(raw):
        q = sample["question"]
        a = sample["answer"]
        category = classify_category(q, a)
        enriched.append({
            "question": q,
            "answer": a,
            "category": category,
            "question_type": extract_question_type(q),
            "safety_critical": category in SAFETY_CRITICAL_CATEGORIES,
            "template_idx": i % 4,   # <-- this is the line in question
        })
    return enriched
```

### data.py — tokenization with answer-only loss masking

```python
def tokenize_fn(batch):
    # Tokenize full sequences (instruction + answer)
    full_enc = tokenizer(batch["text"], truncation=True, padding="max_length",
                         max_length=max_length, return_tensors=None)
    # Tokenize instruction-only to find the answer boundary
    instr_enc = tokenizer(batch["instruction"], truncation=False, padding=False,
                          return_tensors=None, add_special_tokens=True)
    labels = []
    for full_ids, instr_ids in zip(full_enc["input_ids"], instr_enc["input_ids"]):
        label = list(full_ids)
        instr_len = len(instr_ids)
        for i in range(min(instr_len, len(label))):
            label[i] = -100   # mask all instruction tokens
        for i in range(len(label)):
            if full_ids[i] == tokenizer.pad_token_id:
                label[i] = -100   # mask padding
        labels.append(label)
    full_enc["labels"] = labels
    return full_enc
```

### train.py — tokenizer/pad token setup

```python
tokenizer = AutoTokenizer.from_pretrained(model_source, trust_remote_code=True,
                                           local_files_only=is_local)
if tokenizer.pad_token is None:
    tokenizer.pad_token = tokenizer.eos_token        # <-- line in question
    tokenizer.pad_token_id = tokenizer.eos_token_id
tokenizer.padding_side = "right"
```

### train.py — model loading and LoRA setup

```python
model = AutoModelForCausalLM.from_pretrained(model_source, quantization_config=bnb_config,
            device_map="auto", trust_remote_code=True, dtype=torch.float16,
            local_files_only=is_local)

if cfg.quant in ("4bit", "8bit"):
    model = prepare_model_for_kbit_training(model)

lora_cfg = LoraConfig(r=cfg.lora_r, lora_alpha=cfg.lora_alpha, lora_dropout=cfg.lora_dropout,
                      bias="none", task_type=TaskType.CAUSAL_LM,
                      target_modules=cfg.target_modules)
model = get_peft_model(model, lora_cfg)   # <-- use_cache fix goes here
```

### train.py — TrainingArguments (relevant lines)

```python
use_fp16 = cfg.fp16 and cfg.quant != "fp16"   # cfg.fp16 defaults to True
                                                # for quant="fp16": True and False = False
                                                # for quant="4bit": True and True = True

training_args = TrainingArguments(
    ...
    fp16=use_fp16,
    ...
    eval_strategy="epoch",      # <-- line in question
    save_strategy="epoch",
    load_best_model_at_end=True,
    metric_for_best_model="eval_loss",
    ...
    gradient_checkpointing=True,
)
```

### eval_suite.py — inference prompt builder (this is what runs at evaluation time)

```python
SYSTEM_PROMPT = (
    "You are a first aid assistant. Provide accurate, step-by-step emergency "
    "guidance. For life-threatening situations, always advise calling emergency "
    "services immediately."
)

def build_prompt(question: str) -> str:
    return (
        f"<start_of_turn>user\n{SYSTEM_PROMPT}\n\n"
        f"Question: {question}<end_of_turn>\n"
        f"<start_of_turn>model\nAnswer: "
    )
```

---

## The 7 proposed changes and the reasoning given for each

### data.py Change 1 — Add system prompt to Template 3

**Proposed change:**
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

**Reasoning given:** 25% of training examples omit the safety framing entirely. The system
prompt includes "always advise calling emergency services immediately." If 25% of examples
never see this, the model's calibration for EMS escalation is inconsistent. Judges docked
points for missing EMS calls across multiple evaluated questions.

---

### data.py Change 2 — Remove answer-prefix tokens from templates 0–2

**Proposed change:**
```python
# Remove "Answer: ", "Response: ", "Guidance: " from end of instruction strings
# All templates end at: f"<start_of_turn>model\n"
```

**Reasoning given:** The answer prefixes are part of the instruction (masked in labels with
-100), so the model is never trained to generate "Answer:" — it's always given it. At
inference if the prefix isn't provided, the model is in unfamiliar territory.

**Critical finding that complicates this:** eval_suite.py build_prompt() already passes
`f"<start_of_turn>model\nAnswer: "` to the model at inference (see code above). So training
template 0 and inference are currently ALIGNED — both include "Answer: ". Removing the
prefix from data.py templates without also updating eval_suite.py would CREATE a mismatch
in the opposite direction.

**The open question:** Should both be updated (remove "Answer: " from training AND inference),
or should both be left as-is (keep alignment), or does it not matter either way for a
Gemma 2B instruction-tuned model?

---

### data.py Change 3 — Fix template_idx: i % 4 → rng.randint(0, 3)

**Proposed change:**
```python
"template_idx": rng.randint(0, 3),
```

**Reasoning given:** When augmented examples are appended to the end of the JSON file,
`i % 4` will cluster them into specific template indices. Random assignment prevents
augmentation from being confounded with template style. Note: for the current dataset
with no augmentation, `i % 4` gives perfectly balanced distribution (1,387-1,388 per
template) so this change has zero impact until augmentation happens.

---

### train.py Change 1 — pad_token = unk_token instead of eos_token

**Proposed change:**
```python
# OLD:
tokenizer.pad_token = tokenizer.eos_token
tokenizer.pad_token_id = tokenizer.eos_token_id

# NEW:
tokenizer.pad_token = tokenizer.unk_token
tokenizer.pad_token_id = tokenizer.unk_token_id
```

**Reasoning given:** Gemma tokenizer has pad_token=None by default so this branch always
fires. With pad=EOS, the attention mechanism sees EOS tokens at every padding position
across every training sequence. Even though padding is masked in the loss computation,
the attention softens the model's association between EOS and "stop generating." This is
claimed to be the root cause of template bleed (model generating past its answer boundary).

**Counterpoint to consider:** Padding positions are masked in attention via the attention
mask, not just in the labels. The model cannot attend to padding in a standard causal LM
setup. Is the "attention sees EOS at padding" concern actually valid given that padding
is masked in the attention mask, or does the attention mask fully protect against this?

---

### train.py Change 2 — model.config.use_cache = False after get_peft_model

**Proposed change:**
```python
model = get_peft_model(model, lora_cfg)
model.config.use_cache = False  # incompatible with gradient_checkpointing=True
```

**Reasoning given:** gradient_checkpointing=True conflicts with use_cache=True.
prepare_model_for_kbit_training handles this for 4-bit/8-bit. For fp16 (which does not
call prepare_model_for_kbit_training), it is not handled. This change has zero impact
on the 4-bit run but fixes fp16 for later.

**Question:** Does prepare_model_for_kbit_training actually set use_cache=False, and if
gradient_checkpointing and use_cache are both True simultaneously, does it error, silently
waste memory, or silently produce wrong gradients?

---

### train.py Change 3 — use_fp16 = True always

**Proposed change:**
```python
# OLD:
use_fp16 = cfg.fp16 and cfg.quant != "fp16"

# NEW:
use_fp16 = True
```

**Reasoning given:** For the fp16 LoRA variant, the model weights are float16 but the
training loop runs with fp16=False (float32 mixed precision). This is inconsistent and
wastes memory. Setting use_fp16=True for all variants uses fp16 AMP throughout. Zero
impact on 4-bit (already True) or 8-bit (already True). Only affects fp16 variant.

**Question:** Is there a stability reason to intentionally run float32 training with
float16 weights for a LoRA adapter? Some practitioners do this deliberately to avoid
fp16 gradient underflow with small adapters.

---

### train.py Change 4 — eval_strategy="steps", eval_steps=200

**Proposed change:**
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

**Reasoning given:** With ~555 steps per epoch and early stopping patience=2, epoch-based
eval can miss the loss minimum by up to 1,110 steps. Step-based eval at 200 steps gives
~2–3 evaluations per epoch, catching the minimum more precisely. With save_total_limit=2,
only 2 checkpoints are kept at any time.

**Counterpoint to consider:** LoRA with r=16 adds only ~8M trainable parameters. With
weight_decay=0.01, lora_dropout=0.05, and such a small adapter, does intra-epoch
overfitting on a 5,550-sample dataset actually occur in practice? If it doesn't, this
change adds evaluation overhead without benefit.

---

## Specific questions for your second opinion

**1. On the answer prefix (Change 2):**
Training template 0 ends with `<start_of_turn>model\nAnswer: ` and eval_suite.py also
passes `<start_of_turn>model\nAnswer: ` at inference. They are aligned. Is this alignment
a problem, neutral, or beneficial? If neutral/beneficial, should Change 2 be skipped
entirely? If it should still be changed, does eval_suite.py need to be updated simultaneously?

**2. On the pad token (Change 1):**
The tokenizer's attention mask correctly masks padding positions. Given that masking,
does pad_token=eos_token actually affect model training for a causal LM, or is the
concern theoretical? Is unk_token the right replacement, or should a new dedicated
[PAD] token be added to the vocabulary (with embedding resize)?

**3. On template 3 (Change 1 in data.py):**
Is 25% of training examples without a system prompt a meaningful problem for a 2B
instruction-tuned model, or does Gemma's pretraining already provide enough of a
"first aid assistant" prior that 75% coverage of the system prompt is sufficient?

**4. On eval_steps (Change 4):**
For LoRA r=16 on a 5,550-sample dataset with weight_decay and dropout, is intra-epoch
overfitting a real concern or theoretical? What is your expectation for how quickly a
2B model with an 8M-parameter LoRA adapter converges on a dataset of this size?

**5. Overall:**
Are there changes I should be making to these scripts that are not listed above — things
that would materially improve a paper-ready fine-tune of Gemma 2B for medical Q&A?

Please be direct. If any of the 7 changes are unnecessary, counterproductive, or based on
incorrect reasoning, say so explicitly.
