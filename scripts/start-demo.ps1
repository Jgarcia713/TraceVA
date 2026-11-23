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

Push-Location $backend
& .venv\Scripts\alembic upgrade head
& .venv\Scripts\python scripts\register_datasets.py adult
Pop-Location

Start-Process powershell -WorkingDirectory $backend -ArgumentList @(
  "-NoExit", "-Command",
  "`$env:DATABASE_URL='sqlite:///./demo.db'; `$env:LLM_PROVIDER='replay'; `$env:DEMO_STEP_DELAY_S='$($env:DEMO_STEP_DELAY_S)'; .venv\Scripts\uvicorn app.main:create_app --factory --port 8000"
)
Start-Process powershell -WorkingDirectory $frontend -ArgumentList @("-NoExit", "-Command", "npx vite --port 5173")
Start-Sleep -Seconds 4
Start-Process "http://localhost:5173/"
