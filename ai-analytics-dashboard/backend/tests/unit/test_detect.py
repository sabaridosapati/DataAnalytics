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
