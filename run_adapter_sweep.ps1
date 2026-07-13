# run_adapter_sweep.ps1
# =====================
# Full combinatorial sweep: (base_quant × adapter × inference_technique).
#
# For every adapter in the inventory this script runs a set of configs that
# compare it against the no-adapter baseline at the same quantisation level
# and across every inference technique (greedy, T4, T6, BM25 RAG).
#
# ── COMBINATION MATRIX ─────────────────────────────────────────────────────
#
# 4-bit adapters  (8 adapters + canonical)
#   Each 4-bit adapter produces one eval run with configs:
#     A  = base 4-bit, no adapter, greedy       (same-quant baseline)
#     G  = base 4-bit, no adapter, BM25
#     B  = base 4-bit + THIS adapter, greedy
#     D  = base 4-bit + THIS adapter, T4
#     E  = base 4-bit + THIS adapter, T6
#     F  = base 4-bit + THIS adapter, BM25
#   → 6 configs × 9 adapters = 54 combinations (includes camera-ready B/D/E/F)
#
# 8-bit adapter  (1 adapter -- canonical 8-bit training run)
#   Produces one run with configs:
#     H  = base 8-bit, no adapter, greedy       (8-bit baseline)
#     O  = base 8-bit, no adapter, BM25
#     R  = base 8-bit + THIS adapter, greedy
#   → 3 configs × 1 adapter = 3 combinations
#
# Cross-quant runs (canonical 4-bit adapter on 8-bit base)
#   Produces one run with configs:
#     H  = base 8-bit, no adapter, greedy
#     O  = base 8-bit, no adapter, BM25
#     C  = base 8-bit + canonical 4-bit adapter, greedy
#     J  = base 8-bit + canonical 4-bit adapter, T4
#     L  = base 8-bit + canonical 4-bit adapter, T6
#     N  = base 8-bit + canonical 4-bit adapter, BM25
#   → 6 configs, 1 run (camera-ready C already captured in main eval)
#
# fp16 base  (canonical 4-bit adapter only -- no fine-tuned fp16 model)
#   Produces one run with configs:
#     P  = fp16 base, no adapter, greedy
#     Y  = fp16 base, no adapter, BM25
#     Q  = fp16 base + canonical 4-bit adapter, greedy
#     T  = fp16 base + canonical 4-bit adapter, T4
#     V  = fp16 base + canonical 4-bit adapter, T6
#     X  = fp16 base + canonical 4-bit adapter, BM25
#   → 6 configs, 1 run  (VRAM warning: fp16 base ~5 GB)
#
# ── USAGE ──────────────────────────────────────────────────────────────────
#
#   cd C:\Personal_Endeavours\Fine_Tuning
#   .\run_adapter_sweep.ps1                    # full sweep
#   .\run_adapter_sweep.ps1 -SkipExisting      # skip labels with existing SWEEP_ dir
#   .\run_adapter_sweep.ps1 -Only r32          # substring match on label
#   .\run_adapter_sweep.ps1 -Groups 4bit       # only 4-bit adapter runs
#   .\run_adapter_sweep.ps1 -Groups 8bit       # only 8-bit adapter run
#   .\run_adapter_sweep.ps1 -Groups crossquant # 8-bit base + 4-bit adapter
#   .\run_adapter_sweep.ps1 -Groups fp16       # fp16 base runs
#   .\run_adapter_sweep.ps1 -Groups 4bit,fp16  # comma-separated combination
#
# ── OUTPUT ─────────────────────────────────────────────────────────────────
#
#   evaluations\SWEEP_<label>_<timestamp>\
#     run.json, metrics.json, <CONFIG>.json ...
#   evaluations\adapter_sweep_summary.json   (written after all runs)
#
# ── NEXT STEPS ─────────────────────────────────────────────────────────────
#
#   Judge a single sweep run (all configs, chunked into groups of 4):
#     python build_v2_judge_prompt.py \
#       --run_dir evaluations\SWEEP_<label>_<ts> --group 4
#
#   After all sweeps complete, compare across runs using judge_per_item.py.

[CmdletBinding()]
param(
    [switch]$SkipExisting,
    [string]$Only   = "",
    [string]$Groups = "4bit,8bit,crossquant,fp16"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ROOT  = $PSScriptRoot
$EVAL  = Join-Path $ROOT "evaluations"
$MODEL = Join-Path $ROOT "models\gemma-2b-it"
$QBANK = Join-Path $ROOT "evaluations\eval_bank_v2_40q\eval_bank_v2.json"

$GroupsRequested = $Groups -split "," | ForEach-Object { $_.Trim().ToLower() }

# ---------------------------------------------------------------------------
# Adapter inventory
# ---------------------------------------------------------------------------
$ADAPTERS_4BIT = @(
    [pscustomobject]@{
        Label  = "4bit_r16_lr1e-4_p3_v2"
        ExpDir = "10cat_4bit_r16_lr1e-4_p3_v2_20260508_054337"
        Notes  = "CANONICAL -- camera-ready adapter (v2 training)"
    },
    [pscustomobject]@{
        Label  = "4bit_r16_lr1e-4_p5"
        ExpDir = "10cat_4bit_r16_lr1e-4_p5_20260506_192538"
        Notes  = "r16 lr=1e-4 5 epochs"
    },
    [pscustomobject]@{
        Label  = "4bit_r16_lr1e4_p3"
        ExpDir = "10cat_4bit_r16_lr1e4_p3_20260506_012852"
        Notes  = "r16 lr=1 (high LR) 3 epochs"
    },
    [pscustomobject]@{
        Label  = "4bit_r16_lr4e-4_p6"
        ExpDir = "10cat_4bit_r16_lr4e-4_p6_20260506_211729"
        Notes  = "r16 lr=4e-4 6 epochs"
    },
    [pscustomobject]@{
        Label  = "4bit_r32_lr1e-4_p6"
        ExpDir = "10cat_4bit_r32_lr1e-4_p6_20260507_160543"
        Notes  = "r32 double rank 6 epochs"
    },
    [pscustomobject]@{
        Label  = "4bit_r32_lr4e-4_p8"
        ExpDir = "10cat_4bit_r32_lr4e-4_p8_20260507_195206"
        Notes  = "r32 lr=4e-4 8 epochs"
    },
    [pscustomobject]@{
        Label  = "4bit_r8_lr1e-4_p8"
        ExpDir = "10cat_4bit_r8_lr1e-4_p8_20260507_174631"
        Notes  = "r8 half rank 8 epochs"
    },
    [pscustomobject]@{
        Label  = "4bit_r8_lr1e4_p3"
        ExpDir = "10cat_4bit_r8_lr1e4_p3_20260506_012852"
        Notes  = "r8 lr=1 (high LR) 3 epochs"
    }
)

# Canonical adapter paths (used for cross-quant and fp16 runs)
$CANONICAL_4BIT_DIR = Join-Path $ROOT "experiments\10cat_4bit_r16_lr1e-4_p3_v2_20260508_054337\adapter"
$CANONICAL_8BIT_DIR = Join-Path $ROOT "experiments\10cat_8bit_r16_lr1e-4_p3_20260508_195536\adapter"

# ---------------------------------------------------------------------------
# Helper: run one group of configs, return result object
# ---------------------------------------------------------------------------
function Invoke-EvalRun {
    param(
        [string]   $RunLabel,
        [string[]] $Configs,
        [string]   $Adapter4Bit,
        [string]   $Adapter8Bit,
        [string]   $Notes
    )

    Write-Host ""
    Write-Host ("  Label   : $RunLabel") -ForegroundColor Cyan
    Write-Host ("  Configs : $($Configs -join ' ')") -ForegroundColor Gray
    Write-Host ("  Notes   : $Notes") -ForegroundColor Gray

    # Skip if -SkipExisting and a SWEEP_ dir already exists for this label
    if ($SkipExisting) {
        $existing = @(Get-ChildItem -Path $EVAL -Filter "SWEEP_${RunLabel}_*" -Directory -EA SilentlyContinue)
        if ($existing.Count -gt 0) {
            Write-Host ("  SKIP -- existing: $($existing[0].Name)") -ForegroundColor DarkYellow
            return [pscustomobject]@{
                Label   = $RunLabel
                Configs = ($Configs -join " ")
                RunDir  = $existing[0].Name
                Skipped = $true
                Status  = "skipped"
                Notes   = $Notes
            }
        }
    }

    $beforeDirs = @(Get-ChildItem -Path $EVAL -Filter "SWEEP_*" -Directory -EA SilentlyContinue | Select-Object -ExpandProperty Name)

    python v2_comprehensive_eval.py `
        --adapter_4bit $Adapter4Bit `
        --adapter_8bit $Adapter8Bit `
        --model_path   $MODEL `
        --questions    $QBANK `
        --configs      @Configs `
        --max_new_tokens 350 `
        --sweep_label  $RunLabel

    $exitCode = $LASTEXITCODE

    if ($exitCode -ne 0) {
        Write-Host ("  ERROR: eval failed (exit $exitCode)") -ForegroundColor Red
        return [pscustomobject]@{
            Label   = $RunLabel
            Configs = ($Configs -join " ")
            RunDir  = "FAILED"
            Skipped = $false
            Status  = "failed"
            Notes   = $Notes
        }
    }

    $afterDirs  = @(Get-ChildItem -Path $EVAL -Filter "SWEEP_*" -Directory -EA SilentlyContinue | Select-Object -ExpandProperty Name)
    $newDirs    = $afterDirs | Where-Object { $beforeDirs -notcontains $_ }
    $runDirName = if ($newDirs.Count -gt 0) { ($newDirs | Sort-Object -Descending)[0] } else { "UNKNOWN" }

    Write-Host ("  OK  → $runDirName") -ForegroundColor Green
    return [pscustomobject]@{
        Label   = $RunLabel
        Configs = ($Configs -join " ")
        RunDir  = $runDirName
        Skipped = $false
        Status  = "ok"
        Notes   = $Notes
    }
}

# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  FULL COMBINATION SWEEP -- v2 eval bank" -ForegroundColor Cyan
Write-Host "  Groups: $Groups" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan

Write-Host ""
Write-Host "[pre] Syntax check..." -ForegroundColor Yellow
python -c "import ast; ast.parse(open('v2_comprehensive_eval.py', encoding='utf-8').read()); print('OK')"
if ($LASTEXITCODE -ne 0) { Write-Host "SYNTAX ERROR -- aborting." -ForegroundColor Red; exit 1 }
Write-Host "  OK" -ForegroundColor Green

if (-not (Test-Path $MODEL)) {
    Write-Host "ERROR: model not found: $MODEL" -ForegroundColor Red; exit 1
}
if (-not (Test-Path $QBANK)) {
    Write-Host "ERROR: question bank not found: $QBANK" -ForegroundColor Red; exit 1
}
if (-not (Test-Path $CANONICAL_4BIT_DIR)) {
    Write-Host "ERROR: canonical 4-bit adapter not found: $CANONICAL_4BIT_DIR" -ForegroundColor Red; exit 1
}
if (-not (Test-Path $CANONICAL_8BIT_DIR)) {
    Write-Host "ERROR: canonical 8-bit adapter not found: $CANONICAL_8BIT_DIR" -ForegroundColor Red; exit 1
}

$results = [System.Collections.Generic.List[object]]::new()
$runIndex = 0

# ===========================================================================
# GROUP: 4-bit adapters
# Configs per run: A G (4-bit base, no adapter) + B D E F (this adapter)
# ===========================================================================
if ("4bit" -in $GroupsRequested) {
    Write-Host ""
    Write-Host "----------------------------------------------------------------" -ForegroundColor Cyan
    Write-Host "  GROUP: 4-bit adapter runs  (9 adapters x 6 configs each)" -ForegroundColor Cyan
    Write-Host "----------------------------------------------------------------" -ForegroundColor Cyan

    $filtered = $ADAPTERS_4BIT
    if ($Only -ne "") {
        $filtered = $filtered | Where-Object { $_.Label -like "*$Only*" }
    }
    if ($filtered.Count -eq 0) {
        Write-Host "  No 4-bit adapters match -Only '$Only'" -ForegroundColor DarkYellow
    }

    foreach ($adp in $filtered) {
        $runIndex++
        $adapterDir = Join-Path $ROOT "experiments\$($adp.ExpDir)\adapter"

        if (-not (Test-Path $adapterDir)) {
            Write-Host "[$runIndex] SKIP $($adp.Label) -- adapter dir not found: $adapterDir" -ForegroundColor DarkYellow
            $results.Add([pscustomobject]@{
                Label   = $adp.Label
                Configs = "A G B D E F"
                RunDir  = "MISSING_ADAPTER"
                Skipped = $false
                Status  = "missing"
                Notes   = $adp.Notes
            })
            continue
        }

        Write-Host ""
        Write-Host "[$runIndex]" -NoNewline -ForegroundColor White
        $r = Invoke-EvalRun `
            -RunLabel  $adp.Label `
            -Configs   @("A","G","B","D","E","F") `
            -Adapter4Bit $adapterDir `
            -Adapter8Bit $CANONICAL_8BIT_DIR `
            -Notes     $adp.Notes
        $results.Add($r)
    }
}

# ===========================================================================
# GROUP: 8-bit adapter
# Configs: H O (8-bit base, no adapter) + R (8-bit base + 8-bit adapter)
# ===========================================================================
if ("8bit" -in $GroupsRequested) {
    Write-Host ""
    Write-Host "----------------------------------------------------------------" -ForegroundColor Cyan
    Write-Host "  GROUP: 8-bit adapter run  (1 adapter x 3 configs)" -ForegroundColor Cyan
    Write-Host "----------------------------------------------------------------" -ForegroundColor Cyan

    if ($Only -eq "" -or "8bit_r16_lr1e-4_p3" -like "*$Only*") {
        $runIndex++
        Write-Host "[$runIndex]" -NoNewline -ForegroundColor White
        $r = Invoke-EvalRun `
            -RunLabel  "8bit_r16_lr1e-4_p3" `
            -Configs   @("H","O","R") `
            -Adapter4Bit $CANONICAL_4BIT_DIR `
            -Adapter8Bit $CANONICAL_8BIT_DIR `
            -Notes     "8-bit trained adapter on 8-bit base (greedy only)"
        $results.Add($r)
    }
}

# ===========================================================================
# GROUP: Cross-quant -- 8-bit base + canonical 4-bit adapter
# Configs: H O (8-bit base, no adapter) + C I (greedy) J (T4) L (T6) N (RAG)
# Note: C and I are the same computation; request C for camera-ready label.
# ===========================================================================
if ("crossquant" -in $GroupsRequested) {
    Write-Host ""
    Write-Host "----------------------------------------------------------------" -ForegroundColor Cyan
    Write-Host "  GROUP: cross-quant  (8-bit base + 4-bit adapter, 6 configs)" -ForegroundColor Cyan
    Write-Host "----------------------------------------------------------------" -ForegroundColor Cyan

    if ($Only -eq "" -or "crossquant_4bit_adapter" -like "*$Only*") {
        $runIndex++
        Write-Host "[$runIndex]" -NoNewline -ForegroundColor White
        $r = Invoke-EvalRun `
            -RunLabel  "crossquant_8bitbase_4bitadapter" `
            -Configs   @("H","O","C","J","L","N") `
            -Adapter4Bit $CANONICAL_4BIT_DIR `
            -Adapter8Bit $CANONICAL_8BIT_DIR `
            -Notes     "8-bit base + canonical 4-bit adapter -- all techniques"
        $results.Add($r)
    }
}

# ===========================================================================
# GROUP: fp16 base (no fine-tuned fp16 -- adapter still in 4-bit)
# Configs: P Y (fp16 base, no adapter) + Q T V X (fp16 base + 4-bit adapter)
# VRAM WARNING: fp16 base is ~5 GB -- run only if >=6 GB free VRAM.
# ===========================================================================
if ("fp16" -in $GroupsRequested) {
    Write-Host ""
    Write-Host "----------------------------------------------------------------" -ForegroundColor Cyan
    Write-Host "  GROUP: fp16 base runs  (6 configs; VRAM: ~5 GB)" -ForegroundColor Cyan
    Write-Host "----------------------------------------------------------------" -ForegroundColor Cyan

    if ($Only -eq "" -or "fp16_base" -like "*$Only*") {
        $runIndex++
        Write-Host "[$runIndex]" -NoNewline -ForegroundColor White
        $r = Invoke-EvalRun `
            -RunLabel  "fp16_base_all_techniques" `
            -Configs   @("P","Y","Q","T","V","X") `
            -Adapter4Bit $CANONICAL_4BIT_DIR `
            -Adapter8Bit $CANONICAL_8BIT_DIR `
            -Notes     "fp16 base (no quant): no-adapter baseline + 4-bit adapter, all techniques"
        $results.Add($r)
    }
}

# ===========================================================================
# Summary
# ===========================================================================
Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  SWEEP SUMMARY  ($($results.Count) runs)" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host ("  {0,-38} {1,-16} {2,-10}  {3}" -f "Label","Configs","Status","Notes")
Write-Host ("  " + "-" * 90)

foreach ($r in $results) {
    $color = switch ($r.Status) {
        "ok"      { "Green" }
        "skipped" { "DarkYellow" }
        "failed"  { "Red" }
        default   { "Gray" }
    }
    $notesShort = if ($r.Notes.Length -gt 35) { $r.Notes.Substring(0,35) } else { $r.Notes }
    Write-Host ("  {0,-38} {1,-16} {2,-10}  {3}" -f `
        $r.Label, $r.Configs, $r.Status, $notesShort) -ForegroundColor $color
}

$okCount      = ($results | Where-Object { $_.Status -eq "ok" }).Count
$failCount    = ($results | Where-Object { $_.Status -eq "failed" }).Count
$skipCount    = ($results | Where-Object { $_.Status -eq "skipped" }).Count
$missingCount = ($results | Where-Object { $_.Status -eq "missing" }).Count

Write-Host ""
Write-Host ("  OK={0}  failed={1}  skipped={2}  missing_adapter={3}" -f `
    $okCount, $failCount, $skipCount, $missingCount) -ForegroundColor Cyan

$summaryPath = Join-Path $EVAL "adapter_sweep_summary.json"
$results | ConvertTo-Json -Depth 4 | Set-Content -Path $summaryPath -Encoding UTF8
Write-Host ""
Write-Host "  Summary written: $summaryPath" -ForegroundColor Gray

# ---------------------------------------------------------------------------
# Next steps
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  NEXT STEPS" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Judge any sweep run (chunked to stay within context window):" -ForegroundColor White
Write-Host "    python build_v2_judge_prompt.py \" -ForegroundColor Gray
Write-Host "      --run_dir evaluations\SWEEP_<label>_<ts> --group 4" -ForegroundColor Gray
Write-Host ""
Write-Host "  Per-item scoring (after judge prompts are filled in):" -ForegroundColor White
Write-Host "    python judge_per_item.py --run_dir evaluations\SWEEP_<label>_<ts>" -ForegroundColor Gray
Write-Host ""
Write-Host "  Statistical comparison across all runs:" -ForegroundColor White
Write-Host "    python stats_v2.py" -ForegroundColor Gray
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""
