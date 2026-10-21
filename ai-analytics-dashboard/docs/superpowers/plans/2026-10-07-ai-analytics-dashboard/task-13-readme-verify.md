# Task 13: README, spec sync, full verification

**Files:**
- Create: `README.md`
- Modify: `docs/superpowers/specs/2026-10-07-ai-analytics-dashboard-design.md` (record the 5 deliberate deviations listed in the plan index)
- Create (not committed): `test_reports/verification_<date>.md` (git-ignored)

**Interfaces:** none (documentation + verification).

- [ ] **Step 1: Write the README**

**File: `README.md`**
````markdown
# AI Analytics Dashboard

A self-hosted "data insighter": upload raw files (CSV, JSON, Excel, PDF, Word, text, flat files), then ask questions in plain English. An **intent analyzer** plans the work and routes it to specialist agents: **SQL**, **document search (RAG)**, **compute**, and **visualization**. An **aggregator** then combines their outputs into one answer with charts, tables and sources.

It is built to avoid two common problems with AI-powered BI tools:

| Problem | What this project does |
|---|---|
| **Hallucinated SQL / numbers** | The SQL agent only sees the real schema. Generated SQL is parsed (sqlglot) and every table and column is checked against the catalog. Invalid SQL gets one repair attempt, then is rejected. The final answer may only quote numbers that appear in tool outputs (an automatic number check flags anything else). Document answers must cite retrieved excerpts. |
| **Security holes in generated code** | Generated SQL can only be a single `SELECT` on the `data` schema. It runs as a separate **read-only Postgres role** with a 15 s timeout and an automatic row limit. Math uses an AST-whitelisted evaluator (no `eval`/`exec`). Document text is treated as untrusted data in prompts. |

## Architecture

```
Browser ──► nginx (frontend, :3000) ──/api──► FastAPI backend (:8000)
                                               │
              ┌────────────────────────────────┼─────────────────────────────┐
              │ Ingestion                      │ Query                       │
              │ detect → parse                 │ Intent analyzer (typed plan)│
              │  tables → PostgreSQL (data.*)  │ DAG executor → agents:      │
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
- Password: `Test@123`

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
  - Tabular data becomes a typed PostgreSQL table.
  - PDFs are split: tables go to PostgreSQL, text goes to pgvector.
  - Click a dataset to see its schema and a preview.
- **Ask**: type a question. Each answer shows:
  - the text answer
  - charts and sortable tables (with CSV download)
  - a **How I got this** panel with the plan, each agent step, the exact validated SQL and the document citations

  Repeated questions are served from the Redis cache (⚡ badge). Uploading or deleting data invalidates the cache automatically.
- **Dashboard**: pin any chart. **Refresh** re-runs its stored, re-validated SQL with no LLM call.
- **Admin**: activate or deactivate users, change roles, and browse the audit log (question, plan, SQL, latency).

## Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `openai` | `openai` or `fake` (free demo) |
| `OPENAI_API_KEY` | – | Required for `openai` |
| `OPENAI_PLANNER_MODEL` | `gpt-4o` | Intent analyzer + SQL agent |
| `OPENAI_CHAT_MODEL` | `gpt-4o-mini` | RAG, compute, viz, aggregator |
| `OPENAI_EMBED_MODEL` | `text-embedding-3-small` | Must produce 1536-dim vectors |
| `POSTGRES_*`, `QUERY_RO_PASSWORD` | dev values | Use letters/digits only |
| `JWT_SECRET` | – | Long random string |
| `ADMIN_PASSWORD` | `Test@123` | Seeded admin password (first start only) |
| `SMTP_*` | Mailpit | Real SMTP server when going public |
| `MAX_UPLOAD_MB` / `MAX_SQL_ROWS` / `STEP_TIMEOUT_S` | 50 / 5000 / 30 | Limits |

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
  api/         FastAPI routers (auth, datasets, query, dashboard, admin)
  auth/        password hashing, JWT, email verification codes
  ingestion/   file detection, parsers, type inference, chunking, pipeline
  agents/      intent analyzer, executor, SQL/RAG/compute/viz agents, aggregator
  guardrails/  SQL validator, safe expression evaluator, number check
  llm/         OpenAI provider, offline fake provider, demo script
  cache/       Redis cache
frontend/src/  React pages, ECharts views, API client
db/init/       Postgres init (extensions, schemas, read-only role)
```

## Troubleshooting

- **Backend exits with "OPENAI_API_KEY is required"**: set the key in `.env`, or set `LLM_PROVIDER=fake`.
- **Changed DB passwords but login to Postgres fails**: the init script only runs on an empty volume. Run `docker compose down -v` to reset all data.
- **No verification email**: check http://localhost:8025 (Mailpit). For real email, set the `SMTP_*` variables.
- **"Could not build a valid plan"**: rephrase the question to mention the dataset or columns. The planner refuses to guess at data that isn't there.
- **Upload "failed"**: the dataset row shows the reason. Scanned image-only PDFs need OCR, which isn't supported.

## Going public later (ngrok checklist)

The frontend on port 3000 is the single entry point, so `ngrok http 3000` is enough to expose it. Before you do:

1. Change the admin password and set a strong `JWT_SECRET`.
2. Configure real `SMTP_*` settings, so verification emails reach people.
3. Add the public URL to `CORS_ORIGINS`.
4. Consider restricting sign-ups to your company domain.
5. Back up the `pgdata` volume.
````

- [ ] **Step 2: Record the deliberate deviations in the spec**

Append to `docs/superpowers/specs/2026-10-07-ai-analytics-dashboard-design.md`:
```markdown

---

## 12. Implementation notes (deviations decided during planning)

1. Schema creation uses SQLAlchemy `create_all` at startup instead of Alembic (add Alembic before production schema changes).
2. The backend connects as the Postgres owner role ("app_rw" in §3.2); `query_ro` is exactly as specified.
3. File types are detected from magic-byte signatures + extension (no libmagic).
4. Chunking is character-based (~3200 chars ≈ 800 tokens, 400 overlap) to avoid tiktoken's runtime download.
5. Ingestion is serialized with one asyncio lock (avoids slug races; adequate for local use).
6. Demo mode: `LLM_PROVIDER=fake` answers five scripted sample questions for free.
```

- [ ] **Step 3: Full verification: clean start**

Run (PowerShell, repo root; this wipes local volumes, which only hold test data at this point):
```powershell
docker compose down -v
docker compose up -d --build
docker compose ps
```
Expected: all five services `running`; `backend` and `postgres`/`redis` `healthy`.

- [ ] **Step 4: Full verification: test suites**

Run:
```powershell
docker compose run --rm backend pytest -q
docker compose build frontend
```
Expected: backend all pass (record counts); frontend build succeeds with vitest passing.

- [ ] **Step 5: Full verification: smoke test through nginx (demo mode)**

With `LLM_PROVIDER=fake` in `.env`, run this PowerShell script (it uses the real HTTP API through port 3000):
```powershell
$base = "http://localhost:3000/api"
$tok = (Invoke-RestMethod -Method Post "$base/auth/login" -ContentType "application/json" -Body '{"identifier":"admin","password":"Test@123"}').access_token
$h = @{ Authorization = "Bearer $tok" }
foreach ($f in "sales_2024.csv","employees.json","annual_report_2024.pdf") {
  curl.exe -s -H "Authorization: Bearer $tok" -F "files=@backend/tests/fixtures/$f" "$base/datasets/upload" | Out-Null
}
Start-Sleep -Seconds 8
Invoke-RestMethod "$base/datasets" -Headers $h | Select-Object slug, kind, status, row_count, chunk_count | Format-Table
$q = @{ question = "Show the monthly revenue trend and explain the Q3 dip" } | ConvertTo-Json
$r = Invoke-RestMethod -Method Post "$base/query" -Headers $h -ContentType "application/json" -Body $q
$r.steps | Select-Object id, agent, status | Format-Table
$r.answer
```
Expected: 4 datasets `ready` (`sales_2024`, `employees`, `annual_report_2024`, `annual_report_2024_table_p3_1`); steps sql/compute/viz/rag all `ok`; the answer mentions supply-chain disruptions.

Also check manually in a browser: http://localhost:3000 (login as admin, ask a demo question, chart renders, pin it, see it on Dashboard) and http://localhost:8025 (register a new user, receive the code, verify).

- [ ] **Step 6: Write the verification report (git-ignored) with actual results**

Create `test_reports/verification_2026-10-07.md` with the real pass/fail counts and any failures observed in Steps 3–5. Report failures honestly; do not mark the build done while anything is failing.

- [ ] **Step 7: Commit**

```bash
git add README.md docs
git commit -m "docs: README, implementation notes in spec"
```
