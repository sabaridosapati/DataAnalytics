# Task 6: Guardrails — SQL validator, safe expression evaluator, number check

**Files:**
- Create: `backend/app/guardrails/__init__.py` (empty), `backend/app/guardrails/sql_validator.py`, `backend/app/guardrails/safe_eval.py`, `backend/app/guardrails/number_check.py`
- Test: `backend/tests/unit/test_sql_validator.py`, `backend/tests/unit/test_safe_eval.py`, `backend/tests/unit/test_number_check.py`

**Interfaces:**
- Produces `validate_sql(sql: str, schema: dict[str, dict[str, str]], max_rows: int) -> str` — `schema` maps table name (in schema `data`) → {column: pg_type}. Returns the sqlglot-regenerated, `data.`-qualified, LIMIT-clamped SQL. Raises `SQLValidationError` with an actionable message.
- Produces `safe_eval(expr: str, names: dict[str, object])` → number / pandas Series / numpy array; raises `UnsafeExpressionError`.
- Produces `check_numbers(answer: str, sources: object, question: str = "") -> NumberCheckResult(ok: bool, unverified: list[str])`.

- [ ] **Step 1: Write the failing tests**

**File: `backend/tests/unit/test_sql_validator.py`**
```python
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
```

**File: `backend/tests/unit/test_safe_eval.py`**
```python
import pandas as pd
import pytest

from app.guardrails.safe_eval import UnsafeExpressionError, safe_eval


def test_scalar_arithmetic():
    assert safe_eval("a + b * 2", {"a": 1, "b": 3}) == 7
    assert safe_eval("(a - b) / a * 100", {"a": 200, "b": 50}) == 75
    assert safe_eval("-a ** 2", {"a": 3}) == -9


def test_series_arithmetic():
    df = pd.DataFrame({"revenue": [100.0, 200.0], "cost": [60.0, 50.0]})
    out = safe_eval("(revenue - cost) / revenue * 100", {"revenue": df["revenue"], "cost": df["cost"]})
    assert list(out) == [40.0, 75.0]


def test_functions():
    assert safe_eval("abs(-3)", {}) == 3
    assert float(safe_eval("round(3.14159, 2)", {})) == 3.14
    assert float(safe_eval("sqrt(16)", {})) == 4.0
    assert float(safe_eval("max(2, 5)", {})) == 5


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('ls')",
        "a.__class__",
        "(lambda: 1)()",
        "[1, 2]",
        "open('x')",
        "'text'",
        "True + 1",
        "unknown_col + 1",
        "2 ** 1000",
        "a if a else b",
        "a < b",
        "x" * 600,
        "1 +",
        "round(1, ndigits=2)",
    ],
)
def test_rejected(expr):
    with pytest.raises(UnsafeExpressionError):
        safe_eval(expr, {"a": 1, "b": 2})
```

**File: `backend/tests/unit/test_number_check.py`**
```python
from app.guardrails.number_check import check_numbers

SOURCES = [
    {"columns": ["region", "total"], "rows": [["East", 1284330.5], ["West", 990123.25]]},
    {"scalars": {"growth_pct": 12.5, "share": 0.125, "change": -7.25}},
    {"answer": "The report lists 1180 customers in Q3."},
]


def test_exact_numbers_ok():
    r = check_numbers("East had $1,284,330.50 in revenue and West $990,123.25.", SOURCES)
    assert r.ok, r.unverified


def test_rounded_display_ok():
    assert check_numbers("East reached about $1.28M, West roughly $990K.", SOURCES).ok
    assert check_numbers("Growth was 12.5%.", SOURCES).ok


def test_ratio_shown_as_percent_ok():
    assert check_numbers("East's share is 12.5 percent.", [{"scalars": {"share": 0.125}}]).ok


def test_negative_change_described_in_words_ok():
    assert check_numbers("Revenue fell 7.25% month over month.", SOURCES).ok


def test_numbers_from_text_outputs_ok():
    assert check_numbers("There were 1,180 customers.", SOURCES).ok


def test_invented_number_flagged():
    r = check_numbers("West had $999,999 in revenue.", SOURCES)
    assert not r.ok and r.unverified == ["$999,999"]


def test_small_ints_years_and_labels_ignored():
    assert check_numbers("The top 3 regions in 2024 Q3 and FY2024 were strong.", SOURCES).ok


def test_question_numbers_allowed():
    assert check_numbers("Orders above 5000 are rare.", [], question="How many orders above 5000?").ok
```

- [ ] **Step 2: Run them to verify they fail**

Run: `docker compose build backend; docker compose run --rm --no-deps backend pytest tests/unit/test_sql_validator.py tests/unit/test_safe_eval.py tests/unit/test_number_check.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.guardrails'`.

- [ ] **Step 3: Implement the SQL validator**

**File: `backend/app/guardrails/__init__.py`**
```python
```

**File: `backend/app/guardrails/sql_validator.py`**
```python
"""Validate LLM-generated SQL before it can touch the database.

Defense in depth: this parser-level check is backed by the `query_ro` role
(SELECT on schema `data` only), a read-only transaction and a statement timeout.
"""
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, SqlglotError
from sqlglot.optimizer.qualify import qualify

ALLOWED_SCHEMA = "data"
DENY_FUNC_PREFIXES = ("pg_", "dblink", "lo_")
DENY_FUNCS = {
    "current_setting", "set_config", "query_to_xml", "table_to_xml", "query_to_xml_and_xmlschema",
    "version", "inet_server_addr", "inet_server_port", "txid_current", "current_user", "session_user",
    "current_database", "current_schema", "has_table_privilege",
}
_FORBIDDEN_NAMES = (
    "Insert", "Update", "Delete", "Drop", "Create", "Alter", "AlterTable", "Command", "Merge",
    "TruncateTable", "Into", "Lock", "Set", "Transaction", "Commit", "Rollback", "Grant", "Copy",
    "Use", "Pragma", "Cache", "Uncache", "Refresh", "Analyze",
)
FORBIDDEN_NODES = tuple(t for t in (getattr(exp, n, None) for n in _FORBIDDEN_NAMES) if isinstance(t, type))
_SET_OP_NAMES = ("SetOperation", "Union", "Except", "Intersect")
SET_OPS = tuple(t for t in (getattr(exp, n, None) for n in _SET_OP_NAMES) if isinstance(t, type))


class SQLValidationError(ValueError):
    pass


def _function_name(func: exp.Func) -> str:
    if isinstance(func, exp.Anonymous):
        return str(func.this).lower()
    return func.sql_name().lower()


def validate_sql(sql: str, schema: dict[str, dict[str, str]], max_rows: int) -> str:
    try:
        statements = [s for s in sqlglot.parse(sql or "", read="postgres") if s is not None]
    except (ParseError, SqlglotError) as exc:
        raise SQLValidationError(f"SQL could not be parsed: {exc}") from exc
    if len(statements) != 1:
        raise SQLValidationError("Exactly one SQL statement is allowed.")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, *SET_OPS)):
        raise SQLValidationError("Only SELECT queries are allowed.")

    for node in tree.find_all(exp.Expression):
        if isinstance(node, FORBIDDEN_NODES):
            raise SQLValidationError(f"Forbidden SQL construct: {type(node).__name__}.")

    for func in tree.find_all(exp.Func):
        name = _function_name(func)
        if name.startswith(DENY_FUNC_PREFIXES) or name in DENY_FUNCS:
            raise SQLValidationError(f"Function '{name}' is not allowed.")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        name = (table.name or "").lower()
        db = (table.db or "").lower()
        if table.args.get("catalog"):
            raise SQLValidationError("Cross-database references are not allowed.")
        if not db and name in cte_names:
            continue
        if db and db != ALLOWED_SCHEMA:
            raise SQLValidationError(f"Access to schema '{db}' is not allowed. Use tables in schema 'data' only.")
        if name not in schema:
            available = ", ".join(sorted(schema)) or "(none)"
            raise SQLValidationError(f"Unknown table '{name or table.sql()}'. Available tables: {available}.")
        if not db:
            table.set("db", exp.to_identifier(ALLOWED_SCHEMA))

    try:
        qualify(
            tree.copy(),
            db=ALLOWED_SCHEMA,
            schema={ALLOWED_SCHEMA: {t: dict(cols) for t, cols in schema.items()}},
            dialect="postgres",
            validate_qualify_columns=True,
            quote_identifiers=False,
            identify=False,
        )
    except SqlglotError as exc:
        raise SQLValidationError(f"Column validation failed: {exc}") from exc

    limit = tree.args.get("limit")
    if limit is None:
        tree = tree.limit(max_rows)
    else:
        value = limit.args.get("expression")
        if not isinstance(value, exp.Literal) or value.is_string:
            raise SQLValidationError("LIMIT must be a plain number.")
        if int(value.this) > max_rows:
            tree.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))

    return tree.sql(dialect="postgres")
```

- [ ] **Step 4: Implement the safe evaluator**

**File: `backend/app/guardrails/safe_eval.py`**
```python
"""Evaluate arithmetic expressions from the LLM without eval/exec (AST whitelist)."""
import ast
import operator

import numpy as np

MAX_LEN = 500
MAX_EXPONENT = 100
BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}
UNARY = {ast.USub: operator.neg, ast.UAdd: operator.pos}
FUNCS = {
    "abs": np.abs,
    "round": np.round,
    "sqrt": np.sqrt,
    "log": np.log,
    "exp": np.exp,
    "min": np.minimum,
    "max": np.maximum,
}


class UnsafeExpressionError(ValueError):
    pass


def safe_eval(expr: str, names: dict[str, object]):
    if len(expr) > MAX_LEN:
        raise UnsafeExpressionError("Expression is too long.")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise UnsafeExpressionError(f"Invalid expression: {exc.msg}") from exc
    return _eval(tree.body, names)


def _eval(node: ast.AST, names: dict[str, object]):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise UnsafeExpressionError("Only numeric constants are allowed.")
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in names:
            raise UnsafeExpressionError(f"Unknown name '{node.id}'.")
        return names[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in BINOPS:
        left = _eval(node.left, names)
        right = _eval(node.right, names)
        if isinstance(node.op, ast.Pow) and np.any(np.abs(np.asarray(right, dtype=float)) > MAX_EXPONENT):
            raise UnsafeExpressionError("Exponent too large.")
        return BINOPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in UNARY:
        return UNARY[type(node.op)](_eval(node.operand, names))
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in FUNCS
        and not node.keywords
        and 1 <= len(node.args) <= 2
    ):
        return FUNCS[node.func.id](*[_eval(a, names) for a in node.args])
    raise UnsafeExpressionError(f"Disallowed expression element: {type(node).__name__}.")
```

- [ ] **Step 5: Implement the number check**

**File: `backend/app/guardrails/number_check.py`**
```python
"""Verify that every number in a generated answer appears in the tool outputs."""
import math
import re
from dataclasses import dataclass, field

NUM_RE = re.compile(
    r"(?<![A-Za-z0-9_.])(?P<sign>[-+])?\$?(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?P<frac>\.\d+)?"
    r"(?P<suffix>\s*(?:%|percent\b|thousand\b|million\b|billion\b|[kKmMbB]\b))?"
)
MULTIPLIERS = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}


@dataclass
class NumberCheckResult:
    ok: bool
    unverified: list[str] = field(default_factory=list)


def _parse(match: re.Match) -> tuple[float, float, bool, bool]:
    """Return (value, tolerance, is_percent, is_plain_integer)."""
    frac = match.group("frac") or ""
    value = float(match.group("int").replace(",", "") + frac)
    if match.group("sign") == "-":
        value = -value
    decimals = len(frac) - 1 if frac else 0
    suffix = (match.group("suffix") or "").strip().lower()
    is_pct = suffix in ("%", "percent")
    mult = MULTIPLIERS.get(suffix, 1.0)
    tolerance = max(0.01, 0.5 * 10 ** (-decimals) * mult)
    plain_int = decimals == 0 and mult == 1.0 and not is_pct
    return value * mult, tolerance, is_pct, plain_int


def _collect(obj, out: list[float]) -> None:
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        if math.isfinite(obj):
            out.append(float(obj))
        return
    if isinstance(obj, str):
        try:
            value = float(obj.replace(",", "").strip())
            if math.isfinite(value):
                out.append(value)
            return
        except ValueError:
            pass
        for m in NUM_RE.finditer(obj):
            out.append(_parse(m)[0])
        return
    if isinstance(obj, dict):
        for v in obj.values():
            _collect(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect(v, out)


def check_numbers(answer: str, sources, question: str = "") -> NumberCheckResult:
    allowed: list[float] = []
    _collect(sources, allowed)
    _collect(question, allowed)
    allowed_abs = [abs(v) for v in allowed]
    unverified: list[str] = []
    for m in NUM_RE.finditer(answer):
        value, tol, is_pct, plain_int = _parse(m)
        if plain_int and (abs(value) <= 31 or 1900 <= value <= 2100):
            continue  # counts, ranks, days, years
        target = abs(value)
        if any(abs(target - v) <= tol or (is_pct and abs(target - v * 100) <= tol) for v in allowed_abs):
            continue
        unverified.append(m.group(0).strip())
    return NumberCheckResult(ok=not unverified, unverified=unverified)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `docker compose build backend; docker compose run --rm --no-deps backend pytest tests/unit -q`
Expected: all pass. If an individual sqlglot case behaves differently in the installed version (e.g. a forbidden statement is parsed as `Command` and rejected with "Only SELECT" vs "Forbidden"), adjust **only the expected message fragment** in the test — never relax the validator so that a dangerous statement passes.

- [ ] **Step 7: Commit**

```bash
git add backend
git commit -m "feat(guardrails): sqlglot SQL validator, AST-safe math, answer number check"
```
