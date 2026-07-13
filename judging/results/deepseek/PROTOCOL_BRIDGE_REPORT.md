# Test 4 — Protocol Bridge Report
Compares per-item DeepSeek scores (new protocol) to June mega-prompt scores.
Run tag: TEST4_BRIDGE

## Per-Config Means
| Config | New (per-item) | June (mega-prompt) | Delta |
|---|---|---|---|
| A_BASE_4BIT | 1.512 | 1.854 | -0.342 |
| B_FINETUNED_4BIT | 2.415 | 2.780 | -0.366 |
| E_T6_IMPROVED | 2.439 | 2.707 | -0.268 |
| F_RAG_BM25 | 2.390 | 3.220 | -0.829 |

## Spearman ρ per Config (question-level)

| Config | ρ | p |
|---|---|---|
| (requires per-question June scores — see note below) | — | — |

## Interpretation

The per-item protocol uses temperature=0, json_object mode, and the rubric v2 Section 6 text embedded verbatim. The June mega-prompt presented all configs simultaneously to the judge, which may inflate relative differences between configs. Absolute score shifts up to ±0.5 are expected from the format change. Shifts >1.0 on any config should be reported to the human before aggregation.
