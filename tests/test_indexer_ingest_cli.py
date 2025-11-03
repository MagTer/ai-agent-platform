import sys
from types import SimpleNamespace


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http error")

    def json(self):
        return self._payload


def test_ingest_cli_mocks(monkeypatch, tmp_path, capsys):
    # Lazy import to allow monkeypatch
    import importlib

    mod = importlib.import_module("indexer.ingest")

    # Mock requests.post for /extract and /embed
    calls = []

    def fake_post(url, json=None, timeout=30):
        calls.append(url)
        if url.endswith("/extract"):
            return FakeResponse({"items": [{"url": "https://example.com", "text": "hello world"}]})
        if url.endswith("/embed"):
            # Return a single 384-dim vector of zeros
            return FakeResponse({"vectors": [[0.0] * 384]})
        raise AssertionError("Unexpected POST " + url)

    monkeypatch.setattr(mod, "requests", SimpleNamespace(post=fake_post))

    # Fake QdrantClient
    class FakeCollections:
        def __init__(self):
            self.collections = []

    class FakeQdrant:
        def __init__(self, url):
            self.url = url
            self._recreated = False
            self._upserts = []

        def get_collections(self):
            return FakeCollections()

        def recreate_collection(self, name, vectors_config):
            self._recreated = True

        def upsert(self, collection_name, wait, points):
            self._upserts.append((collection_name, wait, points))

    monkeypatch.setattr(mod, "QdrantClient", FakeQdrant)

    # Simulate CLI argv
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    argv = [
        "ingest.py",
        "https://example.com",
        "--webfetch",
        "http://localhost:8081",
        "--embedder",
        "http://localhost:8082",
        "--qdrant",
        "http://localhost:6333",
        "--collection",
        "memory",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    # Run main
    mod.main()

    # Output check
    captured = capsys.readouterr()
    assert "Upserted 1 chunks" in captured.out
