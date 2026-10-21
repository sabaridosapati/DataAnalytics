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
