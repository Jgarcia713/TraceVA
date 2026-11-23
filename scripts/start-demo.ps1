# Start the TraceVA demo: replay backend (port 8000) + frontend dev server (port 5173).
# Uses a separate demo database so replayed runs never mix with real-run records.
# Prerequisites: backend venv installed, datasets/adult/adult.data present, `npm install`
# run in frontend/. Stop with Ctrl+C in each window.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"

$env:DATABASE_URL = "sqlite:///./demo.db"
$env:LLM_PROVIDER = "replay"
if (-not $env:DEMO_STEP_DELAY_S) { $env:DEMO_STEP_DELAY_S = "0.6" }

# $ErrorActionPreference does not stop on native exit codes in Windows PowerShell 5.1,
# so each setup step is checked explicitly before any server starts.
function Invoke-Step($description, [scriptblock]$command) {
  & $command
  if ($LASTEXITCODE -ne 0) { throw "Demo setup failed: $description (exit code $LASTEXITCODE)" }
}

Push-Location $backend
try {
  Invoke-Step "database migration" { & .venv\Scripts\alembic upgrade head }
  Invoke-Step "dataset registration (is datasets\adult\adult.data present?)" {
    & .venv\Scripts\python scripts\register_datasets.py adult
  }
} finally {
  Pop-Location
}

Start-Process powershell -WorkingDirectory $backend -ArgumentList @(
  "-NoExit", "-Command",
  "`$env:DATABASE_URL='sqlite:///./demo.db'; `$env:LLM_PROVIDER='replay'; `$env:DEMO_STEP_DELAY_S='$($env:DEMO_STEP_DELAY_S)'; .venv\Scripts\uvicorn app.main:create_app --factory --port 8000"
)
Start-Process powershell -WorkingDirectory $frontend -ArgumentList @("-NoExit", "-Command", "npx vite --port 5173")
Start-Sleep -Seconds 4
Start-Process "http://localhost:5173/"
