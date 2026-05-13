# run_eval_40.ps1
# Eval suite + ROUGE + LLM judge prompt using the 40-question bank
# Targets the previous best adapter and the v2 template-fixed adapter
# Compatible with Windows PowerShell 5.1+
# Usage: conda activate fine_tuning ; cd C:\Personal_Endeavours\Fine_Tuning ; .\run_eval_40.ps1

$ErrorActionPreference = "Continue"
Set-Location "C:\Personal_Endeavours\Fine_Tuning"

$ADAPTER_PREV  = "10cat_4bit_r16_lr1e4_p3_20260506_012852"
$ADAPTER_V2    = "10cat_4bit_r16_lr1e-4_p3_v2_20260508_054337"
$QUESTIONS     = "data\eval_questions_40.json"
$LOG           = "experiments\eval_40.log"
$BANNER        = "=" * 60

"" | Out-File -FilePath $LOG -Encoding UTF8

Write-Host ""
Write-Host $BANNER                                                   -ForegroundColor Cyan
Write-Host "  40-Question Eval: _2 best vs v2 baseline"             -ForegroundColor Cyan
Write-Host "  Questions : $QUESTIONS"                                -ForegroundColor Cyan
Write-Host "  Log       : $LOG"                                      -ForegroundColor Cyan
Write-Host "  Start     : $(Get-Date -Format 'HH:mm:ss')"           -ForegroundColor Cyan
Write-Host $BANNER                                                   -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# Step 1 -- eval_suite.py
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "  [1/3] Running eval_suite.py (40 questions, 2 adapters)..." -ForegroundColor Yellow

$t1 = Get-Date
& python eval_suite.py `
    --models        $ADAPTER_PREV $ADAPTER_V2 `
    --model_path    models\gemma-2b-it `
    --questions_file $QUESTIONS `
    2>&1 | ForEach-Object {
        Write-Host $_
        $_ | Out-File -FilePath $LOG -Append -Encoding UTF8
    }
$exit1 = $LASTEXITCODE
$col1  = if ($exit1 -eq 0) { "Green" } else { "Red" }
$tag1  = if ($exit1 -eq 0) { "OK ($([math]::Round(((Get-Date)-$t1).TotalMinutes,1)) min)" } else { "FAILED (exit $exit1)" }
Write-Host "  eval_suite  -- $tag1" -ForegroundColor $col1

if ($exit1 -ne 0) {
    Write-Host ""
    Write-Host $BANNER                                               -ForegroundColor Red
    Write-Host "  eval_suite failed -- aborting"                    -ForegroundColor Red
    Write-Host $BANNER                                               -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# Step 2 -- evaluate.py  (ROUGE, no BERTScore)
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "  [2/3] Computing ROUGE on latest run..."               -ForegroundColor Yellow

$t2 = Get-Date
& python evaluate.py --no-bert `
    2>&1 | ForEach-Object {
        Write-Host $_
        $_ | Out-File -FilePath $LOG -Append -Encoding UTF8
    }
$exit2 = $LASTEXITCODE
$col2  = if ($exit2 -eq 0) { "Green" } else { "Red" }
$tag2  = if ($exit2 -eq 0) { "OK ($([math]::Round(((Get-Date)-$t2).TotalMinutes,1)) min)" } else { "FAILED (exit $exit2)" }
Write-Host "  evaluate    -- $tag2" -ForegroundColor $col2

# ---------------------------------------------------------------------------
# Step 3 -- build_llm_judge_prompt.py
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "  [3/3] Building LLM judge prompt..."                   -ForegroundColor Yellow

$t3 = Get-Date
& python build_llm_judge_prompt.py `
    2>&1 | ForEach-Object {
        Write-Host $_
        $_ | Out-File -FilePath $LOG -Append -Encoding UTF8
    }
$exit3 = $LASTEXITCODE
$col3  = if ($exit3 -eq 0) { "Green" } else { "Red" }
$tag3  = if ($exit3 -eq 0) { "OK ($([math]::Round(((Get-Date)-$t3).TotalMinutes,1)) min)" } else { "FAILED (exit $exit3)" }
Write-Host "  judge prompt -- $tag3" -ForegroundColor $col3

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
$all    = ($exit1 -eq 0) -and ($exit2 -eq 0) -and ($exit3 -eq 0)
$colour = if ($all) { "Green" } else { "Red" }
$label  = if ($all) { "ALL OK" } else { "FAILED" }

Write-Host ""
Write-Host $BANNER                                                   -ForegroundColor $colour
Write-Host "  $label  |  Log -> $LOG"                               -ForegroundColor $colour
Write-Host "  Results  -> evaluations\eval_<timestamp>\run.json"    -ForegroundColor $colour
Write-Host "  Metrics  -> evaluations\eval_<timestamp>\metrics.json" -ForegroundColor $colour
Write-Host "  Judge    -> evaluations\llm_judge_prompt_<timestamp>.txt" -ForegroundColor $colour
Write-Host $BANNER                                                   -ForegroundColor $colour
