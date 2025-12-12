import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
import numpy as np
from fastapi import Body, FastAPI, HTTPException

EMBEDDER_BASE = os.getenv("EMBEDDER_BASE", "http://embedder:8082")
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
LITELLM_BASE = os.getenv("LITELLM_BASE", "http://litellm:4000")
QDRANT_TOP_K = int(os.getenv("QDRANT_TOP_K", "5"))
MMR_LAMBDA = float(os.getenv("MMR_LAMBDA", "0.7"))
ENABLE_RAG = os.getenv("ENABLE_RAG", "true").lower() == "true"
RAG_MAX_SOURCES = int(os.getenv("RAG_MAX_SOURCES", "5"))
RAG_MAX_CHARS = int(os.getenv("RAG_MAX_CHARS", "1200"))

http_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient()
    yield
    if http_client:
        await http_client.aclose()


app = FastAPI(title="RAG Proxy", version="0.2.0", lifespan=lifespan)


async def get_client() -> httpx.AsyncClient:
    global http_client
    if http_client is None:
        http_client = httpx.AsyncClient()
    return http_client


async def embed_texts(texts: list[str]) -> list[list[float]]:
    client = await get_client()
    r = await client.post(
        f"{EMBEDDER_BASE.rstrip('/')}/embed",
        json={"inputs": texts, "normalize": True},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("vectors", [])


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    da = np.linalg.norm(a) + 1e-9
    db = np.linalg.norm(b) + 1e-9
    return float(np.dot(a, b) / (da * db))


def _mmr(query_vec: np.ndarray, doc_vecs: list[np.ndarray], k: int, lam: float) -> list[int]:
    if not doc_vecs:
        return []
    sims = [_cosine(query_vec, v) for v in doc_vecs]
    selected: list[int] = []
    candidates = set(range(len(doc_vecs)))
    while candidates and len(selected) < k:
        if not selected:
            i = int(np.argmax(sims))
            selected.append(i)
            candidates.remove(i)
            continue
        best_i = None
        best_score = -1e9
        for i in list(candidates):
            div = max(_cosine(doc_vecs[i], doc_vecs[j]) for j in selected)
            score = lam * sims[i] - (1 - lam) * div
            if score > best_score:
                best_score = score
                best_i = i
        selected.append(best_i)  # type: ignore
        candidates.remove(best_i)  # type: ignore
    return selected


async def qdrant_retrieve(query: str, top_k: int) -> list[dict[str, Any]]:
    vecs = await embed_texts([query])
    if not vecs:
        return []
    qvec = np.array(vecs[0], dtype=np.float32)
    payload = {
        "vector": qvec.tolist(),
        "limit": max(top_k * 3, top_k),
        "with_payload": True,
        "with_vector": True,
    }
    client = await get_client()
    r = await client.post(
        f"{QDRANT_URL.rstrip('/')}/collections/memory/points/search",
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    res = r.json().get("result", [])
    docs: list[dict[str, Any]] = []
    dvecs: list[np.ndarray] = []
    for p in res:
        pl = p.get("payload") or {}
        url = pl.get("url")
        text = pl.get("text") or ""
        vec = p.get("vector")
        if not url or not text or vec is None:
            continue
        docs.append({"url": url, "text": text})
        dvecs.append(np.array(vec, dtype=np.float32))
    if not docs:
        return []
    # Dedup by URL
    seen = set()
    uniq_docs = []
    uniq_vecs = []
    for d, v in zip(docs, dvecs, strict=False):
        u = d["url"]
        if u in seen:
            continue
        seen.add(u)
        uniq_docs.append(d)
        uniq_vecs.append(v)
    idxs = _mmr(qvec, uniq_vecs, min(top_k, len(uniq_docs)), MMR_LAMBDA)
    return [uniq_docs[i] for i in idxs]


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/v1/models")
def models():
    return {
        "object": "list",
        "data": [
            {
                "id": "rag/llama3-en",
                "object": "model",
                "owned_by": "ragproxy",
                "description": "Retrieval-augmented llama3.1-8b via Qdrant/embedder",
            }
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(body: dict[str, Any] = Body(...)):
    model = (body.get("model") or "").lower()
    messages: list[dict[str, str]] = list(body.get("messages") or [])
    if not messages:
        raise HTTPException(status_code=400, detail="messages are required")

    use_rag = ENABLE_RAG and model.startswith("rag/")
    # Map to underlying LiteLLM model
    forward_model = "local/llama3-en"
    if model.endswith("-sv"):
        forward_model = "local/llama3-en"
        # Swedish fallback uses the English model; translation occurs elsewhere.

    final_messages = messages
    if use_rag:
        user_msgs = [m for m in messages if m.get("role") == "user" and m.get("content")]
        query = user_msgs[-1]["content"] if user_msgs else ""
        hits = (await qdrant_retrieve(query, QDRANT_TOP_K))[:RAG_MAX_SOURCES]
        if hits:
            chunks = []
            sources = []
            for i, d in enumerate(hits, start=1):
                text = d.get("text") or ""
                if len(text) > RAG_MAX_CHARS:
                    text = text[:RAG_MAX_CHARS] + "\n...\n"
                chunks.append(f"Source [{i}] {d['url']}\n{text}")
                sources.append(f"- [{i}] {d['url']}")
            context = "\n\n".join(chunks)
            src_list = "\n".join(sources)
            if forward_model.endswith("-sv"):
                sys = (
                    "Du är en noggrann assistent. Använd ENDAST det givna underlaget för fakta. "
                    "Citera som [n] och lista källor."
                )
            else:
                sys = (
                    "You are a precise assistant. Use ONLY the provided context for facts. "
                    "Cite as [n] and list sources."
                )
            user_aug = f"Question: {query}\n\nContext:\n{context}\n\nSources:\n{src_list}\n\n"
            final_messages = [
                {"role": "system", "content": sys},
                {"role": "user", "content": user_aug},
            ]

    fwd = dict(body)
    fwd["model"] = forward_model
    fwd["messages"] = final_messages
    client = await get_client()
    r = await client.post(f"{LITELLM_BASE.rstrip('/')}/v1/chat/completions", json=fwd, timeout=120)
    r.raise_for_status()
    return r.json()
