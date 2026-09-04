# Camera-ready evaluation lane

This directory is the single supported operator interface for new camera-ready
evaluations. It does not move, rename, or delete the existing scripts or historical
outputs. The façade calls the established implementations and keeps generated runs
separate from judgments and statistics.

## Start here

Run these commands from the repository root:

```powershell
python camera_ready/pipeline.py status
python camera_ready/pipeline.py check
```

`check` is read-only. Before an expensive run, use strict mode:

```powershell
python camera_ready/pipeline.py check --strict
```

Strict mode requires the canonical source files to be committed and all generation
dependencies to be available.

## Canonical workflow

```powershell
# 1. Generate all six configurations and verify the immutable run.
python camera_ready/pipeline.py generate

# Add --commit only when you want the verified run committed locally.
python camera_ready/pipeline.py generate --commit

# 2. Run blinded, randomized, per-item judging.
python camera_ready/pipeline.py judge --run evaluations/CAMERA_READY_OFFLINE_<timestamp>

# 3. Analyze only after the complete six-judge panel exists.
python camera_ready/pipeline.py analyze --run evaluations/CAMERA_READY_OFFLINE_<timestamp>
```

Omitting `--run` from `verify`, `judge`, `analyze`, `manual-prompt`, or `status`
selects the newest `CAMERA_READY_OFFLINE_*` run.

Generation output is immutable:

```text
evaluations/CAMERA_READY_OFFLINE_<timestamp>/
```

Judgments, completion records, optional prompts, and statistics go to its sibling:

```text
evaluations/CAMERA_READY_OFFLINE_<timestamp>_ANALYSIS/
```

## Configuration identities

| Code | Saved label | Actual model composition |
|---|---|---|
| A | `A_BASE_4BIT` | 4-bit base, no adapter, greedy |
| B | `B_FINETUNED_4BIT` | 4-bit base, canonical 4-bit-trained adapter, greedy |
| C | `C_FINETUNED_8BIT` | 8-bit base, the same 4-bit-trained adapter as B, greedy |
| E | `E_T6_IMPROVED` | B plus the T6 safety gate |
| F | `F_RAG_BM25` | B plus gated top-1 BM25 |
| G | `G_BASE_RAG` | A plus gated top-1 BM25; no adapter |

The historical name `C_FINETUNED_8BIT` describes its base precision, not an
8-bit-trained adapter.

## Files in this lane

| File | Role |
|---|---|
| `protocol.yaml` | Human-readable, declarative run configuration |
| `pipeline.py` | Single staged CLI for check/generate/verify/judge/analyze/status |
| `check.py` | Read-only consistency, dependency, path, syntax, and test checks |
| `README.md` | Operator guide and script-status map |

`protocol.yaml` deliberately uses JSON-compatible YAML, so it can be read with the
Python standard library and adds no YAML dependency.

## Existing script status

| Existing script | Status | Purpose |
|---|---|---|
| `evaluation_protocol.py` | Canonical dependency | Shared prompt, config, hashing, and provenance contracts |
| `v2_comprehensive_eval.py` | Canonical implementation | Model generation |
| `bm25_rag.py` | Canonical implementation | Gated top-1 retrieval |
| `verify_camera_ready.py` | Canonical implementation | Post-generation verification |
| `judge_per_item.py` | Canonical implementation | Primary blinded judging protocol |
| `stats_v2.py` | Canonical implementation | Complete-panel statistical analysis |
| `build_v2_judge_prompt.py` | Optional | Separate unblinded manual protocol; not an input to primary stats |
| `run_camera_ready.ps1` | Compatibility wrapper | Routes to `pipeline.py generate --commit` |
| `powershell_scripts/run_v2_comprehensive_eval.ps1` | Retired | Obsolete C/RAG/prompt contracts; exits without running |
| Older evaluation scripts and run directories | Historical | Reproduction and forensic comparison only |

## Safety properties

- No command deletes or moves an existing run.
- `check`, `status`, and `verify` are read-only.
- `generate` creates one new run and never overwrites an existing run.
- `--commit` stages only the newly generated run.
- `judge` and `manual-prompt` write only to the sibling analysis directory.
- `analyze` refuses missing, malformed, or stale judgment panels.
- The manual mega-prompt and primary per-item protocol remain explicitly separate.

## Useful scoped commands

```powershell
# Judge one provider while building the panel incrementally.
python camera_ready/pipeline.py judge --judges claude

# Preview judge work without API calls.
python camera_ready/pipeline.py judge --dry-run

# Run the separate pairwise F-vs-B robustness check.
python camera_ready/pipeline.py judge --pairwise

# Build the optional manual prompt; this does not feed stats_v2.py.
python camera_ready/pipeline.py manual-prompt --group 4
```
