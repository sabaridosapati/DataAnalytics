# AI Analytics Dashboard

Upload your data files, then ask questions about them in plain English.

A self-hosted "data insighter": drop in CSV, Excel, JSON, PDF, Word or text files and chat with them. An intent
analyzer plans each question and routes it to specialist agents (**SQL**, **document search**, **compute**,
**charts**). An aggregator combines their results into one answer with charts, tables and sources, and every number
in the answer is checked against the real data.

> The application lives in [`ai-analytics-dashboard/`](ai-analytics-dashboard/). Its own
> [README](ai-analytics-dashboard/README.md) goes deeper on usage and configuration; this page is the overview and
> the fastest way to get running.

## Features

- **Ask in plain English.** Each answer shows the plan, the exact validated SQL, document citations, charts and
  sortable tables with CSV download.
- **Structured data in DuckDB.** CSV/TSV/Excel/JSON tables (and tables found inside PDFs) are converted to one
  `.duckdb` file per upload, and questions about them run there, read-only and sandboxed.
- **Documents in pgvector.** PDF, Word and text files are chunked and embedded so questions can cite them.
- **Chat history.** Conversations are saved per user in Postgres. Reopen, continue or delete them from the Ask page.
- **Dashboard.** Pin charts and refresh them later without calling the LLM.
- **Accounts.** Email sign-up with a 6-digit verification code, bcrypt password hashing, JWT sessions, an admin role
  and a query audit log.
- **Switchable LLM.** Google Gemini by default (Flash-Lite and Embedding 2). OpenAI support is kept commented out so
  you can switch back. A free `fake` mode runs scripted demo answers with no API key.

## How it stays trustworthy

| Risk | What the project does |
|---|---|
| Hallucinated SQL or numbers | The SQL agent only sees the real schema. Generated SQL is parsed with sqlglot and every table and column is checked against the catalog. An answer may only quote numbers that appear in tool outputs. |
| Unsafe generated SQL | Only a single `SELECT` on the `data` schema is accepted. Table functions, file paths and system functions are rejected. It runs on a locked-down in-memory DuckDB: read-only files, no file or network access, locked settings, memory/time/row limits and a cap on concurrent queries. |
| Unsafe generated code | Math uses an AST-whitelisted evaluator, with no `eval` or `exec`. Document text is treated as untrusted data in prompts. |

## Architecture

```
Browser ──► nginx (frontend) ──/api──► FastAPI backend
                                         │
          ┌──────────────────────────────┼──────────────────────────────┐
          │ Ingestion                    │ Query                        │
          │ detect → parse               │ Intent analyzer (typed plan) │
          │  tables → DuckDB file        │ DAG executor → agents:       │
          │  text   → chunks + embeddings│  SQL · RAG · Compute · Viz   │
          │           → pgvector         │ Aggregator + number check    │
          └──────────┬───────────────────┴───────────────┬──────────────┘
                     ▼                                    ▼
        DuckDB files (uploaded tables)         PostgreSQL 16 + pgvector
        uploads/duckdb/upload_<id>.duckdb      users · chats · dataset registry
                                               audit · pins · document chunks
                                               Redis (cache) · Mailpit (local email)
```

Tech stack: Python (FastAPI, SQLAlchemy, pandas, sqlglot, DuckDB), React + TypeScript + Vite + Tailwind + ECharts,
PostgreSQL 16 with pgvector, Redis, Docker Compose.

## Quick start with Docker

Prerequisite: [Docker Desktop](https://www.docker.com/products/docker-desktop/).

```powershell
cd ai-analytics-dashboard
Copy-Item .env.example .env
notepad .env          # set GEMINI_API_KEY and JWT_SECRET (see below)
docker compose up --build -d
```

Open **http://localhost:3000** and sign in as `admin` with the `ADMIN_PASSWORD` from your `.env` (default
`Test@123`, **change it** before sharing the app). Sign-up emails are caught by Mailpit at **http://localhost:8025**.

Generate a strong signing key for `JWT_SECRET`:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Get a Gemini API key from [aistudio.google.com](https://aistudio.google.com). No key? Set `LLM_PROVIDER=fake` for the
demo mode described in the app's README.

## Run it locally without building Docker images

Postgres, Redis and Mailpit still run in Docker (using ready-made images, no build). The backend and frontend run on
your machine. From `ai-analytics-dashboard/`:

```cmd
docker compose up -d --no-build postgres redis mailpit
start-local.bat                      REM starts backend + frontend in the background; stop-local.bat stops them
```

Or run them in the foreground to watch live logs, each in its own Command Prompt window:

```cmd
run-backend.bat                      REM http://localhost:8000/api/health
run-frontend.bat                     REM http://localhost:5173
```

With the local scripts the app is at **http://localhost:5173** (Docker uses port 3000). Logs from `start-local` are in
`.local\logs\`. Don't run the Docker backend and the local backend at the same time (both use port 8000).

## Using it

1. **Datasets**: upload files (up to 50 MB each). Click a dataset for its schema and a preview.
2. **Ask**: type a question. Open **How I got this** to see the plan, SQL and citations. Past conversations are in the
   sidebar.
3. **Dashboard**: pin a chart and refresh it later.
4. **Admin** (admins only): manage users and read the audit log.

Sample files to try are in `ai-analytics-dashboard/backend/tests/fixtures/` (`sales_2024.csv`, `employees.json`,
`annual_report_2024.pdf`). Every signed-in user sees all datasets (one shared workspace).

## Choosing the LLM

Gemini is the default (`LLM_PROVIDER=gemini`). To switch back to OpenAI, uncomment the OpenAI blocks in
`backend/app/config.py` and `backend/app/llm/provider.py`, uncomment `openai` in `backend/requirements.txt` and the
`OPENAI_*` lines in `.env`, then set `LLM_PROVIDER=openai`. Re-upload documents after switching embedding models, because
the stored vectors are not comparable between providers.

## Upgrading from the old Postgres-tables version

Earlier versions stored uploaded tables in Postgres. Those datasets have no DuckDB file, so they no longer appear in
questions. Clean them up and re-upload:

```powershell
docker compose exec backend python scripts/purge_postgres_data.py           # dry run, review the list
docker compose exec backend python scripts/purge_postgres_data.py --yes     # apply
```

Running it against a locally started backend needs extra steps (it reads `DATABASE_URL` and `UPLOAD_DIR` from the
environment, not `.env`); follow the upgrade section in the app README and always check the dry run first.

## Running the tests

```cmd
cd ai-analytics-dashboard
docker compose up -d --no-build postgres redis mailpit
run-tests.bat tests -q               REM backend unit + integration tests (fake LLM, real Postgres/Redis/Mailpit)
```

Frontend: `cd frontend && npm test`. Tests never call a real LLM.

## Repository layout

```
ai-analytics-dashboard/
  backend/        FastAPI app (api, auth, ingestion, agents, guardrails, llm, chats, duckdb_store) and tests
  frontend/       React app
  db/init/        Postgres init (extensions, schemas, read-only role)
  docs/           design specs and implementation plans
  docker-compose.yml, .env.example
  start-local.bat / stop-local.bat / run-backend.bat / run-frontend.bat / run-tests.bat
```

`AnalyticsAI/` is a local Python virtual environment and is not tracked in git.

## Troubleshooting

- **No verification email**: open http://localhost:8025. Mailpit catches all mail locally. For real email set the
  `SMTP_*` variables in `.env`.
- **Backend won't start, says a key is missing**: set `GEMINI_API_KEY`, or use `LLM_PROVIDER=fake`.
- **Port 8000 already in use**: another backend is running (a leftover process or the Docker one). Stop it first.
- **A dataset says its file is missing**: the DuckDB file was removed (for example the uploads volume was wiped).
  Re-upload the file.
- **Postgres password changed but login fails**: the init script only runs on an empty volume. `docker compose down -v`
  resets all data.

## Security checklist before sharing publicly

Use a strong random `JWT_SECRET`, change the admin password, configure real SMTP, add your public URL to
`CORS_ORIGINS`, keep `.env` out of git (it is ignored), and back up the Postgres and uploads volumes.
