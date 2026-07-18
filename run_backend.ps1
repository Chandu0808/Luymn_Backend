param(
  [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  throw "Virtualenv not found at .venv. Create it first, then install requirements."
}

# Bind to IPv6 localhost; on Windows this typically also accepts IPv4-mapped connections.
& $python -m uvicorn app.main:app --host "::" --port $Port

