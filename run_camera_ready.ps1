# run_camera_ready.ps1
# ====================
# Compatibility entrypoint. The canonical implementation is the manifest-driven
# camera_ready/pipeline.py facade, which performs the pre-flight checks, the
# generation run, post-run verification, and the optional local commit.
#
# This file previously carried ~245 lines of unreachable script below the
# routing call, including its own stale copy of the camera-ready source list.
# That copy had drifted from evaluation_protocol.CAMERA_SOURCE_FILES (it was
# missing every camera_ready/* entry), and because this file is itself in
# CAMERA_SOURCE_FILES the divergence was provenance-clean. It is removed rather
# than maintained: git history holds the original if it is ever needed.
#
# USAGE:
#   cd C:\Personal_Endeavours\Fine_Tuning
#   .\run_camera_ready.ps1
#
# The run writes to:
#   evaluations\CAMERA_READY_OFFLINE_<timestamp>\
#
# Judgments and statistics go to the sibling _ANALYSIS directory; the run
# directory itself is immutable after generation.
#
# For the pre-flight checks alone, without generating:
#   python camera_ready/pipeline.py check --strict

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ROOT = $PSScriptRoot

Write-Host "Routing through the canonical manifest-driven camera-ready pipeline..." -ForegroundColor Cyan
& python (Join-Path $ROOT "camera_ready\pipeline.py") generate --commit
exit $LASTEXITCODE
