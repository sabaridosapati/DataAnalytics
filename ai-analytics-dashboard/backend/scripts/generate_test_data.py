"""Generate deterministic sample datasets plus ground-truth answers.

Usage: python scripts/generate_test_data.py [out_dir]   (default: tests/fixtures)
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
REGIONS = ["North", "South", "East", "West"]
PRODUCTS = {
    "Laptop": ("Electronics", 1200.0),
    "Phone": ("Electronics", 800.0),
    "Headphones": ("Electronics", 150.0),
    "Desk": ("Furniture", 350.0),
    "Chair": ("Furniture", 180.0),
    "Lamp": ("Furniture", 45.0),
    "Notebook": ("Office Supplies", 4.5),
    "Pen Pack": ("Office Supplies", 9.99),
    "Stapler": ("Office Supplies", 15.0),
}
# Fewer orders in Jul-Sep: the "Q3 dip" explained in the annual report.
MONTH_WEIGHTS = np.array([9, 8, 9, 9, 10, 10, 6, 5, 6, 10, 11, 12], dtype=float)
DEPARTMENTS = ["Engineering", "Sales", "Marketing", "Finance", "Operations"]
DEPT_BASE = {"Engineering": 125000, "Sales": 85000, "Marketing": 90000, "Finance": 105000, "Operations": 75000}
CITIES = [("Austin", "TX"), ("Seattle", "WA"), ("Boston", "MA"), ("Denver", "CO"), ("Chicago", "IL")]

STRATEGY = [
    "In 2024 the company focused on expanding its retail footprint in the West region and on growing "
    "the Electronics category, which remains our largest source of revenue.",
    "We invested in a new online ordering platform and consolidated our Office Supplies vendors to reduce costs.",
]
Q3_TEXT = [
    "Third-quarter results were weaker than planned. Q3 revenue declined because of supply-chain disruptions "
    "at our primary laptop supplier, which left key Electronics products out of stock for most of August.",
    "A port strike in the same period delayed furniture shipments, so many Desk and Chair orders could not "
    "be fulfilled until October.",
    "Customer feedback in Q3 highlighted long delivery times as the main complaint.",
]
OUTLOOK = [
    "Revenue recovered strongly in the fourth quarter as supply normalized. For 2025 we expect continued growth "
    "driven by the West region and by new supplier contracts that reduce dependency on a single laptop manufacturer.",
    "The table below summarizes quarterly key performance indicators.",
]
KPI_ROWS = [
    ["Quarter", "Revenue (USD M)", "Operating Margin (%)", "Customers"],
    ["Q1", "4.2", "18.5", "1240"],
    ["Q2", "4.6", "19.1", "1310"],
    ["Q3", "3.1", "12.4", "1180"],
    ["Q4", "5.3", "20.2", "1420"],
]


def make_sales(rng: np.random.Generator, n: int = 2000) -> pd.DataFrame:
    months = rng.choice(np.arange(1, 13), size=n, p=MONTH_WEIGHTS / MONTH_WEIGHTS.sum())
    names = list(PRODUCTS)
    rows = []
    for i in range(n):
        month = int(months[i])
        day = int(rng.integers(1, pd.Timestamp(2024, month, 1).days_in_month + 1))
        product = names[int(rng.integers(0, len(names)))]
        category, base = PRODUCTS[product]
        qty = int(rng.integers(1, 11))
        price = round(base * float(rng.uniform(0.9, 1.1)), 2)
        rows.append(
            {
                "order_id": i + 1,
                "order_date": f"2024-{month:02d}-{day:02d}",
                "region": REGIONS[int(rng.integers(0, len(REGIONS)))],
                "product": product,
                "category": category,
                "quantity": qty,
                "unit_price": price,
                "revenue": round(price * qty, 2),
            }
        )
    return pd.DataFrame(rows).sort_values(["order_date", "order_id"]).reset_index(drop=True)


def make_employees(rng: np.random.Generator, n: int = 200) -> list[dict]:
    out = []
    for i in range(n):
        dept = DEPARTMENTS[int(rng.integers(0, len(DEPARTMENTS)))]
        city, state = CITIES[int(rng.integers(0, len(CITIES)))]
        hire = pd.Timestamp("2015-01-01") + pd.Timedelta(days=int(rng.integers(0, 3650)))
        out.append(
            {
                "employee_id": 1000 + i,
                "name": f"Employee {i + 1:03d}",
                "department": dept,
                "salary": int(round(DEPT_BASE[dept] * float(rng.uniform(0.8, 1.25)), -2)),
                "hire_date": hire.strftime("%Y-%m-%d"),
                "address": {"city": city, "state": state},
            }
        )
    return out


def make_pdf(path: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    story = [Paragraph("Annual Report 2024", styles["Title"]), Paragraph("1. Strategy", styles["Heading2"])]
    for p in STRATEGY:
        story += [Paragraph(p, styles["BodyText"]), Spacer(1, 8)]
    story += [PageBreak(), Paragraph("2. Q3 Performance", styles["Heading2"])]
    for p in Q3_TEXT:
        story += [Paragraph(p, styles["BodyText"]), Spacer(1, 8)]
    story += [PageBreak(), Paragraph("3. Outlook and Key Metrics", styles["Heading2"])]
    for p in OUTLOOK:
        story += [Paragraph(p, styles["BodyText"]), Spacer(1, 8)]
    table = Table(KPI_ROWS)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.75, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ]
        )
    )
    story.append(table)
    SimpleDocTemplate(str(path), pagesize=letter, title="Annual Report 2024", invariant=1).build(story)


def expected_answers(sales: pd.DataFrame, employees: list[dict]) -> dict:
    month = pd.to_datetime(sales["order_date"]).dt.month
    elec_mask = sales["category"] == "Electronics"
    emp = pd.json_normalize(employees, sep="_")
    return {
        "sales_rows": int(len(sales)),
        "total_revenue": round(float(sales["revenue"].sum()), 2),
        "revenue_by_region": {k: round(float(v), 2) for k, v in sales.groupby("region")["revenue"].sum().items()},
        "revenue_by_month": {str(int(k)): round(float(v), 2) for k, v in sales.groupby(month)["revenue"].sum().items()},
        "electronics_revenue_by_month": {
            str(int(k)): round(float(v), 2)
            for k, v in sales[elec_mask].groupby(month[elec_mask])["revenue"].sum().items()
        },
        "employee_rows": int(len(emp)),
        "avg_salary_by_department": {k: round(float(v), 2) for k, v in emp.groupby("department")["salary"].mean().items()},
        "headcount_by_department": {k: int(v) for k, v in emp.groupby("department").size().items()},
        "pdf_kpi_rows": len(KPI_ROWS) - 1,
        "pdf_q3_reason_keywords": ["supply-chain", "laptop", "port strike"],
    }


def generate(out_dir: Path) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)
    sales = make_sales(rng)
    employees = make_employees(rng)
    sales.to_csv(out_dir / "sales_2024.csv", index=False, lineterminator="\n")
    (out_dir / "employees.json").write_text(json.dumps(employees, indent=2) + "\n", encoding="utf-8")
    make_pdf(out_dir / "annual_report_2024.pdf")
    expected = expected_answers(sales, employees)
    (out_dir / "expected_answers.json").write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
    return expected


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "tests" / "fixtures"
    result = generate(target)
    print(f"Wrote sample data to {target}: {result['sales_rows']} sales rows, {result['employee_rows']} employees")
