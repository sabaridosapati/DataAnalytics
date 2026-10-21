# Stops everything started by start-local.ps1.
# Usage:  .\stop-local.ps1          stops backend, frontend, and the Docker containers (data is kept)
#         .\stop-local.ps1 -Down    also removes the containers (database volume is still kept)
param([switch]$Down)
$ErrorActionPreference = "Continue"
$Root  = $PSScriptRoot
$State = Join-Path $Root ".local"

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

function Stop-Component($name) {
    $pidFile = Join-Path $State "$name.pid"
    if (-not (Test-Path $pidFile)) { Write-Host "${name}: no PID file (not started by start-local?)"; return }
    $procId = [int](Get-Content $pidFile)
    if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
        taskkill /PID $procId /T /F *> $null   # /T also kills child processes (uvicorn reloader, node)
        Write-Host "$name stopped (PID $procId)"
    } else {
        Write-Host "$name was not running"
    }
    Remove-Item $pidFile -Force
}

Step "Stopping frontend and backend"
Stop-Component "frontend"
Stop-Component "backend"

Step "Stopping Postgres, Redis and Mailpit"
Push-Location $Root
try {
    if ($Down) { docker compose down } else { docker compose stop postgres redis mailpit }
} finally { Pop-Location }

Write-Host "`nAll stopped." -ForegroundColor Green
