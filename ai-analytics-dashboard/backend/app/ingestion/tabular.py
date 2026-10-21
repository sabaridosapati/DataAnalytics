import asyncio
import math
import re

import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

RESERVED = {
    "all", "analyse", "analyze", "and", "any", "array", "as", "asc", "both", "case", "cast", "check", "column",
    "constraint", "create", "current_date", "current_time", "current_timestamp", "current_user", "default",
    "desc", "distinct", "do", "else", "end", "except", "false", "fetch", "for", "foreign", "from", "grant",
    "group", "having", "in", "intersect", "into", "is", "join", "leading", "limit", "not", "null", "offset",
    "on", "only", "or", "order", "primary", "references", "select", "table", "then", "to", "trailing", "true",
    "union", "unique", "user", "using", "when", "where", "window", "with",
}
DATE_RE = re.compile(
    r"^\d{4}-\d{1,2}-\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$|^\d{1,2}/\d{1,2}/\d{4}$"
)
BOOL_VALUES = {"true": True, "false": False, "yes": True, "no": False}


def to_identifier(name, fallback: str) -> str:
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(name).strip())
    s = re.sub(r"[^0-9a-zA-Z]+", "_", s).strip("_").lower()
    if not s:
        s = fallback
    if s[0].isdigit():
        s = f"c_{s}"
    if s in RESERVED:
        s = f"{s}_col"
    return s[:60]


def clean_column_names(columns) -> list[str]:
    used: set[str] = set()
    out: list[str] = []
    for i, col in enumerate(columns):
        base = to_identifier(col, f"col_{i + 1}")
        name, k = base, 2
        while name in used:
            name = f"{base[:55]}_{k}"
            k += 1
        used.add(name)
        out.append(name)
    return out


def _is_integral(s: pd.Series) -> bool:
    return bool(len(s)) and bool(((s % 1) == 0).all()) and bool((s.abs() < 9e15).all())


def _date_or_timestamp(s: pd.Series) -> str:
    nn = s.dropna()
    return "date" if len(nn) and bool((nn.dt.normalize() == nn).all()) else "timestamp"


def infer_types(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    df = df.copy()
    types: dict[str, str] = {}
    for col in df.columns:
        s = df[col]
        if pd.api.types.is_bool_dtype(s):
            types[col] = "boolean"
            continue
        if pd.api.types.is_datetime64_any_dtype(s):
            if getattr(s.dt, "tz", None) is not None:
                df[col] = s = s.dt.tz_convert(None)
            types[col] = _date_or_timestamp(s)
            continue
        if pd.api.types.is_numeric_dtype(s):
            nn = s.dropna()
            if pd.api.types.is_integer_dtype(s) or _is_integral(nn):
                df[col], types[col] = s.astype("Int64"), "bigint"
            else:
                df[col], types[col] = s.astype(float), "double precision"
            continue

        strs = s.map(lambda v: None if v is None or (isinstance(v, float) and math.isnan(v)) else str(v).strip())
        strs = strs.where(strs != "", None)
        nn = strs.dropna()
        if nn.empty:
            df[col], types[col] = strs, "text"
            continue
        num = pd.to_numeric(nn, errors="coerce")
        has_leading_zero = bool(nn.str.match(r"^-?0\d").any())
        if not has_leading_zero and num.notna().all():
            full = pd.to_numeric(strs, errors="coerce")
            if _is_integral(num):
                df[col], types[col] = full.astype("Int64"), "bigint"
            else:
                df[col], types[col] = full.astype(float), "double precision"
            continue
        if nn.str.lower().isin(BOOL_VALUES.keys()).all():
            df[col] = strs.map(lambda v: None if v is None else BOOL_VALUES[v.lower()])
            types[col] = "boolean"
            continue
        if nn.str.match(DATE_RE).mean() >= 0.95:
            parsed = pd.to_datetime(strs, errors="coerce", format="mixed", utc=True).dt.tz_localize(None)
            if parsed.notna().sum() >= 0.95 * len(nn):
                df[col], types[col] = parsed, _date_or_timestamp(parsed)
                continue
        df[col], types[col] = strs, "text"
    return df, types


def _convert(v, pg_type: str):
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if pg_type == "bigint":
        return int(v)
    if pg_type == "double precision":
        f = float(v)
        return f if math.isfinite(f) else None
    if pg_type == "boolean":
        return bool(v)
    if pg_type == "date":
        return pd.Timestamp(v).date()
    if pg_type == "timestamp":
        return pd.Timestamp(v).to_pydatetime()
    return str(v)


def to_records(df: pd.DataFrame, types: dict[str, str]) -> list[tuple]:
    cols = list(df.columns)
    return [tuple(_convert(v, types[c]) for v, c in zip(row, cols)) for row in df.itertuples(index=False, name=None)]


async def create_and_load_table(conn: AsyncConnection, table: str, df: pd.DataFrame, types: dict[str, str]) -> int:
    """Create data.<table> and bulk-load rows with COPY inside the caller's transaction.

    `table` and column names must already be sanitized identifiers (see to_identifier).
    """
    columns_sql = ", ".join(f'"{c}" {types[c]}' for c in df.columns)
    await conn.execute(text(f'CREATE TABLE data."{table}" ({columns_sql})'))
    records = await asyncio.to_thread(to_records, df, types)
    raw = await conn.get_raw_connection()
    await raw.driver_connection.copy_records_to_table(
        table, records=records, columns=list(df.columns), schema_name="data"
    )
    await conn.execute(text(f'GRANT SELECT ON data."{table}" TO query_ro'))
    return len(records)
