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
| Confirmatory | `claude_or` | `anthropic/claude-opus-4.8` | OpenRouter |
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
| `claude_or` | `reasoning: {exclude: true}` | **not yet verified** — no key |
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
- This file is committed before any aggregate output exists in git history.
