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
