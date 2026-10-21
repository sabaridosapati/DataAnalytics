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
