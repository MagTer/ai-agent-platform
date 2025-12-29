import logging
import os
from typing import Any

import numpy as np
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models

from modules.embedder import get_embedder

logger = logging.getLogger(__name__)


class RAGManager:
    def __init__(self) -> None:
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

    async def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        filters: dict[str, Any] | None = None,
        collection_name: str | None = None,
    ) -> list[dict[str, Any]]:
        k = top_k or self.top_k
        target_collection = collection_name or self.collection_name
        try:
            vecs = self.embedder.embed([query])
            if not vecs:
                return []
            qvec = np.array(vecs[0], dtype=np.float32)

            query_filter = None
            if filters:
                conditions: list[Any] = []
                for key, value in filters.items():
                    conditions.append(
                        models.FieldCondition(key=key, match=models.MatchValue(value=value))
                    )
                query_filter = models.Filter(must=conditions)

            # Helper to search
            # 'search' is deprecated/removed in newer clients, use 'query_points'
            res = await self.client.query_points(  # type: ignore
                collection_name=target_collection,
                query=qvec.tolist(),
                query_filter=query_filter,
                limit=max(k * 3, k),
                with_payload=True,
                with_vectors=True,
            )

            docs: list[dict[str, Any]] = []
            dvecs: list[np.ndarray] = []

            for p in res.points:
                payload = p.payload or {}
                # Support both 'url' (web) and 'filepath' (code)
                uri = payload.get("url") or payload.get("filepath")
                text = payload.get("text")
                vec = p.vector

                if not uri or not text or vec is None:
                    continue

                # Reconstruct doc object
                doc_info = {
                    "uri": uri,  # Generic URI
                    "url": payload.get("url"),  # Keep explicit if present
                    "filepath": payload.get("filepath"),
                    "text": text,
                    "score": p.score,
                    "source": payload.get("source", "memory"),
                    # Include other metadata
                    "name": payload.get("name"),
                    "type": payload.get("type"),
                }

                docs.append(doc_info)
                dvecs.append(np.array(vec, dtype=np.float32))

            if not docs:
                return []

            # Dedup by URI (filepath or url)
            seen = set()
            uniq_docs = []
            uniq_vecs = []
            for d, v in zip(docs, dvecs, strict=False):
                if d["uri"] in seen:
                    continue
                seen.add(d["uri"])
                uniq_docs.append(d)
                uniq_vecs.append(v)

            # MMR
            valid_k = min(k, len(uniq_docs))
            idxs = self._mmr(qvec, uniq_vecs, valid_k, self.mmr_lambda)
            return [uniq_docs[i] for i in idxs]

        except Exception as e:
            logger.error(f"RAG Retrieval failed: {e}")
            return []

    async def ingest_document(
        self,
        content: str,
        metadata: dict[str, Any],
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
    ) -> int:
        """
        Ingest a document into Qdrant.
        Splits content into chunks, embeds them, and upserts.
        """
        if not content:
            return 0

        # Simple chunking strategy
        chunks = []
        start = 0
        while start < len(content):
            end = start + chunk_size
            chunk = content[start:end]
            chunks.append(chunk)
            start += chunk_size - chunk_overlap

        if not chunks:
            return 0

        try:
            # Embed all chunks
            embeddings = self.embedder.embed(chunks)
            if not embeddings:
                logger.warning("Embedder returned no embeddings")
                return 0

            points = []
            import uuid

            for i, (chunk, vector) in enumerate(zip(chunks, embeddings, strict=False)):
                point_id = str(uuid.uuid4())
                payload = metadata.copy()
                payload["text"] = chunk
                payload["chunk_index"] = i

                points.append(
                    models.PointStruct(
                        id=point_id,
                        vector=vector,
                        payload=payload,
                    )
                )

            # Upsert to Qdrant
            # Ensure Collection Exists (handled by caller or assumed known)

            await self.client.upsert(collection_name=self.collection_name, points=points)

            logger.info(f"Ingested {len(points)} chunks for doc {metadata.get('uri', 'unknown')}")
            return len(points)

        except Exception:
            logger.exception("Failed to ingest document")
            return 0

    async def close(self) -> None:
        await self.client.close()
