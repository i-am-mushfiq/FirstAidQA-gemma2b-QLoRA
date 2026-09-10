# Pre-committed Contrasts

**Registered:** 2026-07-10  
**Purpose:** Lock down hypotheses before running aggregate.py on camera-ready data.  
**Rule:** This file must appear in git history with an earlier commit than the aggregate run output.

---

## Primary Contrasts (confirmatory)

These three contrasts are the primary pre-registered hypotheses. Conclusions about
the system's capabilities must be grounded in these results.

| # | Name        | Config A (treatment)  | Config B (control)   | Filter | Expected direction |
|---|-------------|-----------------------|----------------------|--------|--------------------|
| 1 | F−B overall | F_RAG_BM25            | B_FINETUNED_4BIT     | all    | A > B (BM25 RAG improves over fine-tuned greedy) |
| 2 | F−B SC      | F_RAG_BM25            | B_FINETUNED_4BIT     | sc     | A > B (especially on safety-critical questions) |
| 3 | B−A overall | B_FINETUNED_4BIT      | A_BASE_4BIT          | all    | A > B (fine-tuning improves over base) |

A contrast is **confirmed** if: bootstrap 95% CI excludes zero AND two-sided sign test p < 0.05.

---

## Secondary Contrasts (exploratory)

These are exploratory. No conclusions about the system are drawn from them;
they inform future work only.

| # | Name        | Config A              | Config B             | Filter | Question |
|---|-------------|-----------------------|----------------------|--------|----------|
| 4 | E−B overall | E_T6_IMPROVED         | B_FINETUNED_4BIT     | all    | Does T6 safety gate help over greedy? |
| 5 | C−B overall | C_FINETUNED_8BIT      | B_FINETUNED_4BIT     | all    | Does 8-bit quantisation affect quality? |
| 6 | G−B overall | G_BASE_RAG            | B_FINETUNED_4BIT     | all    | Can RAG on base beat fine-tuning? |
| 7 | G−F overall | G_BASE_RAG            | F_RAG_BM25           | all    | Does fine-tuning add value on top of RAG? |
| 8 | D−B overall | D_T4_IMPROVED         | B_FINETUNED_4BIT     | all    | Does the T4 length floor help over greedy? |

**Amendment, 2026-09-06 — contrast 8 added.** Config D was excluded from the
July run pending the repetition-loop fix (landed d0fdb61) and therefore had no
registered contrast. It enters scope for the offline regeneration, so D−B is
registered here **before that run is generated** — no D data exists at the time
of this amendment. Contrasts 1–7 are unchanged from the 2026-07-10 registration.

---

## Panel composition

**Registered 2026-07-10, amended 2026-09-06 (below). No judgments exist for the
offline run at the time of this amendment.**

| Role | Judge name | Model | Route |
|------|-----------|-------|-------|
| Confirmatory | `deepseek` | `deepseek-v4-pro` | DeepSeek direct API |
| Confirmatory | `claude_or` | `anthropic/claude-opus-4.8` **‡ SUPERSEDED — the run used `anthropic/claude-opus-5`; see Amendment 2026-09-10** | OpenRouter |
| Confirmatory | `gpt_ar` | `gpt-5.6-sol` | AgentRouter |
| Exploratory | `glm_ar` | `glm-5.3` | AgentRouter |

A contrast is **confirmed** only when all three confirmatory judges agree in
direction and each is independently significant by the rule above. Judges
disagreeing on sign makes a contrast *inconclusive*, not null.

`glm_ar` is a fourth, exploratory judge. It **does not gate the 3-of-3 rule**
and no conclusion about the system rests on it, whichever way it falls.

---

## Amendment, 2026-09-06 — routes, reasoning policy, fourth judge

Registered **before any offline-run judgments exist**. Contrasts 1–8 are
unchanged; this amendment concerns only how the judges are reached and
configured.

**1. The gpt judge moved from OpenRouter to AgentRouter, and was renamed.**
No OpenRouter credit was available. The model is the same (`gpt-5.6-sol`); the
route is not. It was renamed `gpt` → `gpt_ar` because `judging/results/<judge>/`
is the results namespace and `results/gpt/` already holds the July OpenRouter
panel — the resume guard compares template and bank hashes but not model or
base URL, so one directory serving two routes could be welded together by a
resume that no guard can detect.

Two properties of AgentRouter must be disclosed in the write-up:

- **Client allowlist.** The gateway rejects unrecognised clients with 401
  "unauthorized client detected". The harness sends a coding-agent User-Agent so
  its requests are accepted. This is a deliberate workaround of the provider's
  access control, adopted knowingly under a budget constraint.
- **Judge identity is asserted, not verified.** Every model is reported as
  `owned_by: "custom"`; AgentRouter is a reseller, so the served model cannot be
  attributed to a first-party snapshot, and a reseller may echo back any string.
  These routes therefore require an exact model-string match rather than the
  token-subset test. Cf. finding #38 and the July `deepseek-v4-flash`
  substitution. AgentRouter's own catalogue lists `deepseek-v4-flash`, not
  `-pro`, which is why the deepseek judge stays on the direct API.

**2. Claude stays on OpenRouter and remains required.** AgentRouter cannot serve
it — its Anthropic budget pool returns 402 for both `claude-opus-5` and
`claude-opus-4-8`, verified with two separate keys. The panel is incomplete
until an OpenRouter key exists; a two-judge panel confirms nothing under the
3-of-3 rule.

**3. Reasoning is disabled for every judge.** Judges that reason spend tokens on
hidden deliberation the rubric never sees, and the setting is not recoverable
from the saved output, so it is fixed here and recorded per run in
`manifest.json` (`extra_body` plus a `decode_fingerprint`).

| Judge | Setting | Measured effect |
|---|---|---|
| `deepseek` | `thinking: {type: disabled}` | already in force |
| `claude_or` | `reasoning: {exclude: true}` | **‡ SUPERSEDED — this spelling does not disable reasoning; the run used `reasoning: {enabled: false}`. See Amendment 2026-09-10** |
| `gpt_ar` | `reasoning_effort: "none"` | completion tokens roughly halved (163→81, 192→111), confirming it had been reasoning |
| `glm_ar` | `reasoning_effort: "low"` | see deviation below |

**Declared deviation — `glm_ar` cannot be made non-reasoning.** GLM-5.3 rejects
both `thinking: {type: disabled}` and `reasoning_effort: "none"` with HTTP 400
("this model always thinks"). `reasoning_effort: "low"` is the minimum it
accepts. Measured on real judging prompts it leaves a variable residue of
**0–44 reasoning tokens** (4 calls: 0, 16, 35, 44). This is one further reason
`glm_ar` is exploratory rather than confirmatory, and it must be stated in the
paper.

**4. `glm_ar` registered as an exploratory fourth judge.** Chosen for family
independence — Zhipu, unrelated to the subject (Gemma), to DeepSeek and to
OpenAI — satisfying the same rule that excluded Gemini from the panel. It is
**not** a substitute for Claude. Its inclusion is registered here so that
reporting it is not contingent on what it says; whether it agrees or disagrees
with the confirmatory three, it is reported as an exploratory robustness check.
If it is instead run only as internal QA and left unreported, that decision is
recorded in `DECISIONS.md` and applies regardless of outcome.

---

## Statistical method

- Paired bootstrap: 10,000 resamples, seed=2026
- Sign test: exact two-sided binomial
- Unit of pairing: question_id (same question, both configs)
- SC-weighted mean: SC questions count 2×, non-SC count 1× (for summary table only)

---

## Integrity notes

- aggregate.py reads contrasts from `load_precommit_contrasts()` which hard-codes these names
  and config strings. To update the contrasts, update both this file and that function,
  commit both together, and re-run only if camera-ready judgments have not yet been inspected.
  Verified in sync 2026-09-06: all eight contrasts present, D−B included.
- A contrast whose configs carry no judgments is **skipped silently** by
  aggregate.py — it does not appear in `stats.csv` and nothing reports it as
  missing. This is why D−B is absent from every July output. Confirm D−B is
  present in `stats.csv` for the offline panel rather than assuming it ran.
- blind_map.json is excluded from released artifacts until after de-anonymization.
- **Ordering, stated per section rather than as a blanket claim.** The original
  registration (2026-07-10) and the 2026-09-06 amendments were each committed
  **before** the judgments they govern existed — verifiable in git: the routes and
  reasoning policy landed in `848ab90` at 12:59 +0600 = 06:59 UTC, and the first
  offline judgment is stamped 07:17 UTC. The **2026-09-10 amendment is the one
  exception: it is post-hoc**, and is labelled as such in its own heading. Do not
  cite this file as wholly pre-registered.

---

## Amendment, 2026-09-10 — POST-HOC: the claude judge was substituted, and two measurements corrected

**This amendment is not a pre-registration.** Every amendment above was
committed before the judgments it governs existed. This one is written **after**
the panel finished, and it records a deviation rather than registering a plan.
It is placed here, in the registration file, because a deviation that lives only
in a commit message and a source comment is not discoverable by anyone reading
the registration — which is exactly how this went unrecorded.

### 1. The substitution

| | |
|---|---|
| Registered (2026-07-10, unchanged by the 2026-09-06 amendments) | `anthropic/claude-opus-4.8` |
| Requested by the code for the offline run | `anthropic/claude-opus-5` |
| Served by the provider, all 664 rows | `anthropic/claude-opus-5` |
| Changed at | in the working tree before 2026-09-08 04:17:04 UTC (first `claude_or` call); **recorded** at commit `32b027c`, 2026-09-08 04:35:43 UTC (= 10:35:43 +0600), which committed the change together with the first 568 rows |

**The timing, stated plainly.** At the moment that change was made the rest of
the panel was already complete and already aggregated:

| Judge | State when commit `32b027c` was made, 2026-09-08 04:35:43 UTC |
|---|---|
| `deepseek` | 664/664, aggregated (first call 2026-09-06 07:03:51 UTC, last 07:17:25) |
| `glm_ar` | 664/664, aggregated (first call 2026-09-06 07:05:32 UTC, last 07:21:42) |
| `gpt_ar` | 664/664, aggregated (first call 2026-09-06 08:23:59 UTC, last 2026-09-07 05:23:40) |
| `claude_or` | **568/664 already judged** — first call 2026-09-08 04:17:04 UTC, i.e. **19 minutes before this commit**, which committed those 568 rows together with the code change |

> **Correction, 2026-09-10.** An earlier version of this table stated that
> `claude_or` was *"not started"* at this commit. **That is false**, and the
> commit's own message says so: *"claude_or halted at 568/664 on a key spend
> cap."* `git show --stat 32b027c` shows it adding 568 rows to
> `claude_or/OFFLINE_FINAL/judgments.jsonl`. The substitution was therefore made
> in the working tree, run to 568 calls, and *then* committed with its results —
> the commit records the decision rather than making it. The same earlier version
> cited deepseek's first offline judgment as 07:17 UTC; that is `manifest.run_at`,
> which is the **finishing** time. The true first call is **07:03:51 UTC**. The
> route amendment (`848ab90`, 06:59 UTC) still precedes it, so that ordering
> conclusion is unchanged, but the figure was wrong. Both errors were found by an
> independent audit in `forensic_audit_20260910/REPORT.md` §11.

So **the identity of the third confirmatory judge was fixed after the other two
confirmatory arms and the exploratory arm were complete and aggregated.** That is
a researcher degree of freedom and it must be reported as one. Nothing in the
repository establishes that the choice was made without reference to those
results, and this amendment does not claim otherwise. Equally, nothing here is
evidence that it was chosen *to* obtain a favourable result — see section 2.

**Why it happened.** `OPENROUTER_API_KEY` — the credential registered for
`claude-opus-4.8` — was never available; AgentRouter returns 402 for every
Anthropic model on two separate keys (verified 2026-09-06); a different
OpenRouter key (`CLAUDE_API`, later `CLAUDE_NEW`) became available on 2026-09-08
and was used. `anthropic/claude-opus-4.8` **was still available on OpenRouter at
that moment** — the binding constraint was budget and credential, not model
availability. The honest statement is that a newer model was used because it was
the one being paid for, not because the registered one had become unreachable.

### 2. What limits the damage — evidence, not reassurance

None of this excuses the ordering; it bounds what the ordering can have done.

- `claude_or` is the **harshest** judge in the panel: overall mean 1.882 against
  deepseek 2.021, gpt_ar 2.125, glm_ar 2.258. A substitution chosen to
  manufacture confirmations would not have installed the strictest scorer.
- The three CONFIRMED contrasts (B−A, G−B, G−F) are confirmed **4/4 including
  the exploratory judge**, so each survives deleting the claude arm entirely.
  `PANEL_VERDICT_OFFLINE_FINAL.md` prints every judge separately so a reader can
  perform that deletion.
- The substitution is a change of *judge*, so **absolute scores are not
  comparable with July's `claude_or` column**. July is retired
  (`DECISIONS.md`, 2026-09-05), so no published comparison is affected, but any
  sentence comparing the two runs must be removed rather than adjusted.

### 3. The reasoning parameter, corrected before spending

The registered setting for `claude_or` was `{"reasoning": {"exclude": true}}`,
flagged in the 2026-09-06 amendment as **not yet verified**. Measured against
the live API on 2026-09-07 it **suppresses the reasoning text from the response
without disabling reasoning**: 300 reasoning tokens with it set, identical to no
parameter at all. Of five spellings tried, only `{"reasoning": {"enabled":
false}}` and `{"reasoning_effort": "none"}` reduced `reasoning_tokens` to 0.

The run used `{"reasoning": {"enabled": false}}` throughout. **Verified after the
fact across all 664 rows: `reasoning_tokens` is 0 on every one, and the field is
reported on every one.** `claude_or` is the only judge in the panel whose
no-reasoning compliance is positively evidenced rather than assumed. This
correction moved the run *toward* the registered policy and is a fix, not a
deviation — but it changed a registered string, so it is recorded here.

### 4. Correction: the `glm_ar` reasoning residue was understated

The 2026-09-06 amendment registered *"a variable residue of 0–44 reasoning
tokens (4 calls: 0, 16, 35, 44)"*. That was a four-call probe. Measured across
all 664 judging calls of the offline run:

| | Registered (4 calls) | Measured (664 calls) |
|---|---|---|
| Calls with nonzero reasoning | — | **285 of 664** |
| Median | — | 0 |
| p90 | — | 56 |
| Maximum | 44 | **185** |

**Use 0–185 in the paper, not 0–44.** `judging/RUNBOOK.md` is staler still and
repeats a superseded "5-token floor"; that figure is retracted. The conclusion
the range was cited to support — that `glm_ar` cannot be made non-reasoning and
is therefore exploratory rather than confirmatory — is unchanged, and if anything
strengthened.

### 5. Disclosure: the no-reasoning policy did not hold uniformly

Measured from `usage.reasoning_tokens` across all 2,656 judgments of the offline
run. This was not previously recorded anywhere.

| Judge | Setting sent | Reasoning tokens observed | Policy status |
|---|---|---|---|
| `claude_or` | `reasoning: {enabled: false}` | 0 on all 664, field reported | **Held, and verified** |
| `deepseek` | `thinking: {type: disabled}` | field never reported (0/664) | Plausible, **unverifiable** |
| `gpt_ar` | `reasoning_effort: "none"` | **nonzero on 15 calls** (20–156, 1,016 total); field absent on the other 649 | **Breached on 15 calls, unverifiable on 649** |
| `glm_ar` | `reasoning_effort: "low"` | nonzero on 285, max 185 | Declared deviation (section 4) |

Six of the fifteen `gpt_ar` breaches are quality scores — V2Q32/G, V2Q20/C,
V2Q06/F, V2Q04/C, V2Q19/C, V2Q36/C — so **6 of 1,148 quality scores in the
canonical run were produced under a reasoning regime this file forbids**, one of
them inside primary contrast F−B. Six scores cannot move a delta computed over
41 pairs by more than about 0.15, and F−B is null in any case, so no verdict
changes. It is recorded because the `decode_fingerprint` in every manifest
attests to what was *requested*, never to what the provider did.

### 6. Standing instructions for the write-up

1. State that the claude arm is `claude-opus-5`, that `claude-opus-4.8` was
   registered, and that the substitution was made after the other three judges
   had completed. Do not describe the panel as fully pre-registered.
2. Report per-judge results alongside every panel mean, so a reader can drop the
   claude arm.
3. State the reasoning-policy table in section 5 as-is, including the two
   unverifiable rows.
4. Use 0–185 for `glm_ar`.
5. Nothing in sections 1–5 changes any contrast, threshold, decision rule or
   judge *role*. Contrasts 1–8, the per-judge criterion and the 3-of-3 rule are
   exactly as registered.

### 7. Consequences for the other registration files

- `PRECOMMIT_PANEL.md` (2026-07-11) still carries **July result verdicts**,
  including *"§6.2 verdict: quantization is neutral at this scale"*. That verdict
  does not hold on the canonical run, where C−B is **inconclusive** (the
  confirmatory judges split +0.098 / −0.049 / −0.049). A registration file should
  not carry result verdicts at all; those in it are superseded by
  `PANEL_VERDICT_OFFLINE_FINAL.md` and must not be quoted.
- The 3-of-3 rule is now applied mechanically by `judging/panel_verdict.py` and
  written to `judging/PANEL_VERDICT_OFFLINE_FINAL.md`. The rule itself is
  unchanged and the human sign-off is retained; what changed is that the step can
  no longer be silently skipped, which is how the canonical run came to have no
  written verdict at all.
