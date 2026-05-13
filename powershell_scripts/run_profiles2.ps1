# run_profiles.ps1
# Compatible with Windows PowerShell 5.1+
# Usage: cd C:\Personal_Endeavours\Fine_Tuning ; .\run_profiles.ps1

$ErrorActionPreference = "Continue"
Set-Location "C:\Personal_Endeavours\Fine_Tuning"

$profiles = @(
    @{
        Label = "Profile 3 - Capacity at True LR (r32, no clip)"
        Log   = "experiments\profile3_capacity.log"
        Args  = @(
            "train.py",
            "--quant",        "4bit",
            "--model_path",   "models\gemma-2b-it",
            "--splits_dir",   "splits\10cat",
            "--splits_tag",   "10cat",
            "--lora_r",       "32",
            "--lora_alpha",   "64",
            "--lora_dropout", "0.05",
            "--lr",           "1e-4",
            "--max_grad_norm","10.0",
            "--lr_scheduler", "cosine",
            "--warmup_ratio", "0.03",
            "--grad_accum",   "4",
            "--weight_decay", "0.01",
            "--patience",     "6",
            "--epochs",       "10",
            "--seed",         "42"
        )
    },
    @{
        Label = "Profile 4 - Regularised Compression (r8/8, heavy reg)"
        Log   = "experiments\profile4_compress.log"
        Args  = @(
            "train.py",
            "--quant",        "4bit",
            "--model_path",   "models\gemma-2b-it",
            "--splits_dir",   "splits\10cat",
            "--splits_tag",   "10cat",
            "--lora_r",       "8",
            "--lora_alpha",   "8",
            "--lora_dropout", "0.15",
            "--lr",           "1e-4",
            "--max_grad_norm","1.0",
            "--lr_scheduler", "cosine",
            "--warmup_ratio", "0.03",
            "--grad_accum",   "4",
            "--weight_decay", "0.10",
            "--patience",     "8",
            "--epochs",       "12",
            "--seed",         "42"
        )
    },
    @{
        Label = "Profile 5 - Full Synthesis (r32/32, calibrated, linear, big batch)"
        Log   = "experiments\profile5_synthesis.log"
        Args  = @(
            "train.py",
            "--quant",        "4bit",
            "--model_path",   "models\gemma-2b-it",
            "--splits_dir",   "splits\10cat",
            "--splits_tag",   "10cat",
            "--lora_r",       "32",
            "--lora_alpha",   "32",
            "--lora_dropout", "0.10",
            "--lr",           "4e-4",
            "--max_grad_norm","0.3",
            "--lr_scheduler", "linear",
            "--warmup_ratio", "0.10",
            "--grad_accum",   "8",
            "--weight_decay", "0.05",
            "--patience",     "8",
            "--epochs",       "12",
            "--seed",         "42"
        )
    }
)

$totalStart = Get-Date
$results    = @()

for ($i = 0; $i -lt $profiles.Count; $i++) {
    $p      = $profiles[$i]
    $num    = $i + 1
    $banner = "=" * 60

    Write-Host ""
    Write-Host $banner                                    -ForegroundColor Cyan
    Write-Host "  [$num/5] $($p.Label)"                  -ForegroundColor Cyan
    Write-Host "  Log  : $($p.Log)"                      -ForegroundColor Cyan
    Write-Host "  Start: $(Get-Date -Format 'HH:mm:ss')" -ForegroundColor Cyan
    Write-Host $banner                                    -ForegroundColor Cyan

    # Initialise log file (UTF-8, overwrite if exists)
    "" | Out-File -FilePath $p.Log -Encoding UTF8

    $runStart = Get-Date

    # PS 5.1-compatible: stream output live to console AND append to log
    & python $p.Args 2>&1 | ForEach-Object {
        Write-Host $_
        $_ | Out-File -FilePath $p.Log -Append -Encoding UTF8
    }

    $exit    = $LASTEXITCODE
    $elapsed = (Get-Date) - $runStart
    $mins    = [math]::Round($elapsed.TotalMinutes, 1)
    $status  = if ($exit -eq 0) { "OK" } else { "FAILED (exit $exit)" }

    $results += [PSCustomObject]@{
        Profile = "[$num] $($p.Label)"
        Time    = "${mins} min"
        Status  = $status
    }

    $colour = if ($exit -eq 0) { "Green" } else { "Red" }
    Write-Host ""
    Write-Host "  Finished in $mins min -- $status" -ForegroundColor $colour
}

$totalMins = [math]::Round(((Get-Date) - $totalStart).TotalMinutes, 1)
Write-Host ""
Write-Host ("=" * 60) -ForegroundColor Cyan
Write-Host "  ALL RUNS COMPLETE -- total ${totalMins} min" -ForegroundColor Cyan
Write-Host ("=" * 60) -ForegroundColor Cyan
$results | Format-Table -AutoSize
