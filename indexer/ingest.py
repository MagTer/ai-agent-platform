import argparse
import hashlib
import time
import uuid
from typing import List

import requests
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams, PointStruct


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def chunk_text(text: str, size: int = 800, overlap: int = 100) -> List[str]:
    if size <= 0:
        return [text]
    chunks = []
    i = 0
    n = len(text)
    while i < n:
        chunks.append(text[i:i+size])
        i += max(1, size - overlap)
    return chunks


def ensure_collection(client: QdrantClient, name: str, dim: int = 384):
    try:
        exists = client.collection_exists(name)
    except Exception:
        # Fallback for older clients
        exists = name in [c.name for c in client.get_collections().collections or []]
    if not exists:
        client.create_collection(name, vectors_config=VectorParams(size=dim, distance=Distance.COSINE))


def main():
    ap = argparse.ArgumentParser(description="Ingest URLs into Qdrant using webfetch + embedder")
    ap.add_argument("urls", nargs="+", help="One or more URLs to ingest")
    ap.add_argument("--webfetch", default="http://localhost:8081", help="webfetch base URL")
    ap.add_argument("--embedder", default="http://localhost:8082", help="embedder base URL")
    ap.add_argument("--qdrant", default="http://localhost:6333", help="Qdrant URL")
    ap.add_argument("--collection", default="memory", help="Qdrant collection name")
    args = ap.parse_args()

    qd = QdrantClient(url=args.qdrant)
    ensure_collection(qd, args.collection, dim=384)

    # Extract
    r = requests.post(args.webfetch.rstrip("/") + "/extract", json=args.urls, timeout=30)
    r.raise_for_status()
    items = r.json().get("items", [])

    # Prepare chunks
    texts = []
    mapping = []  # (url, chunk_ix)
    for it in items:
        url = it.get("url")
        text = (it.get("text") or "").strip()
        if not url or not text:
            continue
        chunks = chunk_text(text)
        for ix, ch in enumerate(chunks):
            texts.append(ch)
            mapping.append((url, ix))

    if not texts:
        print("No content to index.")
        return

    # Embed
    r = requests.post(args.embedder.rstrip("/") + "/embed", json={"inputs": texts, "normalize": True}, timeout=60)
    r.raise_for_status()
    vectors = r.json().get("vectors", [])
    if len(vectors) != len(texts):
        raise RuntimeError("Embedding count mismatch")

    # Upsert
    points = []
    ts = int(time.time())
    for (url, ix), vec, text in zip(mapping, vectors, texts):
        # Qdrant allows integer or UUID ids; use a stable UUIDv5 from URL+chunk index
        pid = uuid.uuid5(uuid.NAMESPACE_URL, f"{url}#{ix}")
        payload = {"url": url, "text": text, "chunk_ix": ix, "ts": ts, "source": "web"}
        points.append(PointStruct(id=str(pid), vector=vec, payload=payload))

    qd.upsert(collection_name=args.collection, wait=True, points=points)
    print(f"Upserted {len(points)} chunks into '{args.collection}'")


if __name__ == "__main__":
    main()
