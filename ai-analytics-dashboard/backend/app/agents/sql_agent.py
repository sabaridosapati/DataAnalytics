from app.agents.sql_exec import QueryExecutionError, run_readonly_query
from app.agents.types import AgentContext, PlanStep, StepError, StepResult
from app.guardrails.sql_validator import SQLValidationError, validate_sql

SQL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sql", "explanation"],
    "properties": {"sql": {"type": "string"}, "explanation": {"type": "string"}},
}

SYSTEM = """You write ONE PostgreSQL SELECT query that answers an analytics task.
Rules:
- Use ONLY the tables and columns listed in the schema. Never invent tables or columns.
- Reference tables as data.<table>.
- Read-only: a single SELECT (CTEs allowed). No INSERT/UPDATE/DELETE/DDL, no system catalogs or admin functions.
- Give computed columns short snake_case aliases.
- Prefer compact aggregated results over raw rows; order results meaningfully.
- For dates use date_trunc(...) or EXTRACT(...) on date/timestamp columns; cast date_trunc results with ::date for dates.
- Round money/averages with ROUND(x::numeric, 2).
If the task cannot be answered with this schema, return sql as an empty string and explain why."""


def schema_prompt(catalog, slugs: list[str]) -> str:
    blocks = []
    for slug in slugs:
        info = catalog.get(slug)
        if info is None:
            continue
        lines = [f"Table data.{slug} ({info.row_count} rows), source '{info.name}':"]
        for c in info.columns:
            samples = ", ".join(str(v) for v in c.samples[:3])
            desc = f" -- {c.description}" if c.description else ""
            lines.append(f"  - {c.name} {c.pg_type}{desc} (e.g. {samples})")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


async def run_sql_agent(step: PlanStep, deps: list[StepResult], ctx: AgentContext) -> dict:
    full_schema = ctx.catalog.sql_schema()
    base = (
        f"Task: {step.task}\n"
        f"User question: {ctx.question}\n\n"
        f"Schema:\n{schema_prompt(ctx.catalog, step.datasets)}"
    )
    error: str | None = None
    last_sql = ""
    for _ in range(2):
        prompt = base if error is None else f"{base}\n\nYour previous SQL:\n{last_sql}\nfailed with: {error}\nReturn a corrected query."
        out = await ctx.llm.chat_json(
            purpose="sql", system=SYSTEM, user=prompt, schema=SQL_SCHEMA, model=ctx.settings.planner_model
        )
        last_sql = (out.get("sql") or "").strip()
        if not last_sql:
            raise StepError(f"The tables cannot answer this: {out.get('explanation') or 'no explanation given'}")
        try:
            safe_sql = validate_sql(last_sql, full_schema, ctx.settings.max_sql_rows)
            result = await run_readonly_query(ctx.state, safe_sql, ctx.catalog.version, ctx.settings)
            return {"sql": safe_sql, **result}
        except (SQLValidationError, QueryExecutionError) as exc:
            error = str(exc)
    raise StepError(f"Generated SQL was rejected: {error}")
