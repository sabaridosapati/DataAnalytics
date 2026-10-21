from datetime import date, datetime

import pandas as pd

from app.ingestion.tabular import clean_column_names, infer_types, to_records


def test_clean_column_names():
    assert clean_column_names(["Order ID", "order id", "unitPrice", "1st", "select", "", "Revenue ($)"]) == [
        "order_id", "order_id_2", "unit_price", "c_1st", "select_col", "col_6", "revenue",
    ]


def test_infer_types_and_records():
    df = pd.DataFrame(
        {
            "i": ["1", "2", None],
            "f": ["1.5", "2", ""],
            "d": ["2024-01-01", "2024-02-01", None],
            "ts": ["2024-01-01 10:00", "2024-01-02 11:30", None],
            "b": ["true", "false", "yes"],
            "zip": ["02134", "10001", "94105"],
            "t": ["a", "b", "c"],
        }
    )
    out, types = infer_types(df)
    assert types == {
        "i": "bigint", "f": "double precision", "d": "date", "ts": "timestamp",
        "b": "boolean", "zip": "text", "t": "text",
    }
    recs = to_records(out, types)
    assert recs[0] == (1, 1.5, date(2024, 1, 1), datetime(2024, 1, 1, 10, 0), True, "02134", "a")
    assert recs[2][:4] == (None, None, None, None)


def test_float_column_with_missing_integral_values_becomes_bigint():
    out, types = infer_types(pd.DataFrame({"x": [1.0, None, 3.0]}))
    assert types == {"x": "bigint"}
    assert to_records(out, types) == [(1,), (None,), (3,)]


def test_native_numeric_and_nested_values():
    out, types = infer_types(pd.DataFrame({"n": [1, 2], "r": [0.5, 1.25], "tags": ['["a"]', '["b"]']}))
    assert types == {"n": "bigint", "r": "double precision", "tags": "text"}
