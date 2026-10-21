"""Validate LLM-generated SQL before it can touch DuckDB.

Defense in depth: this parser-level check is backed by a locked-down DuckDB connection
(read-only attached files, no external access, locked configuration, memory and time limits).
"""
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

DIALECT = "duckdb"
ALLOWED_SCHEMA = "data"
DENY_FUNC_PREFIXES = ("read_", "write_", "pragma_", "duckdb_", "sniff_", "parquet_", "pg_")
DENY_FUNCS = {
    "glob", "getenv", "query", "query_table", "load_extension", "install_extension", "which_secret",
    "list_secrets", "current_setting", "set_config", "version", "current_user", "session_user",
    "current_database", "current_schema", "current_schemas", "current_catalog", "range", "generate_series",
    # large-allocation functions (memory_limit does not cover huge strings/lists)
    "repeat", "lpad", "rpad", "pad", "list_resize", "array_resize",
}
_FORBIDDEN_NAMES = (
    "Insert", "Update", "Delete", "Drop", "Create", "Alter", "AlterTable", "Command", "Merge",
    "TruncateTable", "Into", "Lock", "Set", "Transaction", "Commit", "Rollback", "Grant", "Copy",
    "Use", "Pragma", "Cache", "Uncache", "Refresh", "Analyze", "Attach", "Detach", "Install", "Export",
)
FORBIDDEN_NODES = tuple(t for t in (getattr(exp, n, None) for n in _FORBIDDEN_NAMES) if isinstance(t, type))
_SET_OP_NAMES = ("SetOperation", "Union", "Except", "Intersect")
SET_OPS = tuple(t for t in (getattr(exp, n, None) for n in _SET_OP_NAMES) if isinstance(t, type))


class SQLValidationError(ValueError):
    pass


def _function_name(func: exp.Func) -> str:
    if isinstance(func, exp.Anonymous):
        # `.name` is the unquoted text, so "getenv"('X') is caught like getenv('X')
        return func.name.strip('"').lower()
    return func.sql_name().lower()


def _cte_references(tree: exp.Expression) -> set[int]:
    """ids of Table nodes that refer to a CTE visible in their *own* scope.

    A CTE name only shadows tables inside the scope that defines it; a name match
    elsewhere must still be treated as a real table.
    """
    try:
        scopes = traverse_scope(tree)
    except SqlglotError as exc:
        raise SQLValidationError(f"SQL could not be analyzed: {exc}") from exc
    refs: set[int] = set()
    for scope in scopes:
        visible = {name.lower() for name in scope.cte_sources}
        for table in scope.tables:
            if not table.db and (table.name or "").lower() in visible:
                refs.add(id(table))
    return refs


def _check_function(name: str) -> None:
    if name.startswith(DENY_FUNC_PREFIXES) or name in DENY_FUNCS:
        raise SQLValidationError(f"Function '{name}' is not allowed.")


def validate_sql(sql: str, schema: dict[str, dict[str, str]], max_rows: int) -> str:
    try:
        statements = [s for s in sqlglot.parse(sql or "", read=DIALECT) if s is not None]
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
        _check_function(_function_name(func))

    for cte in tree.find_all(exp.CTE):
        cte_name = cte.alias_or_name.lower()
        if cte_name.startswith(("pg_", "duckdb_")) or cte_name == "information_schema":
            raise SQLValidationError(f"CTE name '{cte_name}' is not allowed (it shadows a system relation).")
    cte_refs = _cte_references(tree)
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            # read_csv(...), glob(...), range(...), FROM 'file.csv' (replacement scans) and similar
            raise SQLValidationError("Table functions and file paths are not allowed; use the listed tables only.")
        if any(ch in (table.name or "") for ch in "/\\.:*?"):
            # FROM '/path.csv' parses as a quoted identifier (DuckDB replacement scan), not a function
            raise SQLValidationError("Table functions and file paths are not allowed; use the listed tables only.")
        name = (table.name or "").lower()
        db = (table.db or "").lower()
        if table.args.get("catalog"):
            raise SQLValidationError("Cross-database references are not allowed.")
        if id(table) in cte_refs:
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
            dialect=DIALECT,
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

    # Comments are dropped: sqlglot can turn a `-- */` line comment into a block comment that hides code.
    for node in tree.walk():
        node.comments = None
    return tree.sql(dialect=DIALECT, comments=False)
