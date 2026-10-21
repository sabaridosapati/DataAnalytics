import math
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

import numpy as np
import pandas as pd


def to_jsonable(v):
    """Convert DB / pandas / numpy values into JSON-safe Python values."""
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, Decimal):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, (datetime, date, time)):
        return v.isoformat()
    if isinstance(v, timedelta):
        return str(v)
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return None
    if isinstance(v, str):
        return v
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return str(v)


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{float(x):.7g}" for x in values) + "]"
