"""Regression tests for findings from the final code review."""
import time

import pytest

from app.guardrails.safe_eval import UnsafeExpressionError, safe_eval
from app.guardrails.sql_validator import SQLValidationError, validate_sql

SCHEMA = {"sales": {"region": "text", "revenue": "double precision"}}


@pytest.mark.parametrize(
    "sql",
    [
        # CTE defined in an inner scope must not whitelist the same name in an outer scope
        "SELECT r.rolname FROM (WITH pg_roles AS (SELECT 1 AS a) SELECT a FROM pg_roles) s CROSS JOIN pg_roles r",
        "SELECT t.tablename FROM (WITH pg_tables AS (SELECT 1 AS a) SELECT a FROM pg_tables) s CROSS JOIN pg_tables t",
        "SELECT * FROM sales WHERE region IN (WITH pg_stat_activity AS (SELECT 'x' AS q) SELECT q FROM pg_stat_activity) "
        "UNION ALL SELECT query, 1 FROM pg_stat_activity",
        # CTE names shadowing system relations are rejected outright
        "WITH pg_roles AS (SELECT region FROM sales) SELECT region FROM pg_roles",
    ],
)
def test_cte_scope_shadowing_is_blocked(sql):
    with pytest.raises(SQLValidationError):
        validate_sql(sql, SCHEMA, 100)


@pytest.mark.parametrize(
    "sql",
    [
        "WITH t AS (SELECT region FROM sales) SELECT region FROM t",
        "WITH t AS (SELECT region FROM sales) SELECT x.region FROM (SELECT region FROM t) x",
        "WITH a AS (SELECT region FROM sales), b AS (SELECT region FROM a) SELECT region FROM b",
    ],
)
def test_legitimate_ctes_still_allowed(sql):
    out = validate_sql(sql, SCHEMA, 100)
    assert "data.sales" in out


@pytest.mark.parametrize(
    "sql",
    [
        'SELECT "pg_sleep"(5)',
        "SELECT \"set_config\"('statement_timeout', '0', false)",
        "SELECT \"query_to_xml\"('select 1', true, true, '')",
        'SELECT "lo_import"(\'/etc/passwd\')',
        "SELECT pg_catalog.pg_sleep(1)",
    ],
)
def test_quoted_and_dotted_function_names_are_blocked(sql):
    with pytest.raises(SQLValidationError, match="not allowed"):
        validate_sql(sql, SCHEMA, 100)


def test_nested_exponent_cannot_freeze_the_server():
    started = time.perf_counter()
    with pytest.raises(UnsafeExpressionError):
        safe_eval("(((9**99)**99)**99)**99", {})
    assert time.perf_counter() - started < 1.0


def test_large_integer_names_cannot_freeze_the_server():
    started = time.perf_counter()
    with pytest.raises(UnsafeExpressionError):
        safe_eval("((n**99)**99)**99", {"n": 9})
    assert time.perf_counter() - started < 1.0
