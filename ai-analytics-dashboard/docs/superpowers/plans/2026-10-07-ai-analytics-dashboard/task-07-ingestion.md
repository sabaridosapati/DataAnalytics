# Task 7: Ingestion pipeline + datasets API

**Files:**
- Create: `backend/app/util.py`, `backend/app/catalog.py`
- Create: `backend/app/ingestion/__init__.py` (empty), `detect.py`, `parsers.py`, `tabular.py`, `chunker.py`, `pipeline.py`
- Create: `backend/app/api/datasets.py`
- Modify: `backend/app/main.py` (register datasets router)
- Test: `backend/tests/unit/test_detect.py`, `test_tabular.py`, `test_parsers.py`, `test_chunker.py`; `backend/tests/integration/test_datasets_api.py`

**Interfaces:**
- Produces `app.util`: `to_jsonable(v)` (Decimal→float, date/datetime→ISO str, numpy→python, NaN/inf→None, bytes→None, other→str when not JSON-native), `vector_literal(list[float]) -> str` (`"[0.1,0.2]"`).
- Produces `app.ingestion.detect`: `FileKind` (`csv,json,excel,pdf,docx,text`), `UnsupportedFileError`, `detect_kind(filename, head: bytes) -> FileKind`, `guess_delimiter(text) -> str`, `looks_delimited(head: bytes) -> bool`.
- Produces `app.ingestion.parsers`: `ParseError`, `ParsedTable(name_hint, df)`, `TextSegment(text, page)`, `ParseResult(tables, segments)`, `parse_file(path, kind, original_name) -> ParseResult`.
- Produces `app.ingestion.tabular`: `to_identifier(name, fallback)`, `clean_column_names(cols) -> list[str]`, `infer_types(df) -> (df, {col: pg_type})` with pg types in `{bigint, double precision, boolean, date, timestamp, text}`, `to_records(df, types)`, `async create_and_load_table(conn, table, df, types) -> int`.
- Produces `app.ingestion.chunker.chunk_text(text, max_chars=3200, overlap=400) -> list[str]`.
- Produces `app.ingestion.pipeline.process_upload(state, dataset_id, path, original_name)` (background task; serialized by `state.ingest_lock`).
- Produces `app.catalog`: `ColumnInfo(name, pg_type, samples, description)`, `DatasetInfo(id, slug, name, kind, columns, row_count, chunk_count)`, `Catalog(version, datasets)` with `.get(slug)`, `.tables()`, `.documents()`, `.sql_schema(slugs=None) -> {slug: {col: type}}`, `.summary() -> str`; `async load_catalog(state) -> Catalog` (only `status='ready'` datasets; cached on `state.catalog_cache` by version).
- Produces endpoints: `POST /api/datasets/upload` (multipart `files`, 202 → list[DatasetOut]), `GET /api/datasets`, `GET /api/datasets/{id}` (DatasetDetail with `columns` + `preview`), `DELETE /api/datasets/{id}` (admin, 204).
- `DatasetOut = {id, name, slug, kind, source_filename, file_type, parent_upload_id, row_count, chunk_count, status, error, created_at}`.

- [ ] **Step 1: Write the failing unit tests**

**File: `backend/tests/unit/test_detect.py`**
```python
import pytest

from app.ingestion.detect import FileKind, UnsupportedFileError, detect_kind, guess_delimiter, looks_delimited


def test_csv_ok():
    assert detect_kind("a.csv", b"a,b\n1,2\n") is FileKind.CSV


def test_pdf_requires_magic():
    with pytest.raises(UnsupportedFileError):
        detect_kind("a.pdf", b"hello")
    assert detect_kind("a.pdf", b"%PDF-1.7\n...") is FileKind.PDF


@pytest.mark.parametrize("name", ["a.exe", "noext", "script.sh", "photo.png"])
def test_unsupported_extensions(name):
    with pytest.raises(UnsupportedFileError):
        detect_kind(name, b"MZ\x90\x00")


def test_binary_content_in_text_extension_rejected():
    with pytest.raises(UnsupportedFileError):
        detect_kind("a.csv", b"PK\x03\x04binary")
    with pytest.raises(UnsupportedFileError):
        detect_kind("a.txt", b"abc\x00\x00def")


def test_office_formats_require_correct_container():
    assert detect_kind("a.xlsx", b"PK\x03\x04....") is FileKind.EXCEL
    assert detect_kind("a.docx", b"PK\x03\x04....") is FileKind.DOCX
    assert detect_kind("a.xls", b"\xd0\xcf\x11\xe0....") is FileKind.EXCEL
    with pytest.raises(UnsupportedFileError):
        detect_kind("a.xlsx", b"not a zip")


def test_delimited_flat_files_detected():
    assert detect_kind("t.dat", b"id|amount|city\n1|2.5|X\n2|3.5|Y\n") is FileKind.CSV
    assert detect_kind("t.txt", b"id\tamount\n1\t2\n3\t4\n") is FileKind.CSV
    assert detect_kind("notes.txt", b"This is a note about sales.\nAnother line here.\n") is FileKind.TEXT
    assert detect_kind("server.log", b"INFO start\nINFO done\n") is FileKind.TEXT


def test_delimiter_helpers():
    assert guess_delimiter("a;b;c\n1;2;3\n") == ";"
    assert guess_delimiter("a|b\n1|2\n") == "|"
    assert looks_delimited(b"a,b\n1,2\n3,4\n")
    assert not looks_delimited(b"Hello, world.\nNo commas here\n")
```

**File: `backend/tests/unit/test_tabular.py`**
```python
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
```

**File: `backend/tests/unit/test_parsers.py`**
```python
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
```

**File: `backend/tests/unit/test_chunker.py`**
```python
from app.ingestion.chunker import chunk_text


def test_short_text_single_chunk():
    assert chunk_text("hello   world") == ["hello world"]


def test_empty_and_nul():
    assert chunk_text("   ") == []
    assert chunk_text("a\x00b") == ["ab"]


def test_long_text_split_with_overlap():
    text = " ".join(f"Sentence number {i}." for i in range(1000))
    chunks = chunk_text(text, max_chars=500, overlap=100)
    assert len(chunks) > 10
    assert all(len(c) <= 500 for c in chunks)
    assert chunks[1][:30] in chunks[0]
    assert "Sentence number 999." in chunks[-1]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `docker compose build backend; docker compose run --rm --no-deps backend pytest tests/unit/test_detect.py tests/unit/test_tabular.py tests/unit/test_parsers.py tests/unit/test_chunker.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ingestion'`.

- [ ] **Step 3: Implement util, detect, chunker**

**File: `backend/app/util.py`**
```python
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
```

**File: `backend/app/ingestion/__init__.py`**
```python
```

**File: `backend/app/ingestion/detect.py`**
```python
import csv
from enum import Enum
from pathlib import Path


class FileKind(str, Enum):
    CSV = "csv"
    JSON = "json"
    EXCEL = "excel"
    PDF = "pdf"
    DOCX = "docx"
    TEXT = "text"


class UnsupportedFileError(ValueError):
    pass


EXTENSIONS = {
    ".csv": FileKind.CSV, ".tsv": FileKind.CSV, ".psv": FileKind.CSV,
    ".dat": FileKind.TEXT, ".txt": FileKind.TEXT, ".md": FileKind.TEXT, ".log": FileKind.TEXT,
    ".json": FileKind.JSON, ".jsonl": FileKind.JSON, ".ndjson": FileKind.JSON,
    ".xlsx": FileKind.EXCEL, ".xls": FileKind.EXCEL,
    ".pdf": FileKind.PDF,
    ".docx": FileKind.DOCX,
}
SUPPORTED = ", ".join(sorted(EXTENSIONS))


def _sniff(text: str) -> str | None:
    lines = [ln for ln in text[:20000].splitlines() if ln.strip()][:20]
    if not lines:
        return None
    try:
        return csv.Sniffer().sniff("\n".join(lines), delimiters=",\t|;").delimiter
    except csv.Error:
        return None


def guess_delimiter(text: str) -> str:
    first = text.splitlines()[0] if text else ""
    return _sniff(text) or ("\t" if "\t" in first else ",")


def looks_delimited(head: bytes) -> bool:
    """True when the first lines have the same non-zero count of one delimiter."""
    text = head.decode("utf-8", errors="ignore")
    lines = [ln for ln in text.splitlines() if ln.strip()][:20]
    if len(lines) < 2:
        return False
    delim = _sniff(text)
    if delim is None:
        return False
    complete = lines[:-1] if len(lines) > 2 else lines  # last line may be truncated
    counts = {ln.count(delim) for ln in complete}
    return len(counts) == 1 and counts.pop() >= 1


def detect_kind(filename: str, head: bytes) -> FileKind:
    ext = Path(filename).suffix.lower()
    if ext not in EXTENSIONS:
        raise UnsupportedFileError(f"Unsupported file type '{ext or 'none'}'. Supported: {SUPPORTED}.")
    kind = EXTENSIONS[ext]
    is_pdf = head.startswith(b"%PDF-")
    is_zip = head.startswith(b"PK\x03\x04")
    is_ole = head.startswith(b"\xd0\xcf\x11\xe0")
    mismatch = UnsupportedFileError(f"File content does not match its '{ext}' extension.")
    if kind is FileKind.PDF:
        if not is_pdf:
            raise mismatch
        return kind
    if kind is FileKind.EXCEL:
        if (ext == ".xlsx" and not is_zip) or (ext == ".xls" and not is_ole):
            raise mismatch
        return kind
    if kind is FileKind.DOCX:
        if not is_zip:
            raise mismatch
        return kind
    if is_pdf or is_zip or is_ole or b"\x00" in head[:4096]:
        raise mismatch
    if kind is FileKind.TEXT and ext in (".txt", ".dat") and looks_delimited(head):
        return FileKind.CSV
    return kind
```

**File: `backend/app/ingestion/chunker.py`**
```python
import re


def chunk_text(text: str, max_chars: int = 3200, overlap: int = 400) -> list[str]:
    """Split text into ~max_chars chunks with overlap, preferring paragraph/sentence boundaries."""
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            window = text[start:end]
            cut = max(window.rfind("\n\n"), window.rfind(". "), window.rfind("\n"))
            if cut > max_chars // 2:
                end = start + cut + 1
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks
```

- [ ] **Step 4: Implement parsers and tabular helpers**

**File: `backend/app/ingestion/parsers.py`**
```python
import io
import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from app.ingestion.detect import FileKind, guess_delimiter

NA_VALUES = ["", "NA", "N/A", "n/a", "null", "NULL", "None", "NaN", "nan"]


class ParseError(ValueError):
    pass


@dataclass
class ParsedTable:
    name_hint: str
    df: pd.DataFrame


@dataclass
class TextSegment:
    text: str
    page: int | None = None


@dataclass
class ParseResult:
    tables: list[ParsedTable] = field(default_factory=list)
    segments: list[TextSegment] = field(default_factory=list)


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _blank_to_none(df: pd.DataFrame) -> pd.DataFrame:
    return df.where(df != "", None)


def _parse_csv(path: Path, name: str) -> ParseResult:
    text = read_text(path)
    if not text.strip():
        raise ParseError("The file is empty.")
    try:
        df = pd.read_csv(
            io.StringIO(text),
            sep=guess_delimiter(text),
            dtype=str,
            keep_default_na=False,
            na_values=NA_VALUES,
            skipinitialspace=True,
        )
    except pd.errors.EmptyDataError as exc:
        raise ParseError("The file is empty.") from exc
    except pd.errors.ParserError as exc:
        raise ParseError(f"Could not parse the delimited file: {exc}") from exc
    return ParseResult(tables=[ParsedTable(Path(name).stem, df.dropna(how="all"))])


def _find_records(data):
    if isinstance(data, list) and data and sum(isinstance(r, dict) for r in data) / len(data) >= 0.8:
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list) and v and all(isinstance(r, dict) for r in v)]
        if len(lists) == 1:
            return lists[0]
    return None


def _parse_json(path: Path, name: str) -> ParseResult:
    text = read_text(path).strip()
    if not text:
        raise ParseError("The file is empty.")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            data = [json.loads(line) for line in text.splitlines() if line.strip()]
        except json.JSONDecodeError as exc:
            raise ParseError(f"Invalid JSON: {exc}") from exc
    records = _find_records(data)
    if records is None:
        return ParseResult(segments=[TextSegment(json.dumps(data, indent=2, ensure_ascii=False))])
    df = pd.json_normalize(records, sep="_")
    for col in df.columns:
        if df[col].map(lambda v: isinstance(v, (list, dict))).any():
            df[col] = df[col].map(lambda v: json.dumps(v) if isinstance(v, (list, dict)) else v)
    return ParseResult(tables=[ParsedTable(Path(name).stem, df)])


def _parse_excel(path: Path, name: str) -> ParseResult:
    try:
        sheets = pd.read_excel(path, sheet_name=None, dtype=object)
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"Could not read the Excel file: {exc}") from exc
    stem = Path(name).stem
    tables = []
    for sheet, df in sheets.items():
        df = df.dropna(how="all").dropna(axis=1, how="all")
        if df.empty:
            continue
        tables.append(ParsedTable(stem if len(sheets) == 1 else f"{stem}_{sheet}", df))
    return ParseResult(tables=tables)


def _parse_pdf(path: Path, name: str) -> ParseResult:
    import pdfplumber

    stem = Path(name).stem
    result = ParseResult()
    try:
        with pdfplumber.open(path) as pdf:
            for pno, page in enumerate(pdf.pages, start=1):
                for k, raw in enumerate(page.extract_tables() or [], start=1):
                    rows = [[(c or "").strip() for c in r] for r in raw if r and any(c and c.strip() for c in r)]
                    if len(rows) < 2 or len(rows[0]) < 2:
                        continue
                    header = [h or f"col_{i + 1}" for i, h in enumerate(rows[0])]
                    body = [(r + [""] * len(header))[: len(header)] for r in rows[1:]]
                    df = _blank_to_none(pd.DataFrame(body, columns=header))
                    result.tables.append(ParsedTable(f"{stem}_table_p{pno}_{k}", df))
                text = page.extract_text() or ""
                if text.strip():
                    result.segments.append(TextSegment(text, page=pno))
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"Could not read the PDF: {exc}") from exc
    if not result.tables and not result.segments:
        raise ParseError("No extractable text found in the PDF (scanned/image-only PDFs are not supported).")
    return result


def _parse_docx(path: Path, name: str) -> ParseResult:
    import docx

    try:
        document = docx.Document(str(path))
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"Could not read the Word document: {exc}") from exc
    stem = Path(name).stem
    result = ParseResult(segments=[TextSegment("\n".join(p.text for p in document.paragraphs))])
    for k, table in enumerate(document.tables, start=1):
        rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
        if len(rows) >= 2 and len(rows[0]) >= 2:
            result.tables.append(ParsedTable(f"{stem}_table_{k}", _blank_to_none(pd.DataFrame(rows[1:], columns=rows[0]))))
    return result


def _parse_text(path: Path, name: str) -> ParseResult:
    return ParseResult(segments=[TextSegment(read_text(path))])


PARSERS = {
    FileKind.CSV: _parse_csv,
    FileKind.JSON: _parse_json,
    FileKind.EXCEL: _parse_excel,
    FileKind.PDF: _parse_pdf,
    FileKind.DOCX: _parse_docx,
    FileKind.TEXT: _parse_text,
}


def parse_file(path: Path, kind: FileKind, original_name: str) -> ParseResult:
    result = PARSERS[kind](Path(path), original_name)
    result.tables = [t for t in result.tables if not t.df.empty and len(t.df.columns) > 0]
    result.segments = [s for s in result.segments if s.text.strip()]
    if not result.tables and not result.segments:
        raise ParseError("The file contains no readable data.")
    return result
```

**File: `backend/app/ingestion/tabular.py`**
```python
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
    records = to_records(df, types)
    raw = await conn.get_raw_connection()
    await raw.driver_connection.copy_records_to_table(
        table, records=records, columns=list(df.columns), schema_name="data"
    )
    await conn.execute(text(f'GRANT SELECT ON data."{table}" TO query_ro'))
    return len(records)
```

- [ ] **Step 5: Run unit tests to verify they pass**

Run: `docker compose build backend; docker compose run --rm --no-deps backend pytest tests/unit -q`
Expected: all pass.

- [ ] **Step 6: Commit the parsing layer**

```bash
git add backend
git commit -m "feat(ingestion): file detection, parsers, type inference, chunking"
```

- [ ] **Step 7: Write the failing datasets API integration tests**

**File: `backend/tests/integration/test_datasets_api.py`**
```python
from helpers import auth, register_verified_user, upload_and_wait


async def test_upload_three_fixtures(client, admin_token, fixtures_dir):
    ds = await upload_and_wait(
        client,
        admin_token,
        [fixtures_dir / "sales_2024.csv", fixtures_dir / "employees.json", fixtures_dir / "annual_report_2024.pdf"],
    )
    assert all(d["status"] == "ready" for d in ds), ds
    by_slug = {d["slug"]: d for d in ds}
    assert by_slug["sales_2024"]["kind"] == "table" and by_slug["sales_2024"]["row_count"] == 2000
    assert by_slug["employees"]["row_count"] == 200
    assert by_slug["annual_report_2024"]["kind"] == "document" and by_slug["annual_report_2024"]["chunk_count"] >= 3
    kpi = by_slug["annual_report_2024_table_p3_1"]
    assert kpi["kind"] == "table" and kpi["row_count"] == 4
    assert kpi["parent_upload_id"] == by_slug["annual_report_2024"]["id"] or by_slug["annual_report_2024"]["parent_upload_id"] == kpi["id"]

    async with client.app.state.ro_pool.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM data.sales_2024") == 2000

    detail = (await client.get(f"/api/datasets/{by_slug['employees']['id']}", headers=auth(admin_token))).json()
    types = {c["column_name"]: c["pg_type"] for c in detail["columns"]}
    assert types["salary"] == "bigint" and types["hire_date"] == "date" and types["address_city"] == "text"
    assert len(detail["preview"]["rows"]) == 20 and "salary" in detail["preview"]["columns"]

    doc = (await client.get(f"/api/datasets/{by_slug['annual_report_2024']['id']}", headers=auth(admin_token))).json()
    assert doc["preview"]["chunks"][0]["metadata"]["file"] == "annual_report_2024.pdf"


async def test_same_filename_twice_gets_unique_slug(client, admin_token, fixtures_dir):
    first = await upload_and_wait(client, admin_token, [fixtures_dir / "sales_2024.csv"])
    second = await upload_and_wait(client, admin_token, [fixtures_dir / "sales_2024.csv"])
    assert first[0]["slug"] == "sales_2024" and second[0]["slug"] == "sales_2024_2"
    async with client.app.state.ro_pool.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM data.sales_2024_2") == 2000


async def test_regular_user_can_upload_text(client):
    _, token = await register_verified_user(client)
    files = [("files", ("notes.txt", b"Customers complained about late deliveries in August.", "text/plain"))]
    r = await client.post("/api/datasets/upload", files=files, headers=auth(token))
    assert r.status_code == 202
    listing = (await client.get("/api/datasets", headers=auth(token))).json()
    assert listing[0]["status"] == "ready" and listing[0]["kind"] == "document"


async def test_unsupported_and_mismatched_files_rejected(client, admin_token):
    r = await client.post("/api/datasets/upload", files=[("files", ("evil.exe", b"MZ\x90", "application/octet-stream"))], headers=auth(admin_token))
    assert r.status_code == 415 and r.json()["error"]["code"] == "unsupported_file"
    r = await client.post("/api/datasets/upload", files=[("files", ("fake.pdf", b"hello", "application/pdf"))], headers=auth(admin_token))
    assert r.status_code == 415
    assert (await client.get("/api/datasets", headers=auth(admin_token))).json() == []


async def test_too_large_rejected(client, admin_token, monkeypatch):
    monkeypatch.setattr(client.app.state.settings, "max_upload_mb", 0)
    r = await client.post("/api/datasets/upload", files=[("files", ("a.csv", b"a,b\n1,2\n", "text/csv"))], headers=auth(admin_token))
    assert r.status_code == 413


async def test_bad_content_marks_dataset_failed(client, admin_token):
    files = [("files", ("broken.json", b"{not json", "application/json"))]
    r = await client.post("/api/datasets/upload", files=files, headers=auth(admin_token))
    assert r.status_code == 202
    d = (await client.get("/api/datasets", headers=auth(admin_token))).json()[0]
    assert d["status"] == "failed" and "Invalid JSON" in d["error"]


async def test_delete_requires_admin_and_drops_table(client, admin_token, fixtures_dir):
    ds = await upload_and_wait(client, admin_token, [fixtures_dir / "sales_2024.csv"])
    _, user_token = await register_verified_user(client)
    r = await client.delete(f"/api/datasets/{ds[0]['id']}", headers=auth(user_token))
    assert r.status_code == 403
    r = await client.delete(f"/api/datasets/{ds[0]['id']}", headers=auth(admin_token))
    assert r.status_code == 204
    async with client.app.state.ro_pool.acquire() as conn:
        assert await conn.fetchval("SELECT to_regclass('data.sales_2024')") is None
    assert (await client.get("/api/datasets", headers=auth(admin_token))).json() == []


async def test_requires_auth(client):
    assert (await client.get("/api/datasets")).status_code == 401
```

- [ ] **Step 8: Run them to verify they fail**

Run: `docker compose build backend; docker compose run --rm backend pytest tests/integration/test_datasets_api.py -q`
Expected: FAIL — 404 on `/api/datasets/upload` (router not registered).

- [ ] **Step 9: Implement catalog, pipeline, API**

**File: `backend/app/catalog.py`**
```python
from dataclasses import dataclass, field

from sqlalchemy import select

from app.db.models import Dataset, DatasetColumn
from app.db.versions import get_catalog_version


@dataclass
class ColumnInfo:
    name: str
    pg_type: str
    samples: list = field(default_factory=list)
    description: str = ""


@dataclass
class DatasetInfo:
    id: int
    slug: str
    name: str
    kind: str  # table | document
    columns: list[ColumnInfo] = field(default_factory=list)
    row_count: int = 0
    chunk_count: int = 0


@dataclass
class Catalog:
    version: int
    datasets: list[DatasetInfo] = field(default_factory=list)

    def get(self, slug: str) -> DatasetInfo | None:
        return next((d for d in self.datasets if d.slug == slug), None)

    def tables(self) -> list[DatasetInfo]:
        return [d for d in self.datasets if d.kind == "table"]

    def documents(self) -> list[DatasetInfo]:
        return [d for d in self.datasets if d.kind == "document"]

    def sql_schema(self, slugs: list[str] | None = None) -> dict[str, dict[str, str]]:
        return {
            d.slug: {c.name: c.pg_type for c in d.columns}
            for d in self.tables()
            if slugs is None or d.slug in slugs
        }

    def summary(self) -> str:
        lines = []
        for d in self.tables():
            cols = ", ".join(f"{c.name} ({c.pg_type})" for c in d.columns[:40])
            more = f", … {len(d.columns) - 40} more" if len(d.columns) > 40 else ""
            lines.append(f"- {d.slug} [table, {d.row_count} rows] from '{d.name}': {cols}{more}")
        for d in self.documents():
            lines.append(f"- {d.slug} [document, {d.chunk_count} text chunks] from '{d.name}'")
        return "\n".join(lines) if lines else "(no datasets)"


async def load_catalog(state) -> Catalog:
    async with state.sessionmaker() as session:
        version = await get_catalog_version(session)
        cached = getattr(state, "catalog_cache", None)
        if cached is not None and cached.version == version:
            return cached
        datasets = (
            await session.scalars(select(Dataset).where(Dataset.status == "ready").order_by(Dataset.id))
        ).all()
        columns = (await session.scalars(select(DatasetColumn).order_by(DatasetColumn.id))).all()
    by_dataset: dict[int, list[ColumnInfo]] = {}
    for c in columns:
        by_dataset.setdefault(c.dataset_id, []).append(ColumnInfo(c.column_name, c.pg_type, c.sample_values or [], c.description or ""))
    catalog = Catalog(
        version=version,
        datasets=[
            DatasetInfo(d.id, d.slug, d.name, d.kind, by_dataset.get(d.id, []), d.row_count, d.chunk_count)
            for d in datasets
        ],
    )
    state.catalog_cache = catalog
    return catalog
```

**File: `backend/app/ingestion/pipeline.py`**
```python
import asyncio
import json
import logging
from pathlib import Path

import pandas as pd
from sqlalchemy import select, text

from app.db.models import Dataset, DatasetColumn
from app.db.versions import bump_catalog_version
from app.ingestion.chunker import chunk_text
from app.ingestion.detect import UnsupportedFileError, detect_kind
from app.ingestion.parsers import ParsedTable, ParseError, ParseResult, TextSegment, parse_file
from app.ingestion.tabular import clean_column_names, create_and_load_table, infer_types, to_identifier
from app.util import to_jsonable, vector_literal

log = logging.getLogger(__name__)

DESCRIBE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["descriptions"],
    "properties": {
        "descriptions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["column", "description"],
                "properties": {"column": {"type": "string"}, "description": {"type": "string"}},
            },
        }
    },
}


def _samples(series: pd.Series, n: int = 5) -> list:
    return [to_jsonable(v) for v in pd.unique(series.dropna())[:n]]


async def _set_status(state, dataset_id: int, status: str, error: str | None = None) -> None:
    async with state.sessionmaker() as s:
        ds = await s.get(Dataset, dataset_id)
        if ds is not None:
            ds.status = status
            ds.error = error
            await s.commit()


async def unique_slug(session, base: str) -> str:
    base = to_identifier(base, "dataset")[:50]
    taken = set((await session.scalars(select(Dataset.slug).where(Dataset.slug.like(f"{base}%")))).all())
    taken |= set(
        (await session.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'data'"))).scalars().all()
    )
    slug, k = base, 2
    while slug in taken:
        slug = f"{base}_{k}"
        k += 1
    return slug


async def _target(session, reuse_id: int | None, parent_id: int) -> Dataset:
    if reuse_id is not None:
        return await session.get(Dataset, reuse_id)
    parent = await session.get(Dataset, parent_id)
    ds = Dataset(
        name=parent.source_filename,
        slug=f"pending_child_{parent_id}",
        source_filename=parent.source_filename,
        file_type=parent.file_type,
        stored_path=parent.stored_path,
        uploaded_by=parent.uploaded_by,
        parent_upload_id=parent_id,
        status="processing",
    )
    session.add(ds)
    return ds


async def _describe_columns(state, hint: str, df: pd.DataFrame, types: dict[str, str]) -> dict[str, str]:
    lines = [
        f"- {c} ({types[c]}): e.g. {', '.join(str(v) for v in _samples(df[c], 3))}" for c in list(df.columns)[:60]
    ]
    try:
        out = await state.llm.chat_json(
            purpose="describe_columns",
            system="Write one short, factual description (max 12 words) for each column of a dataset, "
            "based only on its name, type and sample values.",
            user=f"Dataset: {hint}\nColumns:\n" + "\n".join(lines),
            schema=DESCRIBE_SCHEMA,
        )
        return {
            d["column"]: d["description"][:200]
            for d in out.get("descriptions", [])
            if isinstance(d, dict) and d.get("column") in df.columns
        }
    except Exception as exc:  # noqa: BLE001 - descriptions are optional
        log.warning("Column description failed for %s: %s", hint, exc)
        return {}


async def _store_table(state, reuse_id: int | None, parent_id: int, original_name: str, table: ParsedTable) -> None:
    df = table.df.copy()
    df.columns = clean_column_names(df.columns)
    df, types = infer_types(df)
    descriptions = await _describe_columns(state, table.name_hint, df, types)
    async with state.sessionmaker() as s:
        slug = await unique_slug(s, table.name_hint)
        ds = await _target(s, reuse_id, parent_id)
        is_main = table.name_hint == Path(original_name).stem
        ds.name = original_name if is_main else f"{original_name} ({table.name_hint})"
        ds.slug = slug
        ds.kind = "table"
        await s.flush()
        conn = await s.connection()
        ds.row_count = await create_and_load_table(conn, slug, df, types)
        for col in df.columns:
            s.add(
                DatasetColumn(
                    dataset_id=ds.id,
                    column_name=col,
                    pg_type=types[col],
                    sample_values=_samples(df[col]),
                    description=descriptions.get(col, ""),
                )
            )
        ds.status, ds.error = "ready", None
        await s.commit()


async def _store_document(
    state, reuse_id: int | None, parent_id: int, original_name: str, segments: list[TextSegment]
) -> None:
    chunks: list[tuple[str, dict]] = []
    for seg in segments:
        for piece in chunk_text(seg.text):
            chunks.append((piece, {"file": original_name, "page": seg.page}))
    if not chunks:
        if reuse_id is not None:
            raise ParseError("The file contains no readable text.")
        return
    embeddings: list[list[float]] = []
    for i in range(0, len(chunks), 100):
        embeddings.extend(await state.llm.embed([c[0] for c in chunks[i : i + 100]]))
    async with state.sessionmaker() as s:
        slug = await unique_slug(s, Path(original_name).stem)
        ds = await _target(s, reuse_id, parent_id)
        ds.name, ds.slug, ds.kind = original_name, slug, "document"
        await s.flush()
        await s.execute(
            text(
                "INSERT INTO vector.chunks (dataset_id, chunk_index, content, metadata, embedding) "
                "VALUES (:dataset_id, :idx, :content, CAST(CAST(:meta AS text) AS jsonb), "
                "CAST(CAST(:emb AS text) AS vector))"
            ),
            [
                {"dataset_id": ds.id, "idx": i, "content": c, "meta": json.dumps(m), "emb": vector_literal(e)}
                for i, ((c, m), e) in enumerate(zip(chunks, embeddings))
            ],
        )
        ds.chunk_count = len(chunks)
        ds.status, ds.error = "ready", None
        await s.commit()


async def _store(state, dataset_id: int, original_name: str, result: ParseResult) -> None:
    reuse: int | None = dataset_id
    for table in result.tables:
        await _store_table(state, reuse, dataset_id, original_name, table)
        reuse = None
    if result.segments:
        await _store_document(state, reuse, dataset_id, original_name, result.segments)
    async with state.sessionmaker() as s:
        await bump_catalog_version(s)
        await s.commit()


async def process_upload(state, dataset_id: int, path: Path, original_name: str) -> None:
    async with state.ingest_lock:
        try:
            await _set_status(state, dataset_id, "processing")
            kind = detect_kind(original_name, Path(path).read_bytes()[:8192])
            result = await asyncio.to_thread(parse_file, Path(path), kind, original_name)
            await _store(state, dataset_id, original_name, result)
        except Exception as exc:  # noqa: BLE001
            log.exception("Ingestion failed for dataset %s", dataset_id)
            if isinstance(exc, (ParseError, UnsupportedFileError)):
                message = str(exc)
            else:
                message = f"Processing failed: {type(exc).__name__}: {exc}"
            await _set_status(state, dataset_id, "failed", message[:1000])
```

**File: `backend/app/api/datasets.py`**
```python
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import admin_user, current_user, get_session
from app.api.errors import api_error
from app.api.ratelimit import limiter
from app.db.models import Dataset, DatasetColumn, User
from app.db.versions import bump_catalog_version
from app.ingestion.detect import UnsupportedFileError, detect_kind
from app.ingestion.pipeline import process_upload
from app.util import to_jsonable

router = APIRouter(prefix="/api/datasets", tags=["datasets"])
MAX_FILES = 10


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    slug: str
    kind: str
    source_filename: str
    file_type: str
    parent_upload_id: int | None
    row_count: int
    chunk_count: int
    status: str
    error: str | None
    created_at: datetime


class ColumnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    column_name: str
    pg_type: str
    sample_values: list
    description: str


class DatasetDetail(DatasetOut):
    columns: list[ColumnOut]
    preview: dict


@router.post("/upload", status_code=202, response_model=list[DatasetOut])
@limiter.limit("10/minute")
async def upload(
    request: Request,
    background: BackgroundTasks,
    files: list[UploadFile] = File(...),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    settings = request.app.state.settings
    if not files:
        raise api_error(400, "no_files", "No files were uploaded.")
    if len(files) > MAX_FILES:
        raise api_error(400, "too_many_files", f"Upload at most {MAX_FILES} files at a time.")
    limit = settings.max_upload_mb * 1024 * 1024
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved: list[tuple[str, Path, str]] = []  # (original name, stored path, file kind)
    written: list[Path] = []
    try:
        for f in files:
            name = Path(f.filename or "upload").name[:200]
            dest = upload_dir / f"{uuid4().hex}{Path(name).suffix.lower()}"
            written.append(dest)
            size, head = 0, b""
            with dest.open("wb") as out:
                while chunk := await f.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise api_error(413, "file_too_large", f"'{name}' exceeds the {settings.max_upload_mb} MB limit.")
                    if len(head) < 8192:
                        head += chunk[: 8192 - len(head)]
                    out.write(chunk)
            if size == 0:
                raise api_error(400, "empty_file", f"'{name}' is empty.")
            try:
                kind = detect_kind(name, head)
            except UnsupportedFileError as exc:
                raise api_error(415, "unsupported_file", f"'{name}': {exc}")
            saved.append((name, dest, kind.value))
    except Exception:
        for path in written:
            path.unlink(missing_ok=True)
        raise

    created: list[Dataset] = []
    for name, path, kind in saved:
        ds = Dataset(
            name=name,
            slug=f"pending_{uuid4().hex[:16]}",
            kind="pending",
            source_filename=name,
            file_type=kind,
            stored_path=str(path),
            uploaded_by=user.id,
            status="pending",
        )
        session.add(ds)
        created.append(ds)
    await session.commit()
    for ds, (name, path, _) in zip(created, saved):
        background.add_task(process_upload, request.app.state, ds.id, path, name)
    return created


@router.get("", response_model=list[DatasetOut])
async def list_datasets(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return (await session.scalars(select(Dataset).order_by(Dataset.created_at.desc(), Dataset.id.desc()))).all()


@router.get("/{dataset_id}", response_model=DatasetDetail)
async def get_dataset(
    dataset_id: int, request: Request, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    ds = await session.get(Dataset, dataset_id)
    if ds is None:
        raise api_error(404, "not_found", "Dataset not found.")
    columns = (
        await session.scalars(select(DatasetColumn).where(DatasetColumn.dataset_id == ds.id).order_by(DatasetColumn.id))
    ).all()
    preview: dict = {}
    if ds.status == "ready" and ds.kind == "table":
        async with request.app.state.ro_pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                stmt = await conn.prepare(f'SELECT * FROM data."{ds.slug}" LIMIT 20')
                records = await stmt.fetch()
                cols = [a.name for a in stmt.get_attributes()]
        preview = {"columns": cols, "rows": [[to_jsonable(v) for v in r.values()] for r in records]}
    elif ds.status == "ready" and ds.kind == "document":
        rows = await session.execute(
            text(
                "SELECT chunk_index, content, metadata FROM vector.chunks WHERE dataset_id = :id "
                "ORDER BY chunk_index LIMIT 20"
            ),
            {"id": ds.id},
        )
        preview = {"chunks": [{"index": r.chunk_index, "content": r.content, "metadata": r.metadata} for r in rows]}
    base = DatasetOut.model_validate(ds).model_dump()
    return DatasetDetail(**base, columns=[ColumnOut.model_validate(c) for c in columns], preview=preview)


@router.delete("/{dataset_id}", status_code=204)
async def delete_dataset(
    dataset_id: int, admin: User = Depends(admin_user), session: AsyncSession = Depends(get_session)
):
    ds = await session.get(Dataset, dataset_id)
    if ds is None:
        raise api_error(404, "not_found", "Dataset not found.")
    if ds.status in ("pending", "processing"):
        raise api_error(409, "busy", "This dataset is still being processed. Try again shortly.")
    if ds.kind == "table":
        await session.execute(text(f'DROP TABLE IF EXISTS data."{ds.slug}"'))
    stored = ds.stored_path
    await session.delete(ds)
    await bump_catalog_version(session)
    await session.commit()
    if stored:
        remaining = await session.scalar(select(func.count()).select_from(Dataset).where(Dataset.stored_path == stored))
        if not remaining:
            Path(stored).unlink(missing_ok=True)
    return Response(status_code=204)
```

**Modify `backend/app/main.py`:** change the router import and list:
```python
from app.api import auth, datasets
```
```python
ROUTERS = [auth.router, datasets.router]
```

- [ ] **Step 10: Run the integration tests to verify they pass**

Run: `docker compose build backend; docker compose run --rm backend pytest tests/integration/test_datasets_api.py -q`
Expected: all pass.

- [ ] **Step 11: Run everything**

Run: `docker compose run --rm backend pytest -q`
Expected: all pass.

- [ ] **Step 12: Commit**

```bash
git add backend
git commit -m "feat(ingestion): upload pipeline to Postgres/pgvector and datasets API"
```
