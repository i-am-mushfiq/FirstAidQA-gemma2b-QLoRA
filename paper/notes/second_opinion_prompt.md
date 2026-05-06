# Second Opinion Request — Fine-Tuning Dataset Audit

## Context

I am fine-tuning Google Gemma 2B Instruct (2.51B parameters) using LoRA/QLoRA for offline
first-aid emergency guidance on Android devices. The dataset is 5,550 Q&A pairs in this format:

```json
{"question": "What should you do if someone is choking and cannot speak?", "answer": "..."}
```

I trained three variants: 4-bit QLoRA (NF4), 8-bit LoRA (INT8), and fp16 LoRA. I then
evaluated the outputs using four LLM judges (Claude, GPT, Gemini, DeepSeek) on 20 curated
first-aid questions with this rubric:

- Medical Accuracy (0–2)
- Critical Step Coverage (0–2)
- Safety & Escalation (0–1)
- Dangerous Advice Penalty (0 or -1)
- Total: -1 to 5 per question

## What failed in the trained models (per judge evaluation)

All four variants failed on these specific questions:

**Q4 — Adult choking, cannot speak or breathe:**
Every model missed the back blows + abdominal thrusts alternating sequence.
8-bit explicitly told the rescuer NOT to use back blows. Score: 0/5 all variants.

**Q17 — Position for conscious shock casualty:**
Every model recommended the recovery (lateral) position. Correct answer is supine flat,
legs elevated ~30 cm. Score: 0/5 all variants across all judges.

**Q11 — Anaphylaxis from bee sting:**
4-bit omitted epinephrine entirely and recommended cold compress instead.
Base and 4-bit scored 0/5. 8-bit and fp16 scored 4-5/5.

**Q6 — Severe arterial bleeding, direct pressure failed:**
8-bit recommended a "ring pad" (used for embedded objects, not arterial bleeding)
and stated "if in shock, do not attempt to control the bleeding" — directly dangerous.
Only 4-bit correctly applied tourniquet protocol.

## Dataset audit results (exact regex counts against 5,550 examples)

I ran targeted regex searches against the raw training dataset. Here are the exact counts
for the protocol steps that failed in model outputs:

| What I searched for | Count |
|---------------------|-------|
| "back blow" anywhere in answer | 12 |
| "heimlich" or "abdominal thrust" WITHOUT "back blow" | 41 |
| Shock + correct position (supine/legs elevated) in same answer | 40 |
| Shock + "recovery position" in same answer (contradictory label) | 3 |
| Anaphylaxis + EpiPen/epinephrine in first 80 chars of answer | 7 |
| Anaphylaxis + EpiPen/epinephrine anywhere in answer | 29 |
| "arterial" or "severe bleeding" + "tourniquet" in same answer | 2 |
| "ring pad" anywhere | 24 |

The 2 arterial+tourniquet examples are not usable: one discourages tourniquet for snake
bites, one discusses tourniquet for dislocated joints (both in negative/cautionary context).
Effectively 0 examples of "arterial bleeding → apply tourniquet."

## My interpretation (seeking a second opinion on this specifically)

1. Arterial bleeding → tourniquet: effectively 0 usable examples. I plan to write 60–80
   new examples from scratch sourced from ANZCOR 2024 guidelines.

2. Choking: 12 back-blow examples, all hedged with "if trained." The model learned to treat
   back blows as optional rather than the mandatory first step. I plan to add 50–60 examples
   with explicit step sequence (back blows first, then abdominal thrusts, alternate).

3. Shock position: 40 correct examples but hedged ("if no spinal injury prevents it"), plus
   3 contradictory examples. I plan to add 40–50 examples that explicitly contrast conscious
   shock (supine + legs) vs. unconscious (recovery position).

4. Anaphylaxis: 29 examples total but only 7 lead with EpiPen in the first sentence.
   I plan to add 40–50 examples where EpiPen is the first step, not buried after symptoms.

## Questions for your second opinion

1. Are my count thresholds reasonable? Is 12 back-blow examples genuinely too low for a
   5,550-sample fine-tune of a 2B model, or would more epochs / better hyperparameters
   overcome it?

2. Is the "contaminated label" issue (3 shock+recovery position examples) likely to matter
   at 5,550 samples, or is the correct signal (40 examples) strong enough to override 3
   contradictory ones?

3. For the arterial bleeding gap — given 0 usable examples — is 60–80 augmented examples
   the right target, or does it risk over-representing that protocol relative to others?

4. Is there anything in the failure pattern I described (universal Q17 failure, 4-bit Q11
   failure, 8-bit Q6 failure) that points to a different root cause than data scarcity —
   for example, a systematic training configuration issue I might be missing?

5. For a 2B parameter model fine-tuned with LoRA (r=16, alpha=32) on 5,550 samples with
   early stopping — what is your expectation for how many examples of a specific protocol
   are needed before it reliably reproduces that protocol at inference?

Please be critical. If my diagnosis is wrong or my augmentation targets are misguided,
I want to know before I train the final paper-ready model.
