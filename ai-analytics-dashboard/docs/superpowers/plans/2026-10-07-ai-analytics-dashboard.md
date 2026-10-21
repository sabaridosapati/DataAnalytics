# AI Analytics Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a localhost, Docker-composed AI analytics dashboard that ingests raw files into Postgres/pgvector and answers natural-language questions through an intent analyzer + multi-agent (SQL, RAG, Compute, Viz, Aggregator) backend with strong anti-hallucination and SQL-security guardrails.

**Architecture:** FastAPI backend (async SQLAlchemy + asyncpg) with an ingestion pipeline (parse → typed Postgres tables or chunked/embedded pgvector rows), a typed-plan orchestrator (OpenAI JSON-schema planner → DAG executor → agents → aggregator), sqlglot SQL validation + read-only DB role, AST-whitelisted math, Redis caching. React/Vite/Tailwind/ECharts frontend served by nginx, which also proxies `/api`. Five compose services: frontend, backend, postgres (pgvector), redis, mailpit.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 (async), asyncpg, pgvector, sqlglot, pandas, pdfplumber, OpenAI SDK, Redis, bcrypt, PyJWT, aiosmtplib, slowapi, pytest; React 18, TypeScript, Vite 5, Tailwind 3, ECharts 5, TanStack Query 5, Vitest; Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-07-ai-analytics-dashboard-design.md`

**Task files:** each task's full steps and code live in `docs/superpowers/plans/2026-10-07-ai-analytics-dashboard/task-NN-*.md`. Code blocks are introduced by a line `**File: \`<path>\`**` (paths relative to repo root `ai-analytics-dashboard/`); each block is the complete file content.

## Global Constraints

- Everything binds to `127.0.0.1` only; frontend on `http://localhost:3000` is the single entry point (nginx proxies `/api` → backend:8000).
- Seeded admin: username `admin`, email `admin@analytics.local`, password `Test@123`, role admin, verified.
- Shared workspace: every verified, active user sees all datasets. Admin-only: delete datasets, manage users, view audit log.
- Registration requires a 6-digit email code: 10-min expiry, max 5 wrong attempts, 60-s resend cooldown, stored as HMAC-SHA256.
- LLM: OpenAI only, via the `LLMProvider` interface. `OPENAI_PLANNER_MODEL` (default `gpt-4o`) for intent + SQL; `OPENAI_CHAT_MODEL` (default `gpt-4o-mini`) for everything else; `text-embedding-3-small`, `vector(1536)`.
- **Zero-cost tests:** no test may call OpenAI. `LLM_PROVIDER=fake` in tests; a socket guard blocks `*.openai.com`. `tests/live/` exists only as opt-in (`-m live`) and is never run during this build.
- LLM-generated SQL: one SELECT only, `data` schema only, catalog-validated tables/columns, function denylist, `LIMIT` ≤ `MAX_SQL_ROWS` (5000), executed as `query_ro` in a read-only transaction with `statement_timeout=15s`. The SQL executed is the sqlglot-regenerated SQL, never the raw LLM string.
- No `eval`/`exec` of model output anywhere.
- Upload limit `MAX_UPLOAD_MB` = 50; per-step timeout `STEP_TIMEOUT_S` = 30; cache TTL 1 h (answers, SQL results), 24 h (query embeddings).
- Secrets only from `.env` (git-ignored); `.env.example` committed with placeholders.
- Shell scripts must have LF line endings (`.gitattributes`).
- All backend tests run inside the backend container: `docker compose run --rm backend pytest ...`.

## Deviations from spec (deliberate simplifications — spec updated in Task 13)

1. Alembic dropped: tables created with `Base.metadata.create_all` at startup (idempotent). Fine for v1; add Alembic before schema changes in production.
2. Backend uses the Postgres owner role (from `POSTGRES_USER`) as the "app_rw" role; `query_ro` is created exactly as specified.
3. File-type detection uses magic-byte signatures + extension (no libmagic dependency).
4. Chunking is character-based (~3200 chars ≈ 800 tokens, 400 overlap) to avoid tiktoken's runtime network download.
5. Ingestion runs serially (one `asyncio.Lock`) to avoid slug races; adequate for local use.

## Review Focus

Inputs the spec implies but doesn't spell out, most likely to bite first. Each has a test in the owning task:

1. **Excel-exported CSVs with a UTF-8 BOM or Windows-1252 encoding** → columns load cleanly (`name`, not `﻿name`), accented text preserved. *(Task 7, `test_csv_with_bom_and_cp1252`)*
2. **Uploading a file with the same name twice** → second dataset gets a unique slug (`sales_2024_2`); both are queryable. *(Task 7, `test_same_filename_twice_gets_unique_slug`)*
3. **Asking a question while a dataset is still processing** → planner only sees `ready` datasets; no reference to half-loaded tables. *(Task 10, `test_catalog_excludes_datasets_not_ready`)*
4. **SQL that returns zero rows** → columns still reported, viz falls back to an empty table, no crash. *(Task 9, `test_sql_agent_zero_rows_returns_columns` and `test_viz_empty_rows_falls_back_to_table`)*
5. **OpenAI key missing or OpenAI down** → startup fails with a clear message when the key is missing; a query during an outage returns HTTP 502 `llm_unavailable`, not a 500. *(Task 3, `test_build_provider_requires_key`; Task 10, `test_llm_outage_returns_502`)*

---

## File Map

```
ai-analytics-dashboard/
├── .gitignore .gitattributes .env.example docker-compose.yml README.md
├── db/init/01-init.sh                      # extension, schemas, query_ro role, test DB
├── backend/
│   ├── Dockerfile .dockerignore .gitignore pyproject.toml requirements.txt requirements-dev.txt
│   ├── app/
│   │   ├── main.py                         # app factory, lifespan, health
│   │   ├── config.py                       # Settings (env-driven)
│   │   ├── catalog.py                      # Catalog dataclasses + load_catalog
│   │   ├── util.py                         # to_jsonable, vector_literal
│   │   ├── api/  errors.py ratelimit.py deps.py auth.py datasets.py query.py dashboard.py admin.py
│   │   ├── auth/ security.py mailer.py email_codes.py
│   │   ├── db/   models.py session.py seed.py versions.py
│   │   ├── ingestion/ detect.py parsers.py tabular.py chunker.py pipeline.py
│   │   ├── guardrails/ sql_validator.py safe_eval.py number_check.py
│   │   ├── agents/ types.py intent.py executor.py sql_exec.py sql_agent.py rag_agent.py
│   │   │           compute_agent.py viz_agent.py aggregator.py orchestrator.py __init__.py
│   │   ├── llm/  provider.py openai_provider.py fake_provider.py demo_script.py
│   │   └── cache/ redis_cache.py
│   ├── scripts/generate_test_data.py
│   └── tests/ conftest.py helpers.py fixtures/ unit/ integration/ live/
└── frontend/
    ├── Dockerfile nginx.conf .dockerignore .gitignore package.json tsconfig.json
    ├── vite.config.ts tailwind.config.js postcss.config.js index.html
    └── src/ main.tsx App.tsx index.css api/ auth/ components/ pages/ charts/ utils/ test/
```

## Tasks

| # | Task | File | Deliverable / test gate |
|---|---|---|---|
| 1 | Scaffolding, compose, DB init | `task-01-scaffolding.md` | images build; `test_config` passes; schemas + `query_ro` exist |
| 2 | DB models, session, security, admin seed | `task-02-db-security.md` | unit security tests; integration DB/seed/ro-role tests |
| 3 | LLM provider (OpenAI + Fake) and Redis cache | `task-03-llm-cache.md` | unit tests for fake provider, embeddings, cache |
| 4 | App factory + auth API (register/verify/login) | `task-04-auth-api.md` | integration auth flow via Mailpit |
| 5 | Deterministic test-data generator | `task-05-test-data.md` | generator tests; fixtures committed |
| 6 | Guardrails: SQL validator, safe_eval, number_check | `task-06-guardrails.md` | ~60 unit cases |
| 7 | Ingestion pipeline + datasets API | `task-07-ingestion.md` | unit parser tests; integration upload tests |
| 8 | Plan types, intent analyzer, DAG executor | `task-08-intent-executor.md` | unit tests |
| 9 | SQL, RAG, Compute, Viz agents + Aggregator | `task-09-agents.md` | unit + integration agent tests |
| 10 | Query API, orchestration, dashboard pins, admin, demo mode | `task-10-query-api.md` | end-to-end integration tests (fake LLM) |
| 11 | Frontend foundation: API client, auth, pages for login/register/verify | `task-11-frontend-auth.md` | vitest passes |
| 12 | Frontend app pages, charts, Dockerfile, nginx | `task-12-frontend-app.md` | `docker compose build frontend` (runs tests) |
| 13 | README, spec sync, full verification | `task-13-readme-verify.md` | full stack up; all tests green; smoke checks |
