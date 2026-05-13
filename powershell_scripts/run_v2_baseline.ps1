# run_v2_baseline.ps1
# Corrected _2 baseline using train_v2.py (apply_chat_template fix)
# Compatible with Windows PowerShell 5.1+
# Usage: cd C:\Personal_Endeavours\Fine_Tuning ; .\run_v2_baseline.ps1

$ErrorActionPreference = "Continue"
Set-Location "C:\Personal_Endeavours\Fine_Tuning"

$profile = @{
    Label = "v2 Baseline - _2 profile (r16, alpha32, lr=1e-4, cosine, patience=3) + template fix"
    Log   = "experiments\v2_baseline.log"
    Args  = @(
        "train_v2.py",
        "--quant",        "4bit",
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
        "--grad_accum",   "4",
        "--weight_decay", "0.01",
        "--patience",     "3",
        "--epochs",       "10",
        "--seed",         "42",
        "--max_length",   "320"
    )
}

$banner = "=" * 60

Write-Host ""
Write-Host $banner                                         -ForegroundColor Cyan
Write-Host "  $($profile.Label)"                          -ForegroundColor Cyan
Write-Host "  Log  : $($profile.Log)"                     -ForegroundColor Cyan
Write-Host "  Start: $(Get-Date -Format 'HH:mm:ss')"      -ForegroundColor Cyan
Write-Host $banner                                         -ForegroundColor Cyan

"" | Out-File -FilePath $profile.Log -Encoding UTF8

$runStart = Get-Date

& python $profile.Args 2>&1 | ForEach-Object {
    Write-Host $_
    $_ | Out-File -FilePath $profile.Log -Append -Encoding UTF8
}

$exit    = $LASTEXITCODE
$elapsed = (Get-Date) - $runStart
$mins    = [math]::Round($elapsed.TotalMinutes, 1)
$status  = if ($exit -eq 0) { "OK" } else { "FAILED (exit $exit)" }
$colour  = if ($exit -eq 0) { "Green" } else { "Red" }

Write-Host ""
Write-Host $banner                                         -ForegroundColor $colour
Write-Host "  Finished in $mins min -- $status"           -ForegroundColor $colour
Write-Host "  Log saved -> $($profile.Log)"               -ForegroundColor $colour
Write-Host $banner                                         -ForegroundColor $colour
