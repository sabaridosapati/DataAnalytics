# Task 1: Scaffolding, compose, DB init

**Files:**
- Create: `.gitignore`, `.gitattributes`, `.env.example`, `docker-compose.yml`, `db/init/01-init.sh`
- Create: `backend/Dockerfile`, `backend/.dockerignore`, `backend/.gitignore`, `backend/pyproject.toml`, `backend/requirements.txt`, `backend/requirements-dev.txt`
- Create: `backend/app/__init__.py` (empty), `backend/app/config.py`, `backend/app/main.py` (temporary; replaced in Task 4)
- Test: `backend/tests/conftest.py`, `backend/tests/unit/test_config.py`

**Interfaces:**
- Produces: `app.config.Settings`, `app.config.get_settings()` (lru-cached); `Settings.cors_origin_list: list[str]`.
- Produces: pytest fixture `fixtures_dir` (session) → `Path` containing generated fixtures (uses `scripts.generate_test_data.generate`, created in Task 5).
- Produces: env for tests — `LLM_PROVIDER=fake`, test DB URLs, Redis DB 1, rate limiting off; socket guard blocking `*.openai.com`.

- [ ] **Step 1: Write repo-level files**

**File: `.gitignore`**
```gitignore
# secrets
.env
*.env.local

# python
__pycache__/
*.pyc
.pytest_cache/
.venv/
venv/

# node
node_modules/
dist/

# editors / OS
.vscode/
.idea/
.DS_Store
Thumbs.db

# runtime
*.log
uploads/
test_reports/
```

**File: `.gitattributes`**
```gitattributes
* text=auto
*.sh text eol=lf
*.pdf binary
*.xlsx binary
*.png binary
```

**File: `.env.example`**
```dotenv
# ---- LLM ----
# openai = real OpenAI API (needs OPENAI_API_KEY, billed per use)
# fake   = free demo mode: scripted answers for the sample questions in README
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-your-key-here
OPENAI_PLANNER_MODEL=gpt-4o
OPENAI_CHAT_MODEL=gpt-4o-mini
OPENAI_EMBED_MODEL=text-embedding-3-small

# ---- Database (use letters/digits only in passwords) ----
POSTGRES_USER=analytics
POSTGRES_PASSWORD=analyticsdevpw
POSTGRES_DB=analytics
QUERY_RO_PASSWORD=queryrodevpw

# ---- Auth ----
JWT_SECRET=replace-with-a-long-random-string
ADMIN_PASSWORD=Test@123

# ---- Email (Mailpit catches all mail locally: http://localhost:8025) ----
SMTP_HOST=mailpit
SMTP_PORT=1025
SMTP_FROM=AI Analytics <no-reply@analytics.local>
SMTP_USER=
SMTP_PASSWORD=
SMTP_STARTTLS=false

# ---- Limits ----
MAX_UPLOAD_MB=50
MAX_SQL_ROWS=5000
STEP_TIMEOUT_S=30
CORS_ORIGINS=http://localhost:3000,http://localhost:5173
```

**File: `docker-compose.yml`**
```yaml
name: ai-analytics

services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
      QUERY_RO_PASSWORD: ${QUERY_RO_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./db/init:/docker-entrypoint-initdb.d:ro
    ports:
      - "127.0.0.1:5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 5s
      timeout: 5s
      retries: 30

  redis:
    image: redis:7-alpine
    command: ["redis-server", "--maxmemory", "256mb", "--maxmemory-policy", "allkeys-lru"]
    ports:
      - "127.0.0.1:6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 20

  mailpit:
    image: axllent/mailpit:latest
    ports:
      - "127.0.0.1:8025:8025"
      - "127.0.0.1:1025:1025"

  backend:
    build: ./backend
    env_file: .env
    environment:
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
      QUERY_RO_URL: postgresql://query_ro:${QUERY_RO_PASSWORD}@postgres:5432/${POSTGRES_DB}
      TEST_DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}_test
      TEST_QUERY_RO_URL: postgresql://query_ro:${QUERY_RO_PASSWORD}@postgres:5432/${POSTGRES_DB}_test
      REDIS_URL: redis://redis:6379/0
      UPLOAD_DIR: /data/uploads
      MAILPIT_API: http://mailpit:8025
    volumes:
      - uploads:/data/uploads
    ports:
      - "127.0.0.1:8000:8000"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      mailpit:
        condition: service_started
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=4)"]
      interval: 10s
      timeout: 5s
      retries: 12
      start_period: 20s

  frontend:
    build: ./frontend
    ports:
      - "127.0.0.1:3000:80"
    depends_on:
      backend:
        condition: service_healthy

volumes:
  pgdata:
  uploads:
```

> Note: the `frontend` service's build context is created in Task 12. Until then, build/run services by name (`docker compose build backend`).

**File: `db/init/01-init.sh`**
```bash
#!/bin/bash
# Runs once, on first start of an empty Postgres volume.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<EOSQL
CREATE ROLE query_ro LOGIN PASSWORD '${QUERY_RO_PASSWORD}';
ALTER ROLE query_ro SET statement_timeout = '15s';
ALTER ROLE query_ro SET default_transaction_read_only = on;
ALTER ROLE query_ro SET search_path = data;
CREATE DATABASE "${POSTGRES_DB}_test";
EOSQL

setup_db() {
  local db="$1"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$db" <<EOSQL
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS app;
CREATE SCHEMA IF NOT EXISTS data;
CREATE SCHEMA IF NOT EXISTS vector;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE "$db" FROM PUBLIC;
GRANT CONNECT ON DATABASE "$db" TO query_ro;
GRANT USAGE ON SCHEMA data TO query_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA data TO query_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE "$POSTGRES_USER" IN SCHEMA data GRANT SELECT ON TABLES TO query_ro;
EOSQL
}

setup_db "$POSTGRES_DB"
setup_db "${POSTGRES_DB}_test"
```

- [ ] **Step 2: Write backend packaging files**

**File: `backend/requirements.txt`**
```text
fastapi>=0.115,<0.116
uvicorn[standard]>=0.30,<0.33
python-multipart>=0.0.9
pydantic>=2.7,<3
pydantic-settings>=2.3,<3
email-validator>=2.1
sqlalchemy[asyncio]>=2.0.30,<2.1
asyncpg>=0.29
pgvector>=0.3
redis>=5.0,<6
openai>=1.40,<2
sqlglot>=25.20,<26
pandas>=2.2,<3
numpy>=1.26,<3
openpyxl>=3.1
xlrd>=2.0
pdfplumber>=0.11
python-docx>=1.1
bcrypt>=4.1,<5
pyjwt>=2.8
aiosmtplib>=3.0
slowapi>=0.1.9
```

**File: `backend/requirements-dev.txt`**
```text
-r requirements.txt
pytest>=8,<9
pytest-asyncio>=0.24,<0.25
httpx>=0.27,<0.29
asgi-lifespan>=2.1
reportlab>=4.2
```

**File: `backend/pyproject.toml`**
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
testpaths = ["tests"]
pythonpath = [".", "tests"]
addopts = "-m 'not live' --import-mode=importlib -p no:cacheprovider"
markers = [
  "live: calls the real OpenAI API (costs money; excluded by default)",
]
filterwarnings = ["ignore::DeprecationWarning"]
```

**File: `backend/Dockerfile`**
```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements-dev.txt

COPY . .
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/uploads \
    && chown -R appuser:appuser /data /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**File: `backend/.dockerignore`**
```text
__pycache__/
*.pyc
.pytest_cache/
.venv/
venv/
.env
```

**File: `backend/.gitignore`**
```gitignore
__pycache__/
*.pyc
.pytest_cache/
.venv/
venv/
*.egg-info/
.coverage
htmlcov/
```

**File: `backend/app/__init__.py`**
```python
```

- [ ] **Step 3: Write the global test conftest and the failing config test**

**File: `backend/tests/conftest.py`**
```python
"""Global test setup. Executed before any `app` module is imported."""
import os
import socket
from pathlib import Path

import pytest

# ---- zero-cost, isolated test environment ----
os.environ["LLM_PROVIDER"] = "fake"
os.environ["OPENAI_API_KEY"] = ""
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["JWT_SECRET"] = "test-secret"
os.environ["UPLOAD_DIR"] = "/tmp/test_uploads"
if os.environ.get("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
if os.environ.get("TEST_QUERY_RO_URL"):
    os.environ["QUERY_RO_URL"] = os.environ["TEST_QUERY_RO_URL"]
_redis = os.environ.get("REDIS_URL", "redis://redis:6379/0")
os.environ["REDIS_URL"] = _redis.rsplit("/", 1)[0] + "/1"

# ---- network guard: tests must never reach OpenAI ----
# Only the opt-in live suite (LIVE_OPENAI_API_KEY set explicitly by the user) lifts it.
_real_getaddrinfo = socket.getaddrinfo


def _guarded_getaddrinfo(host, *args, **kwargs):
    if host and "openai.com" in str(host):
        raise RuntimeError("Tests must not call the OpenAI API (zero-cost policy)")
    return _real_getaddrinfo(host, *args, **kwargs)


if not os.environ.get("LIVE_OPENAI_API_KEY"):
    socket.getaddrinfo = _guarded_getaddrinfo

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures_dir(tmp_path_factory) -> Path:
    """Committed fixtures if present, otherwise generate them into a temp dir."""
    if (FIXTURES / "expected_answers.json").exists():
        return FIXTURES
    from scripts.generate_test_data import generate

    out = tmp_path_factory.mktemp("fixtures")
    generate(out)
    return out
```

**File: `backend/tests/unit/test_config.py`**
```python
from app.config import Settings


def test_settings_read_env(monkeypatch):
    monkeypatch.setenv("MAX_SQL_ROWS", "123")
    s = Settings()
    assert s.max_sql_rows == 123


def test_cors_origin_list_default():
    s = Settings(cors_origins="http://localhost:3000, http://localhost:5173 ,")
    assert s.cors_origin_list == ["http://localhost:3000", "http://localhost:5173"]


def test_test_env_uses_fake_llm():
    assert Settings().llm_provider == "fake"
```

- [ ] **Step 4: Create `.env` for local builds and build the backend image**

Run (PowerShell, repo root):
```powershell
Copy-Item .env.example .env
(Get-Content .env) -replace '^LLM_PROVIDER=openai','LLM_PROVIDER=fake' | Set-Content -Encoding utf8 .env
docker compose build backend
```
Expected: image builds. (`LLM_PROVIDER=fake` lets the stack start without an API key during the build; the user switches it to `openai` later.)

- [ ] **Step 5: Run the config test to verify it fails**

Run: `docker compose run --rm --no-deps backend pytest tests/unit/test_config.py -q`
Expected: FAIL / error — `ModuleNotFoundError: No module named 'app.config'`.

- [ ] **Step 6: Implement config and a temporary main**

**File: `backend/app/config.py`**
```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str = "postgresql+asyncpg://analytics:analytics@postgres:5432/analytics"
    query_ro_url: str = "postgresql://query_ro:query_ro@postgres:5432/analytics"
    redis_url: str = "redis://redis:6379/0"

    llm_provider: str = "openai"
    openai_api_key: str = ""
    openai_chat_model: str = "gpt-4o-mini"
    openai_planner_model: str = "gpt-4o"
    openai_embed_model: str = "text-embedding-3-small"
    openai_temperature: float | None = 0.0
    embed_dim: int = 1536

    jwt_secret: str = "change-me"
    jwt_expire_minutes: int = 60
    admin_email: str = "admin@analytics.local"
    admin_password: str = "Test@123"

    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_from: str = "AI Analytics <no-reply@analytics.local>"
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = False

    upload_dir: str = "/data/uploads"
    max_upload_mb: int = 50
    max_sql_rows: int = 5000
    step_timeout_s: int = 30
    cache_ttl_s: int = 3600
    rate_limit_enabled: bool = True
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

**File: `backend/app/main.py`**
```python
# Temporary entrypoint; replaced by the full app factory in Task 4.
from fastapi import FastAPI

app = FastAPI(title="AI Analytics Dashboard")


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}
```

- [ ] **Step 7: Rebuild and run the test to verify it passes**

Run: `docker compose build backend; docker compose run --rm --no-deps backend pytest tests/unit/test_config.py -q`
Expected: `3 passed`.

- [ ] **Step 8: Verify database initialization**

Run:
```powershell
docker compose up -d postgres redis mailpit
docker compose exec postgres psql -U analytics -d analytics_test -c "\dn"
docker compose exec postgres psql -U analytics -d analytics -c "select rolname from pg_roles where rolname='query_ro'"
```
Expected: schemas `app`, `data`, `vector` listed; one row `query_ro`.

- [ ] **Step 9: Commit**

```bash
git add .gitignore .gitattributes .env.example docker-compose.yml db backend
git commit -m "chore: scaffold backend, compose stack and database init"
```
