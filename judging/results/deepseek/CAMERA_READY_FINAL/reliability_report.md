# Reliability Report

## Test 3 — Intra-Judge Stability


- Items: 20 real items (first 20 from items.jsonl)
- Runs: TEST3_STABILITY_run1 (nonce=stability_run1) and TEST3_STABILITY_run2 (nonce=stability_run2)
- Prompt type: quality only

| Metric | Value |
|---|---|
| Pairs compared | 20 |
| Exact agreement | 18/20 = 90.0% |
| Within +-1 | 19/20 = 95.0% |

Acceptable stability.

## Length–Score Correlation (Bias Check)

- Spearman rho (candidate answer length vs score): -0.163  p=0.0104
  *(**Significant** at alpha=.05 (p=0.0104): a length-score association is present in this run and must be reported, not dismissed.)*

