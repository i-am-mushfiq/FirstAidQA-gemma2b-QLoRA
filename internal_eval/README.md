# Internal evaluation lane

Multi-judge scoring used **for internal decisions before a camera-ready run**:
which adapter to promote, whether a technique is worth a full run, where the
training gaps are. Nothing here is paper-facing.

Published camera-ready results come from [`judging/`](../judging/) instead. See
[Which lane to use](#which-lane-to-use) below.

## Files

| File | Role |
|---|---|
| `judge_per_item.py` | Per-item blinded scoring across up to six judge APIs; resumable via a per-item cache |
| `stats_v2.py` | Panel means, bootstrap CIs, sign tests, judge agreement, LaTeX tables |

Both are invoked through the camera-ready façade, which resolves their paths
from `camera_ready/protocol.yaml`:

```powershell
python camera_ready/pipeline.py judge   --run evaluations/<run>
python camera_ready/pipeline.py analyze --run evaluations/<run>
```

Or directly, from the repository root:

```powershell
python internal_eval/judge_per_item.py --run_dir evaluations/<run> --judges deepseek
python internal_eval/stats_v2.py       --run_dir evaluations/<run>
```

Both write to the sibling `<run>_ANALYSIS/` directory, leaving the generation
run immutable. Both read questions, references and safety-critical labels from
the frozen snapshot inside `run.json`, never from a live bank.

## Which lane to use

| | `internal_eval/` (here) | `judging/` |
|---|---|---|
| Purpose | internal go/no-go decisions | published camera-ready results |
| Judges | up to 6, each on its own vendor API | 3 pinned in `judging/PRECOMMIT_PANEL.md` |
| Control items | none | 45 planted controls with known-correct verdicts |
| Safety scoring | same call as quality | a separate second call per item |
| Precommit | `paper/PRECOMMIT_STATS_v2.md` | `judging/PRECOMMIT.md` + `PRECOMMIT_PANEL.md` |

Do not mix outputs between the lanes in a single table or claim. They use
different judges, different prompts and different confirmation rules.

## Analysing a partial panel

`--judges` selects the panel. It must be complete for every judge named — a
half-scored judge is still refused, so a subset analysis is never quietly built
on missing data:

```powershell
python internal_eval/stats_v2.py --run_dir evaluations/<run> --judges deepseek claude gpt4o
python camera_ready/pipeline.py analyze --run evaluations/<run> --judges deepseek claude
```

The default is all six, matching `paper/PRECOMMIT_STATS_v2.md`. Any smaller
panel prints a notice naming the deviation.

## Reading the output

- `stats_v2_pairwise.csv` carries `direction_match` and `supports_hypothesis`
  alongside `significant_at_alpha_0.05`. The significance test is two-sided, so
  a primary hypothesis labelled "F > B" can be significant *in the opposite
  direction*: that row prints `[SIGNIFICANT, OPPOSITE DIRECTION]` and
  `supports_hypothesis = False`. Read `supports_hypothesis`, not `significant`.
- Per-config bootstrap CIs share one seed for reproducibility, so they are not
  independent across configs. Do not read CI overlap between two configs as a
  test — use the paired delta rows for comparisons.
- A non-zero exit means items went unscored. The unscored list is printed to
  stderr and `completion_matrix.csv` shows the gaps.
- `completion_matrix.csv` and `pairwise_F_vs_B.json` always cover the full
  panel, not just the judges of the current invocation, so incremental
  `--judges` runs no longer shrink them.

## Design notes

- Each judgment records `json_mode`, so a call that fell back to free-text
  output because the endpoint rejected `response_format` is identifiable. The
  fallback fires only on a request-shape rejection, never on a rate limit.
- Rate limits and 5xx back off exponentially; a schema violation retries
  immediately, since it is deterministic.
- `judging/judge_deepseek.py` remains the stricter reference implementation
  for response parsing: it also re-prompts with corrective feedback and marks
  unparseable items INVALID rather than dropping them.
