# Starts the app locally without building any Docker images:
#   Postgres + Redis + Mailpit  -> Docker (pre-built images, `docker compose up` without --build)
#   Backend (FastAPI/uvicorn)   -> Python venv in backend\.venv
#   Frontend (Vite dev server)  -> npm
# Usage:  .\start-local.ps1        (or double-click start-local.bat)
$ErrorActionPreference = "Stop"
$Root     = $PSScriptRoot
$Backend  = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$State    = Join-Path $Root ".local"
$Logs     = Join-Path $State "logs"
New-Item -ItemType Directory -Force $Logs | Out-Null

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

# ---- 1. load .env into this process (children inherit it) ----
$envFile = Join-Path $Root ".env"
if (-not (Test-Path $envFile)) { throw ".env not found. Copy .env.example to .env and set GEMINI_API_KEY." }
Get-Content $envFile | Where-Object { $_ -match '^\s*[A-Za-z_][A-Za-z0-9_]*=' } | ForEach-Object {
    $k, $v = $_ -split '=', 2
    Set-Item "env:$($k.Trim())" $v.Trim()
}
if ($env:LLM_PROVIDER -eq "gemini" -and [string]::IsNullOrWhiteSpace($env:GEMINI_API_KEY)) {
    Write-Warning "GEMINI_API_KEY is empty in .env - the backend will refuse to start. Set it, or use LLM_PROVIDER=fake."
}

# ---- 2. point services at localhost instead of docker hostnames ----
$env:DATABASE_URL = "postgresql+asyncpg://$($env:POSTGRES_USER):$($env:POSTGRES_PASSWORD)@127.0.0.1:5432/$($env:POSTGRES_DB)"
$env:QUERY_RO_URL = "postgresql://query_ro:$($env:QUERY_RO_PASSWORD)@127.0.0.1:5432/$($env:POSTGRES_DB)"
$env:REDIS_URL    = "redis://127.0.0.1:6379/0"
$env:SMTP_HOST    = "127.0.0.1"
$env:UPLOAD_DIR   = Join-Path $Backend "uploads"
New-Item -ItemType Directory -Force $env:UPLOAD_DIR | Out-Null

# ---- 3. Postgres / Redis / Mailpit via Docker (no build) ----
Step "Starting Postgres, Redis and Mailpit (docker compose, no build)"
Push-Location $Root
try {
    docker compose up -d --no-build postgres redis mailpit
    if ($LASTEXITCODE -ne 0) { throw "docker compose failed. Is Docker Desktop running?" }

    Step "Waiting for Postgres"
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        docker compose exec -T postgres pg_isready -U $env:POSTGRES_USER -d $env:POSTGRES_DB *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 2
    }
    if (-not $ready) { throw "Postgres did not become ready in 120 s. Check: docker compose logs postgres" }
} finally { Pop-Location }

# ---- helper: start a hidden background process, remember its PID ----
function Start-Component($name, $file, $argList, $workDir) {
    $pidFile = Join-Path $State "$name.pid"
    if (Test-Path $pidFile) {
        $old = Get-Process -Id ([int](Get-Content $pidFile)) -ErrorAction SilentlyContinue
        if ($old) { Write-Host "$name already running (PID $($old.Id))"; return }
    }
    $p = Start-Process -FilePath $file -ArgumentList $argList -WorkingDirectory $workDir -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $Logs "$name.out.log") -RedirectStandardError (Join-Path $Logs "$name.err.log")
    Set-Content $pidFile $p.Id
    Write-Host "$name started (PID $($p.Id))"
}

# ---- 4. Backend ----
Step "Preparing backend"
$venvPy = Join-Path $Backend ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    python -m venv (Join-Path $Backend ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Could not create venv. Is Python 3.11+ on PATH?" }
}
$req    = Join-Path $Backend "requirements.txt"
$marker = Join-Path $Backend ".venv\.requirements.hash"
$hash   = (Get-FileHash $req).Hash
if (-not (Test-Path $marker) -or (Get-Content $marker) -ne $hash) {
    Step "Installing backend dependencies (first run / requirements changed)"
    & $venvPy -m pip install -r $req
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
    Set-Content $marker $hash
}
Step "Starting backend on http://localhost:8000"
Start-Component "backend" $venvPy "-m uvicorn app.main:app --host 127.0.0.1 --port 8000" $Backend

# ---- 5. Frontend ----
Step "Preparing frontend"
if (-not (Test-Path (Join-Path $Frontend "node_modules"))) {
    Push-Location $Frontend; try { npm install } finally { Pop-Location }
}
Step "Starting frontend on http://localhost:5173"
Start-Component "frontend" "cmd.exe" "/c npm run dev -- --host 127.0.0.1 --port 5173" $Frontend

# ---- 6. wait for backend health ----
Step "Waiting for backend health"
$up = $false
for ($i = 0; $i -lt 45; $i++) {
    try { Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/health" -UseBasicParsing -TimeoutSec 2 | Out-Null; $up = $true; break }
    catch { Start-Sleep -Seconds 2 }
}
if ($up) {
    Write-Host "`nReady:" -ForegroundColor Green
    Write-Host "  App:      http://localhost:5173"
    Write-Host "  API:      http://localhost:8000/api/health"
    Write-Host "  Mailpit:  http://localhost:8025"
    Write-Host "  Login:    $($env:ADMIN_EMAIL) / ADMIN_PASSWORD from .env (default admin@analytics.local / Test@123)"
    Write-Host "  Logs:     $Logs"
    Write-Host "  Stop:     .\stop-local.ps1"
} else {
    Write-Warning "Backend did not become healthy. See $Logs\backend.err.log"
    exit 1
}
