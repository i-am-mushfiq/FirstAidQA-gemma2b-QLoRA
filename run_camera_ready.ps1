# run_camera_ready.ps1
# ====================
# Compatibility entrypoint. The canonical implementation is now the manifest-driven
# camera_ready/pipeline.py façade. Historical implementation remains below this shim.
#
# BEFORE RUNNING:
#   git pull origin main     (get latest bm25_rag.py + v2_comprehensive_eval.py)
#   git push origin main     (push your Task 1 commits if you haven't already)
#
# USAGE:
#   cd C:\Personal_Endeavours\Fine_Tuning
#   .\run_camera_ready.ps1
#
# The run writes to:
#   evaluations\CAMERA_READY_OFFLINE_<timestamp>\
#
# Do NOT edit the output directory after the run completes.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ROOT = $PSScriptRoot

Write-Host "Routing through the canonical manifest-driven camera-ready pipeline..." -ForegroundColor Cyan
& python (Join-Path $ROOT "camera_ready\pipeline.py") generate --commit
exit $LASTEXITCODE

$sourceFiles = @(
    "evaluation_protocol.py",
    "bm25_rag.py",
    "v2_comprehensive_eval.py",
    "audit_gap_gate.py",
    "verify_camera_ready.py",
    "build_v2_judge_prompt.py",
    "judge_per_item.py",
    "stats_v2.py",
    "rubric_v2.md",
    "run_camera_ready.ps1",
    "test_camera_ready_pipeline.py",
    "evaluations/eval_bank_v2_40q/eval_bank_v2.json"
)
$dirtySources = @(git status --porcelain -- $sourceFiles)
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Git source state."
}
if ($dirtySources.Count -gt 0) {
    Write-Host "ERROR: Camera-ready source files are modified or untracked." -ForegroundColor Red
    Write-Host "Commit the reviewed pipeline before a reproducible camera-ready run:" -ForegroundColor Yellow
    $dirtySources | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
    exit 1
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  CAMERA-READY EVAL -- Task 2 pre-flight" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# ------------------------------------------------------------------
# Step 1: Syntax checks (no GPU, no model)
# ------------------------------------------------------------------
Write-Host "[1/5] Syntax checks..." -ForegroundColor Yellow

$scripts = @(
    "evaluation_protocol.py",
    "bm25_rag.py",
    "v2_comprehensive_eval.py",
    "audit_gap_gate.py",
    "verify_camera_ready.py",
    "build_v2_judge_prompt.py",
    "judge_per_item.py",
    "stats_v2.py",
    "test_camera_ready_pipeline.py"
)

foreach ($s in $scripts) {
    $path = Join-Path $ROOT $s
    python -c "
import ast, sys
with open(r'$path', encoding='utf-8') as f:
    src = f.read()
ast.parse(src)
print('OK  $s')
"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "SYNTAX ERROR in $s -- aborting." -ForegroundColor Red
        exit 1
    }
}

Write-Host "  All syntax checks passed." -ForegroundColor Green
python -m unittest -q test_camera_ready_pipeline.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "CAMERA-READY CONTRACT TESTS FAILED -- aborting." -ForegroundColor Red
    exit 1
}
Write-Host "  Camera-ready contract tests passed." -ForegroundColor Green
Write-Host ""

# ------------------------------------------------------------------
# Step 2: Topic-gate pattern verification (no GPU)
# ------------------------------------------------------------------
Write-Host "[2/5] Topic-gate pattern verification..." -ForegroundColor Yellow

python -c "
import re, sys
from bm25_rag import GAP_TOPIC_PATTERNS
TESTS = [
    ('How do you apply a tourniquet to control arterial bleeding?',       True,  'tourniquet_escalation'),
    ('How do you help a choking infant under 1 year?',                   True,  'infant_choking'),
    ('How do you log-roll a casualty with spinal injury?',               True,  'spinal_logroll'),
    ('What precautions when moving a casualty with a possible spinal injury?', True, 'spinal_logroll'),
    ('What is the correct way to apply a chest seal?',                   True,  'chest_seal'),
    ('Can you give naloxone for opioid overdose at home?',               True,  'naloxone_opioid'),
    ('How do you give rescue breaths to a drowning child?',              True,  'rescue_breaths_drowning'),
    ('How long should you cool a burn under running water?',             True,  'burn_cooling'),
    ('How do you perform CPR on an adult?',                              False, None),
    ('What are the signs of anaphylaxis?',                               False, None),
    ('Signs and treatment of heat stroke?',                              False, None),
]
errors = 0
for query, expect_gate, expect_topic in TESTS:
    hits = [(k, p) for k, (p, _) in GAP_TOPIC_PATTERNS.items() if p.search(query)]
    actual_gate = len(hits) > 0
    actual_topic = hits[0][0] if hits else None
    ok = (actual_gate == expect_gate) and (actual_topic == expect_topic)
    if not ok:
        errors += 1
        print(f'FAIL  expected gate={expect_gate} topic={expect_topic}  query: {query[:60]}')
if errors == 0:
    print(f'All {len(TESTS)} topic-gate assertions PASSED')
sys.exit(errors)
" --% 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Host "TOPIC-GATE VERIFICATION FAILED -- aborting." -ForegroundColor Red
    exit 1
}
Write-Host "  Topic-gate patterns OK." -ForegroundColor Green
Write-Host ""

# ------------------------------------------------------------------
# Step 3: verify_template_v1.py (tokenizer check -- informational only)
# ------------------------------------------------------------------
# NOTE: The 1-token newline mismatch (token 108) is the KNOWN template
# alignment bug from Chapter 6 of PROJECT_HANDOFF_v3.md.  The v2 adapter
# was trained with the manual template from data.py; eval uses the same
# manual template.  Training and eval are internally consistent.
# This check is run for audit purposes but does NOT block the eval run.
# Fix: retrain using train_v2.py + data_v2.py (apply_chat_template natively).
# ------------------------------------------------------------------
Write-Host "[3/5] Template alignment check (informational -- known bug, non-blocking)..." -ForegroundColor Yellow

python verify_template_v1.py
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "  NOTE: Template mismatch confirmed (known Ch.6 bug -- 1 newline token)." -ForegroundColor Yellow
    Write-Host "  Eval uses same manual template as training: internally consistent." -ForegroundColor Yellow
    Write-Host "  Continuing (non-blocking for eval-only run)." -ForegroundColor Yellow
} else {
    Write-Host "  Template alignment OK." -ForegroundColor Green
}
Write-Host ""

# ------------------------------------------------------------------
# Step 4: Camera-ready eval run
# ------------------------------------------------------------------
Write-Host "[4/5] Running offline camera-ready eval (6 configs x 41 questions)..." -ForegroundColor Yellow
Write-Host "  Configs: A B C E F G (D excluded -- loop-fix pending)" -ForegroundColor Gray
Write-Host "  Prompt policy: offline_definitive_v1 (EMS unreachable)" -ForegroundColor Gray
Write-Host "  Expected time: ~2 GPU-hours" -ForegroundColor Gray
Write-Host ""

$ts = (Get-Date -Format "yyyyMMdd_HHmmss")
Write-Host "  Start time: $ts" -ForegroundColor Gray

$beforeRunDirs = @(Get-ChildItem -Path (Join-Path $ROOT "evaluations") `
    -Filter "CAMERA_READY_OFFLINE_*" -Directory -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty FullName)

python v2_comprehensive_eval.py `
    --configs A B C E F G `
    --max_new_tokens 350 `
    --prompt_policy offline_definitive_v1 `
    --camera_ready

if ($LASTEXITCODE -ne 0) {
    Write-Host "EVAL RUN FAILED -- check output above." -ForegroundColor Red
    exit 1
}

$afterRunDirs = @(Get-ChildItem -Path (Join-Path $ROOT "evaluations") `
    -Filter "CAMERA_READY_OFFLINE_*" -Directory -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty FullName)
$newRunDirs = @($afterRunDirs | Where-Object { $_ -notin $beforeRunDirs })
if ($newRunDirs.Count -ne 1) {
    Write-Host "ERROR: Expected exactly one new offline camera-ready directory; found $($newRunDirs.Count)." -ForegroundColor Red
    exit 1
}
$cameraRunDir = $newRunDirs[0]

Write-Host ""
Write-Host "  Eval run complete." -ForegroundColor Green
Write-Host ""

# ------------------------------------------------------------------
# Step 5: Post-run verification
# ------------------------------------------------------------------
Write-Host "[5/5] Post-run verification..." -ForegroundColor Yellow

python verify_camera_ready.py --run_dir $cameraRunDir

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "POST-RUN VERIFICATION FAILED." -ForegroundColor Red
    Write-Host "The run directory exists but is NOT cleared for CAMERA_READY status." -ForegroundColor Red
    Write-Host "Fix the issues listed above before committing." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  ALL CHECKS PASSED" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""

# ------------------------------------------------------------------
# Git commit
# ------------------------------------------------------------------
Write-Host "Staging and committing camera-ready run..." -ForegroundColor Yellow

$latest = $cameraRunDir
$dirname = Split-Path -Leaf $cameraRunDir

Write-Host "  Committing: $dirname" -ForegroundColor Gray

git add "$latest"

git commit -m "CAMERA_READY_OFFLINE: 6-config v2 eval with aligned no-EMS premise

Run: $dirname
Configs: A_BASE_4BIT B_FINETUNED_4BIT C_FINETUNED_8BIT
         E_T6_IMPROVED F_RAG_BM25 G_BASE_RAG
D_T4_IMPROVED: excluded (loop-fix pending)
Questions: 41 (eval_bank_v2_40q/eval_bank_v2.json, patched SC flags)
Prompt policy: offline_definitive_v1 (EMS unreachable; aligned with rubric_v2)
BM25 gate: topic-keyed (7 patterns), top-1 retrieval
V2Q35 (tourniquet): gated in F and G
V2Q41 (spinal movement): gated in F and G
Config G: base model + RAG (no adapter) -- adapter ablation baseline

Do NOT edit outputs in $dirname after this commit."

if ($LASTEXITCODE -ne 0) {
    Write-Host "Git commit failed -- check for lock files or unstaged changes." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  CAMERA-READY RUN COMMITTED" -ForegroundColor Green
Write-Host "  Run: $dirname" -ForegroundColor Green
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor Cyan
Write-Host "    git push origin main" -ForegroundColor White
Write-Host "    python judge_per_item.py --run_dir evaluations\$dirname" -ForegroundColor White
Write-Host "    python stats_v2.py --run_dir evaluations\$dirname" -ForegroundColor White
Write-Host ""
Write-Host "  Optional separate manual mega-prompt protocol:" -ForegroundColor Cyan
Write-Host "    python build_v2_judge_prompt.py --run_dir evaluations\$dirname" -ForegroundColor White
Write-Host "  Judgments and stats go to evaluations\${dirname}_ANALYSIS; the run stays immutable." -ForegroundColor Gray
Write-Host "============================================================" -ForegroundColor Green
