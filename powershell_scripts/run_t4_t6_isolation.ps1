# run_t4_t6_isolation.ps1
# Full T4/T6 isolation pipeline:
#   1. Run t4_t6_isolation_eval.py (6 configs over 40Q bank)
#   2. Build LLM judge prompt from results
#   3. Print output path for submission
#
# Usage: conda activate fine_tuning
#        cd C:\Personal_Endeavours\Fine_Tuning\powershell_scripts
#        .\run_t4_t6_isolation.ps1
# -----------------------------------------------------------------------

Set-Location "C:\Personal_Endeavours\Fine_Tuning"
$ErrorActionPreference = "Continue"

$ADAPTER   = "experiments\10cat_4bit_r16_lr1e-4_p3_v2_20260508_054337\adapter"
$MODEL     = "models\gemma-2b-it"
$QUESTIONS = "data\eval_questions_40.json"
$CONFIGS   = "A", "B", "C", "D", "E", "F"   # all six; remove any to skip
$LOG       = "experiments\t4_t6_isolation.log"
$BANNER    = "=" * 60

# ── Step 1: Isolation eval ─────────────────────────────────────────────
Write-Host ""
Write-Host $BANNER -ForegroundColor Cyan
Write-Host "  T4 / T6 Isolation Ablation Eval" -ForegroundColor Cyan
Write-Host "  Configs: $($CONFIGS -join ', ')" -ForegroundColor Cyan
Write-Host "  Start: $(Get-Date -Format 'HH:mm:ss')" -ForegroundColor Cyan
Write-Host $BANNER -ForegroundColor Cyan

$t1 = Get-Date
& python t4_t6_isolation_eval.py `
    --adapter_path $ADAPTER `
    --model_path   $MODEL `
    --questions    $QUESTIONS `
    --configs      @CONFIGS `
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

# ── Step 2: Find the output directory ─────────────────────────────────
$runDir = Get-ChildItem "evaluations\t4_t6_isolation_*" |
          Sort-Object LastWriteTime -Descending |
          Select-Object -First 1 -ExpandProperty FullName

if (-not $runDir) {
    Write-Host "ERROR: Could not find t4_t6_isolation_* output directory." -ForegroundColor Red
    exit 1
}
Write-Host "  Run directory: $runDir" -ForegroundColor Cyan

# ── Step 3: Build judge prompt ─────────────────────────────────────────
Write-Host ""
Write-Host "  Building LLM judge prompt..." -ForegroundColor Cyan

& python build_t4_t6_judge_prompt.py --run_dir $runDir 2>&1 | Tee-Object -FilePath $LOG -Append
$promptExit = $LASTEXITCODE

$promptFile = Join-Path $runDir "llm_judge_t4_t6_prompt.txt"

# ── Summary ────────────────────────────────────────────────────────────
Write-Host ""
Write-Host $BANNER -ForegroundColor Green
Write-Host "  DONE" -ForegroundColor Green
Write-Host ""
Write-Host "  Eval results : $runDir" -ForegroundColor White
Write-Host "  Metrics JSON : $runDir\metrics.json" -ForegroundColor White
Write-Host "  Judge prompt : $promptFile" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Next: open the judge prompt and submit to all 7 judges:" -ForegroundColor Cyan
Write-Host "    GPT-4o, Claude, Gemini, Grok, DeepSeek, Kimi K2, Manus" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Decision gate:" -ForegroundColor Cyan
Write-Host "    T4 proceed if: Config C SC mean >= Config A SC mean" -ForegroundColor White
Write-Host "    T6 proceed if: Config E safety flags <= Config A safety flags" -ForegroundColor White
Write-Host "                   AND Config E SC mean >= Config A SC mean - 0.05" -ForegroundColor White
Write-Host $BANNER -ForegroundColor Green
