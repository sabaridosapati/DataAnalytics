import pytest

from app.guardrails.sql_validator import SQLValidationError, validate_sql

SCHEMA = {
    "sales": {"order_id": "BIGINT", "order_date": "DATE", "region": "VARCHAR", "category": "VARCHAR", "revenue": "DOUBLE"},
    "employees": {"employee_id": "BIGINT", "department": "VARCHAR", "salary": "BIGINT"},
}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT region, SUM(revenue) AS total FROM sales GROUP BY region ORDER BY total DESC",
        "SELECT * FROM data.sales",
        "WITH m AS (SELECT date_trunc('month', order_date) AS month, SUM(revenue) AS rev FROM sales GROUP BY 1) "
        "SELECT month, rev FROM m ORDER BY month",
        "SELECT s.region, e.department FROM sales s JOIN employees e ON s.order_id = e.employee_id",
        "SELECT region FROM sales UNION SELECT department FROM employees",
        "SELECT COUNT(*) FROM sales WHERE region = 'East'",
        "SELECT EXTRACT(MONTH FROM order_date) AS m, AVG(revenue) FROM sales GROUP BY m",
        "SELECT region, ROUND(SUM(revenue), 2) AS total FROM sales GROUP BY region",
        "SELECT region, ROUND(SUM(revenue)::DOUBLE, 2) AS total FROM sales GROUP BY region",
        "SELECT department, AVG(salary) FILTER (WHERE salary > 0) FROM employees GROUP BY department",
        "SELECT region, revenue, RANK() OVER (PARTITION BY region ORDER BY revenue DESC) AS r FROM sales",
        "SELECT region, strftime(order_date, '%Y-%m') AS ym FROM sales WHERE order_date >= CURRENT_DATE - INTERVAL 30 DAY",
    ],
)
def test_allowed(sql):
    out = validate_sql(sql, SCHEMA, 5000)
    assert "data." in out
    assert "LIMIT" in out.upper()


@pytest.mark.parametrize(
    "sql, fragment",
    [
        ("DROP TABLE sales", "Only SELECT"),
        ("DELETE FROM sales", "Only SELECT"),
        ("UPDATE sales SET revenue = 0", "Only SELECT"),
        ("INSERT INTO sales (order_id) VALUES (1)", "Only SELECT"),
        ("TRUNCATE sales", "Only SELECT"),
        ("CREATE TABLE x AS SELECT * FROM sales", "Only SELECT"),
        ("COPY sales TO '/tmp/x'", "Only SELECT"),
        ("ATTACH '/tmp/evil.duckdb' AS evil", "Only SELECT"),
        ("INSTALL httpfs", "Only SELECT"),
        ("PRAGMA database_list", "Only SELECT"),
        ("SET enable_external_access = true", "Only SELECT"),
        ("SELECT 1; DROP TABLE sales", "one SQL statement"),
        ("SELECT * FROM sales; SELECT * FROM employees", "one SQL statement"),
        ("SELECT * FROM sales -- note\n; DROP TABLE sales", "one SQL statement"),
        ("SELECT * FROM app.users", "schema 'app'"),
        ("SELECT * FROM information_schema.tables", "schema 'information_schema'"),
        ("SELECT * FROM main.sales", "schema 'main'"),
        ("SELECT * FROM users", "Unknown table 'users'"),
        ("SELECT * FROM duckdb_tables", "Unknown table"),
        ("SELECT * FROM read_csv('/etc/passwd')", "not allowed"),
        ("SELECT * FROM read_text('/etc/passwd')", "not allowed"),
        ("SELECT * FROM '/etc/passwd.csv'", "not allowed"),
        ("SELECT * FROM glob('/*')", "not allowed"),
        ("SELECT * FROM duckdb_settings()", "not allowed"),
        ("SELECT * FROM range(10)", "not allowed"),
        ("SELECT getenv('HOME')", "not allowed"),
        ("SELECT * FROM (SELECT * FROM read_csv('x')) t", "not allowed"),
        ("SELECT * FROM db.data.sales", "Cross-database"),
        ('SELECT * FROM "x.csv"', "not allowed"),
        ("SELECT * FROM 'x.parquet'", "not allowed"),
        ("SELECT * FROM 'http://a/b.csv'", "not allowed"),
        ("SELECT length(repeat('x', 300000000))", "not allowed"),
        ("SELECT lpad('x', 300000000, 'y')", "not allowed"),
        ("SELECT rpad('x', 300000000, 'y')", "not allowed"),
        ("SELECT list_resize([1], 300000000)", "not allowed"),
        ("SELECT array_resize([1], 300000000)", "not allowed"),
        ("SELECT current_setting('threads')", "not allowed"),
        ("SELECT version()", "not allowed"),
        ("SELECT secret_col FROM sales", "could not be resolved"),
        ("SELECT * FROM sales LIMIT (SELECT 1)", "LIMIT"),
        ("", "one SQL statement"),
        ("this is not sql at all", ""),
    ],
)
def test_blocked(sql, fragment):
    with pytest.raises(SQLValidationError) as err:
        validate_sql(sql, SCHEMA, 5000)
    assert fragment in str(err.value)


def test_limit_added_kept_and_clamped():
    assert validate_sql("SELECT region FROM sales", SCHEMA, 5000).endswith("LIMIT 5000")
    assert validate_sql("SELECT region FROM sales LIMIT 10", SCHEMA, 5000).endswith("LIMIT 10")
    assert validate_sql("SELECT region FROM sales LIMIT 99999", SCHEMA, 5000).endswith("LIMIT 5000")


def test_tables_are_schema_qualified():
    out = validate_sql("SELECT region FROM sales", SCHEMA, 100)
    assert out == "SELECT region FROM data.sales LIMIT 100"


COMMENT_TRICK = "SELECT region -- */ , getenv('X') AS g /*\nFROM sales"


def test_comment_trick_cannot_smuggle_a_function_call():
    # sqlglot used to re-emit `-- */ ...` as a block comment that hid the real getenv() call
    try:
        out = validate_sql(COMMENT_TRICK, SCHEMA, 5000)
    except SQLValidationError:
        return
    assert "getenv" not in out.lower()
    assert "/*" not in out and "--" not in out


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT region /* hi */ FROM sales -- tail",
        "SELECT region -- */ \nFROM sales",
        "/* a */ SELECT region FROM sales /* b */",
    ],
)
def test_validated_output_never_contains_comment_markers(sql):
    out = validate_sql(sql, SCHEMA, 5000)
    assert "/*" not in out and "--" not in out and "*/" not in out
