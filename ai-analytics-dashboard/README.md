# AI Analytics Dashboard

A self-hosted "data insighter": upload raw files (CSV, JSON, Excel, PDF, Word, text, flat files), then ask questions in plain English. An **intent analyzer** plans the work and routes it to specialist agents: **SQL**, **document search (RAG)**, **compute**, and **visualization**. An **aggregator** then combines their outputs into one answer with charts, tables and sources.

It is built to avoid two common problems with AI-powered BI tools:

| Problem | What this project does |
|---|---|
| **Hallucinated SQL / numbers** | The SQL agent only sees the real schema. Generated SQL is parsed (sqlglot) and every table and column is checked against the catalog. Invalid SQL gets one repair attempt, then is rejected. The final answer may only quote numbers that appear in tool outputs (an automatic number check flags anything else). Document answers must cite retrieved excerpts. |
| **Security holes in generated code** | Generated SQL can only be a single `SELECT` on the `data` schema. It runs in a **read-only, sandboxed DuckDB** connection with a timeout and an automatic row limit. Math uses an AST-whitelisted evaluator (no `eval`/`exec`). Document text is treated as untrusted data in prompts. |

## Architecture

```
Browser ──► nginx (frontend, :3000) ──/api──► FastAPI backend (:8000)
                                               │
              ┌────────────────────────────────┼─────────────────────────────┐
              │ Ingestion                      │ Query                       │
              │ detect → parse                 │ Intent analyzer (typed plan)│
              │  tables → DuckDB files        │ DAG executor → agents:      │
              │  text   → chunks + embeddings  │  SQL · RAG · Compute · Viz  │
              │           → pgvector           │ Aggregator + number check   │
              └───────────────┬────────────────┴──────────────┬──────────────┘
                              ▼                               ▼
                 PostgreSQL 16 + pgvector              Redis (cache)
                 schemas: app · data · vector          Mailpit (local email)
```

Services (all bound to `127.0.0.1` only): `frontend`, `backend`, `postgres`, `redis`, `mailpit`.

## Quick start (Windows PowerShell)

Prerequisites: [Docker Desktop](https://www.docker.com/products/docker-desktop/). For real questions you also need an **OpenAI API key** from platform.openai.com. A ChatGPT subscription does not include API access; API usage is billed separately.

```powershell
cd ai-analytics-dashboard
Copy-Item .env.example .env
notepad .env        # set OPENAI_API_KEY, JWT_SECRET (any long random string), passwords
docker compose up --build -d
```

Open **http://localhost:3000** and sign in with:

- Username: `admin`
- Password: `useyourpwd`

Change this password before exposing the app anywhere.

### Free demo mode (no OpenAI key)

Set `LLM_PROVIDER=fake` in `.env` and run `docker compose up -d --build backend`. The app then answers these sample questions with scripted agent outputs. The real SQL validation, database queries, retrieval and charts still run; only the LLM is replaced.

1. What is the total revenue by region?
2. Show the monthly revenue trend
3. Why did Q3 performance drop?
4. What is the average salary by department?
5. Show the monthly revenue trend and explain the Q3 dip

First upload the sample files from `backend/tests/fixtures/` on the **Datasets** page: `sales_2024.csv`, `employees.json` and `annual_report_2024.pdf`.

## Registering users

1. Click **Create an account**, enter your email and password.
2. A 6-digit code is emailed to you. Locally, all mail is caught by **Mailpit**: open **http://localhost:8025** to read it.
3. Enter the code to finish. Codes expire after 10 minutes, allow 5 attempts, and can be resent after 60 seconds.

Every verified user sees all datasets (one shared organization workspace). Only admins can delete datasets, manage users and see the query audit log.

## Using it

- **Datasets**: drag and drop files (up to 50 MB each, 10 per upload).
  - Tabular data becomes a typed table in a DuckDB file (see below).
  - PDFs are split: tables go to DuckDB, text goes to pgvector.
  - Click a dataset to see its schema and a preview.
- **Ask**: type a question. Each answer shows:
  - the text answer
  - charts and sortable tables (with CSV download)
  - a **How I got this** panel with the plan, each agent step, the exact validated SQL and the document citations

  Repeated questions are served from the Redis cache (⚡ badge). Uploading or deleting data invalidates the cache automatically.
- **Dashboard**: pin any chart. **Refresh** re-runs its stored, re-validated SQL with no LLM call.
- **Admin**: activate or deactivate users, change roles, and browse the audit log (question, plan, SQL, latency).

### Chat history

Every question is saved to Postgres (`app.chats`, `app.chat_turns`) under the signed-in user. The Ask page lists
your conversations; open one to continue it, or delete it. Follow-up questions use the last 3 answered turns of the
open chat as context. Admins cannot read other users' chats (they keep the query audit under Admin).

### Structured data (DuckDB)

CSV, TSV, Excel and JSON tables (and tables found inside PDFs) are converted to one DuckDB file per upload:
`<UPLOAD_DIR>/duckdb/upload_<id>.duckdb`. In Docker this is `/data/uploads/duckdb/` (the `uploads` volume); when you
run the backend with `run-backend.bat` or `start-local.ps1`, `UPLOAD_DIR` is `backend\uploads`, so the files are in
`backend\uploads\duckdb\`. Questions about these tables run in DuckDB (read-only, sandboxed). Postgres keeps users,
email codes, chats, the dataset registry, the audit log, pins and document embeddings. Deleting a dataset removes its
DuckDB file.

#### Upgrading from the Postgres-tables version

Earlier versions stored tables in Postgres (`data.*`). To clean those up, run from the `backend` folder:

```powershell
python scripts/purge_postgres_data.py          # dry run: shows what would be removed
python scripts/purge_postgres_data.py --yes    # drops data.* tables and 'table' dataset rows
```

Then re-upload your files.

## Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `gemini` | `gemini` or `fake` (free demo); `openai` after un-commenting the OpenAI code |
| `GEMINI_API_KEY` | – | Required for `gemini` (aistudio.google.com) |
| `GEMINI_PLANNER_MODEL` | `gemini-2.5-flash-lite` | Intent analyzer + SQL agent |
| `GEMINI_CHAT_MODEL` | `gemini-2.5-flash-lite` | RAG, compute, viz, aggregator |
| `GEMINI_EMBED_MODEL` | `gemini-embedding-2` | Truncated to 1536 dims to match the DB column |
| `POSTGRES_*`, `QUERY_RO_PASSWORD` | dev values | Use letters/digits only |
| `JWT_SECRET` | – | Long random string |
| `ADMIN_PASSWORD` | `Test@123` | Seeded admin password (first start only) |
| `SMTP_*` | Mailpit | Real SMTP server when going public |
| `MAX_UPLOAD_MB` / `MAX_SQL_ROWS` / `STEP_TIMEOUT_S` | 50 / 5000 / 30 | Limits |
| `DUCKDB_DIR` | `<UPLOAD_DIR>/duckdb` | Where the per-upload DuckDB files live |
| `DUCKDB_MEMORY_LIMIT` | `512MB` | Memory limit per DuckDB query connection |
| `DUCKDB_THREADS` | `2` | DuckDB threads per query connection |

## Tests (free, no OpenAI calls)

```powershell
docker compose up -d postgres redis mailpit
docker compose run --rm backend pytest -q          # unit + integration (fake LLM, real Postgres/Redis/Mailpit)
docker compose build frontend                      # runs vitest + type-check + build
```

A network guard makes any test that tries to reach `api.openai.com` fail. The live OpenAI smoke test in `backend/tests/live` only runs if you explicitly set `LIVE_OPENAI_API_KEY` and pass `-m live`.

Regenerate the sample data:

```powershell
docker compose run --rm --no-deps -v "${PWD}/backend/tests/fixtures:/out" --user root backend python scripts/generate_test_data.py /out
```

## Project layout

```
backend/app/
  api/         FastAPI routers (auth, datasets, query, chats, dashboard, admin)
  chats.py     saved chats and turns (api/chats.py routes, server-side follow-up context)
  auth/        password hashing, JWT, email verification codes
  ingestion/   file detection, parsers, type inference, chunking, pipeline
  duckdb_store.py  writes/reads the per-upload DuckDB files (atomic create, preview, delete)
  maintenance.py   one-off Postgres clean-up used by scripts/purge_postgres_data.py
  agents/      intent analyzer, executor, SQL/RAG/compute/viz agents, aggregator
  guardrails/  SQL validator, safe expression evaluator, number check
  llm/         OpenAI provider, offline fake provider, demo script
  cache/       Redis cache
frontend/src/  React pages, ECharts views, API client
backend/scripts/purge_postgres_data.py  removes old Postgres data.* tables (dry run by default)
db/init/       Postgres init (extensions, schemas, read-only role)
```

## Troubleshooting

- **Backend exits with "OPENAI_API_KEY is required"**: set the key in `.env`, or set `LLM_PROVIDER=fake`.
- **Changed DB passwords but login to Postgres fails**: the init script only runs on an empty volume. Run `docker compose down -v` to reset all data.
- **No verification email**: check http://localhost:8025 (Mailpit). For real email, set the `SMTP_*` variables.
- **"Could not build a valid plan"**: rephrase the question to mention the dataset or columns. The planner refuses to guess at data that isn't there.
- **A dataset's file is missing** (e.g. the uploads folder or volume was wiped): its DuckDB file is gone, so re-upload the file.
- **Upload "failed"**: the dataset row shows the reason. Scanned image-only PDFs need OCR, which isn't supported.

## Going public later (ngrok checklist)

The frontend on port 3000 is the single entry point, so `ngrok http 3000` is enough to expose it. Before you do:

1. Change the admin password and set a strong `JWT_SECRET`.
2. Configure real `SMTP_*` settings, so verification emails reach people.
3. Add the public URL to `CORS_ORIGINS`.
4. Consider restricting sign-ups to your company domain.
5. Back up the `pgdata` volume.
