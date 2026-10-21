import pandas as pd

from app.ingestion.tabular import clean_column_names, infer_types


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
        "i": "BIGINT", "f": "DOUBLE", "d": "DATE", "ts": "TIMESTAMP",
        "b": "BOOLEAN", "zip": "VARCHAR", "t": "VARCHAR",
    }
    assert out["i"].iloc[0] == 1 and out["f"].iloc[0] == 1.5 and out["zip"].iloc[0] == "02134"
    assert out["i"].isna().iloc[2] and out["f"].isna().iloc[2] and out["d"].isna().iloc[2] and out["ts"].isna().iloc[2]


def test_float_column_with_missing_integral_values_becomes_bigint():
    out, types = infer_types(pd.DataFrame({"x": [1.0, None, 3.0]}))
    assert types == {"x": "BIGINT"}
    assert out["x"].tolist()[0] == 1 and out["x"].isna().iloc[1] and out["x"].tolist()[2] == 3


def test_native_numeric_and_nested_values():
    out, types = infer_types(pd.DataFrame({"n": [1, 2], "r": [0.5, 1.25], "tags": ['["a"]', '["b"]']}))
    assert types == {"n": "BIGINT", "r": "DOUBLE", "tags": "VARCHAR"}
