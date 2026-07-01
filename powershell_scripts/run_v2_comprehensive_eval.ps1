# run_v2_comprehensive_eval.ps1
# ============================================================
# Full v2 evaluation pipeline:
#   1. Run v2_comprehensive_eval.py (6 configs x v2 41-question bank)
#   2. Build LLM judge prompt from results
#   3. Print paths and next steps
#
# Configs run:
#   A  BASE_4BIT       Base Gemma 2B-IT, no fine-tuning, 4-bit
#   B  FINETUNED_4BIT  Best v2 adapter, 4-bit (canonical baseline)
#   C  FINETUNED_8BIT  Best v2 adapter, 8-bit
#   D  T4_IMPROVED     4-bit fine-tuned + T4 soft-retry
#   E  T6_IMPROVED     4-bit fine-tuned + T6 binary safety gate (recalibrated)
#   F  RAG_BM25        4-bit fine-tuned + BM25 RAG top-3
#
# Model load passes (minimises GPU reloads):
#   Pass 1: base 4-bit (no adapter)      -> A
#   Pass 2: fine-tuned 4-bit (adapter)   -> B, D, E, F
#   Pass 3: fine-tuned 8-bit (adapter)   -> C
#
# Question bank: evaluations/eval_bank_v2_40q/eval_bank_v2.json
#   Statistically representative: 22% SC, all 10 training categories,
#   proportional category allocation - replaces the over-specified v1 bank.
#
# Prerequisites:
#   conda activate fine_tuning
#   pip install rank_bm25 --break-system-packages
#
# Usage:
#   cd C:\Personal_Endeavours\Fine_Tuning\powershell_scripts
#   .\run_v2_comprehensive_eval.ps1
#
# To run a subset of configs (e.g. skip 8-bit and RAG):
#   .\run_v2_comprehensive_eval.ps1 -Configs A,B,D,E
# ============================================================

param(
    [string[]]$Configs       = @("A","B","C","D","E","F"),
    [int]$MaxNewTokens       = 350,
    [int]$RagTopK            = 3
)

Set-Location "C:\Personal_Endeavours\Fine_Tuning"
$ErrorActionPreference = "Continue"

# --- Paths ------------------------------------------------------------------
$ADAPTER_4BIT = "experiments\10cat_4bit_r16_lr1e-4_p3_v2_20260508_054337\adapter"
$ADAPTER_8BIT = "experiments\10cat_8bit_r16_lr1e-4_p3_20260508_195536\adapter"
$MODEL        = "models\gemma-2b-it"
$QUESTIONS    = "evaluations\eval_bank_v2_40q\eval_bank_v2.json"
$LOG          = "experiments\v2_comprehensive_eval.log"
$BANNER       = "=" * 65

# --- Validate prerequisites -------------------------------------------------
Write-Host ""
Write-Host $BANNER -ForegroundColor Cyan
Write-Host "  v2 Comprehensive Evaluation Pipeline" -ForegroundColor Cyan
Write-Host "  Configs : $($Configs -join ', ')" -ForegroundColor Cyan
Write-Host "  Bank    : $QUESTIONS" -ForegroundColor Cyan
Write-Host "  Start   : $(Get-Date -Format 'HH:mm:ss')" -ForegroundColor Cyan
Write-Host $BANNER -ForegroundColor Cyan

if (-not (Test-Path $QUESTIONS)) {
    Write-Host ""
    Write-Host "ERROR: Question bank not found: $QUESTIONS" -ForegroundColor Red
    Write-Host "       Run the v2 bank generation first." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $ADAPTER_4BIT)) {
    Write-Host ""
    Write-Host "ERROR: 4-bit adapter not found: $ADAPTER_4BIT" -ForegroundColor Red
    exit 1
}
if (("C" -in $Configs) -and (-not (Test-Path $ADAPTER_8BIT))) {
    Write-Host ""
    Write-Host "WARNING: 8-bit adapter not found: $ADAPTER_8BIT" -ForegroundColor Yellow
    Write-Host "         Removing C from config list." -ForegroundColor Yellow
    $Configs = $Configs | Where-Object { $_ -ne "C" }
}
if (-not (Test-Path $MODEL)) {
    Write-Host ""
    Write-Host "ERROR: Model not found: $MODEL" -ForegroundColor Red
    Write-Host "       Run: python download_model.py" -ForegroundColor Red
    exit 1
}

# --- Check rank_bm25 if F is requested --------------------------------------
if ("F" -in $Configs) {
    $bm25Check = & python -c "import rank_bm25; print('ok')" 2>&1
    if ($bm25Check -ne "ok") {
        Write-Host ""
        Write-Host "WARNING: rank_bm25 not installed. Config F (RAG) will be skipped." -ForegroundColor Yellow
        Write-Host "         Install: pip install rank_bm25 --break-system-packages" -ForegroundColor Yellow
        $Configs = $Configs | Where-Object { $_ -ne "F" }
    }
}

Write-Host ""
Write-Host "  Final config list: $($Configs -join ', ')" -ForegroundColor Green

# --- Remove stale git lock file if present ----------------------------------
$lockFile = ".git\index.lock"
if (Test-Path $lockFile) {
    Write-Host "  Removing stale git lock file..." -ForegroundColor Yellow
    Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
}

# --- Step 1: Run eval -------------------------------------------------------
Write-Host ""
Write-Host "  [Step 1] Running v2_comprehensive_eval.py..." -ForegroundColor Cyan
Write-Host "           Expected runtime: ~35-55 min (A100/V100) or ~90 min (T4)"
Write-Host "           Model reloads: up to 3 passes (base-4bit, ft-4bit, ft-8bit)"
Write-Host ""

$t1 = Get-Date

& python v2_comprehensive_eval.py `
    --adapter_4bit   $ADAPTER_4BIT `
    --adapter_8bit   $ADAPTER_8BIT `
    --model_path     $MODEL `
    --questions      $QUESTIONS `
    --configs        @Configs `
    --max_new_tokens $MaxNewTokens `
    --rag_top_k      $RagTopK `
    2>&1 | Tee-Object -FilePath $LOG

$evalExit = $LASTEXITCODE
$mins     = [math]::Round(((Get-Date) - $t1).TotalMinutes, 1)

if ($evalExit -ne 0) {
    Write-Host ""
    Write-Host $BANNER -ForegroundColor Red
    Write-Host "  EVAL FAILED (exit $evalExit) after $mins min" -ForegroundColor Red
    Write-Host "  Check log: $LOG" -ForegroundColor Red
    Write-Host $BANNER -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "  Eval complete in $mins min" -ForegroundColor Green
Write-Host "  (ROUGE-L table printed above; also saved to metrics.json)" -ForegroundColor Cyan

# --- Find output directory --------------------------------------------------
$runDir = Get-ChildItem "evaluations\v2_comprehensive_*" -ErrorAction SilentlyContinue |
          Sort-Object LastWriteTime -Descending |
          Select-Object -First 1 -ExpandProperty FullName

if (-not $runDir) {
    Write-Host "ERROR: Could not find v2_comprehensive_* output directory." -ForegroundColor Red
    exit 1
}
Write-Host "  Run directory: $runDir" -ForegroundColor Cyan

$metricsPath = Join-Path $runDir "metrics.json"

# --- Step 2: Build judge prompt ---------------------------------------------
Write-Host ""
Write-Host "  [Step 2] Building LLM judge prompt..." -ForegroundColor Cyan

& python build_v2_judge_prompt.py --run_dir $runDir 2>&1 | Tee-Object -FilePath $LOG -Append
$promptExit = $LASTEXITCODE

$promptFile = Join-Path $runDir "llm_judge_v2_prompt.txt"

# --- Final summary ----------------------------------------------------------
Write-Host ""
Write-Host $BANNER -ForegroundColor Green
Write-Host "  DONE  ($mins min total)" -ForegroundColor Green
Write-Host ""
Write-Host "  Outputs:" -ForegroundColor White
Write-Host "    Run dir     : $runDir" -ForegroundColor White
Write-Host "    metrics.json: $metricsPath" -ForegroundColor White

if (Test-Path $promptFile) {
    $kb = [math]::Round((Get-Item $promptFile).Length / 1024, 1)
    Write-Host "    Judge prompt: $promptFile  ($kb KB)" -ForegroundColor Yellow
} else {
    Write-Host "    Judge prompt: (not generated - check log)" -ForegroundColor Red
}

Write-Host ""
Write-Host "  Next steps:" -ForegroundColor Cyan
Write-Host "    1. Open the judge prompt and submit to all 7 judges:" -ForegroundColor White
Write-Host "       GPT-4o | Claude | Gemini | Grok | DeepSeek | Kimi K2 | Manus" -ForegroundColor White
Write-Host ""
Write-Host "    2. After scores are returned, create v2_judge_synthesis.md" -ForegroundColor White
Write-Host "       in: $runDir" -ForegroundColor White
Write-Host ""
Write-Host "    3. Decision gates:" -ForegroundColor White
Write-Host "       FT gain   : B ROUGE-L > A ROUGE-L          (expected: YES)" -ForegroundColor White
Write-Host "       8-bit OK  : C ROUGE-L >= B ROUGE-L - 0.01  (parity check)" -ForegroundColor White
Write-Host "       T4 adopt  : D SC mean >= B SC mean" -ForegroundColor White
Write-Host "       T6 adopt  : E SC mean >= B SC mean - 0.05  AND  false-pos <= 20pct" -ForegroundColor White
Write-Host "       RAG adopt : F ROUGE-L > B ROUGE-L          (prior result: anti-correlated)" -ForegroundColor White
Write-Host ""
Write-Host "    4. Full log: $LOG" -ForegroundColor White
Write-Host $BANNER -ForegroundColor Green
