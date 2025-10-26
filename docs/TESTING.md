# Testing

## Overview
- Uses `pytest` for fast, offline-friendly tests.
- Focus: microservice health, retrieval logic, and ingestion helpers without network/GPU.

## Running

```bash
pytest -q
```

## What’s Covered

- `tests/test_embedder_health.py`
  - Imports `embedder.app` and checks `/health` and `/model` respond without downloading the model.

- `tests/test_indexer_chunking.py`
  - Verifies chunking size and overlap logic used during ingestion.

- `tests/test_fetcher_mmr.py`
  - Tests the MMR selection helper in `fetcher.app` with synthetic vectors.

- `tests/test_fetcher_toggle.py`
  - Ensures `ENABLE_QDRANT` toggles the retrieval path in `fetcher`.
  - Stubs network calls (`search`, `fetch_and_extract`, `qdrant_query`, and summarization) to stay offline.

## Integration (Optional)
For an end-to-end smoke test, run services first, then:

```powershell
python .\indexer\ingest.py "https://qdrant.tech/"
irm "http://localhost:8081/retrieval_debug?q=Qdrant"
```

## Notes
- Tests avoid pulling large models or hitting external network.
- If you add new retrieval/config flags, consider adding a toggle test similar to `test_fetcher_toggle.py`.

