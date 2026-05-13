# run_phase1_rag.ps1
# Phase 1 inference evaluation -- BM25 RAG isolated test
# =======================================================
#
# Runs enhanced_inference.py with BM25 RAG (T5) as the ONLY active technique.
# T2, T4, T6 are all OFF so the delta is attributable to retrieval alone.
#
# Design decisions (4-LLM expert consensus, May 2026):
#   - BM25 preferred over dense retrieval for short medical keyword queries
#   - Hard 1-example limit, 150-token cap (in bm25_rag.py)
#   - Gap-question gate: Q6/Q17/Q21/Q22/Q28 skipped automatically
#     (retrieval is actively dangerous for these protocol-gap questions)
#
# This script runs two configurations:
#   Phase1-A  BM25 RAG only (baseline for comparison)
#   Phase1-B  BM25 RAG + T2 greedy SC (ready for Phase 2 combination)
#
# After both runs, evaluate.py and build_llm_judge_prompt.py are called
# automatically so results are ready for LLM judge scoring.
#
# Prerequisites:
#   conda activate fine_tuning
#   pip install rank_bm25 --break-system-packages
#
# Usage:
#   conda activate fine_tuning
#   cd C:\Personal_Endeavours\Fine_Tuning
#   .\run_phase1_rag.ps1
#
# To run only one configuration, comment out the other entry in $runs.

$ErrorActionPreference = "Continue"
Set-Location "C:\Personal_Endeavours\Fine_Tuning"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

$ADAPTER_PATH   = "experiments\10cat_4bit_r16_lr1e-4_p3_v2_20260508_054337\adapter"
$QUESTIONS_FILE = "data\eval_questions_40.json"
$MAX_TOKENS     = 250
$LOG_DIR        = "experiments"
$BANNER         = "=" * 60

# ---------------------------------------------------------------------------
# Smoke-test BM25 retriever before loading the GPU model
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host $BANNER                                                       -ForegroundColor Cyan
Write-Host "  PHASE 1 -- BM25 RAG Smoke Test"                          -ForegroundColor Cyan
Write-Host $BANNER                                                       -ForegroundColor Cyan

$smokeOutput = & python bm25_rag.py 2>&1
$smokeOutput | ForEach-Object { Write-Host $_ }

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "  ERROR: bm25_rag.py smoke test failed." -ForegroundColor Red
    Write-Host "  Check that rank_bm25 is installed:" -ForegroundColor Red
    Write-Host "    pip install rank_bm25 --break-system-packages" -ForegroundColor Yellow
    exit 1
}

# Verify gap gate fired correctly (Q6/Q17/Q21/Q22/Q28 should all show GATED)
$gatedCount = ($smokeOutput | Where-Object { $_ -match "GATED" }).Count
if ($gatedCount -lt 5) {
    Write-Host ""
    Write-Host "  WARNING: Expected 5 GATED lines, got $gatedCount." -ForegroundColor Yellow
    Write-Host "  Gap gate may not be working correctly." -ForegroundColor Yellow
} else {
    Write-Host ""
    Write-Host "  Smoke test PASSED: $gatedCount gap questions correctly gated." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Run definitions
# ---------------------------------------------------------------------------

$runs = @(
    @{
        Label     = "Phase1-A -- BM25 RAG only (T5 ON, T2+T4+T6 OFF)"
        Log       = "$LOG_DIR\phase1_A_bm25_only.log"
        ExtraArgs = @("--no_greedy_sc", "--no_min_tokens", "--no_two_pass",
                      "--rag_retriever", "bm25",
                      "--show_rag_context")
    },
    @{
        Label     = "Phase1-B -- BM25 RAG + T2 greedy (T5+T2 ON, T4+T6 OFF)"
        Log       = "$LOG_DIR\phase1_B_bm25_t2.log"
        ExtraArgs = @("--no_min_tokens", "--no_two_pass",
                      "--rag_retriever", "bm25",
                      "--show_rag_context")
    }
)

# ---------------------------------------------------------------------------
# Run each configuration
# ---------------------------------------------------------------------------

$results      = @()
$runJsonPaths = @()

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
        "--adapter_path",   $ADAPTER_PATH,
        "--questions_file", $QUESTIONS_FILE,
        "--max_new_tokens", $MAX_TOKENS
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
    Write-Host "  Log -> $($run.Log)"                                    -ForegroundColor $colour
    Write-Host $BANNER                                                   -ForegroundColor $colour

    $results += [PSCustomObject]@{
        Label   = $run.Label
        Status  = $status
        Minutes = $mins
        Log     = $run.Log
    }

    # Capture run.json path for judge prompt
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
# Step 2: ROUGE evaluation
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host $BANNER                                                       -ForegroundColor Cyan
Write-Host "  STEP 2: evaluate.py (ROUGE) over Phase 1 results"        -ForegroundColor Cyan
Write-Host $BANNER                                                       -ForegroundColor Cyan

$evalLog = "$LOG_DIR\phase1_evaluate.log"
"" | Out-File -FilePath $evalLog -Encoding UTF8

& python evaluate.py --all --no-bert 2>&1 | ForEach-Object {
    Write-Host $_
    $_ | Out-File -FilePath $evalLog -Append -Encoding UTF8
}

# ---------------------------------------------------------------------------
# Step 3: LLM judge prompt
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host $BANNER                                                       -ForegroundColor Cyan
Write-Host "  STEP 3: Building LLM judge prompt"                        -ForegroundColor Cyan
Write-Host $BANNER                                                       -ForegroundColor Cyan

$judgeLog = "$LOG_DIR\phase1_judge.log"
"" | Out-File -FilePath $judgeLog -Encoding UTF8

if ($runJsonPaths.Count -gt 0) {
    Write-Host "  Merging $($runJsonPaths.Count) run.json files..." -ForegroundColor DarkGray
    $judgeArgs = @("build_llm_judge_prompt.py", "--runs") + $runJsonPaths
    & python $judgeArgs 2>&1 | ForEach-Object {
        Write-Host $_
        $_ | Out-File -FilePath $judgeLog -Append -Encoding UTF8
    }
} else {
    Write-Host "  No run.json paths captured -- falling back to latest run." -ForegroundColor Yellow
    & python build_llm_judge_prompt.py 2>&1 | ForEach-Object {
        Write-Host $_
        $_ | Out-File -FilePath $judgeLog -Append -Encoding UTF8
    }
}

# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host $BANNER                                                       -ForegroundColor Cyan
Write-Host "  PHASE 1 -- BM25 RAG -- SUMMARY"                          -ForegroundColor Cyan
Write-Host $BANNER                                                       -ForegroundColor Cyan

foreach ($r in $results) {
    $col = if ($r.Status -eq "OK") { "Green" } else { "Red" }
    Write-Host "  $($r.Minutes) min  $($r.Status.PadRight(20))  $($r.Label)" -ForegroundColor $col
}

Write-Host ""
Write-Host "  Results      : evaluations\enhanced_eval_*\run.json"      -ForegroundColor Gray
Write-Host "  ROUGE log    : $evalLog"                                   -ForegroundColor Gray
Write-Host "  Judge log    : $judgeLog"                                  -ForegroundColor Gray
Write-Host ""
Write-Host "  Baseline reference (4-bit V2, standard inference, DeepSeek):" -ForegroundColor Gray
Write-Host "    Mean score  : 2.18 / 5.00"                              -ForegroundColor Gray
Write-Host "    SC mean     : 1.61 / 5.00"                              -ForegroundColor Gray
Write-Host "    Dangerous penalties: 3 / 40"                            -ForegroundColor Gray
Write-Host ""
Write-Host "  What to check in the judge results:"                      -ForegroundColor Gray
Write-Host "    1. SC mean delta for NON-GAP questions (should improve)" -ForegroundColor Gray
Write-Host "    2. SC mean for GAP questions Q6/Q17/Q21/Q22/Q28"       -ForegroundColor Gray
Write-Host "       (should be unchanged -- gate is working)"            -ForegroundColor Gray
Write-Host "    3. T5 gap-gated count in each log (should be 5)"       -ForegroundColor Gray
Write-Host $BANNER                                                       -ForegroundColor Cyan
