# 1. Executive Assessment

There **is a credible paper inside this project**, but it is narrower—and scientifically more interesting—than the original “offline medical assistant” ambition.

The strongest paper is **not**:

> “We built a clinically safe offline first-aid AI using a 2B model.”

The evidence cannot support that. There is no prospective untouched test, no clinical outcome study, no validated safety endpoint, no verified on-device evaluation, only one canonical training checkpoint, and the reference/safety instruments themselves contain important weaknesses.

The strongest paper is approximately:

> **Parameter-efficient domain adaptation produces a large and statistically robust improvement in automated first-aid answer-quality scores for a 2B language model, but those gains do not establish medical safety: clinically consequential errors persist, and evaluator variability is large enough to make several smaller intervention effects unresolved.**

That is a much better research story.

The headline B−A result is real within the study: across the three principal judge routes, fine-tuning improves the 0–5 score by **+1.024, +1.342, and +0.902**, with paired standardized effects around **0.80–0.98**, confidence intervals excluding zero, and very small sign-test p-values. The stored arithmetic was independently reconstructed without discrepancy.

But the absolute fine-tuned performance remains only **2.268/5**, and the best configuration reaches only **2.504/5**. Under the project's own rubric, that corresponds roughly to incomplete-to-partially-complete answers rather than reliable medical guidance.

More importantly, the fine-tuned model still produces consequential errors involving CPR conditions, choking, bleeding escalation, burn cooling and other safety-critical steps. Several of those errors are *not detected by the safety instrument*.

### Direct publication verdict

**Yes, but with narrower claims.**

A transparent **safety-critical medical NLP / evaluation paper** is viable without retraining the entire project. A stronger **medical reliability/generalization paper** would require new evaluation evidence, particularly an independently locked scenario bank and expert clinical adjudication.

I would rate:

**Current camera-ready state:** approximately **54/100**, consistent with the forensic report's own assessment.

**Publishability potential after proper reframing and artifact/reporting corrections:** **~66/100**.

**Clinical-effectiveness/safety paper with the existing evidence:** **~30–35/100**.

The best primary framing is therefore **safety/evaluation-centered, with the adaptation result as the empirical anchor**.

---

# 2. What the Research Is Actually About

At its core, this is an experiment asking what happens when a **small general-purpose instruction model is adapted to procedural first-aid QA**, and whether several lightweight inference interventions improve it further.

The experimental chain is:

**5,550 first-aid Q&A → structured train/validation/test splits → Gemma-2B-IT QLoRA/LoRA adaptation → seven inference configurations → 41-question first-aid evaluation bank → pointwise reference-assisted LLM scoring + separate safety screening → paired statistical comparisons.**

The primary trained model is `google/gemma-2b-it`, with 4-bit NF4 loading and LoRA rank 16, alpha 32, dropout 0.05. Training uses 4,441 rows, 556 validation rows and 553 nominal test rows; checkpoint 1000 was selected from validation loss.

The seven final configurations are scientifically important to understand:

| Arm | Actual intervention                    |
| --- | -------------------------------------- |
| A   | 4-bit base Gemma-2B-IT                 |
| B   | Same base + selected LoRA adapter      |
| C   | Same adapter with 8-bit base inference |
| D   | B + category-dependent length retry    |
| E   | B + short self-safety-check/fallback   |
| F   | B + train-only BM25 retrieval          |
| G   | Base model + same BM25 retrieval       |

These definitions are verified in the report; importantly, C is **not an independently 8-bit-trained model**.

### Central research question

I would rewrite the paper's research question as:

> **Under a fixed first-aid information-access setting, how much does parameter-efficient domain adaptation of a 2B instruction model improve paired reference-assisted answer quality relative to the quantized base model, and what safety-critical limitations remain despite that improvement?**

A stronger future-study version would be:

> **Does parameter-efficient adaptation of a 2B instruction model improve independently adjudicated first-aid answer quality over the same base model on previously unseen scenarios, without increasing clinically consequential errors?**

The second is better scientifically—but the current experiment cannot answer it because the 41-question bank was repeatedly inspected and influenced later development.

### Hypothesis reconstruction

The audit report does **not** preserve enough information to quote a clean original hypothesis verbatim, so those distinctions should be reported transparently.

| Hypothesis type                            | Best reconstruction                                                 | Status                                                    |
| ------------------------------------------ | ------------------------------------------------------------------- | --------------------------------------------------------- |
| Original / implied H1                      | Domain adaptation improves first-aid answer quality over base Gemma | **Supported within this bank**                      |
| Registered central contrast                | B > A on the quality evaluation                                     | **Supported**                                       |
| Registered/central augmentation hypothesis | Adding retrieval to B improves quality                              | **Inconclusive / not statistically established**    |
| Implied safety hypothesis                  | Adaptation improves safety-critical performance                     | **Inconclusive**                                    |
| Implied quantization hypothesis            | C and B are effectively equivalent                                  | **Not supported; equivalence was never tested**     |
| Implied retry hypothesis                   | Longer retry improves quality                                       | **Not supported as an overall gain**                |
| Implied self-check hypothesis              | The safety gate improves quality/safety                             | **Not established**                                 |
| Post-hoc subgroup/category analyses        | Effects across categories and SC/non-SC subsets                     | **Exploratory**                                     |
| Post-hoc Holm sensitivity                  | B−A survives multiplicity correction                               | **Supports robustness of the bank-specific effect** |
| Post-hoc judge reliability analysis        | Repeated identical prompts reveal scoring instability               | **Supported as an exploratory measurement finding** |

The audit explicitly states that subgroup and multiplicity analyses added by the audit are post hoc and should not be represented as preregistered.

---

# 3. Primary Research Contribution

The strongest contribution is **not QLoRA**. QLoRA/LoRA is standard methodology.

The contribution is the **empirical separation between improvement in general answer-quality scores and demonstrated safety-critical correctness** in a small, domain-adapted first-aid language model.

I would build the contribution stack as follows.

### Contribution 1 — Core empirical contribution

A selected 2B model adapted with parameter-efficient fine-tuning shows a **large, consistent improvement over its base model in paired first-aid answer-quality judgments**.

This provides the paper's quantitative anchor.

### Contribution 2 — Safety boundary condition

The large quality improvement **does not eliminate clinically consequential errors**, and several serious errors survive without being triggered by the dedicated safety checklist.

That prevents a misleading interpretation of the main positive result.

### Contribution 3 — Evaluation insight

The final experiment contains a useful repeated-prompt reliability probe: identical candidate answers under identical visible judge prompts sometimes receive different scores on different calls.

For B/E identical answers, differences occurred on **9/34 DeepSeek, 1/34 Claude and 11/34 GPT-route cases**. That variability is much smaller than B−A, but comparable with—or larger than—the C/D/E/F incremental effects.

This is scientifically useful because it tells reviewers:

> the large adaptation effect is distinguishable from judge instability, while the small ablation effects may not be.

### Contribution 4 — Negative/boundary result

Simple additions—BM25 retrieval, longer-response retry and a short self-safety check—do **not establish incremental overall quality gains** beyond fine-tuning under this evaluation.

The correct result is not “they do not work.” It is:

> **their incremental effects remain unresolved in this experiment.**

### Contribution 5 — Evaluation/reproducibility lesson

The project demonstrates a useful distinction between:

**analysis reproducibility** — very strong,

and

**model/system reproducibility** — incomplete.

All saved score arithmetic and candidate/judge linkages can be reconstructed, but generation cannot be rerun exactly because required weights/tokenizer assets and fully pinned environments are absent.

### What belongs in the abstract

Contributions 1, 2 and 3.

Contribution 4 can appear as a secondary result.

Contribution 5 belongs primarily in Methods/Limitations or supplement unless reproducibility becomes part of the title.

### Medical-AI classification

This is best described as:

**safety-critical medical NLP / medical question answering / health-information generation evaluation**.

It is **not** currently:

- a clinical effectiveness study;
- a diagnostic AI study;
- an autonomous clinical decision-support validation;
- a clinical safety study;
- an on-device deployment study.

The study measures **generated medical-information quality**, not patient outcomes, diagnostic accuracy or clinical utility.

---

# 4. Primary Findings

Here are the findings I would put closest to the front of the paper.

| Finding                                                                               | Evidence                                                                        |                                                  Strength |          Novelty |     Importance | Publishable?                       |
| ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- | --------------------------------------------------------: | ---------------: | -------------: | ---------------------------------- |
| PEFT substantially improves quality scores over the base model                        | Three paired judge contrasts, CIs excluding zero, large`dz`, Holm sensitivity |                                          **Strong** |         Moderate |           High | **Yes, narrowly**            |
| Absolute performance remains low despite improvement                                  | B = 2.268/5; best arm = 2.504/5                                                 |                                          **Strong** |         Moderate |      Very high | **Yes**                      |
| Clinically consequential failures persist after tuning                                | CPR, choking, bleeding, burn and sequencing examples                            | **Strong that failures exist; weak for prevalence** |   High relevance |      Very high | **Yes**                      |
| Dedicated safety scoring misses important failures                                    | Multiple dangerous examples have every safety category false                    |            **Moderate–Strong diagnostic evidence** | Potentially high |      Very high | **Yes, as failure analysis** |
| Small ablation effects overlap judge variability                                      | Identical-answer rescoring differences vs C/D/E/F effect magnitudes             |                   **Strong within this instrument** |   Moderate–High |           High | **Yes**                      |
| Fine-tuned+RAG has positive estimates over fine-tuned alone but no established effect | F−B = +0.17 to +0.27 depending judge; CIs/sign tests not confirmatory          |                                                  Moderate |              Low |       Moderate | Yes as secondary/null result       |
| Fine-tuning outperforms base+RAG                                                      | G−B negative by −0.68 to −1.29 depending judge                               |          **Strong within-bank; exploratory design** |         Moderate | Moderate–High | Yes with label                     |

### Headline quantitative result

The three primary judges show:

| Judge     | Base A | Tuned B |           Δ B−A | 95% CI           | Paired dz |  Sign-test p |
| --------- | -----: | ------: | ----------------: | ---------------- | --------: | -----------: |
| DeepSeek  | 1.2195 |  2.2439 | **+1.0244** | [0.6341, 1.4146] |     0.804 | 1.52×10⁻⁵ |
| Claude    | 0.9024 |  2.2439 | **+1.3415** | [0.9268, 1.7561] |     0.978 | 3.47×10⁻⁶ |
| GPT route | 1.4146 |  2.3171 | **+0.9024** | [0.5854, 1.2439] |     0.810 | 5.95×10⁻⁵ |

The descriptive three-judge mean goes from **1.1789 to 2.2683**, an absolute change of **+1.0894 rubric points**.

I would **not report a “92% improvement”** even though that arithmetic can be computed. The score is ordinal, has no established ratio interpretation and has no validated minimally important clinical difference. Relative percentage improvement would imply more measurement precision than the instrument supports.

### Practical interpretation

The correct sentence is:

> Fine-tuning substantially changes the behavior of this model toward answers that the specified evaluators rate as more relevant and complete.

Not:

> Fine-tuning makes the system 92% more medically accurate.

And certainly not:

> Fine-tuning makes the system clinically safe.

---

# 5. Secondary Findings

### Fine-tuning versus base + RAG

The fine-tuned B model beats base+RAG G by:

- +0.878 DeepSeek points;
- +1.293 Claude points;
- +0.683 GPT-route points.

The G−B comparisons survive the report's Holm sensitivity but were exploratory comparisons.

This supports a limited claim:

> **For this model, corpus and retrieval configuration, domain adaptation provides value that retrieval over the unadapted base model does not recover.**

Do not generalize that to “fine-tuning is better than RAG.”

### Category consistency

The descriptive B−A average is positive in all ten reported categories, which argues against the total effect being driven entirely by one category.

However, each category contains only **1–7 questions**. These analyses should therefore be a supplementary consistency analysis, not a headline domain-by-domain result.

### Fine-tuned output behavior changes

The report observes that fine-tuning tends to generate more concise, relevant responses and reduces generic refusals and irrelevant padding. This is plausible supporting evidence for the adaptation effect.

But there is a confound worth highlighting: the base model averages **134.4 words**, versus **42.8 words for B**, and multiple base answers hit the 350-token limit. Therefore B−A should be described as the **total behavioral consequence of adaptation under this decoding setup**, not a pure estimate of increased medical knowledge.

That distinction will matter to an NLP reviewer.

---

# 6. Negative / Null Findings

These are scientifically useful and should be retained.

### Retrieval after fine-tuning

F−B estimates are consistently positive:

**+0.268, +0.171, +0.268**.

But their confidence intervals include zero or touch zero, and sign-test p-values are .093, .286 and .115.

Therefore:

> **Retrieval produced positive point estimates but no statistically established incremental improvement.**

Not:

> “RAG does not help.”

Meaningful improvements are still compatible with the uncertainty intervals.

### Quantization

C−B is approximately:

**+0.098, −0.049, −0.049** across judges.

That does not demonstrate harm, benefit or equivalence.

An equivalence claim would require a **prespecified clinically/scientifically justified margin and an equivalence/non-inferiority analysis**.

Thus:

> **The effect of changing inference precision remains unresolved.**

### Length retry

D−B is only:

**+0.049, +0.024, 0.000**.

No overall benefit is established.

Worse, at least one inspected case shows that the retry can introduce an unsafe instruction not present in the shorter answer.

That is a useful negative result:

> More generated content is not monotonically better in safety-critical procedural QA.

### Self-safety gate

E changes behavior on **7/41 questions**, so it is not a no-op.

But its effect on overall quality is approximately zero/slightly negative, it misses several important errors, and its parser can accept malformed outputs that omit the string `UNSAFE`.

Therefore:

> The lightweight self-assessment gate changes model behavior but does not establish dependable safety protection.

### Safety-critical subgroup

The 11-item SC subset does not produce confirmatory sign-test evidence for B−A across the principal judges: p=.125, .109 and .0625.

Because the subset labels themselves are problematic, I would not try to rescue this result.

Call it:

> **inconclusive and affected by questionable subgroup construction.**

---

# 7. Potentially Publishable Insights

I see **six genuinely paper-worthy insights**.

### 1. Large domain-adaptation effect

**Finding:** LoRA adaptation substantially increases reference-assisted first-aid quality scores relative to the same base model.

**Why it matters:** It demonstrates that a small 2B model can be materially shifted toward a specialized procedural health-information task.

**Unknown beforehand:** The magnitude and consistency of the shift for this specific model/domain.

**Evidence:** Three paired judge contrasts with large effect sizes and CIs excluding zero.

**Weakness:** Reused development bank, single checkpoint, automated evaluation.

**Paper role:** Primary quantitative finding.

---

### 2. Quality improvement is not safety validation

**Finding:** Large mean-score gains coexist with consequential medical errors.

**Why it matters:** Average semantic/quality scores can make a safety-critical model appear much more successful than a critical-step analysis would.

**Evidence:** CPR, choking, severe bleeding, burns and CO exposure examples.

**Weakness:** Errors are diagnostic examples rather than a clinically adjudicated prevalence estimate.

**Paper role:** Core safety finding.

---

### 3. Safety checklists can systematically miss harm types

**Finding:** The 12-category screen fails to flag several clinically meaningful errors because some error structures—conditional logic, critical omissions, treatment scope and sequencing—are not represented.

**Why it matters:** Safety metrics can fail because of the *ontology of harm*, not just because the evaluator model is inaccurate.

**Weakness:** The present taxonomy has not been systematically compared with an expert-derived taxonomy.

**Paper role:** Evaluation/failure-analysis contribution.

---

### 4. LLM-judge variance matters most for small effects

**Finding:** Identical candidate/prompt pairs receive different ratings across repeated calls.

**Why it matters:** The variability is modest compared with B−A, but it is similar in magnitude to many secondary ablations.

**Previously uncertain:** Whether observed ~0.02–0.25-point intervention differences were distinguishable from evaluator instability.

**Weakness:** This was an accidental/natural repeated-measure experiment rather than a prespecified judge-stability study.

**Paper role:** Methodological/evaluation supporting result.

---

### 5. Lightweight interventions have ambiguous incremental value

**Finding:** Retrieval, length retry and the safety self-check do not establish additional overall benefits beyond the tuned model.

**Why it matters:** Adding layers of intervention does not guarantee meaningful gain.

**Weakness:** Small benchmark, evaluator noise and adaptive development mean the effects remain unresolved.

**Paper role:** Negative/ablation story.

---

### 6. Reproducible statistics do not equal a reproducible model

**Finding:** Every reported score can be reconstructed while the model itself cannot be exactly regenerated from the released checkout.

**Why it matters:** This makes a useful distinction between *analytical provenance* and *computational reproducibility*.

**Weakness:** Mostly a reproducibility lesson rather than a novel ML result.

**Paper role:** Discussion/reproducibility contribution.

---

# 8. Best Paper Framing Options

### A. Safety/evaluation-centered — **Best**

**Possible thesis:** A large improvement in automated first-aid QA scores after small-model adaptation does not imply medically adequate behavior, and conventional LLM-based evaluation can miss consequential failures.

**Strength:** 9/10.

This framing uses both the strongest positive and strongest negative findings.

---

### B. Evaluation-centered

**Possible thesis:** LLM-as-judge instability can be negligible for large model effects yet decisive for interpreting small medical-QA ablations.

**Strength:** 8/10.

Potentially novel, but it would be considerably stronger with clinician ratings and a planned repeated-scoring experiment.

---

### C. Model-centered

**Possible thesis:** QLoRA adaptation improves Gemma-2B on first-aid QA.

**Strength:** 5/10.

Too little novelty by itself.

---

### D. Efficiency-centered

**Possible thesis:** A 2B model can acquire first-aid capability under constrained training.

**Strength:** 4/10.

The project does not have a rigorous compute-efficiency comparison, training hardware characterization or quality-per-compute curve.

---

### E. Systems/offline-AI centered

**Possible thesis:** A deployable offline emergency first-aid system.

**Strength:** 2–3/10 with existing evidence.

There is no verified Android/on-device latency, memory, energy, thermal or user study.

### Best Primary Framing

**A — Safety-critical evaluation of parameter-efficient small-model adaptation.**

It is the only framing in which virtually every unusual feature of the study contributes to one coherent scientific argument rather than looking like disconnected engineering.

---

# 9. Recommended Central Thesis

I would use this as the intellectual thesis:

> **Parameter-efficient first-aid adaptation of a 2B instruction model produces a large and reproducible improvement in in-domain automated answer-quality scores, but that improvement is insufficient evidence of medical reliability: clinically consequential errors persist, dedicated safety scoring misses important failure modes, and evaluator variability makes several small inference-time improvements statistically unresolved.**

The important conceptual contribution is therefore:

> **Improved domain competence and demonstrated safety are separable evaluation targets.**

That is stronger than merely showing that LoRA works.

It also creates a clean narrative:

**adaptation succeeds → average score rises substantially → absolute competence remains limited → safety-specific inspection reveals important failures → evaluator reliability explains why minor optimization claims should be treated cautiously.**

---

# 10. Novelty Assessment

This is where the original model-centered story becomes substantially weaker.

### Known technique: PEFT/LoRA in medical language models

Parameter-efficient fine-tuning for medical language tasks is already well established. ClinicalNLP work has shown medical-domain improvement in smaller language models using domain fine-tuning, and other work explicitly studies PEFT for clinical applications.

By 2025, published work was already fine-tuning **Gemma2-2B with LoRA** under a <3B constraint for medical QA and using **Qwen3-1.7B with QLoRA** for patient-centric healthcare tasks.

Therefore:

> **“We fine-tuned a 2B medical model with LoRA and performance improved” is not a sufficient novelty claim.**

### First-aid dataset novelty is also questionable

There is already a 2025 **FirstAidQA** resource specifically framed around first aid, emergency response and low-connectivity settings, presented as a NeurIPS 2025 Muslims in ML affinity-event poster and released publicly. It contains 5,500 synthetic first-aid QA pairs and explicitly proposes small/offline model adaptation.

Your report describes a 5.5k-scale first-aid corpus that appears very closely related. If this is the same corpus or a derivative version, **dataset novelty has already been substantially consumed by that publication**.

Do not frame this manuscript as a new dataset paper unless you can establish that the benchmark/resource is materially distinct.

### First-aid LLM evaluation is no longer untouched territory

A 2026 study has already compared major LLMs on twenty pediatric first-aid questions using ratings from a nurse and a physician, finding substantial variability and explicitly cautioning against replacement of professional care.

So avoid claims such as:

> “first evaluation of LLMs for first aid.”

### Where the novelty actually lives

The stronger novelty is at the intersection of:

**small-model adaptation + safety-critical procedural QA + evaluator reliability + residual harm analysis**.

Healthcare LLM-as-judge research is growing rapidly, but recent reviews still find significant weaknesses in human validation, bias analysis and judge reliability.

Your repeated-identical-answer analysis and concrete examples of unflagged safety-critical mistakes fit directly into that unresolved evaluation problem.

### Novel contribution versus competent technique

**Potentially novel / differentiated:**

- empirical demonstration that a large adaptation-score effect can coexist with safety-critical failures;
- explicit comparison of effect magnitude with LLM-judge repeatability noise;
- demonstration of harm-taxonomy blind spots in first-aid QA evaluation;
- detailed intervention boundary conditions.

**Not novel:**

- QLoRA;
- LoRA rank selection;
- Gemma 2B;
- BM25 RAG;
- greedy decoding;
- LLM-as-judge itself;
- bootstrap confidence intervals;
- multi-judge panels;
- safety checklists;
- synthetic first-aid QA if this is the previously released FirstAidQA resource.

---

# 11. Evidence Strength

My overall evidence hierarchy is:

| Claim                                                | Evidence strength                                          | Why                                                                             |
| ---------------------------------------------------- | ---------------------------------------------------------- | ------------------------------------------------------------------------------- |
| B scores higher than A under the final evaluation    | **Strong**                                           | Large paired differences; all three judge routes; CIs; reconstructed arithmetic |
| The result generalizes to unseen first-aid scenarios | **Weak / Unsupported**                               | Evaluation bank repeatedly inspected and development-informed                   |
| Fine-tuning makes the model clinically safer         | **Unsupported**                                      | No validated safety endpoint or clinical adjudication                           |
| Fine-tuning changes behavior substantially           | **Strong**                                           | Scores, length/refusal behavior and per-item outputs agree                      |
| Clinically meaningful errors persist                 | **Strong that they exist**                           | Concrete inspected outputs                                                      |
| Their prevalence is known                            | **Unsupported**                                      | No systematic clinician adjudication of all outputs                             |
| Safety instrument has false negatives                | **Moderate–Strong**                                 | Multiple clear diagnostic examples                                              |
| Safety instrument sensitivity/specificity is known   | **Unsupported**                                      | No representative expert gold standard                                          |
| RAG improves B                                       | **Moderate positive signal, inconclusive inference** | Positive estimates, broad intervals                                             |
| RAG has no effect                                    | **Unsupported**                                      | No equivalence test                                                             |
| C and B are equivalent                               | **Unsupported**                                      | No equivalence margin/design                                                    |
| Judge noise affects small ablations                  | **Strong within this dataset**                       | Exact repeated-answer comparisons                                               |
| Fine-tuning beats base+RAG in this experiment        | **Strong but exploratory**                           | Large consistent differences                                                    |
| The system runs effectively offline on phones        | **Unsupported**                                      | No device experiment                                                            |

The strongest generalization boundary is:

> **one selected Gemma-2B-IT adapter, one reused 41-question development/evaluation bank, one generation setup, and three specified primary judge routes.**

That is the population the result actually speaks about.

---

# 12. Missing Evidence

There are two very different levels of missing evidence.

### Necessary for the narrow publishable paper

**1. Clinician-grounded reference audit.**
A qualified independent clinical panel should reconcile each of the 41 reference answers, critical steps and severity labels. The report itself documents CPR, spinal-injury and protocol-scope inconsistencies.

**2. Human adjudication of at least the headline A/B outputs.**
You need to know whether the large automated B−A improvement also appears under expert judgment. This would dramatically strengthen the paper.

**3. Correct all statistical language.**
Remove “NULL,” “equivalent,” “no effect” and similar interpretations where no equivalence analysis exists.

**4. Freeze the actual release.**
One authoritative generation directory, item set, prompt set, results table and analysis commit.

**5. Release reproducibility artifacts.**
Adapter weights, tokenizer/vocabulary assets, exact base revision, environment lock and commands. The report currently verifies score analysis but cannot reproduce generation.

### Necessary only for stronger generalization/clinical claims

A **completely new, independently authored and frozen test bank** is necessary before claiming unseen-scenario generalization.

It should be grouped/deduplicated by clinical scenario rather than row, because the current splits contain exact and near-duplicate material across train/validation/test.

Multiple independent training seeds are needed before claiming that the adaptation procedure reliably produces the effect rather than this selected checkpoint producing it.

Real device benchmarks are necessary before making an on-device/offline-system claim.

Clinical/user outcomes are necessary before making clinical-utility claims.

### Nice to have

- larger external benchmark;
- model-family replication;
- matched-length control;
- multiple judge reruns;
- calibration curves;
- category-balanced external evaluation;
- error-severity weighting;
- cost/latency/energy measurements;
- comparison against a stronger modern small medical model;
- comparison with retrieval-only and fine-tuning+retrieval systems under a fully isolated retrieval protocol.

---

# 13. Publishability Assessment

| Dimension                    |   Score /10 | Assessment                                                               |
| ---------------------------- | ----------: | ------------------------------------------------------------------------ |
| Research question            | **8** | Strong once narrowed                                                     |
| Novelty                      | **6** | Weak model novelty; better evaluation/safety novelty                     |
| Empirical contribution       | **7** | Large real adaptation effect                                             |
| Methodological rigor         | **6** | Good paired design, but adaptive bank reuse hurts                        |
| Evaluation quality           | **5** | Multi-judge and controls help; clinical validity is weak                 |
| Statistical rigor            | **7** | Pairing/bootstrap/sign tests solid; null/multiplicity issues correctable |
| Practical significance       | **7** | Small-model safety-critical QA is important                              |
| Reproducibility              | **5** | Excellent analysis provenance; incomplete model replay                   |
| Medical/scientific relevance | **7** | Highly relevant question, weak clinical validation                       |
| Presentation potential       | **8** | Strong positive + negative + methodological story                        |

### Overall Publishability: **66/100**

This means:

**credible paper with careful framing—not a mature clinical validation paper.**

The report's existing **54/100 camera-ready judgment** is reasonable for the project in its current presentation because it penalizes the known-bank design, evaluator validity, missing model artifacts and safety claims.

### Can this be published without new experiments?

**Yes, but with narrower claims.**

More precisely:

- **Without new model training:** yes.
- **Without a new prospective benchmark:** yes, for an explicitly exploratory/in-domain case study.
- **Without any additional human clinical validation:** possible for an ML/NLP workshop-style paper, but materially weaker.
- **For a serious medical-AI publication:** I would add at least independent clinical adjudication.
- **For a generalization/clinical-safety claim:** no; new evaluation is necessary.

---

# 14. Recommended Paper Structure

## Title

Recommended working title:

**Large In-Domain Gains, Persistent Safety Failures: Auditing Parameter-Efficient Adaptation of a 2B First-Aid Language Model**

## Abstract

The abstract should establish five things.

**Problem:** Small local language models are attractive for low-connectivity health-information access, but average QA improvements may not reflect safety-critical correctness.

**Method:** Adapt Gemma-2B-IT with LoRA using first-aid QA and evaluate it against the base model plus lightweight inference interventions on the 41-question development/evaluation bank using three specified judge routes, paired statistics and safety/error analysis.

**Main finding:** Fine-tuning improves automated scores by +0.90 to +1.34/5 across the three primary judges.

**Secondary finding:** Absolute quality remains low; clinically consequential errors persist; minor interventions are unresolved; repeated identical answers expose non-trivial judge variability.

**Limitation:** The bank was development-informed, evaluation is largely automated, only one selected checkpoint is tested and the result does not demonstrate clinical safety or prospective generalization.

## Introduction

Structure the introduction around:

1. constrained/offline medical-information generation;
2. attractiveness of small models and PEFT;
3. inadequacy of simply asking whether the score increases;
4. safety-critical procedural QA requires correct conditions, sequencing and omissions;
5. research question;
6. contributions.

The gap should not be “nobody has fine-tuned small medical LMs.” Existing literature disproves that.

The gap should instead be:

> **How should a large in-domain improvement in a small medical LM be interpreted when clinical-critical failure modes and evaluator reliability are examined simultaneously?**

## Related Work

Use four literature families:

**Small/parameter-efficient medical LMs.**
PEFT, QLoRA, medical QA and resource-constrained models.

**First-aid and emergency-response QA.**
Include FirstAidQA and contemporary first-aid LLM evaluations.

**LLM evaluation in healthcare.**
LLM-as-judge, expert alignment and reliability.

**Safety-critical generative evaluation.**
Critical omissions, harmful procedural sequences and rubric design.

## Method

### Data

Describe the 5.5k source corpus, split construction, duplicates and the 41-question bank separately.

Never call the 41 questions an untouched held-out test.

### Model

Give exact Gemma model, quantization, LoRA, optimization and checkpoint selection.

### Interventions

Define A–G explicitly.

### Evaluation

Separate:

**quality evaluation** from **safety evaluation**.

This distinction is important because the two are not mathematically integrated in the existing protocol.

### Statistical analysis

Primary unit = question.

Report:

- paired mean differences;
- percentile bootstrap CIs;
- exact sign tests;
- wins/losses/ties;
- `dz` as a secondary standardized measure;
- multiplicity sensitivity.

Make clear that three judges are repeated measurements of the same 41 questions, not 123 independent observations.

## Experimental Setup

Predeclare which analyses are:

**central/registered**, **secondary**, and **post hoc**.

Use a chronology table in the supplement to disclose the panel changes and prior bank exposure.

## Results

I would order them:

**Result 1 — Fine-tuning produces a large bank-specific improvement.**

**Result 2 — Absolute model quality remains limited.**

**Result 3 — Clinical-critical failures persist despite the gain.**

**Result 4 — LLM-judge repeatability is adequate for the large effect but problematic for small ablations.**

**Result 5 — Retrieval/retry/self-check comparisons remain unresolved.**

**Result 6 — Exploratory fine-tuned versus base+RAG comparison.**

That order is much stronger than presenting seven model configurations as if they all had equal scientific importance.

## Error / Safety Analysis

Organize errors by **mechanism**, not question ID:

- wrong intervention condition;
- missing critical action;
- wrong sequencing;
- inappropriate treatment extension;
- unsafe escalation;
- omission of rescuer safety;
- negation error;
- safety-gate withholding;
- reference-standard ambiguity.

The error analysis could become one of the most valuable parts of the paper.

## Discussion

The central interpretation should be:

> **Domain adaptation produces useful task alignment, but average quality improvement and safety-critical reliability remain distinct questions.**

Discuss why retrieval/self-check additions might not improve outcomes, but do not speculate beyond the evidence.

## Limitations

Explicitly list:

- reused development/evaluation bank;
- evaluation-informed development;
- single checkpoint;
- no training-seed replication;
- automated primary evaluation;
- no prospective external set;
- imperfect safety taxonomy;
- imperfect references;
- no patient/clinician user study;
- no on-device evaluation;
- incomplete computational reproducibility.

## Conclusion

The narrowest strong conclusion is:

> **Parameter-efficient adaptation substantially improved automated first-aid answer-quality ratings for this 2B model on the studied bank, but clinically consequential errors and evaluation limitations prevent interpreting that improvement as evidence of medical safety or deployment readiness.**

### Reporting framework

For this study, **TRIPOD-LLM is much more appropriate than TRIPOD+AI as the principal LLM-specific transparency checklist**. TRIPOD-LLM is explicitly designed for development/evaluation of healthcare LLMs and emphasizes transparency, human oversight and task-specific performance reporting.

TRIPOD+AI can inform general transparency practices, but its developers explicitly note that it was primarily designed for prediction models and that foundation/generative LLMs were not its original focus.

Because the intended system provides health advice in natural language, **CHART** is also worth consulting as a secondary reporting framework; current guidance specifically identifies health-advice chatbot evaluations, including fine-tuned systems, as within CHART's scope.

Do not present checklist adherence as evidence that the study itself is clinically valid.

---

# 15. Figures & Tables Plan

You do not need many figures. You need the *right* figures.

### Figure 1 — Experimental design

Show:

**corpus → LoRA training → selected checkpoint → A–G arms → 41-question bank → quality/safety evaluation → paired statistics.**

Why it matters: makes the complex pipeline immediately understandable and prevents confusion about what C/D/E/F/G actually represent.

### Figure 2 — Main paired B−A effects

A forest-style figure showing each judge's:

**mean Δ + 95% CI + effect size**.

Possibly overlay question-level difference distribution.

Why it matters: this is the central empirical result.

### Figure 3 — Quality improvement versus residual critical errors

Show a small structured matrix:

**error category × representative examples × whether safety checker flagged it**.

Do not turn diagnostic examples into prevalence bars.

Why it matters: visually establishes the paper's central distinction between average quality and safety-critical correctness.

### Figure 4 — Judge variability versus ablation magnitude

Compare the magnitude of repeated-identical-answer score disagreement against the mean C−B, D−B, E−B and F−B effects.

Why it matters: makes the evaluation contribution immediately obvious.

### Critical tables

**Table 1 — Dataset, model and arm definitions.**

Include splits, benchmark size, model/LoRA settings and A–G intervention mapping.

**Table 2 — Primary result.**

A vs B by judge: mean, Δ, CI, `dz`, sign p, W/L/T.

**Table 3 — Secondary ablations.**

F−B, C−B, D−B, E−B, G−B and G−F with correct inferential language.

**Table 4 — Safety/error taxonomy.**

Error mechanism, representative example, quality score behavior, safety screen response, consequence.

**Table 5 — Reproducibility profile** should preferably go into supplementary material: what can/cannot be reproduced, artifact availability, versions, missing weights.

---

# 16. Title Candidates

### Safety-centered — strongest

**Large In-Domain Gains, Persistent Safety Failures: Auditing Parameter-Efficient Adaptation of a 2B First-Aid Language Model**

### Empirical

**Adapting a 2B Language Model for First Aid: Large Quality Gains with Persistent Critical Errors**

### Evaluation-centered

**When Better Scores Are Not Safer: Evaluating a Fine-Tuned Small Language Model for First-Aid Question Answering**

### LLM-judge/evaluation

**Beyond Mean Scores: Evaluator Reliability and Safety-Critical Errors in First-Aid Language Model Adaptation**

### Negative/boundary framing

**Fine-Tuning Helps, Safety Remains Unresolved: A Controlled Evaluation of a 2B First-Aid Language Model**

My preference is **#1 or #3**.

---

# 17. Contribution Statements

These are close to what I would put at the end of the introduction:

1. **We quantify the effect of parameter-efficient first-aid adaptation of a 2B instruction model, finding a 0.90–1.34-point improvement on a five-point reference-assisted rubric across three specified evaluator routes on the studied 41-question bank.**
2. **We show that this substantial quality improvement does not establish medical reliability: the adapted model retains clinically consequential errors involving intervention conditions, omissions and sequencing, several of which are not detected by the study's safety screen.**
3. **We characterize evaluator reliability using repeated identical candidate–prompt pairs and show that judge variability is small relative to the primary fine-tuning effect but comparable with several secondary ablation effects.**
4. **We evaluate lightweight retrieval, response-retry and self-assessment interventions and find no statistically established incremental quality benefit under the current protocol, while identifying intervention-specific failure modes.**
5. **We provide an artifact-level reconstruction distinguishing reproducibility of the saved statistical analysis from reproducibility of the underlying model execution, and delineate the scope in which the reported findings can be interpreted.**

I would put only the first **three** in the abstract.

---

# 18. Reviewer Objections

| Reviewer                           | Likely objection                                                          | Valid?               | Existing evidence                                     | Best response                                                                          |
| ---------------------------------- | ------------------------------------------------------------------------- | -------------------- | ----------------------------------------------------- | -------------------------------------------------------------------------------------- |
| **ML reviewer**              | “This is one checkpoint on a repeatedly used benchmark.”                | **Yes**        | Single canonical adapter; known bank                  | Call it a bank-specific exploratory study; do not claim training-method generalization |
| **ML reviewer**              | “B−A may partly be style/length rather than knowledge.”                | **Yes**        | Large output-length difference and token-cap behavior | Describe B−A as bundled behavioral effect; add matched-length sensitivity if possible |
| **NLP reviewer**             | “LoRA fine-tuning a small model isn't novel.”                           | **Yes**        | Prior PEFT/small medical LM work exists               | Move novelty to safety/evaluation findings                                             |
| **NLP reviewer**             | “Why should I trust LLM judges?”                                        | **Very valid** | Moderate agreement and repeated-prompt instability    | Add clinical human evaluation; present judge instability as a result                   |
| **Medical-AI reviewer**      | “Your gold standard isn't adequately clinically adjudicated.”           | **Yes**        | Reference inconsistencies are documented              | Expert item-level reconciliation is the most important pre-submission improvement      |
| **Medical-AI reviewer**      | “No patient outcomes or clinical utility.”                              | **Yes**        | None measured                                         | Explicitly state that the study evaluates information quality, not clinical outcomes   |
| **Safety reviewer**          | “The safety taxonomy misses critical omissions and conditional errors.” | **Yes**        | Multiple false-negative examples                      | Make this part of the contribution rather than defending the taxonomy                  |
| **Safety reviewer**          | “Your safety gate may itself cause harmful omission.”                   | **Yes**        | Fallback behavior documented                          | Treat E as an exploratory failed safeguard, not a validated safety mechanism           |
| **Reproducibility reviewer** | “I cannot regenerate the final answers.”                                | **Yes**        | Weights/tokenizer/revisions incomplete                | Release exact adapter, tokenizer, base revision, lockfile/container and commands       |
| **Statistics reviewer**      | “Non-significant does not mean equivalent.”                             | **Yes**        | C/F/D/E CIs retain plausible effects                  | Remove equivalence/null claims                                                         |
| **Statistics reviewer**      | “Three judges are not three independent replications.”                  | **Yes**        | Same 41 items and references                          | Treat judges as repeated evaluation instruments                                        |
| **Reviewer generally**       | “You adapted components after inspecting the benchmark.”                | **Yes**        | Git chronology confirms this                          | Explicitly call it a reused development/evaluation bank                                |

The report itself already identifies essentially these attacks, including benchmark adaptation, cross-split leakage, evaluator validity, panel changes and irreproducible weights.

---

# 19. Claims to Avoid

## Claims This Paper Should NOT Make

Do **not** claim:

- the model is clinically safe;
- the model provides reliable autonomous first aid;
- the system improves clinical outcomes;
- the system can replace professional care;
- the experiment proves clinical utility;
- the 41 questions constitute an untouched prospective test;
- results demonstrate broad first-aid generalization;
- safety-critical performance is proven;
- absence of a safety flag means an answer is safe;
- the safety gate is validated;
- RAG does not help;
- 4-bit and 8-bit inference are equivalent;
- length retry has no effect;
- three judges are three independent experiments;
- the final study was wholly preregistered;
- the evaluation was completely blinded;
- the entire model is fully reproducible;
- the system has been validated on Android/offline hardware;
- all first-aid categories have been individually validated;
- the SC versus non-SC labels reliably represent clinical severity;
- a 90% judge-stability result applies to the final DeepSeek evaluation;
- a relative “92% medical-quality improvement” follows from the ordinal score;
- statistically significant rubric improvement implies clinically significant improvement.

Also avoid:

> “The fine-tuned model is medically accurate.”

A safer version is:

> **The fine-tuned model received substantially higher reference-assisted answer-quality ratings on the studied development/evaluation bank.**

The report itself rejects clean unseen-test, clinical-safety, quantization-equivalence and fully reproducible-system claims.

---

# 20. Final Paper Story

> **If I were submitting this paper tomorrow, this is the exact story I would tell.**

**Problem →** Small language models are attractive for health-information access where cloud connectivity or compute is constrained, but first-aid is unusually unforgiving: a response may be broadly relevant while still omitting or mis-sequencing one lifesaving action.

**Gap →** Existing work already shows that small medical language models can benefit from parameter-efficient fine-tuning. The harder unanswered question for this project is whether a large improvement in conventional answer-quality evaluation corresponds to sufficiently reliable behavior in a safety-critical procedural domain.

**Research Question →** How much does parameter-efficient first-aid adaptation change the quality of a 2B instruction model relative to its base model under a fixed evaluation setting, and what important limitations remain after that gain?

**Method →** Fine-tune Gemma-2B-IT with a LoRA adapter on first-aid QA, compare the base, tuned and several retrieval/retry/self-check configurations on 41 first-aid scenarios, and evaluate responses using three primary reference-assisted judge routes, paired statistics, repeated-prompt reliability analysis and targeted safety/error inspection.

**Finding 1 →** Fine-tuning produces a large and consistent bank-specific improvement: **+0.90 to +1.34 points on the five-point rubric**, with confidence intervals excluding zero across all three primary judge routes.

**Finding 2 →** That gain does **not** amount to medical adequacy. Absolute scores remain modest, and the adapted model still makes consequential errors involving CPR conditions, choking, bleeding, burns and critical sequencing. Some receive surprisingly favorable quality scores or evade the dedicated safety screen.

**Evaluation finding →** Identical candidate answers sometimes receive different ratings when rejudged, showing that evaluator instability is minor relative to the large fine-tuning effect but non-negligible relative to the much smaller quantization, retry, self-check and retrieval effects.

**Negative finding →** None of the tested lightweight post-training additions establishes a clear incremental overall quality advantage over the fine-tuned model; retrieval has positive but uncertain estimates, while retry and safety gating expose additional failure modes.

**Implication →** In safety-critical medical QA, **a large benchmark-quality gain should be treated as evidence of improved task alignment—not evidence of clinical safety**. Evaluation must explicitly test critical omissions, conditions and action sequencing and must quantify evaluator reliability when interpreting small model differences.

**Limitation →** The current study uses a repeatedly inspected development/evaluation bank, one selected checkpoint, imperfect reference and safety instruments, predominantly automated evaluation and no prospective clinical or on-device study. Therefore its results establish a **bounded adaptation and evaluation finding**, not a clinically validated first-aid system.

That is the paper I would build. The project becomes significantly stronger once you stop trying to prove that the system is “safe enough” and instead make the scientifically defensible result the point: **fine-tuning clearly improves the model, but the evaluation shows exactly why improvement must not be confused with safety.**
