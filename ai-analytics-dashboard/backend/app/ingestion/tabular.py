import math
import re

import pandas as pd

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
    return "DATE" if len(nn) and bool((nn.dt.normalize() == nn).all()) else "TIMESTAMP"


def infer_types(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    df = df.copy()
    types: dict[str, str] = {}
    for col in df.columns:
        s = df[col]
        if pd.api.types.is_bool_dtype(s):
            types[col] = "BOOLEAN"
            continue
        if pd.api.types.is_datetime64_any_dtype(s):
            if getattr(s.dt, "tz", None) is not None:
                df[col] = s = s.dt.tz_convert(None)
            types[col] = _date_or_timestamp(s)
            continue
        if pd.api.types.is_numeric_dtype(s):
            nn = s.dropna()
            if pd.api.types.is_integer_dtype(s) or _is_integral(nn):
                df[col], types[col] = s.astype("Int64"), "BIGINT"
            else:
                df[col], types[col] = s.astype(float), "DOUBLE"
            continue

        strs = s.map(lambda v: None if v is None or (isinstance(v, float) and math.isnan(v)) else str(v).strip())
        strs = strs.where(strs != "", None)
        nn = strs.dropna()
        if nn.empty:
            df[col], types[col] = strs, "VARCHAR"
            continue
        num = pd.to_numeric(nn, errors="coerce")
        has_leading_zero = bool(nn.str.match(r"^-?0\d").any())
        if not has_leading_zero and num.notna().all():
            full = pd.to_numeric(strs, errors="coerce")
            if _is_integral(num):
                df[col], types[col] = full.astype("Int64"), "BIGINT"
            else:
                df[col], types[col] = full.astype(float), "DOUBLE"
            continue
        if nn.str.lower().isin(BOOL_VALUES.keys()).all():
            df[col] = strs.map(lambda v: None if v is None else BOOL_VALUES[v.lower()])
            types[col] = "BOOLEAN"
            continue
        if nn.str.match(DATE_RE).mean() >= 0.95:
            parsed = pd.to_datetime(strs, errors="coerce", format="mixed", utc=True).dt.tz_localize(None)
            if parsed.notna().sum() >= 0.95 * len(nn):
                df[col], types[col] = parsed, _date_or_timestamp(parsed)
                continue
        df[col], types[col] = strs, "VARCHAR"
    return df, types
