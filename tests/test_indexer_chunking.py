from services.indexer.ingest import chunk_text


def test_chunk_text_basic():
    text = "abcdefghijklmnopqrstuvwxyz" * 10  # 260 chars
    chunks = chunk_text(text, size=50, overlap=10)
    assert len(chunks) > 0
    # All chunks except possibly last should be size<=50
    assert all(len(c) <= 50 for c in chunks)
    # Overlap: consecutive chunks share the first 10 chars (when long enough)
    if len(chunks) >= 2:
        assert chunks[0][-10:] == chunks[1][:10]
