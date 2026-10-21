@echo off
rem Runs backend tests locally against the Docker Postgres/Redis/Mailpit (no image build).
rem Usage: run-tests.bat tests\integration\test_chats_service.py -q
setlocal
cd /d "%~dp0backend"

if not exist .venv\Scripts\python.exe (
  python -m venv .venv || exit /b 1
)
.venv\Scripts\python.exe -c "import pytest, asgi_lifespan" 2>nul || .venv\Scripts\python.exe -m pip install -r requirements-dev.txt || exit /b 1

rem Values from .env.example defaults; override by setting them before calling this script.
if not defined POSTGRES_USER set "POSTGRES_USER=analytics"
if not defined POSTGRES_PASSWORD set "POSTGRES_PASSWORD=analyticsdevpw"
if not defined POSTGRES_DB set "POSTGRES_DB=analytics"
if not defined QUERY_RO_PASSWORD set "QUERY_RO_PASSWORD=queryrodevpw"

set "DATABASE_URL=postgresql+asyncpg://%POSTGRES_USER%:%POSTGRES_PASSWORD%@127.0.0.1:5432/%POSTGRES_DB%_test"
set "QUERY_RO_URL=postgresql://query_ro:%QUERY_RO_PASSWORD%@127.0.0.1:5432/%POSTGRES_DB%_test"
set "REDIS_URL=redis://127.0.0.1:6379/0"
set "MAILPIT_API=http://127.0.0.1:8025"
set "SMTP_HOST=127.0.0.1"
set "ADMIN_PASSWORD=Test@123"
set "ADMIN_EMAIL=admin@analytics.local"
set "UPLOAD_DIR=%TEMP%\test_uploads"

.venv\Scripts\python.exe -m pytest %*
