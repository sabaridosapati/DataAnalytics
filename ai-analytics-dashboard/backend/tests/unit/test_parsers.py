import json

import pytest

from app.ingestion.detect import FileKind
from app.ingestion.parsers import ParseError, parse_file


def test_parse_sales_csv(fixtures_dir):
    r = parse_file(fixtures_dir / "sales_2024.csv", FileKind.CSV, "sales_2024.csv")
    assert len(r.tables) == 1 and not r.segments
    t = r.tables[0]
    assert t.name_hint == "sales_2024" and len(t.df) == 2000


def test_parse_employees_json_flattens_nested(fixtures_dir):
    r = parse_file(fixtures_dir / "employees.json", FileKind.JSON, "employees.json")
    df = r.tables[0].df
    assert len(df) == 200
    assert {"address_city", "address_state"} <= set(df.columns)


def test_parse_pdf_tables_and_text(fixtures_dir):
    r = parse_file(fixtures_dir / "annual_report_2024.pdf", FileKind.PDF, "annual_report_2024.pdf")
    assert len(r.tables) == 1
    kpi = r.tables[0]
    assert kpi.name_hint == "annual_report_2024_table_p3_1"
    assert len(kpi.df) == 4 and "Quarter" in kpi.df.columns
    text = " ".join(s.text for s in r.segments)
    assert "supply-chain" in text and "port strike" in text
    assert {s.page for s in r.segments} == {1, 2, 3}


def test_csv_with_bom_and_cp1252(tmp_path):
    p = tmp_path / "bom.csv"
    p.write_bytes("﻿name,amount\nCafé,10\n".encode("utf-8"))
    df = parse_file(p, FileKind.CSV, "bom.csv").tables[0].df
    assert list(df.columns) == ["name", "amount"] and df.iloc[0, 0] == "Café"
    p2 = tmp_path / "legacy.csv"
    p2.write_bytes("name,amount\nCaf\xe9,10\n".encode("latin-1"))
    assert parse_file(p2, FileKind.CSV, "legacy.csv").tables[0].df.iloc[0, 0] == "Café"


def test_json_records_under_single_key(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"meta": {"v": 1}, "items": [{"a": 1, "tags": ["x"]}, {"a": 2, "tags": []}]}))
    df = parse_file(p, FileKind.JSON, "x.json").tables[0].df
    assert len(df) == 2 and df.loc[0, "tags"] == '["x"]'


def test_jsonl(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"a": 1}\n{"a": 2}\n')
    assert len(parse_file(p, FileKind.JSON, "x.jsonl").tables[0].df) == 2


def test_non_tabular_json_becomes_text(tmp_path):
    p = tmp_path / "doc.json"
    p.write_text(json.dumps({"title": "Policy", "body": "Remote work is allowed."}))
    r = parse_file(p, FileKind.JSON, "doc.json")
    assert not r.tables and "Remote work" in r.segments[0].text


def test_text_file(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("Customers complained about delivery delays.")
    assert parse_file(p, FileKind.TEXT, "notes.txt").segments[0].text.startswith("Customers")


def test_empty_file_raises(tmp_path):
    p = tmp_path / "empty.csv"
    p.write_text("")
    with pytest.raises(ParseError):
        parse_file(p, FileKind.CSV, "empty.csv")


def test_excel_multiple_sheets(tmp_path):
    import pandas as pd

    p = tmp_path / "book.xlsx"
    with pd.ExcelWriter(p) as w:
        pd.DataFrame({"a": [1, 2]}).to_excel(w, sheet_name="Stock", index=False)
        pd.DataFrame({"b": ["x"]}).to_excel(w, sheet_name="Suppliers", index=False)
        pd.DataFrame().to_excel(w, sheet_name="Empty", index=False)
    r = parse_file(p, FileKind.EXCEL, "book.xlsx")
    assert [t.name_hint for t in r.tables] == ["book_Stock", "book_Suppliers"]
