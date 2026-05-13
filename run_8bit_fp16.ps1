# run_8bit_fp16.ps1
# 8-bit and FP16 LoRA training runs for cross-precision comparison
# Uses train.py (NOT train_v2.py) -- identical procedure to _2 adapter for clean comparison
# Compatible with Windows PowerShell 5.1+
# Usage: conda activate fine_tuning ; cd C:\Personal_Endeavours\Fine_Tuning ; .\run_8bit_fp16.ps1
#
# ============================================================
# PRE-FLIGHT AUDIT (verified against _2 adapter train.log)
# ============================================================
# _2 adapter confirmed params:
#   quant=4bit  lr=1e-4  lora_r=16  lora_alpha=32  lora_dropout=0.05
#   patience=3  max_grad_norm=1.0  lr_scheduler=cosine  warmup_ratio=0.03
#   weight_decay=0.01  seed=42  max_length=320  epochs=10 (stopped @2.879)
#   batch_size=2  grad_accum=4  effective_batch=8
#   best_val_loss=1.360 @ epoch 1.800   peak_VRAM=9841 MB
#
# VRAM ESTIMATES (8-bit and FP16 use batch_size=1 to reduce activation pressure):
#   4-bit  batch=2 grad_accum=4 -> measured 9841 MB (reference)
#   8-bit  batch=1 grad_accum=8 -> estimated ~12000-14000 MB (model 2x, paged_adamw_8bit)
#   FP16   batch=1 grad_accum=8 -> estimated ~18000-21000 MB (fp32 Adam states, adamw_torch)
#
# Effective batch is preserved at 8 for both runs (batch*accum = 1*8 = 8).
# FP16 may OOM depending on GPU VRAM. If it does, log the failure -- it is a
# valid paper result: QLoRA is necessary for training on this hardware.
# ============================================================

$ErrorActionPreference = "Continue"
Set-Location "C:\Personal_Endeavours\Fine_Tuning"

$BANNER = "=" * 60

# ---------------------------------------------------------------------------
# Profile definitions
# ---------------------------------------------------------------------------

$profiles = @(
    @{
        Label    = "8-bit LoRA  (r16, alpha32, lr=1e-4, cosine, patience=3)"
        Log      = "experiments\8bit_run.log"
        Args     = @(
            "train.py",
            "--quant",        "8bit",
            "--model_path",   "models\gemma-2b-it",
            "--splits_dir",   "splits\10cat",
            "--splits_tag",   "10cat",
            "--lora_r",       "16",
            "--lora_alpha",   "32",
            "--lora_dropout", "0.05",
            "--lr",           "1e-4",
            "--max_grad_norm","1.0",
            "--lr_scheduler", "cosine",
            "--warmup_ratio", "0.03",
            "--batch_size",   "1",       # reduced from 2: 8-bit model is 2x larger
            "--grad_accum",   "8",       # doubled: preserves effective batch=8 (1x8=8)
            "--weight_decay", "0.01",
            "--patience",     "3",
            "--epochs",       "10",
            "--seed",         "42",
            "--max_length",   "320"
        )
    },
    @{
        Label    = "FP16 LoRA   (r16, alpha32, lr=1e-4, cosine, patience=3)"
        Log      = "experiments\fp16_run.log"
        Args     = @(
            "train.py",
            "--quant",        "fp16",
            "--model_path",   "models\gemma-2b-it",
            "--splits_dir",   "splits\10cat",
            "--splits_tag",   "10cat",
            "--lora_r",       "16",
            "--lora_alpha",   "32",
            "--lora_dropout", "0.05",
            "--lr",           "1e-4",
            "--max_grad_norm","1.0",
            "--lr_scheduler", "cosine",
            "--warmup_ratio", "0.03",
            "--batch_size",   "1",       # reduced from 2: full fp16 model ~5.5 GB
            "--grad_accum",   "8",       # doubled: preserves effective batch=8 (1x8=8)
            "--weight_decay", "0.01",
            "--patience",     "3",
            "--epochs",       "10",
            "--seed",         "42",
            "--max_length",   "320"
        )
    }
)

# ---------------------------------------------------------------------------
# Run each profile sequentially
# ---------------------------------------------------------------------------

$results = @()

foreach ($p in $profiles) {

    "" | Out-File -FilePath $p.Log -Encoding UTF8

    Write-Host ""
    Write-Host $BANNER                                               -ForegroundColor Cyan
    Write-Host "  $($p.Label)"                                      -ForegroundColor Cyan
    Write-Host "  Log  : $($p.Log)"                                 -ForegroundColor Cyan
    Write-Host "  Start: $(Get-Date -Format 'HH:mm:ss')"            -ForegroundColor Cyan
    Write-Host $BANNER                                              -ForegroundColor Cyan

    $runStart = Get-Date

    & python $p.Args 2>&1 | ForEach-Object {
        Write-Host $_
        $_ | Out-File -FilePath $p.Log -Append -Encoding UTF8
    }

    $exit    = $LASTEXITCODE
    $elapsed = (Get-Date) - $runStart
    $mins    = [math]::Round($elapsed.TotalMinutes, 1)
    $status  = if ($exit -eq 0) { "OK" } else { "FAILED (exit $exit)" }
    $colour  = if ($exit -eq 0) { "Green" } else { "Red" }

    Write-Host ""
    Write-Host $BANNER                                              -ForegroundColor $colour
    Write-Host "  $($p.Label)"                                      -ForegroundColor $colour
    Write-Host "  Finished in $mins min -- $status"                 -ForegroundColor $colour
    Write-Host "  Log saved -> $($p.Log)"                           -ForegroundColor $colour
    Write-Host $BANNER                                              -ForegroundColor $colour

    $results += [PSCustomObject]@{
        Label   = $p.Label
        Status  = $status
        Minutes = $mins
        Log     = $p.Log
    }

    # Flush GPU memory between runs
    Write-Host ""
    Write-Host "  Waiting 15s for GPU memory to clear before next run..." -ForegroundColor Yellow
    Start-Sleep -Seconds 15
}

# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host $BANNER                                                  -ForegroundColor Cyan
Write-Host "  SUMMARY"                                              -ForegroundColor Cyan
Write-Host $BANNER                                                  -ForegroundColor Cyan

foreach ($r in $results) {
    $col = if ($r.Status -eq "OK") { "Green" } else { "Red" }
    Write-Host "  $($r.Minutes) min  $($r.Status.PadRight(20))  $($r.Label)" -ForegroundColor $col
}

Write-Host ""
Write-Host "  Reference  (4-bit _2 adapter) : best_val_loss=1.360 @ epoch 1.800"  -ForegroundColor Gray
Write-Host "  Compare new adapter logs with the above to assess precision impact."  -ForegroundColor Gray
Write-Host $BANNER                                                  -ForegroundColor Cyan
