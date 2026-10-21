import pytest

from app.guardrails.sql_validator import SQLValidationError, validate_sql

SCHEMA = {
    "sales": {"order_id": "bigint", "order_date": "date", "region": "text", "category": "text", "revenue": "double precision"},
    "employees": {"employee_id": "bigint", "department": "text", "salary": "bigint"},
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
        "SELECT region, ROUND(SUM(revenue)::numeric, 2) AS total FROM sales GROUP BY region",
        "SELECT department, AVG(salary) FILTER (WHERE salary > 0) FROM employees GROUP BY department",
        "SELECT region, revenue, RANK() OVER (PARTITION BY region ORDER BY revenue DESC) AS r FROM sales",
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
        ("GRANT ALL ON sales TO public", "Only SELECT"),
        ("COPY sales TO '/tmp/x'", "Only SELECT"),
        ("SELECT 1; DROP TABLE sales", "one SQL statement"),
        ("SELECT * FROM sales; SELECT * FROM employees", "one SQL statement"),
        ("SELECT * FROM sales -- note\n; DROP TABLE sales", "one SQL statement"),
        ("SELECT * FROM app.users", "schema 'app'"),
        ("SELECT * FROM information_schema.tables", "schema 'information_schema'"),
        ("SELECT * FROM pg_catalog.pg_user", "schema 'pg_catalog'"),
        ("SELECT * FROM vector.chunks", "schema 'vector'"),
        ("SELECT * FROM users", "Unknown table 'users'"),
        ("SELECT * FROM pg_user", "Unknown table"),
        ("SELECT pg_read_file('/etc/passwd')", "not allowed"),
        ("SELECT pg_sleep(10)", "not allowed"),
        ("SELECT current_setting('server_version')", "not allowed"),
        ("SELECT version()", "not allowed"),
        ("SELECT secret_col FROM sales", "could not be resolved"),
        ("SELECT * INTO new_table FROM sales", "Forbidden"),
        ("SELECT * FROM sales FOR UPDATE", "Forbidden"),
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
