# run_enhanced_eval.ps1
# Enhanced inference ablation study -- five configurations covering all
# four techniques (T2, T4, T5, T6) and their combinations.
#
# Techniques:
#   T2  -- Greedy decoding for SC queries
#   T4  -- Calibrated min_new_tokens floor
#   T5  -- RAG with reference-answer KB (eval_questions_40.json)
#   T6  -- Two-pass self-critique
#
# Ablation configurations:
#   Run A  -- Baseline  (all OFF)   -- should reproduce eval_suite output
#   Run B  -- T2+T4     (deterministic only, no RAG, no two-pass)
#   Run C  -- T5 only   (RAG alone -- isolates retrieval effect)
#   Run D  -- T6 only   (two-pass alone -- isolates self-critique)
#   Run E  -- All ON    (T2+T4+T5+T6) -- full enhanced
#
# Compatible with Windows PowerShell 5.1+
# Usage:
#   conda activate fine_tuning
#   cd C:\Personal_Endeavours\Fine_Tuning
#   .\run_enhanced_eval.ps1
#
# Prerequisites:
#   pip install sentence-transformers rank_bm25 --break-system-packages
#
# To run a single configuration, comment out the other $runs entries.
# To change the adapter, update $ADAPTER_PATH.

$ErrorActionPreference = "Continue"
Set-Location "C:\Personal_Endeavours\Fine_Tuning"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

$ADAPTER_PATH   = "experiments\10cat_4bit_r16_lr1e4_p3_20260506_012852\adapter"
$QUESTIONS_FILE = "data\eval_questions_40.json"
$MAX_TOKENS     = 250
$LOG_DIR        = "experiments"
$BANNER         = "=" * 60

# ---------------------------------------------------------------------------
# Ablation run definitions
# ---------------------------------------------------------------------------

$runs = @(
    @{
        Label     = "Run A -- Baseline (all OFF)"
        Log       = "$LOG_DIR\enhanced_A_baseline.log"
        ExtraArgs = @("--no_greedy_sc", "--no_min_tokens", "--no_rag", "--no_two_pass")
    },
    @{
        Label     = "Run B -- Deterministic only (T2+T4 ON, T5+T6 OFF)"
        Log       = "$LOG_DIR\enhanced_B_det_only.log"
        ExtraArgs = @("--no_rag", "--no_two_pass")
    },
    @{
        Label     = "Run C -- RAG only (T5 ON, T2+T4+T6 OFF)"
        Log       = "$LOG_DIR\enhanced_C_rag_only.log"
        ExtraArgs = @("--no_greedy_sc", "--no_min_tokens", "--no_two_pass")
    },
    @{
        Label     = "Run D -- Two-pass only (T6 ON, T2+T4+T5 OFF)"
        Log       = "$LOG_DIR\enhanced_D_two_pass_only.log"
        ExtraArgs = @("--no_greedy_sc", "--no_min_tokens", "--no_rag")
    },
    @{
        Label     = "Run E -- Full enhanced (T2+T4+T5+T6 all ON)"
        Log       = "$LOG_DIR\enhanced_E_full.log"
        ExtraArgs = @()
    }
)

# ---------------------------------------------------------------------------
# Run each ablation configuration
# ---------------------------------------------------------------------------

$results      = @()
$runJsonPaths = @()   # collect output run.json paths for the merged judge prompt

foreach ($run in $runs) {

    "" | Out-File -FilePath $run.Log -Encoding UTF8

    Write-Host ""
    Write-Host $BANNER                                                   -ForegroundColor Cyan
    Write-Host "  $($run.Label)"                                         -ForegroundColor Cyan
    Write-Host "  Log   : $($run.Log)"                                   -ForegroundColor Cyan
    Write-Host "  Start : $(Get-Date -Format 'HH:mm:ss')"               -ForegroundColor Cyan
    Write-Host $BANNER                                                   -ForegroundColor Cyan

    $runStart = Get-Date

    $baseArgs = @(
        "enhanced_inference.py",
        "--adapter_path",    $ADAPTER_PATH,
        "--questions_file",  $QUESTIONS_FILE,
        "--max_new_tokens",  $MAX_TOKENS
    ) + $run.ExtraArgs

    $output = & python $baseArgs 2>&1
    $output | ForEach-Object {
        Write-Host $_
        $_ | Out-File -FilePath $run.Log -Append -Encoding UTF8
    }

    $exit    = $LASTEXITCODE
    $elapsed = (Get-Date) - $runStart
    $mins    = [math]::Round($elapsed.TotalMinutes, 1)
    $status  = if ($exit -eq 0) { "OK" } else { "FAILED (exit $exit)" }
    $colour  = if ($exit -eq 0) { "Green" } else { "Red" }

    Write-Host ""
    Write-Host $BANNER                                                   -ForegroundColor $colour
    Write-Host "  $($run.Label)"                                         -ForegroundColor $colour
    Write-Host "  Finished in $mins min -- $status"                      -ForegroundColor $colour
    Write-Host "  Log saved -> $($run.Log)"                              -ForegroundColor $colour
    Write-Host $BANNER                                                   -ForegroundColor $colour

    $results += [PSCustomObject]@{
        Label   = $run.Label
        Status  = $status
        Minutes = $mins
        Log     = $run.Log
    }

    # Parse the "Results saved ->" line to capture the output run.json path
    $savedLine = $output | Where-Object { $_ -match "Results saved ->" }
    if ($savedLine) {
        $runJsonPath = ($savedLine -split "->")[1].Trim()
        if (Test-Path $runJsonPath) {
            $runJsonPaths += $runJsonPath
            Write-Host "  Captured: $runJsonPath" -ForegroundColor DarkGray
        }
    }

    # Allow GPU to settle between runs
    Write-Host ""
    Write-Host "  Waiting 15s for GPU memory to clear..." -ForegroundColor Yellow
    Start-Sleep -Seconds 15
}

# ---------------------------------------------------------------------------
# Step 2: evaluate.py over all enhanced_eval_* results
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host $BANNER                                                       -ForegroundColor Cyan
Write-Host "  STEP 2: evaluate.py (ROUGE) over all enhanced_eval_* results" -ForegroundColor Cyan
Write-Host $BANNER                                                       -ForegroundColor Cyan

$evalLog = "$LOG_DIR\enhanced_evaluate.log"
"" | Out-File -FilePath $evalLog -Encoding UTF8

& python evaluate.py --all --no-bert 2>&1 | ForEach-Object {
    Write-Host $_
    $_ | Out-File -FilePath $evalLog -Append -Encoding UTF8
}

if ($LASTEXITCODE -ne 0) {
    Write-Host "  evaluate.py returned non-zero -- check $evalLog" -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# Step 3: build_llm_judge_prompt.py (picks up latest run.json)
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host $BANNER                                                       -ForegroundColor Cyan
Write-Host "  STEP 3: Building LLM judge prompts"                       -ForegroundColor Cyan
Write-Host $BANNER                                                       -ForegroundColor Cyan

$judgeLog = "$LOG_DIR\enhanced_judge.log"
"" | Out-File -FilePath $judgeLog -Encoding UTF8

if ($runJsonPaths.Count -gt 0) {
    Write-Host "  Merging $($runJsonPaths.Count) run.json files into one judge prompt..." -ForegroundColor DarkGray
    $judgeArgs = @("build_llm_judge_prompt.py", "--runs") + $runJsonPaths
    & python $judgeArgs 2>&1 | ForEach-Object {
        Write-Host $_
        $_ | Out-File -FilePath $judgeLog -Append -Encoding UTF8
    }
} else {
    Write-Host "  No run.json paths captured -- falling back to latest run..." -ForegroundColor Yellow
    & python build_llm_judge_prompt.py 2>&1 | ForEach-Object {
        Write-Host $_
        $_ | Out-File -FilePath $judgeLog -Append -Encoding UTF8
    }
}

if ($LASTEXITCODE -ne 0) {
    Write-Host "  build_llm_judge_prompt.py returned non-zero -- check $judgeLog" -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host $BANNER                                                       -ForegroundColor Cyan
Write-Host "  ENHANCED INFERENCE -- ABLATION SUMMARY"                   -ForegroundColor Cyan
Write-Host $BANNER                                                       -ForegroundColor Cyan

foreach ($r in $results) {
    $col = if ($r.Status -eq "OK") { "Green" } else { "Red" }
    Write-Host "  $($r.Minutes) min  $($r.Status.PadRight(20))  $($r.Label)" -ForegroundColor $col
}

Write-Host ""
Write-Host "  Results    : evaluations\enhanced_eval_*\run.json"        -ForegroundColor Gray
Write-Host "  ROUGE log  : $evalLog"                                     -ForegroundColor Gray
Write-Host "  Judge log  : $judgeLog"                                    -ForegroundColor Gray
Write-Host ""
Write-Host "  Reference (4-bit _2 adapter baseline): best_val_loss=1.360" -ForegroundColor Gray
Write-Host "  Compare enhanced runs against baseline ROUGE + judge scores." -ForegroundColor Gray
Write-Host $BANNER                                                       -ForegroundColor Cyan
