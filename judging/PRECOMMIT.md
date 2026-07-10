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
- blind_map.json is excluded from released artifacts until after de-anonymization.
- This file is committed before any aggregate output exists in git history.
