# =============================================================================
# git_push_github.ps1
# One-shot: stage everything, commit, tag, set remote, push to GitHub
# Run from: C:\Personal_Endeavours\Fine_Tuning\
# =============================================================================

# Ensure we are in the correct directory
Set-Location -Path $PSScriptRoot

# ── 0. Remove stale git lock if present ──────────────────────────────────────
$lockFile = Join-Path $PSScriptRoot ".git\index.lock"
if (Test-Path $lockFile) {
    Write-Host "[0] Removing stale git lock file..." -ForegroundColor Yellow
    Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
    Write-Host "    Done." -ForegroundColor Green
}

# ── 1. Ensure git identity is set ────────────────────────────────────────────
Write-Host "[1] Checking git identity..." -ForegroundColor Cyan
git config user.name "i-am-mushfiq"
git config user.email "sittul.muna666@gmail.com"
Write-Host "    Identity set to: $(git config user.name) <$(git config user.email)>"

# ── 2. Stage everything (respects .gitignore) ────────────────────────────────
Write-Host "[2] Staging all files..." -ForegroundColor Cyan
git add .
if ($LASTEXITCODE -ne 0) { 
    Write-Error "git add failed. Check for open files or lock issues."
    exit 1 
}
Write-Host "    Files staged." -ForegroundColor Green

# ── 3. Commit ────────────────────────────────────────────────────────────────
Write-Host "[3] Committing..." -ForegroundColor Cyan

# Small pause to ensure the index is fully unlocked by background processes
Start-Sleep -Milliseconds 300

$msg = @"
feat: Phase 1 BM25 RAG complete + full research documentation

Core findings:
- Final adapter: 10cat_4bit_r16_lr1e-4_p3_v2_20260508_054337
  ROUGE-L 0.2352 overall | 0.2245 SC | 0.2616 Non-SC | 19.26 tok/s

- 8-bit QLoRA trialled and rejected (safety, not metrics)
  ROUGE-L 0.2347 vs 4-bit 0.2352 (gap=0.0005, negligible)
  Rejection reason: systematic dangerous positioning heuristic on Q2/Q16/Q18/Q28/Q33

- Enhanced inference (T4 min_new_tokens, T6 two-pass self-critique) rejected
  T2+T4+T6 combined ROUGE-L 0.1963 at 4.1 tok/s — active harm at 2B scale
  T4: Q22 embedded-glass hallucination (critical failure)
  T6: SC mean dropped to 1.52, dangerous advice on Q15/Q18/Q27/Q28/Q32

- Phase 1 BM25 RAG (bm25_rag.py):
  Top-1 BM25Okapi retrieval, 150-token cap, gap-question gate
  Gap gate (Q6/Q17/Q21/Q22/Q28): retrieval skipped to prevent injection harm
  Phase1-A ROUGE-L 0.2194 | SC 0.2036 | Non-SC 0.2611
  Phase1-B (BM25+T2) ROUGE-L 0.2157 | SC 0.1974 | Non-SC 0.2641

Evaluation:
- 40-question eval bank across 10 first-aid categories (SC and non-SC)
- 7 LLM judges: GPT-4o, Claude, Gemini, Grok, DeepSeek, Kimi K2, Manus

Added files:
- bm25_rag.py, data_v2.py, train_v2.py, evaluations, research reports, run scripts
"@

git commit -m $msg
if ($LASTEXITCODE -ne 0) { 
    Write-Warning "Commit failed. This often happens if there is nothing new to commit." 
} else {
    Write-Host "    Committed." -ForegroundColor Green
}

# ── 4. Set remote ────────────────────────────────────────────────────────────
Write-Host "[4] Setting remote origin..." -ForegroundColor Cyan
$remoteUrl = "https://github.com/i-am-mushfiq/FirstAidQA-gemma2b-QLoRA.git"
$existingRemote = git remote get-url origin 2>$null

if ($null -eq $existingRemote) {
    git remote add origin $remoteUrl
} else {
    git remote set-url origin $remoteUrl
}
Write-Host "    Remote: $(git remote get-url origin)" -ForegroundColor Green

# ── 5. Push ──────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "============================================================" -ForegroundColor Yellow
Write-Host " ENSURE REPO EXISTS: https://github.com/i-am-mushfiq/FirstAidQA-gemma2b-QLoRA" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Yellow
Write-Host ""

$ready = Read-Host "Ready to push to GitHub? (y to push / n to exit)"
if ($ready -eq "y") {
    Write-Host "[5] Pushing main branch..." -ForegroundColor Cyan
    git push -u origin main --force
    
    Write-Host "[6] Pushing tag..." -ForegroundColor Cyan
    git tag phase1-rag-complete 2>$null
    git push origin phase1-rag-complete
    
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host " SUCCESS! Repo live at: $remoteUrl" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
} else {
    Write-Host "Exiting without push." -ForegroundColor Red
}