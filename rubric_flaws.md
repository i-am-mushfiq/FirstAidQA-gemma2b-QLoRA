Generation is on pass 2 (A and G done). Here's the rubric assessment — measured, not asserted.

Verdict
The rubric is a sound instrument. What it exposes is a problem in your training data, not a defect in the rubric. Its one real structural flaw is that band 5 is unreachable by construction — and that is worth knowing precisely, because it changes what your headline number means.

Where it demonstrably works
It discriminates. Config means over 738 scores: A 1.49, G 1.25, B 2.24, C 2.32, E 2.24, F 2.20. B−A = +0.75, confirmed 3/3 judges with CI and sign test. A rubric that separates base from fine-tuned that cleanly is doing its job.

It's reliable. Three independent judges on 246 answers:

Pair	r	Exact	Within 1
deepseek × claude	0.762	54%	98%
deepseek × gpt	0.700	52%	91%
claude × gpt	0.735	60%	93%
Unanimous on 36%, spread ≥2 on only 12%. For a 0–5 clinical scale judged by LLMs, that's respectable.

It obeys its own anti-verbosity instruction. Overall r(words, score) = −0.339, but that's a config artifact — base models ramble at 146 words and score badly. Within configs: B +0.099, C +0.070, E +0.154, F +0.148, G +0.019. Essentially no length reward. The one exception, A at −0.502, is the base model degenerating as it runs long — a real quality signal, not bias.

It isn't a disguised similarity metric. r(ROUGE-L to reference, judge score) = +0.34 overall — about 12% shared variance. Judges are scoring clinical content, not word overlap, exactly as the rubric instructs.

Band 2 isn't a refuge. Of 281 score-2 rows, only 59 mention EMS and 7 are T6 fallbacks. 222 are substantive "incomplete or generic" judgements, not cap-driven defaults.

The structural flaw: band 5 is dead
Score	Count (738)	Share
0	82	11.1%
1	149	20.2%
2	281	38.1%
3	173	23.4%
4	51	6.9%
5	2	0.3%
Two fives in 738 scores. Meanwhile the planted verbatim references scored 5 in 39 of 39. So band 5 is not broken — it is precisely calibrated to the reference answers, and nothing your model produces gets near it. In practice you are running a 0–4 scale.

Here's why, concretely. Score 5 requires named specifics. I checked each against the 4,441 training answers:

Rubric anchor for score 5	In the 41 references	In 4,441 training answers
tourniquet 5–7 cm above wound	1	0
5 rescue breaths for drowning	1	0
30:2 CPR ratio	1	6 (0.14%)
PIB from toes upward	1	3 (0.07%)
back blows before thrusts	1	11 (0.25%)
vinegar for box jellyfish	1	23 (0.52%)
burn cooling 20 minutes	3	70 (1.58%)
Two of the seven appear zero times in the entire training corpus. Fine-tuning could not have taught them. The rubric demands them for a 5.

Rubric versus your dataset
Words
Training answers	mean 43.8, median 43, p90 55
Reference answers	mean 101.5, median 102, min 82
All 41 references exceed the 90th percentile of the training data. The shortest reference is longer than 90% of everything the model was trained on. The gold standard sits entirely outside the model's learned output distribution.

And fine-tuning worked exactly as intended: B/C/E/F produce ~42 words, tracking the training median of 43. The model faithfully learned a corpus whose answers are less than half the length and specificity of the standard it's graded against.

That is the real finding of this project. Not "our system scores 2.4/5" but: fine-tuning on this corpus reliably improves the model (+0.75, 3/3 confirmed) while the corpus itself cannot reach the clinical standard the references encode. That's publishable, and it's more honest than the absolute score.

Specific weaknesses, with sizes
No safety-critical rule. SC questions score 1.65 vs 2.07 non-SC — a −0.42 gap that judges apply on their own initiative. The flag is shown to them with a gloss but no scoring instruction. Uniform across configs, so it doesn't bias contrasts; it does add unmodelled variance.
gpt runs systematically low by 0.32–0.39 versus the other two. Harmless for paired contrasts, but do not report a raw cross-judge mean without that spread visible.
Category claims are not supportable. Means range 1.11 (Spinal) to 2.44 (Trauma) — but Spinal is one question, Poisoning is two, Envenomation and Burns are three each. Only Bleeding (7), Cardiac (6) and Minor Injuries (6) have enough to say anything.
The safety lane's 36% three-way agreement (established earlier) means "12-category violation detection" is a noisy instrument and shouldn't be described as if it were precise.
What I would not change
You asked me not to redesign it, and I wouldn't. Widening band 5 to make the model reachable would be exactly the sin your own OPEN_FINDINGS refused — moving a standard after seeing which way it moves scores. The 5 band being reserved for reference-quality answers is the correct behaviour for a clinical rubric. Report the ceiling as a finding, don't lower it.

The one change worth considering is adding an explicit SC rule, because judges are already applying one you never wrote down. But that's a change of standard too, so it belongs in the next pre-registration, not this run.

I'll report back when generation finishes and the verifier clears it. Want this written up as a dated companion doc alongside FINDINGS_20260905.md?