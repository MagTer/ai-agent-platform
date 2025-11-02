import importlib


def test_mmr_selection():
    # Import fetcher.app to access _mmr and cosine via module
    app = importlib.import_module("fetcher.app")
    # Build simple vectors: query near v0 and v1; v1 similar to v0; v2 far
    import numpy as np

    q = np.array([1.0, 0.0], dtype=np.float32)
    v0 = np.array([0.99, 0.01], dtype=np.float32)
    v1 = np.array([0.98, 0.02], dtype=np.float32)
    v2 = np.array([0.0, 1.0], dtype=np.float32)

    idxs = app._mmr(q, [v0, v1, v2], k=2, lam=0.7)
    # Expect to pick one of v0/v1 first, then diversify to v2
    assert len(idxs) == 2
    assert 2 in idxs
    assert any(i in idxs for i in (0, 1))
