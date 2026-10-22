@echo off
rem Runs the backend in THIS window (foreground) so you see live logs. Ctrl+C to stop.
rem Requires Postgres + Redis running:  docker compose up -d --no-build postgres redis mailpit
setlocal
cd /d "%~dp0backend"

if not exist "%~dp0.env" (
  echo .env not found. Copy .env.example to .env and set GEMINI_API_KEY.
  exit /b 1
)

rem ---- load .env (skips lines starting with #) ----
for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%~dp0.env") do set "%%A=%%B"

rem ---- point services at localhost instead of docker hostnames ----
set "DATABASE_URL=postgresql+asyncpg://%POSTGRES_USER%:%POSTGRES_PASSWORD%@127.0.0.1:5432/%POSTGRES_DB%"
set "QUERY_RO_URL=postgresql://query_ro:%QUERY_RO_PASSWORD%@127.0.0.1:5432/%POSTGRES_DB%"
set "REDIS_URL=redis://127.0.0.1:6379/0"
set "SMTP_HOST=127.0.0.1"
set "UPLOAD_DIR=%CD%\uploads"
if not exist "%UPLOAD_DIR%" mkdir "%UPLOAD_DIR%"

rem ---- own venv (not project01's) ----
if not exist ".venv\Scripts\python.exe" (
  echo Creating backend\.venv ...
  python -m venv .venv || exit /b 1
)
.venv\Scripts\python.exe -c "import duckdb, fastapi" 2>nul || (
  echo Installing backend requirements ...
  .venv\Scripts\python.exe -m pip install -r requirements.txt || exit /b 1
)

echo Backend: http://localhost:8000  (health: /api/health)
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --log-level debug
