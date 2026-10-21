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
