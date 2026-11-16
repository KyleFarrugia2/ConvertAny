# Runs the full stack locally without Docker (Windows PowerShell).
# Prerequisites: Python 3.12, Node 22, FFmpeg on PATH. See README.md.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

if (-not (Test-Path "$root\backend\.venv")) {
  py -3.12 -m venv "$root\backend\.venv"
  & "$root\backend\.venv\Scripts\python" -m pip install -r "$root\backend\requirements-dev.txt"
}
if (-not (Test-Path "$root\frontend\node_modules")) { Push-Location "$root\frontend"; npm ci; Pop-Location }

Push-Location "$root\backend"
& .\.venv\Scripts\python -m alembic upgrade head
Pop-Location

$py = "$root\backend\.venv\Scripts\python.exe"
$api = Start-Process $py -ArgumentList "-m","uvicorn","omniconvert.api.main:app","--host","127.0.0.1","--port","8000" -WorkingDirectory "$root\backend" -PassThru -NoNewWindow
$wrk = Start-Process $py -ArgumentList "-m","omniconvert.worker.main","--queues","cpu,gpu" -WorkingDirectory "$root\backend" -PassThru -NoNewWindow
Write-Host "API: http://127.0.0.1:8000/api/docs   Web: http://localhost:3000"
try {
  Push-Location "$root\frontend"; npm run dev
} finally {
  Pop-Location
  Stop-Process -Id $api.Id, $wrk.Id -ErrorAction SilentlyContinue
}
