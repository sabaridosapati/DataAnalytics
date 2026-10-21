import numpy as np
import pandas as pd

from app.agents.types import AgentContext, PlanStep, StepError, StepResult
from app.guardrails.safe_eval import UnsafeExpressionError, safe_eval
from app.util import to_jsonable

OPS = ["sum", "mean", "median", "min", "max", "count", "std", "group_agg", "pct_change", "growth", "ratio", "rank",
       "percentile", "corr", "moving_avg", "expression"]
AGGS = ["sum", "mean", "median", "min", "max", "count"]
SCALAR_AGGS = {"sum", "mean", "median", "min", "max", "count", "std"}
MAX_OPS = 10

COMPUTE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["operations"],
    "properties": {
        "operations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["op", "column", "columns", "group_by", "agg", "expression", "alias", "window", "q"],
                "properties": {
                    "op": {"type": "string", "enum": OPS},
                    "column": {"type": ["string", "null"]},
                    "columns": {"type": "array", "items": {"type": "string"}},
                    "group_by": {"type": "array", "items": {"type": "string"}},
                    "agg": {"type": ["string", "null"], "enum": AGGS + [None]},
                    "expression": {"type": ["string", "null"]},
                    "alias": {"type": ["string", "null"]},
                    "window": {"type": ["integer", "null"]},
                    "q": {"type": ["number", "null"]},
                },
            },
        }
    },
}

SYSTEM = f"""You choose numeric operations to apply to a table produced by an earlier step.
Allowed ops: {", ".join(OPS)}.
- Scalar aggregates (sum, mean, median, min, max, count, std): set `column`.
- group_agg: `group_by` columns, `column`, `agg` in {AGGS}.
- pct_change / moving_avg (`window`) / rank: per-row, set `column`.
- growth: % change from first to last row of `column`.
- ratio and corr: `columns` = [numerator, denominator] / [a, b].
- percentile: `column` and `q` between 0 and 1.
- expression: arithmetic over column names with + - * / ** % ( ), abs, round, sqrt, log, exp, min, max.
Always give a short snake_case `alias`. Use only existing column names. Unused fields must be null or []."""


def _numeric(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce")


def apply_operations(df: pd.DataFrame, ops: list[dict]) -> tuple[pd.DataFrame, dict, list[str]]:
    if len(ops) > MAX_OPS:
        raise StepError(f"Too many operations (max {MAX_OPS}).")
    df = df.copy()
    scalars: dict = {}
    applied: list[str] = []

    def need(*cols: str) -> None:
        for c in cols:
            if not c or c not in df.columns:
                raise StepError(f"Column '{c}' not found. Available: {', '.join(map(str, df.columns))}.")

    for op in ops:
        name, col, alias = op.get("op"), op.get("column"), op.get("alias")
        cols = op.get("columns") or []
        if name in SCALAR_AGGS:
            need(col)
            s = _numeric(df, col)
            value = int(s.count()) if name == "count" else getattr(s, name)()
            scalars[alias or f"{name}_{col}"] = to_jsonable(value)
        elif name == "group_agg":
            group_by = op.get("group_by") or []
            need(col, *group_by)
            if not group_by:
                raise StepError("group_agg needs group_by columns.")
            agg = op.get("agg") or "sum"
            if agg not in AGGS:
                raise StepError(f"Unsupported aggregation '{agg}'.")
            numeric = df.assign(**{col: _numeric(df, col)})
            df = numeric.groupby(group_by, dropna=False, sort=True)[col].agg(agg).reset_index(name=alias or f"{agg}_{col}")
        elif name == "pct_change":
            need(col)
            df[alias or f"{col}_pct_change"] = _numeric(df, col).pct_change() * 100
        elif name == "growth":
            need(col)
            s = _numeric(df, col).dropna()
            if len(s) < 2 or s.iloc[0] == 0:
                raise StepError(f"Cannot compute growth for '{col}' (need ≥2 values and a non-zero first value).")
            scalars[alias or f"{col}_growth_pct"] = to_jsonable((s.iloc[-1] - s.iloc[0]) / abs(s.iloc[0]) * 100)
        elif name == "ratio":
            if len(cols) != 2:
                raise StepError("ratio needs exactly two columns.")
            need(*cols)
            df[alias or f"{cols[0]}_to_{cols[1]}"] = _numeric(df, cols[0]) / _numeric(df, cols[1]).replace(0, np.nan)
        elif name == "rank":
            need(col)
            df[alias or f"{col}_rank"] = _numeric(df, col).rank(ascending=False, method="min")
        elif name == "percentile":
            need(col)
            q = op.get("q")
            if q is None:
                raise StepError("percentile needs q.")
            q = q / 100 if 1 < q <= 100 else q
            if not 0 <= q <= 1:
                raise StepError("percentile q must be between 0 and 1.")
            scalars[alias or f"{col}_p{int(round(q * 100))}"] = to_jsonable(_numeric(df, col).quantile(q))
        elif name == "corr":
            if len(cols) != 2:
                raise StepError("corr needs exactly two columns.")
            need(*cols)
            scalars[alias or f"{cols[0]}_{cols[1]}_corr"] = to_jsonable(_numeric(df, cols[0]).corr(_numeric(df, cols[1])))
        elif name == "moving_avg":
            need(col)
            window = int(op.get("window") or 3)
            if not 1 <= window <= 365:
                raise StepError("moving_avg window must be between 1 and 365.")
            df[alias or f"{col}_ma{window}"] = _numeric(df, col).rolling(window, min_periods=1).mean()
        elif name == "expression":
            expr = op.get("expression") or ""
            names = {c: _numeric(df, c) for c in df.columns if isinstance(c, str) and c.isidentifier()}
            names.update({k: v for k, v in scalars.items() if isinstance(v, (int, float))})
            try:
                value = safe_eval(expr, names)
            except UnsafeExpressionError as exc:
                raise StepError(f"Expression rejected: {exc}") from exc
            except ZeroDivisionError as exc:
                raise StepError("Division by zero in expression.") from exc
            if isinstance(value, (pd.Series, np.ndarray)):
                df[alias or "result"] = value
            else:
                scalars[alias or "result"] = to_jsonable(value)
        else:
            raise StepError(f"Unsupported operation '{name}'.")
        applied.append(f"{name}({', '.join(filter(None, [col, *cols]))}) -> {alias or name}")
    return df, scalars, applied


def _source_table(deps: list[StepResult]) -> StepResult | None:
    for dep in reversed(deps):
        if dep.output and "columns" in dep.output and "rows" in dep.output and dep.output["columns"]:
            return dep
    return None


async def run_compute_agent(step: PlanStep, deps: list[StepResult], ctx: AgentContext) -> dict:
    source = _source_table(deps)
    if source is None:
        raise StepError("Compute needs a table from an earlier step.")
    df = pd.DataFrame(source.output["rows"], columns=source.output["columns"])
    preview = df.head(20).to_csv(index=False)
    prompt = (
        f"Task: {step.task}\nUser question: {ctx.question}\n\n"
        f"Table columns: {', '.join(df.columns)} ({len(df)} rows). First rows (CSV):\n{preview}"
    )
    out = await ctx.llm.chat_json(purpose="compute", system=SYSTEM, user=prompt, schema=COMPUTE_SCHEMA)
    result, scalars, applied = apply_operations(df, out.get("operations") or [])
    rows = [[to_jsonable(v) for v in r] for r in result.itertuples(index=False, name=None)]
    return {"columns": [str(c) for c in result.columns], "rows": rows[:5000], "scalars": scalars, "operations_applied": applied}
