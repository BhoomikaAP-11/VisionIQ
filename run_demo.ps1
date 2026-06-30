# BEL — one-shot local demo runner (Windows PowerShell).
# Run from the project root:
#     powershell -ExecutionPolicy Bypass -File .\run_demo.ps1
#
# What it does:
#   1. Creates / activates a venv in backend\venv
#   2. Installs requirements (idempotent — skips if already satisfied)
#   3. Runs the offline smoke test (no API keys needed)
#   4. If a sample .xlsx is found in your last uploads folder, profiles it
#      directly via the Python pipeline and prints the dashboard JSON
#   5. Starts the FastAPI server on http://localhost:8000

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host ""
Write-Host "[1/5] Setting up Python venv..." -ForegroundColor Cyan
if (-not (Test-Path "backend\venv")) {
    python -m venv backend\venv
}
. backend\venv\Scripts\Activate.ps1

Write-Host "[2/5] Installing dependencies (this may take a minute the first time)..." -ForegroundColor Cyan
pip install --quiet -r backend\requirements.txt

Write-Host "[3/5] Running offline smoke test..." -ForegroundColor Cyan
python -m backend.smoke_test
if ($LASTEXITCODE -ne 0) {
    Write-Host "Smoke test FAILED. Fix errors before continuing." -ForegroundColor Red
    exit 1
}

Write-Host "[4/5] Looking for the sample .xlsx you uploaded..." -ForegroundColor Cyan
$uploadRoot = "$env:APPDATA\Claude\local-agent-mode-sessions"
$sample = Get-ChildItem -Path $uploadRoot -Recurse -Filter "*sample-data-10mins*.xlsx" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($sample) {
    $dest = "backend\uploads\sample-data-10mins.xlsx"
    Copy-Item $sample.FullName $dest -Force
    Write-Host "Copied to $dest. Profiling..." -ForegroundColor Green
    python -m backend.demo_profile $dest
} else {
    Write-Host "  No sample-data-10mins.xlsx found in uploads — skipping." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[5/5] Starting FastAPI server..." -ForegroundColor Cyan
Write-Host "  -> Open http://localhost:8000/docs"
Write-Host ""
uvicorn backend.main:app --reload --port 8000
