# run_camera_ready.ps1
# ====================
# Camera-ready eval runner for v2 comprehensive evaluation.
# Runs all pre-flight checks, then the 6-config eval, then post-run verification.
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
#   evaluations\CAMERA_READY_<timestamp>\
#
# Do NOT edit the output directory after the run completes.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ROOT = $PSScriptRoot

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
    "bm25_rag.py",
    "v2_comprehensive_eval.py",
    "audit_gap_gate.py",
    "verify_camera_ready.py"
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
# Step 3: verify_template_v1.py (tokenizer check, no model load)
# ------------------------------------------------------------------
Write-Host "[3/5] Template alignment verification..." -ForegroundColor Yellow

python verify_template_v1.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "TEMPLATE VERIFICATION FAILED -- aborting." -ForegroundColor Red
    Write-Host "Check verify_template_v1.py output above." -ForegroundColor Red
    exit 1
}
Write-Host "  Template alignment OK." -ForegroundColor Green
Write-Host ""

# ------------------------------------------------------------------
# Step 4: Camera-ready eval run
# ------------------------------------------------------------------
Write-Host "[4/5] Running camera-ready eval (6 configs x 41 questions)..." -ForegroundColor Yellow
Write-Host "  Configs: A B C E F G (D excluded -- loop-fix pending)" -ForegroundColor Gray
Write-Host "  Expected time: ~2 GPU-hours" -ForegroundColor Gray
Write-Host ""

$ts = (Get-Date -Format "yyyyMMdd_HHmmss")
Write-Host "  Start time: $ts" -ForegroundColor Gray

python v2_comprehensive_eval.py `
    --configs A B C E F G `
    --max_new_tokens 350 `
    --camera_ready

if ($LASTEXITCODE -ne 0) {
    Write-Host "EVAL RUN FAILED -- check output above." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "  Eval run complete." -ForegroundColor Green
Write-Host ""

# ------------------------------------------------------------------
# Step 5: Post-run verification
# ------------------------------------------------------------------
Write-Host "[5/5] Post-run verification..." -ForegroundColor Yellow

python verify_camera_ready.py

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

$run_dirs = Get-ChildItem -Path (Join-Path $ROOT "evaluations") -Filter "CAMERA_READY_*" -Directory |
            Sort-Object Name -Descending

if ($run_dirs.Count -eq 0) {
    Write-Host "ERROR: No CAMERA_READY_* directory found to commit." -ForegroundColor Red
    exit 1
}

$latest = $run_dirs[0].FullName
$dirname = $run_dirs[0].Name

Write-Host "  Committing: $dirname" -ForegroundColor Gray

git add "$latest"
git add "bm25_rag.py" "v2_comprehensive_eval.py" "audit_gap_gate.py" "verify_camera_ready.py"

git commit -m "CAMERA_READY: 6-config v2 eval with topic-gated BM25 RAG

Run: $dirname
Configs: A_BASE_4BIT B_FINETUNED_4BIT C_FINETUNED_8BIT
         E_T6_IMPROVED F_RAG_BM25 G_BASE_RAG
D_T4_IMPROVED: excluded (loop-fix pending)
Questions: 41 (eval_bank_v2_40q/eval_bank_v2.json, patched SC flags)
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
Write-Host "    python build_v2_judge_prompt.py --run_dir evaluations\$dirname" -ForegroundColor White
Write-Host "============================================================" -ForegroundColor Green
