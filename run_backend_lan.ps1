param(
  [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  $python = Join-Path $root "venv\Scripts\python.exe"
}
if (-not (Test-Path $python)) {
  throw "Virtualenv not found. Create .venv or venv and install requirements."
}

# LAN host fix (disabled — use run_backend.ps1 for local development)
# Listen on all interfaces so other devices on the same WiFi can reach the API.
# & $python -m uvicorn app.main:app --host 0.0.0.0 --port $Port

Write-Host "LAN backend start is disabled. Use .\run_backend.ps1 or:" -ForegroundColor Yellow
Write-Host "  python -m uvicorn app.main:app --host 0.0.0.0 --port $Port" -ForegroundColor Yellow
