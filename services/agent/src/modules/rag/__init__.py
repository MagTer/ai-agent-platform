import logging
import os
from typing import Any

import numpy as np
from qdrant_client import AsyncQdrantClient

from modules.embedder import get_embedder

logger = logging.getLogger(__name__)


class RAGManager:
    def __init__(self):
        self.qdrant_url = os.getenv("QDRANT_URL", "http://qdrant:6333")
        self.client = AsyncQdrantClient(url=self.qdrant_url)
        self.embedder = get_embedder()
        self.top_k = int(os.getenv("QDRANT_TOP_K", "5"))
        self.mmr_lambda = float(os.getenv("MMR_LAMBDA", "0.7"))
        self.collection_name = os.getenv(
            "QDRANT_COLLECTION", "agent-memories"
        )  # Default from fetcher

    def _cosine(self, a: np.ndarray, b: np.ndarray) -> float:
        da = np.linalg.norm(a) + 1e-9
        db = np.linalg.norm(b) + 1e-9
        return float(np.dot(a, b) / (da * db))

    def _mmr(
        self, query_vec: np.ndarray, doc_vecs: list[np.ndarray], k: int, lam: float
    ) -> list[int]:
        if not doc_vecs:
            return []
        sims = [self._cosine(query_vec, v) for v in doc_vecs]
        selected: list[int] = []
        candidates = set(range(len(doc_vecs)))
        while candidates and len(selected) < k:
            if not selected:
                i = int(np.argmax(sims))
                selected.append(i)
                candidates.remove(i)
                continue

            best_i = None
            best_score = float("-inf")
            for i in list(candidates):
                redundancy = max(self._cosine(doc_vecs[i], doc_vecs[j]) for j in selected)
                diversity = 1.0 - redundancy
                score = (1.0 - lam) * sims[i] + lam * diversity
                if score > best_score:
                    best_score = score
                    best_i = i

            if best_i is None:
                break
            selected.append(best_i)
            candidates.remove(best_i)
        return selected

    async def retrieve(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        k = top_k or self.top_k
        try:
            vecs = self.embedder.embed([query])
            if not vecs:
                return []
            qvec = np.array(vecs[0], dtype=np.float32)

            # Helper to search
            # We assume collection exists. In a full implementation we might want to check/create.
            res = await self.client.search(
                collection_name=self.collection_name,
                query_vector=qvec.tolist(),
                limit=max(k * 3, k),
                with_payload=True,
                with_vectors=True,
            )

            docs: list[dict[str, Any]] = []
            dvecs: list[np.ndarray] = []

            for p in res:
                payload = p.payload or {}
                url = payload.get("url")
                text = payload.get("text")
                vec = p.vector

                if not url or not text or vec is None:
                    continue

                docs.append({"url": url, "text": text, "score": p.score, "source": "memory"})
                dvecs.append(np.array(vec, dtype=np.float32))

            if not docs:
                return []

            # Dedup by URL
            seen = set()
            uniq_docs = []
            uniq_vecs = []
            for d, v in zip(docs, dvecs, strict=False):
                if d["url"] in seen:
                    continue
                seen.add(d["url"])
                uniq_docs.append(d)
                uniq_vecs.append(v)

            # MMR
            valid_k = min(k, len(uniq_docs))
            idxs = self._mmr(qvec, uniq_vecs, valid_k, self.mmr_lambda)
            return [uniq_docs[i] for i in idxs]

        except Exception as e:
            logger.error(f"RAG Retrieval failed: {e}")
            return []

    async def close(self):
        await self.client.close()
