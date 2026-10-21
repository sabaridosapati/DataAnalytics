# Structured data in DuckDB, Postgres for app state — design

Date: 2026-10-20 · Status: draft for review

## Goal

Process all structured uploads (CSV, TSV/PSV, Excel sheets, JSON arrays) with **DuckDB** instead of loading them
into Postgres. Each upload becomes a `.duckdb` file; every analytical SQL query the app runs against uploaded
tables executes in DuckDB. Postgres no longer stores or queries any uploaded row data.

**Understanding (confirmed in chat):**
- "Structured data" = everything that today becomes a `data.<slug>` table in Postgres.
- Existing structured data in Postgres is **cleaned up, not migrated**; users re-upload.
- Layout: **one `.duckdb` file per upload**, holding every table produced from that upload.
- Postgres keeps: `users`, `email_codes`, `chats`, `chat_turns` **plus** the small app tables
  (`datasets`, `dataset_columns`, `query_audit`, `dashboard_pins`, `catalog_version`) and the pgvector document
  chunks (`vector.chunks`). Documents (PDF/DOCX/TXT) are unstructured and are not touched.
- Stack assumption: the stricter reading ("Postgres only for chats/users") was offered and not chosen; moving the
  registry, audit log and document embeddings out of Postgres is a non-goal here.

## Non-goals

- Moving documents, embeddings, the dataset registry, audit log or dashboard pins out of Postgres.
- Reading CSVs natively with DuckDB (`read_csv`) for speed. Parsing/typing stays on the existing pandas path;
  this can be a later optimisation.
- Per-user dataset isolation (datasets remain shared by all users, as today).
- Migrating old data, or old pinned-chart rows (pins keep their saved SQL; see Cleanup).
- Dropping the unused `data` schema and `query_ro` role from Postgres (left in place, unused).

## Current behaviour (what changes)

Upload → `ingestion/pipeline.py` parses with pandas, cleans column names, infers types, asks the LLM for column
descriptions, then `create_and_load_table` creates `data.<slug>` in Postgres (COPY) and grants SELECT to the
read-only role `query_ro`. Questions → the SQL agent writes Postgres SQL, `guardrails/sql_validator.py` validates
it with sqlglot (`postgres` dialect), `agents/sql_exec.run_readonly_query` runs it through an asyncpg pool as
`query_ro`. Dataset preview and delete, dashboard pin refresh, the demo script and ~12 test files assume this.

## Design

### 1. Storage layout

- Directory `settings.duckdb_dir` (default `<UPLOAD_DIR>/duckdb`).
- File name `upload_<U>.duckdb` where `U` is the upload id: the `parent_upload_id` of a table dataset, or its own
  `id` when it has no parent. All tables from one upload live in that file as `main.<slug>`.
- `app.datasets` keeps **one row per table** (existing behaviour: parent row + child rows for multi-sheet/
  multi-array uploads). The file path is **derived** (`duckdb_dir / f"upload_{U}.duckdb"`), so no schema change.
- `app.dataset_columns.pg_type` keeps its name (no migration) but now stores **DuckDB type names**:
  `BIGINT`, `DOUBLE`, `BOOLEAN`, `DATE`, `TIMESTAMP`, `VARCHAR`. `infer_types` emits these names directly;
  its tests are updated. The column name is documented as a legacy misnomer.
- Writes are **atomic**: data is written to `upload_<U>.duckdb.tmp` and renamed on success. A failed or
  interrupted upload leaves no partial file (stale `.tmp` files are removed at startup by `recover_interrupted`).
- The original uploaded file (`Dataset.stored_path`) is kept and removed exactly as today.

### 2. Ingestion

- Unchanged: parsing, column-name cleaning, type inference, LLM column descriptions, slug generation,
  `ingest_lock`, status transitions, error recording.
- Replaced: `create_and_load_table` → `write_duckdb_table(db_path_tmp, slug, df, types) -> int` in a new module
  `app/duckdb_store.py`. It runs in a worker thread (`asyncio.to_thread`), registers the DataFrame, and executes
  `CREATE TABLE main."<slug>" (<col> <TYPE>, …)` + `INSERT … SELECT` with explicit casts, returning the row count.
- Multi-table uploads write all their tables into the **same** temp file before the rename; a failure in any
  table fails the whole upload (matches the current "failed part" handling).

### 3. Query path

`agents/sql_exec.run_readonly_query(state, sql, version, settings)` keeps its signature, caching (Redis key by
catalog version + SQL) and return shape. Internally:

1. Parse the (already validated) SQL, collect the `data.<slug>` tables it references, resolve each slug through
   the catalog to its upload file. Unknown slug → `QueryExecutionError`.
2. In `asyncio.to_thread`: create a **fresh in-memory DuckDB connection**, `ATTACH '<file>' AS up_<U> (READ_ONLY)`
   for each distinct file, `CREATE SCHEMA data`, and for each table
   `CREATE VIEW data."<slug>" AS SELECT * FROM up_<U>.main."<slug>"`. The SQL agent therefore keeps writing
   `data.<slug>` and cross-dataset joins keep working.
3. Lock the connection: `SET memory_limit`, `SET threads`, `SET enable_external_access=false`,
   `SET lock_configuration=true` — in that order, **after** the ATTACH statements.
4. Execute with a **timeout**: a `threading.Timer` calls `connection.interrupt()` after
   `settings.step_timeout_s`; an interrupted query raises `QueryExecutionError("… timed out")`.
5. Fetch at most `max_sql_rows`; return `{columns, rows, row_count, truncated}`; close the connection.

The row/column serialisation reuses `to_jsonable`; `Decimal`, `date`, `datetime` are already handled.

### 4. SQL validation (`guardrails/sql_validator.py`)

- Dialect `postgres` → `duckdb` for parsing, qualification and output. Same rules: exactly one statement, SELECT /
  set operations only, forbidden node types, single allowed schema `data`, known tables/columns, LIMIT cap.
- New DuckDB-specific rules (parser layer, in addition to the connection lock):
  - Reject any `Table` whose source is not a plain identifier (table functions such as `read_csv`,
    `read_parquet`, `read_json`, `glob`, `sniff_csv`, replacement scans like `FROM 'file.csv'`).
  - Deny function names/prefixes: `read_`, `glob`, `getenv`, `duckdb_`, `pragma_`, `current_setting`,
    `sniff_`, `parquet_`, `write_`, `copy`, `load`, `install`, `query`, `query_table`, `current_database`,
    `current_schema`, `version`, `which_secret`, `list_secrets` (final list fixed in the plan from DuckDB docs).
  - Reject `catalog`-qualified references and `information_schema`/`duckdb_*`/`pg_*` system relations.
- The Postgres-only deny entries (`pg_*`, `dblink`, `lo_`, `txid_current`, …) are dropped.
- The prompt for the SQL agent (`agents/sql_agent.py`) says the engine is **DuckDB** and keeps "Reference tables as
  data.<table>". `llm/demo_script.py` SQL is rewritten for DuckDB (e.g. `::numeric` → `ROUND(…, 2)` on
  `DOUBLE`, `date_trunc` kept).

### 5. Other consumers

- `api/datasets.py` preview: reads the first 20 rows from the dataset's DuckDB file through the same helper
  (read-only attach, no view layer needed).
- `api/datasets.py` delete: deletes the dataset row; when the **last** table dataset of an upload is removed, deletes
  the `.duckdb` file. If the file is locked (Windows, in use by a running query) the API returns 409 `busy`
  and nothing is deleted.
- `api/dashboard.py` pin refresh: unchanged call to `run_readonly_query`.
- `main.py`: remove the asyncpg `ro_pool` creation/close. `QUERY_RO_URL` / the `query_ro` role stay in config and
  the DB init script (unused by the app; `test_db.py` still verifies the role is locked down).
- `catalog.py`: `DatasetInfo` gains `upload_id: int` (parent or self) so the path can be derived; the type labels
  shown to the LLM become DuckDB type names.
- `requirements.txt`: add `duckdb`. `README.md` documents the new storage and the purge script.

### 6. Clean-up of existing Postgres data

`backend/scripts/purge_postgres_data.py` (run as `python scripts/purge_postgres_data.py`; **dry-run by default**,
`--yes` to apply). It:
1. lists and drops every table in Postgres schema `data`;
2. deletes `app.datasets` rows with `kind = 'table'` (their `app.dataset_columns` rows cascade);
3. bumps `catalog_version`;
4. prints what it removed. Documents, chunks, users, chats, audit rows and pins are not touched.

It is deliberately **not** run automatically: it destroys uploaded data. Users then re-upload; old dashboard pins keep
their stored SQL and work again once a table with the same slug exists.

## Error handling

| Situation | Behaviour |
|---|---|
| Parse/type/DuckDB write error during upload | dataset marked `failed` with the error text, no `.duckdb` file left |
| Upload file missing at query time (e.g. deleted by hand) | `QueryExecutionError("Dataset file is missing; re-upload it")` → SQL step error, answer says it couldn't answer |
| Query exceeds `step_timeout_s` | interrupted → `QueryExecutionError("timed out")` |
| Query exceeds memory limit | DuckDB error surfaced as `QueryExecutionError` |
| Delete while a query is reading the file | 409 `busy`, dataset kept |
| Validator rejects SQL | existing `SQLValidationError` path (retry loop in the SQL agent) unchanged |

## Security

The SQL is LLM-generated, so defence in depth stays: (1) parser-level validation; (2) read-only attach of the
upload files; (3) `enable_external_access=false` + `lock_configuration=true` so even a validator bypass cannot read
other files, use `httpfs`, install extensions or change settings; (4) memory/thread/timeout limits; (5) fresh
connection per query, nothing shared between users' queries. DuckDB files hold only uploaded data (no secrets).

## Testing

- Unit: `duckdb_store` (types → DuckDB DDL, NaN/NA/dates, empty frame, special column names, atomic rename,
  failure leaves no file); validator rewritten for DuckDB: allowed SELECT/CTE/JOIN/window/aggregate, rejection of
  table functions, replacement scans, `read_csv`, `ATTACH`/`COPY`/`PRAGMA`/`INSTALL`, system relations, multi-
  statement, unknown table/column, LIMIT cap; `infer_types` expectations updated to DuckDB names.
- Integration (real DuckDB files in a temp dir, existing fake LLM): upload CSV/Excel/JSON → file exists with the
  right tables and rows; question answered with the same ground truth as `expected_answers.json`; cross-dataset
  join; preview; delete removes the file (and 409 when busy); failed upload leaves nothing; dashboard pin refresh;
  a malicious SQL (`read_csv('/etc/passwd')`, `COPY`, `ATTACH`) is rejected and, if the validator is bypassed in a
  test, blocked by the connection lock; query timeout via a deliberately slow query.
- The plan added `tests/unit/test_duckdb_store.py`, including the concurrency spike.
- The existing integration/unit suite (255 backend tests) is adapted, not deleted: tests that asserted Postgres
  `data` tables or `query_ro` now assert DuckDB files; unrelated tests must pass unchanged.
- Purge script: dry run changes nothing; `--yes` drops tables and `table` rows only.

## Risks / open items

- **Concurrent read-only attach of the same file from several in-memory connections** (one per query) is believed to
  be supported by DuckDB but is the load-bearing assumption of the query path. The plan's **first task is a spike
  test** (two threads, same file, `READ_ONLY`, plus a lock-configuration check). If it fails, fall back to a
  process-wide read-only connection per upload file, with one `cursor()` per query.
- DuckDB SQL differs from Postgres (casts, `date_trunc` return types, `numeric`, string/regex functions): the LLM
  prompt and demo script change, and answer quality on unusual questions should be spot-checked manually.
- `app.dataset_columns.pg_type` is a misnomer kept to avoid a migration.
- Windows cannot delete a `.duckdb` file while it is attached; handled via 409 `busy`.
- There is no DuckDB schema-evolution story: re-upload replaces a file.
