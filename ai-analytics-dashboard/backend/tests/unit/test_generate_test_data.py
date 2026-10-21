import json

import pandas as pd

from scripts.generate_test_data import generate


def test_generate_files_and_expected_answers(tmp_path):
    exp = generate(tmp_path)
    sales = pd.read_csv(tmp_path / "sales_2024.csv")
    assert len(sales) == 2000 == exp["sales_rows"]
    assert list(sales.columns) == ["order_id", "order_date", "region", "product", "category", "quantity", "unit_price", "revenue"]
    assert round(float(sales["revenue"].sum()), 2) == exp["total_revenue"]
    assert set(exp["revenue_by_region"]) == {"North", "South", "East", "West"}

    employees = json.loads((tmp_path / "employees.json").read_text(encoding="utf-8"))
    assert len(employees) == 200 == exp["employee_rows"]
    assert set(employees[0]["address"]) == {"city", "state"}

    assert (tmp_path / "annual_report_2024.pdf").read_bytes().startswith(b"%PDF")
    assert json.loads((tmp_path / "expected_answers.json").read_text(encoding="utf-8")) == exp

    by_month = exp["revenue_by_month"]
    q2 = sum(by_month[m] for m in ("4", "5", "6"))
    q3 = sum(by_month[m] for m in ("7", "8", "9"))
    assert q3 < q2, "sample data must contain the Q3 dip described in the annual report"


def test_generation_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    generate(a)
    generate(b)
    assert (a / "sales_2024.csv").read_bytes() == (b / "sales_2024.csv").read_bytes()
    assert (a / "employees.json").read_bytes() == (b / "employees.json").read_bytes()
