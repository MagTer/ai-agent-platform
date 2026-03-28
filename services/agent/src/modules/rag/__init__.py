import logging
from typing import Any

import numpy as np
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models

from core.protocols import IEmbedder

from ..indexer import SemanticChunker

logger = logging.getLogger(__name__)


class RAGManager:
    """RAG Manager with dependency injection for embedder."""

    def __init__(
        self,
        embedder: IEmbedder,
        qdrant_url: str = "http://qdrant:6333",
        collection_name: str = "documents_v2",
        top_k: int = 5,
        mmr_lambda: float = 0.7,
        qdrant_api_key: str | None = None,
        chunker: SemanticChunker | None = None,
    ) -> None:
        # Configuration
        self.qdrant_url = qdrant_url
        self.top_k = top_k
        self.mmr_lambda = mmr_lambda
        self.collection_name = collection_name
        self._qdrant_api_key = qdrant_api_key

        # Injected dependencies
        self.embedder = embedder

        # Chunker with default SemanticChunker
        self.chunker = chunker or SemanticChunker()

        # Lazy-loaded resources (NOT initialized here for faster startup)
        self._client: AsyncQdrantClient | None = None

    @property
    def client(self) -> AsyncQdrantClient:
        """Lazy-load Qdrant client on first access."""
        if self._client is None:
            logger.info(f"Connecting to Qdrant at {self.qdrant_url} (lazy load)")
            self._client = AsyncQdrantClient(url=self.qdrant_url, api_key=self._qdrant_api_key)
        return self._client

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
            vecs = await self.embedder.embed([query])
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
            res = await self.client.query_points(
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
                # Support 'url' (web), 'filepath' (code), and 'uri' (wiki/generic)
                uri = payload.get("url") or payload.get("filepath") or payload.get("uri")
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
        chunker: SemanticChunker | None = None,
        document_type: str = "prose",
    ) -> int:
        """
        Ingest a document into Qdrant using semantic chunking.

        Args:
            content: The document content to ingest
            metadata: Document metadata (uri, name, etc.)
            chunker: Optional SemanticChunker instance (defaults to self.chunker)
            document_type: Type of document ('markdown' or 'prose') for content-type routing

        Returns:
            Number of chunks ingested
        """
        if not content:
            return 0

        # Use provided chunker or instance default
        active_chunker = chunker or self.chunker

        # Semantic chunking with metadata preservation
        chunks_with_metadata = active_chunker.split_text(
            content, document_type=document_type
        )

        if not chunks_with_metadata:
            return 0

        # Extract just the text chunks for embedding
        chunk_texts = [c["text"] for c in chunks_with_metadata]

        try:
            # Embed all chunks
            embeddings = await self.embedder.embed(chunk_texts)
            if not embeddings:
                logger.warning("Embedder returned no embeddings")
                return 0

            points = []
            import uuid

            for i, (chunk_data, vector) in enumerate(zip(chunks_with_metadata, embeddings, strict=False)):
                point_id = str(uuid.uuid4())
                
                # Build payload with chunk metadata preserved
                payload = metadata.copy()
                payload["text"] = chunk_data["text"]
                payload["chunk_index"] = i
                
                # Preserve semantic chunk metadata
                chunk_metadata = chunk_data.get("metadata", {})
                if "section_title" in chunk_metadata:
                    payload["section_title"] = chunk_metadata["section_title"]
                if "chunk_type" in chunk_metadata:
                    payload["chunk_type"] = chunk_metadata["chunk_type"]
                if "document_type" in chunk_metadata:
                    payload["document_type"] = chunk_metadata["document_type"]

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

        except Exception:  # Intentional: catches embedder, chunker, and Qdrant errors
            logger.exception("Failed to ingest document")
            return 0

    async def close(self) -> None:
        """Close the Qdrant client if it was initialized."""
        if self._client is not None:
            await self._client.close()
            self._client = None
