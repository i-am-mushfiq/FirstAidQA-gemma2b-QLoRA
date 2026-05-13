# run_v2_eval.ps1
# Targeted evaluation: previous best (_2) vs v2 baseline (template-fixed)
# Runs eval_suite.py for both adapters, then ROUGE via evaluate.py
# Compatible with Windows PowerShell 5.1+
# Usage: conda activate fine_tuning ; cd C:\Personal_Endeavours\Fine_Tuning ; .\run_v2_eval.ps1

$ErrorActionPreference = "Continue"
Set-Location "C:\Personal_Endeavours\Fine_Tuning"

$ADAPTER_PREV = "10cat_4bit_r16_lr1e4_p3_20260506_012852"
$ADAPTER_V2   = "10cat_4bit_r16_lr1e-4_p3_v2_20260508_054337"
$LOG          = "experiments\v2_eval.log"
$BANNER       = "=" * 60

# -- Header ------------------------------------------------------------------
"" | Out-File -FilePath $LOG -Encoding UTF8
Write-Host ""
Write-Host $BANNER                                              -ForegroundColor Cyan
Write-Host "  v2 Eval: _2 best vs v2 baseline (ROUGE, no BERTScore)"  -ForegroundColor Cyan
Write-Host "  Log  : $LOG"                                     -ForegroundColor Cyan
Write-Host "  Start: $(Get-Date -Format 'HH:mm:ss')"           -ForegroundColor Cyan
Write-Host $BANNER                                             -ForegroundColor Cyan

# -- Step 1: eval_suite.py ---------------------------------------------------
Write-Host ""
Write-Host "  [1/2] Running eval_suite.py on both adapters..."  -ForegroundColor Yellow

$step1Start = Get-Date
& python eval_suite.py `
    --models $ADAPTER_PREV $ADAPTER_V2 `
    --model_path models\gemma-2b-it `
    2>&1 | ForEach-Object {
        Write-Host $_
        $_ | Out-File -FilePath $LOG -Append -Encoding UTF8
    }
$exit1   = $LASTEXITCODE
$mins1   = [math]::Round(((Get-Date) - $step1Start).TotalMinutes, 1)
$status1 = if ($exit1 -eq 0) { "OK ($mins1 min)" } else { "FAILED (exit $exit1)" }
$col1    = if ($exit1 -eq 0) { "Green" } else { "Red" }
Write-Host ""
Write-Host "  eval_suite.py -- $status1"                      -ForegroundColor $col1

if ($exit1 -ne 0) {
    Write-Host ""
    Write-Host $BANNER                                         -ForegroundColor Red
    Write-Host "  eval_suite.py failed -- aborting before ROUGE step"  -ForegroundColor Red
    Write-Host $BANNER                                         -ForegroundColor Red
    exit 1
}

# -- Step 2: evaluate.py (ROUGE only, skip BERTScore) -----------------------
Write-Host ""
Write-Host "  [2/2] Computing ROUGE on latest eval run..."     -ForegroundColor Yellow

$step2Start = Get-Date
& python evaluate.py --no-bert `
    2>&1 | ForEach-Object {
        Write-Host $_
        $_ | Out-File -FilePath $LOG -Append -Encoding UTF8
    }
$exit2   = $LASTEXITCODE
$mins2   = [math]::Round(((Get-Date) - $step2Start).TotalMinutes, 1)
$status2 = if ($exit2 -eq 0) { "OK ($mins2 min)" } else { "FAILED (exit $exit2)" }
$col2    = if ($exit2 -eq 0) { "Green" } else { "Red" }
Write-Host ""
Write-Host "  evaluate.py -- $status2"                        -ForegroundColor $col2

# -- Footer ------------------------------------------------------------------
$overall = if (($exit1 -eq 0) -and ($exit2 -eq 0)) { "ALL OK" } else { "FAILED" }
$colour  = if ($overall -eq "ALL OK") { "Green" } else { "Red" }

Write-Host ""
Write-Host $BANNER                                             -ForegroundColor $colour
Write-Host "  $overall  |  Log saved -> $LOG"                 -ForegroundColor $colour
Write-Host $BANNER                                             -ForegroundColor $colour
