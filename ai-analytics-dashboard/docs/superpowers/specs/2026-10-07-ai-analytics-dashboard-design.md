# AI Analytics Dashboard — Design Spec

- **Date:** 2026-10-07
- **Status:** Draft — awaiting user review
- **Deployment target (now):** localhost only. Public exposure later via ngrok (out of scope for this build, but design must not block it).

---

## 1. Purpose & Success Criteria

### 1.1 Intent (user-stated)
Build a self-hosted "AI Analytics Dashboard" (data insighter) that:
- Ingests raw files of many formats (CSV, JSON, flat files, text, PDF, Excel, …), parses them, and stores structured data in PostgreSQL and unstructured text in pgvector.
- Answers natural-language questions by converting them to queries/code via an LLM, using a **multi-agent** backend: an **intent analyzer** routes each question to one or more specialist agents, whose outputs are combined into one answer.
- Produces visualizations and performs arithmetic/numerical operations.
- Uses caching.
- Requires email registration with verification; ships with a seeded `admin` / `Test@123` user.
- Runs as separate frontend/backend Docker images orchestrated by one `docker-compose.yml`.
- Specifically addresses two pain points with existing BI tools: **hallucination** in text-to-code and **security vulnerabilities**.

### 1.2 Decisions made during brainstorming
| Topic | Decision |
|---|---|
| LLM + embeddings | OpenAI API (models configurable via `.env`) |
| Data visibility | Single shared workspace — every registered user sees all data (one organization) |
| Registration | Email + password, verified by a 6-digit code sent by email |
| Orchestration | Plain async Python with a typed planner + DAG executor (no LangGraph/agent SDK) |
| Testing | Zero cost: no OpenAI calls in tests; fake LLM + fake embedder; 3 self-generated datasets |
| Hosting | localhost only; all ports bound to `127.0.0.1` |
| Extras | README, `.gitignore`s, `.dockerignore`s, `.env.example` |

### 1.3 Success criteria
1. `docker compose up --build` from a clean checkout (with `.env` filled) brings up all services healthy.
2. Admin can log in at `http://localhost:3000` with `admin` / `Test@123`.
3. A new user can register, receive a code in Mailpit (`http://localhost:8025`), verify, and log in.
4. Uploading the 3 test datasets succeeds; CSV/JSON/PDF-table land in Postgres tables, PDF text lands in pgvector.
5. Questions route to the correct agent(s); multi-agent questions combine outputs; charts render.
6. LLM-generated SQL that is destructive, references unknown tables/columns, or touches system catalogs is **blocked** before execution.
7. All numbers in a final answer are traceable to tool outputs.
8. Full automated test suite passes with **no network calls to OpenAI**.

### 1.4 Non-goals (this build)
- Public hosting / TLS / ngrok setup (documented as future work only).
- Per-user data isolation, SSO, password reset by email link (password reset can be done by admin).
- Scanned-image PDFs (OCR).
- Real-time streaming data sources / DB connectors.

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│ 1. PRESENTATION   React + Vite + TS + Tailwind + ECharts (nginx) │
└───────────────▲──────────────────────────────────────────────────┘
                │ /api (same origin, proxied by nginx), JWT bearer
┌───────────────┴──────────────────────────────────────────────────┐
│ 2. API LAYER      FastAPI — auth, validation, rate limiting      │
└──────┬─────────────────────────────┬─────────────────────────────┘
       │ upload                      │ query
┌──────▼───────────────┐   ┌─────────▼──────────────────────────────┐
│ 3. INGESTION         │   │ 4. ORCHESTRATION                       │
│ detect → parse →     │   │ Intent Analyzer → typed Plan           │
│ tabular → Postgres   │   │ DAG Executor → SQL / RAG / Compute /   │
│ text → chunk → embed │   │   Viz agents → Aggregator              │
│   → pgvector         │   └──────┬──────────────────┬──────────────┘
└──────┬───────────────┘          │                  │
       │                 ┌────────▼────────┐ ┌───────▼──────────┐
       │                 │ 5. GUARDRAILS   │ │ 6. CACHE (Redis) │
       │                 └────────┬────────┘ └──────────────────┘
┌──────▼──────────────────────────▼────────────────────────────────┐
│ 7. DATA   PostgreSQL 16 + pgvector (schemas: app, data, vector)  │
└──────────────────────────────────────────────────────────────────┘
```

Model usage: `OPENAI_PLANNER_MODEL` (default `gpt-4o`) for the intent analyzer and SQL agent (accuracy-critical); `OPENAI_CHAT_MODEL` (default `gpt-4o-mini`) for RAG, compute, viz, aggregator, and column descriptions.

All LLM access goes through one `LLMProvider` interface (`chat_json(schema, messages)`, `chat_text(messages)`, `embed(texts)`) with two implementations: `OpenAIProvider` (runtime) and `FakeLLMProvider` (tests). Selected by `LLM_PROVIDER=openai|fake`.

---

## 3. Data Model & Ingestion

### 3.1 Postgres schemas
```
app.users            id, email (unique, lowercased), password_hash, role (admin|user),
                     is_verified, is_active, created_at
app.email_codes      id, user_id, code_hash, expires_at, attempts, last_sent_at
app.datasets         id, name, slug, kind (table|document), source_filename, file_type,
                     parent_upload_id (groups tables/doc produced from one file),
                     uploaded_by, row_count, chunk_count,
                     status (pending|processing|ready|failed), error, created_at
app.dataset_columns  id, dataset_id, column_name, pg_type, sample_values (jsonb), description
app.query_audit      id, user_id, question, plan_json, sql_executed (jsonb list),
                     agents_used, latency_ms, cache_hit, status, error, created_at
app.dashboard_pins   id, title, chart_spec (jsonb), sql (validated), created_by, created_at
app.catalog_version  single-row counter, bumped on any dataset create/delete

data.<slug>          one typed table per tabular dataset (snake_case columns)

vector.chunks        id, dataset_id, chunk_index, content, metadata jsonb (file, page),
                     embedding vector(1536), content_tsv tsvector
                     indexes: HNSW (vector_cosine_ops), GIN (content_tsv)
```
Migrations via Alembic. An init SQL script (run by the postgres container) creates the extension, schemas, and roles.

### 3.2 Database roles
- `app_rw` — backend's normal role: full access to `app` and `vector`, DDL/DML in `data` (for loading).
- `query_ro` — used **only** to execute LLM-generated SQL: `USAGE` + `SELECT` on schema `data` only; no access to `app`/`vector`; `statement_timeout=15s`; `default_transaction_read_only=on`. Default privileges ensure new `data.*` tables are SELECT-able by `query_ro`.

### 3.3 Upload pipeline
1. `POST /api/datasets/upload` (multipart, multiple files). Max size `MAX_UPLOAD_MB` (default 50). Type detected by content sniffing (python-magic / signatures) + extension; mismatches or unsupported types rejected with 415.
2. File saved to `uploads` volume under a generated UUID name; dataset row(s) created `pending`; FastAPI `BackgroundTasks` processes it.
3. Parser selection:

| Input | Parser | Destination |
|---|---|---|
| CSV / TSV / delimited flat (`.dat`, `.txt` that sniffs as delimited, `.psv`) | pandas + `csv.Sniffer` | Postgres table |
| JSON / JSONL | `pandas.json_normalize` if list-of-records; otherwise pretty-printed → text | Postgres or pgvector |
| XLSX / XLS | pandas + openpyxl, one table per non-empty sheet | Postgres table(s) |
| PDF | pdfplumber: `extract_tables()` per page → tables; remaining page text → text | Postgres table(s) + pgvector |
| TXT / MD / DOCX (python-docx) / LOG | text extraction | pgvector |

4. **Tabular load:** sanitize column names (snake_case, dedupe, prefix digits), infer types (bigint, double precision, date/timestamp, boolean, text), create table in `data` schema, bulk insert via `COPY`, all in one transaction. Populate `app.dataset_columns` with type, up to 5 distinct sample values, and an LLM-generated one-line description (skipped/empty under fake provider).
5. **Text load:** chunk ~800 tokens, 100 overlap (tiktoken), batch-embed (batches of 100), insert with metadata `{file, page}`.
6. On success → `ready`, bump `catalog_version`. On failure → `failed` with human-readable error; transaction rolled back.

---

## 4. Orchestration & Agents

### 4.1 Intent Analyzer
Input: question, catalog summary (dataset name, kind, column names/types), last 3 conversation turns.
Output (OpenAI JSON-schema strict mode), validated with Pydantic:
```json
{ "intent": "string",
  "answerable": true,
  "reason": "string|null",
  "steps": [
    {"id": "s1", "agent": "sql|rag|compute|viz", "task": "string",
     "datasets": ["slug"], "depends_on": ["id"]}
  ] }
```
Plan validation (code, not LLM): agents in allowed set; datasets exist in catalog and match kind (sql→table, rag→document); `depends_on` references existing ids; no cycles; ≤ 6 steps. Invalid plan → one re-plan attempt with the validation error, then fail with a clear message. `answerable:false` → returned to user with `reason`, no agents run.

### 4.2 Executor
Topological execution; independent steps run concurrently via `asyncio.gather`; per-step timeout `STEP_TIMEOUT_S` (30). A step receives outputs of its dependencies. Failed step → recorded; dependents skipped; independent steps continue; aggregator reports partial results.

### 4.3 Agents
| Agent | Input | Behavior | Output |
|---|---|---|---|
| SQL | task + schema of named tables (columns, types, samples, descriptions) | LLM writes one Postgres SELECT → guardrail validates → executed as `query_ro`. On validation/DB error: one repair attempt with the error message. | `{columns, rows (≤5000), sql, truncated}` |
| RAG | task + dataset filter | Hybrid retrieval: pgvector cosine top 20 + full-text top 20, merged by Reciprocal Rank Fusion → top 8. LLM answers only from chunks, returning cited chunk ids; ids not in retrieved set are dropped; zero valid citations → "not found in documents". | `{answer, citations: [{file, page, chunk_id, snippet}]}` |
| Compute | task + dependency tables | LLM returns a list of operations from an allow-list: `sum, mean, median, min, max, count, std, pct_change, growth, ratio, rank, percentile, corr, moving_avg, group_agg, expression`. Executed in pandas/numpy. `expression` evaluated by `safe_eval` (AST whitelist: numeric literals, column names, `+ - * / ** % ()`, unary minus, functions `abs round sqrt log exp min max`). | `{columns, rows, operations_applied}` |
| Viz | task + dependency tables | LLM returns chart spec (JSON schema): `type ∈ {bar, line, area, pie, scatter, table, kpi}`, `x`, `y[]`, `series?`, `title`. Fields validated against actual data columns. | `{chart_spec, data_ref}` |
| Aggregator | question + all step outputs | LLM writes the final answer using only provided outputs. `number_check` extracts numbers from the answer and verifies each appears (within rounding tolerance) in step outputs or is a trivially derived value (years, counts of items listed); failure → one regeneration, then flag `unverified_numbers` in response. | `{answer, charts[], tables[], sources, plan, sql[]}` |

---

## 5. Guardrails & Security

### 5.1 SQL validator (`sqlglot`, dialect postgres)
Reject unless **all** hold:
- Exactly one statement; root is `SELECT` (CTEs/`WITH`, `UNION` of selects allowed).
- No DML/DDL/`COPY`/`CALL`/`DO`/`SET`/`GRANT`/transaction statements anywhere in the tree.
- Every table reference is in schema `data` (unqualified names are qualified to `data.`) and exists in the catalog; CTE names allowed.
- Every column reference resolves to a catalog column of a referenced table (or a CTE/alias output).
- No function calls on a denylist: `pg_*`, `dblink*`, `lo_*`, `current_setting`, `set_config`, `query_to_xml`, `version`, any `*_file`.
- No references to `information_schema`, `pg_catalog`, `app`, `vector`.
- `LIMIT` added (or clamped) to `MAX_SQL_ROWS` (5000).
DB-level backstops: `query_ro` role, read-only transaction, statement timeout.

### 5.2 Other controls
- Passwords: bcrypt (passlib). Policy: ≥8 chars, letter + digit.
- JWT access tokens (HS256, 60 min), secret from `.env`.
- Verification codes: 6 digits, stored as HMAC-SHA256 hash, 10-min expiry, 5 attempts max, 60-s resend cooldown.
- Rate limiting (slowapi): auth endpoints 10/min/IP; query 30/min/user; upload 10/min/user.
- Roles: `admin` can delete datasets, manage users, view audit log; `user` can upload, query, pin.
- Prompt-injection: retrieved document text wrapped in explicit delimiters and labeled as untrusted data in system prompts; agents have no capabilities beyond their single function.
- No `eval`/`exec` of model output anywhere.
- Uploaded files stored under random names; never served back raw.
- CORS restricted to `http://localhost:3000`; configurable for later ngrok domain.
- All compose ports bound to `127.0.0.1`.
- Backend container runs as non-root.

---

## 6. Caching (Redis)
| Cache | Key | TTL |
|---|---|---|
| Answer | `ans:{catalog_version}:{sha256(normalized question)}` | 1 h |
| SQL result | `sql:{catalog_version}:{sha256(sql)}` | 1 h |
| Query embedding | `emb:{model}:{sha256(text)}` | 24 h |
| Catalog snapshot | in-process, keyed by `catalog_version` | until version changes |

Redis configured `maxmemory 256mb`, `allkeys-lru`. Bumping `catalog_version` implicitly invalidates answer/SQL caches. Responses include `cache_hit: bool`. Cache failures degrade gracefully (logged, bypassed).

---

## 7. API (all under `/api`)
| Method & path | Auth | Purpose |
|---|---|---|
| POST `/auth/register` | – | create unverified user, send code |
| POST `/auth/verify` | – | email + code → verified, returns JWT |
| POST `/auth/resend-code` | – | resend (cooldown) |
| POST `/auth/login` | – | returns JWT (verified + active only) |
| GET `/auth/me` | user | current user |
| POST `/datasets/upload` | user | upload files |
| GET `/datasets` | user | list with status |
| GET `/datasets/{id}` | user | schema + preview (20 rows or 20 chunks) |
| DELETE `/datasets/{id}` | admin | drop table / chunks, bump version |
| POST `/query` | user | `{question, history?}` → answer payload |
| GET/POST/DELETE `/dashboard/pins` | user (delete: creator or admin) | shared pinned charts |
| POST `/dashboard/pins/{id}/refresh` | user | re-run stored validated SQL (no LLM) |
| GET `/admin/users`, PATCH `/admin/users/{id}` | admin | list / activate-deactivate / set role |
| GET `/admin/audit` | admin | paginated audit log |
| GET `/health` | – | liveness incl. DB + Redis |

Admin seeding: on startup, if no user with username/email `admin` exists, create it with password `Test@123`, role admin, verified. Login accepts email **or** the literal username `admin`.

---

## 8. Frontend
React 18 + Vite + TypeScript + Tailwind + ECharts; React Router; TanStack Query for API state.

| Page | Contents |
|---|---|
| Login / Register / Verify | forms; 6-digit code input with resend countdown |
| Ask | chat-style Q&A; answer cards with text, charts, sortable table + CSV download, collapsible "How I got this" (plan, agents, SQL, citations), cached badge, "Pin to dashboard" |
| Datasets | drag-and-drop multi-upload, live status polling, schema/row or chunk preview, admin-only delete |
| Dashboard | shared grid of pinned charts with refresh |
| Admin | users table, audit log |

Light/dark theme, sidebar layout. JWT kept in memory + `sessionStorage`.

---

## 9. Deployment

### 9.1 Layout
```
ai-analytics-dashboard/
├── docker-compose.yml  .env.example  .gitignore  README.md
├── docs/superpowers/specs/
├── db/init/01-init.sql
├── backend/  Dockerfile .dockerignore .gitignore pyproject.toml
│   ├── app/{main.py,config.py,api/,auth/,db/,ingestion/,agents/,guardrails/,llm/,cache/}
│   ├── scripts/generate_test_data.py
│   └── tests/{unit,integration,fixtures,live}/
└── frontend/ Dockerfile nginx.conf .dockerignore .gitignore package.json src/
```

### 9.2 Compose services (all ports bound to 127.0.0.1)
| Service | Image | Port |
|---|---|---|
| frontend | build `frontend/` (node:20 build → nginx:alpine); proxies `/api` → backend | 3000 |
| backend | build `backend/` (python:3.12-slim, non-root, uvicorn); runs migrations + seed on start | 8000 |
| postgres | `pgvector/pgvector:pg16`, volume `pgdata`, `db/init` mounted | 5432 |
| redis | `redis:7-alpine` with maxmemory config | 6379 |
| mailpit | `axllent/mailpit` | 8025 (UI), 1025 (SMTP) |

Healthchecks on postgres, redis, backend; `depends_on: condition: service_healthy`.

### 9.3 Future public exposure (not built now)
The frontend on port 3000 is the single entry point (it proxies the API), so `ngrok http 3000` will be sufficient later. To prepare, `CORS_ORIGINS`, `PUBLIC_BASE_URL`, and SMTP settings are already env-driven; README will include a "Going public" checklist (real SMTP, strong `JWT_SECRET`, change admin password, tighten rate limits).

### 9.4 Environment (`.env.example`)
`OPENAI_API_KEY`, `LLM_PROVIDER=openai`, `OPENAI_CHAT_MODEL=gpt-4o-mini`, `OPENAI_PLANNER_MODEL=gpt-4o`, `OPENAI_EMBED_MODEL=text-embedding-3-small`, `POSTGRES_*`, `QUERY_RO_PASSWORD`, `REDIS_URL`, `JWT_SECRET`, `SMTP_HOST=mailpit`, `SMTP_PORT=1025`, `SMTP_FROM`, `MAX_UPLOAD_MB=50`, `MAX_SQL_ROWS=5000`, `STEP_TIMEOUT_S=30`, `CORS_ORIGINS=http://localhost:3000`, `ADMIN_PASSWORD=Test@123`.

---

## 10. Testing (zero cost)

### 10.1 Test data (`backend/scripts/generate_test_data.py`, fixed seed)
1. `sales_2024.csv` — ~2,000 orders: order_id, order_date, region, product, category, quantity, unit_price, revenue.
2. `employees.json` — 200 records with nested `address{city,state}`, department, salary, hire_date.
3. `annual_report_2024.pdf` — 3 pages of prose (strategy, Q3 supply-chain issues, outlook) + one quarterly KPI table (generated with reportlab).
The generator also writes `expected_answers.json` (ground-truth aggregates computed with pandas).

### 10.2 Cost controls
- `LLM_PROVIDER=fake` in all tests. `FakeLLMProvider` returns scripted plans/SQL/answers keyed by test question, including deliberately malicious outputs.
- `FakeEmbedder`: deterministic hash-seeded unit vectors (1536-d) so pgvector queries run for real.
- A network guard fixture fails any test that tries to reach `api.openai.com`.
- `tests/live/` (real OpenAI) is marked `@pytest.mark.live`, excluded by default, and **will not be run** during this build.

### 10.3 Suites
- **Unit:** parsers (row/column counts, types, PDF table + text split), chunker, SQL validator (~40 allow/deny cases), safe_eval, number_check, plan validation, executor ordering/timeout/partial failure, cache keys, password/code hashing.
- **Integration** (against compose Postgres/Redis/Mailpit): register → read code from Mailpit API → verify → login; expired/wrong-attempts/cooldown; admin seeded; role enforcement; upload 3 fixtures via API and verify tables/chunks; `/query` end-to-end with fake LLM for SQL-only, RAG-only, multi-agent (sql+compute+viz+rag), unanswerable, and malicious-SQL-blocked cases; `query_ro` cannot read `app.users` even with hand-written SQL; cache hit on repeat and invalidation after upload.
- **Frontend:** Vitest smoke tests for auth flow components and chart rendering from a spec.

### 10.4 Verification before claiming done
`docker compose up --build` healthy → backend unit + integration tests pass → frontend tests + build pass → manual smoke: admin login, upload fixtures, one question per agent type. Results reported faithfully including failures.

---

## 11. Error Handling
- API errors return `{error: {code, message}}` with proper HTTP status; no stack traces to clients.
- Ingestion failures stored on the dataset row and shown in UI.
- Agent step failures surface in the answer as "partial result: <step> failed because <reason>".
- OpenAI errors (rate limit / timeout): 2 retries with exponential backoff, then a user-facing error.
- Structured JSON logging; every query gets a `request_id` written to the audit log.

---

## 12. Implementation notes (deviations decided during planning)

1. Schema creation uses SQLAlchemy `create_all` at startup instead of Alembic (add Alembic before production schema changes).
2. The backend connects as the Postgres owner role ("app_rw" in §3.2); `query_ro` is exactly as specified.
3. File types are detected from magic-byte signatures + extension (no libmagic).
4. Chunking is character-based (~3200 chars ≈ 800 tokens, 400 overlap) to avoid tiktoken's runtime download.
5. Ingestion is serialized with one asyncio lock (avoids slug races; adequate for local use).
6. Demo mode: `LLM_PROVIDER=fake` answers five scripted sample questions for free.
