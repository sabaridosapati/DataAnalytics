from app.agents.types import AgentContext, PlanStep, StepError, StepResult

CHART_TYPES = ["bar", "line", "area", "pie", "scatter", "table", "kpi"]
MAX_CHART_ROWS = 1000

VIZ_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["type", "title", "x", "y", "series"],
    "properties": {
        "type": {"type": "string", "enum": CHART_TYPES},
        "title": {"type": "string"},
        "x": {"type": ["string", "null"]},
        "y": {"type": "array", "items": {"type": "string"}},
        "series": {"type": ["string", "null"]},
    },
}

SYSTEM = """You pick the best chart for a result table.
- line/area: trends over time (x = date/month column); bar: category comparisons; pie: share of a whole (≤8 slices);
  scatter: relationship between two numeric columns; table: when no chart fits.
- x must be an existing column; y must be existing NUMERIC columns; series (optional) is a category column to split lines/bars.
- Write a short descriptive title."""


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _validate(spec: dict, columns: list[str], rows: list[list]) -> str | None:
    kind = spec.get("type")
    if kind not in CHART_TYPES:
        return f"unknown chart type '{kind}'"
    if kind == "table":
        return None
    if spec.get("x") not in columns:
        return f"x column '{spec.get('x')}' does not exist; columns are {columns}"
    ys = spec.get("y") or []
    if not ys:
        return "y must list at least one numeric column"
    for y in ys:
        if y not in columns:
            return f"y column '{y}' does not exist; columns are {columns}"
        idx = columns.index(y)
        if not any(_is_number(r[idx]) for r in rows):
            return f"y column '{y}' is not numeric"
    if spec.get("series") and spec["series"] not in columns:
        return f"series column '{spec['series']}' does not exist"
    if kind == "pie" and len(ys) != 1:
        return "pie charts need exactly one y column"
    return None


def _chart(kind: str, title: str, x, y, series, columns, rows, source_sql) -> dict:
    return {
        "chart": {
            "type": kind,
            "title": title,
            "x": x,
            "y": y,
            "series": series,
            "data": {"columns": columns, "rows": rows[:MAX_CHART_ROWS]},
            "source_sql": source_sql,
        }
    }


async def run_viz_agent(step: PlanStep, deps: list[StepResult], ctx: AgentContext) -> dict:
    source = next((d for d in reversed(deps) if d.output and ("rows" in d.output or "scalars" in d.output)), None)
    if source is None:
        raise StepError("Nothing to visualize: no table from an earlier step.")
    out = source.output
    columns, rows = out.get("columns") or [], out.get("rows") or []
    source_sql = out.get("sql") if source.agent == "sql" else None
    scalars = out.get("scalars") or {}

    if not rows:
        if scalars:
            kpi_rows = [[k, v] for k, v in scalars.items()]
            return _chart("kpi", step.task, "metric", ["value"], None, ["metric", "value"], kpi_rows, None)
        return _chart("table", step.task, None, [], None, columns, rows, source_sql)

    sample = "\n".join(", ".join(str(v) for v in r) for r in rows[:10])
    base = f"Task: {step.task}\nUser question: {ctx.question}\n\nColumns: {columns}\nFirst rows:\n{sample}"
    error = None
    for _ in range(2):
        prompt = base if error is None else f"{base}\n\nYour previous chart spec was invalid: {error}. Fix it."
        spec = await ctx.llm.chat_json(purpose="viz", system=SYSTEM, user=prompt, schema=VIZ_SCHEMA)
        error = _validate(spec, columns, rows)
        if error is None:
            title = spec.get("title") or step.task
            if spec["type"] == "table":
                return _chart("table", title, None, [], None, columns, rows, source_sql)
            return _chart(spec["type"], title, spec["x"], spec["y"], spec.get("series"), columns, rows, source_sql)
    return _chart("table", step.task, None, [], None, columns, rows, source_sql)
