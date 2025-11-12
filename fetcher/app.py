import hashlib
import json
import os
import pathlib
import time
from collections import deque
from html.parser import HTMLParser
from typing import Any

import numpy as np
import requests
from fastapi import Body, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from qdrant_client import QdrantClient

try:
    import trafilatura
except ModuleNotFoundError:  # pragma: no cover - exercised via fallback
    trafilatura = None  # type: ignore[assignment]


class _PlainTextExtractor(HTMLParser):
    """Minimal HTML-to-text converter used when trafilatura is unavailable."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:  # pragma: no cover - trivial
        if data.strip():
            self._chunks.append(data.strip())

    def get_text(self) -> str:
        return "\n".join(self._chunks)


def _extract_text(html: str) -> str:
    if trafilatura is not None:
        return (
            trafilatura.extract(  # type: ignore[no-any-return]
                html, include_images=False, include_tables=False
            )
            or ""
        )
    parser = _PlainTextExtractor()
    parser.feed(html)
    return parser.get_text()


# -----------------------------
# Environment / defaults
# -----------------------------
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng:8080")
LITELLM_BASE = os.getenv("LITELLM_BASE", "http://litellm:4000")
EMBEDDER_BASE = os.getenv("EMBEDDER_BASE", "http://embedder:8082")
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
ENABLE_QDRANT = os.getenv("ENABLE_QDRANT", "true").lower() == "true"
QDRANT_TOP_K = int(os.getenv("QDRANT_TOP_K", "5"))
MMR_LAMBDA = float(os.getenv("MMR_LAMBDA", "0.7"))

MODEL_EN = os.getenv("MODEL_EN", "local/phi3-en")
MODEL_SV = os.getenv(
    "MODEL_SV",
    "local/phi3-en",
)
# Swedish queries still run via the English pipeline; translation happens elsewhere.
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", MODEL_EN)

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))
MAX_CHARS = int(os.getenv("MAX_CHARS", "12000"))
MAX_HTML_CHARS = int(os.getenv("MAX_HTML_CHARS", "20000"))

# NOTE: The production container binds the repository at /app, but CI and
# local pytest runs execute in arbitrary working directories where /app may not
# be writable. Attempt to honor an explicit CACHE_DIR first, then gracefully
# fall back to a per-project cache under the current workspace when the default
# container path is unavailable.


def _resolve_cache_dir() -> pathlib.Path:
    env_dir = os.getenv("CACHE_DIR")
    if env_dir:
        path = pathlib.Path(env_dir)
    else:
        path = pathlib.Path("/app/.cache")

    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except PermissionError:
        fallback = pathlib.Path.cwd() / ".cache"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


CACHE_DIR = _resolve_cache_dir()
CACHE_TTL = int(os.getenv("CACHE_TTL", str(60 * 60 * 24)))  # 24h

RATE_WINDOW = int(os.getenv("RATE_WINDOW", "60"))  # seconds
RATE_MAX_REQ = int(os.getenv("RATE_MAX_REQ", "60"))  # requests per window

CACHE_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Web Fetcher", version="0.3.2")

# -----------------------------
# CORS
# -----------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# Rate limiter (simple, per-process)
# -----------------------------
_hits = deque()


def limiter():
    now = time.time()
    while _hits and now - _hits[0] > RATE_WINDOW:
        _hits.popleft()
    if len(_hits) >= RATE_MAX_REQ:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    _hits.append(now)


# -----------------------------
# HTTP helper with retries
# -----------------------------
def http_get(
    url: str,
    timeout: int,
    tries: int = 3,
    backoff: float = 1.5,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    if headers is None:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; WebFetch/0.3.2)"}
    last_exc: Exception | None = None
    for i in range(tries):
        try:
            return requests.get(url, timeout=timeout, params=params, headers=headers)
        except Exception as e:
            last_exc = e
            if i < tries - 1:
                time.sleep(backoff**i)
    raise last_exc  # type: ignore


# -----------------------------
# Cache helpers
# -----------------------------
def cache_key(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def cache_get(url: str):
    p = CACHE_DIR / cache_key(url)
    if p.exists() and (time.time() - p.stat().st_mtime) < CACHE_TTL:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def cache_set(url: str, data: dict):
    (CACHE_DIR / cache_key(url)).write_text(json.dumps(data), encoding="utf-8")


# -----------------------------
# Core fetch/extract
# -----------------------------
def _truncate_html(html: str) -> str:
    if len(html) <= MAX_HTML_CHARS:
        return html
    return html[:MAX_HTML_CHARS] + "\n... (truncated)\n"


def fetch_and_extract(url: str) -> dict[str, Any]:
    cached = cache_get(url)
    if cached:
        return cached
    try:
        r = http_get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        raw_html = r.text
        text = _extract_text(raw_html)
        text = text.strip()
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS] + "\n...\n"
        data = {
            "url": url,
            "ok": True,
            "text": text,
            "html": _truncate_html(raw_html),
        }
        cache_set(url, data)
        return data
    except Exception as e:
        data = {"url": url, "ok": False, "error": str(e), "text": "", "html": ""}
        cache_set(url, data)
        return data


# -----------------------------
# Embeddings via embedder service
# -----------------------------
def embed_texts(texts: list[str], normalize: bool = True) -> list[list[float]]:
    try:
        r = requests.post(
            EMBEDDER_BASE.rstrip("/") + "/embed",
            json={"inputs": texts, "normalize": normalize},
            timeout=REQUEST_TIMEOUT * 2,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("vectors", [])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"embedder error: {e}") from e


# -----------------------------
# Qdrant retrieval (best-effort)
# -----------------------------
_qdrant: QdrantClient | None = None


def get_qdrant() -> QdrantClient:
    global _qdrant
    if _qdrant is None:
        _qdrant = QdrantClient(url=QDRANT_URL)
    return _qdrant


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    da = np.linalg.norm(a) + 1e-9
    db = np.linalg.norm(b) + 1e-9
    return float(np.dot(a, b) / (da * db))


def _mmr(query_vec: np.ndarray, doc_vecs: list[np.ndarray], k: int, lam: float) -> list[int]:
    """Return indices selected via Maximal Marginal Relevance."""

    if not doc_vecs:
        return []

    lam = float(np.clip(lam, 0.0, 1.0))
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
        best_score = float("-inf")
        for i in list(candidates):
            redundancy = max(_cosine(doc_vecs[i], doc_vecs[j]) for j in selected)
            diversity = 1.0 - redundancy
            score = (1.0 - lam) * sims[i] + lam * diversity
            if score > best_score:
                best_score = score
                best_i = i

        if best_i is None:  # pragma: no cover - defensive, candidates non-empty
            break
        selected.append(best_i)
        candidates.remove(best_i)
    return selected


def qdrant_query(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    try:
        vecs = embed_texts([query])
        if not vecs:
            return []
        qvec = np.array(vecs[0], dtype=np.float32)
        client = get_qdrant()
        res = client.search(
            collection_name="memory",
            query_vector=qvec.tolist(),
            limit=max(top_k * 3, top_k),
            with_payload=True,
            with_vectors=True,
        )
        docs: list[dict[str, Any]] = []
        dvecs: list[np.ndarray] = []
        urls: list[str] = []
        for p in res or []:
            payload = p.payload or {}
            url = payload.get("url")
            text = payload.get("text") or ""
            vec = None
            if getattr(p, "vector", None) is not None:
                vec = np.array(p.vector, dtype=np.float32)
            if not url or not text or vec is None:
                continue
            docs.append({"ok": True, "url": url, "text": text, "source": "qdrant"})
            dvecs.append(vec)
            urls.append(url)
        if not docs:
            return []
        # Dedup by URL
        seen = set()
        uniq_docs: list[dict[str, Any]] = []
        uniq_vecs: list[np.ndarray] = []
        for d, v, u in zip(docs, dvecs, urls, strict=False):
            if u in seen:
                continue
            seen.add(u)
            uniq_docs.append(d)
            uniq_vecs.append(v)
        # MMR pick
        k = min(top_k, len(uniq_docs))
        idxs = _mmr(qvec, uniq_vecs, k, MMR_LAMBDA)
        return [uniq_docs[i] for i in idxs]
    except Exception:
        return []


# -----------------------------
# SearxNG search
# -----------------------------
@app.get("/search")
def search(q: str = Query(..., min_length=2), k: int = 5, lang: str = "sv") -> dict[str, Any]:
    url = SEARXNG_URL.rstrip("/") + "/search"
    params = {"q": q, "format": "json", "language": lang, "safesearch": 1}
    try:
        r = http_get(url, timeout=REQUEST_TIMEOUT, params=params)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"searxng error: {e}") from e
    results = []
    for item in data.get("results", [])[:k]:
        results.append(
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "snippet": item.get("content") or item.get("snippet"),
            }
        )
    return {"query": q, "lang": lang, "results": results}


# -----------------------------
# Summarization (via LiteLLM)
# -----------------------------
def _build_summary_prompt(
    query: str, urls: list[str], items: list[dict[str, Any]]
) -> dict[str, str]:
    # Build chunks
    chunks = []
    idx = 1
    for it in items:
        if it.get("ok") and it.get("text"):
            chunk = "Source [{}] {}\n{}\n".format(idx, it["url"], it["text"])
            chunks.append(chunk)
            idx += 1

    if not chunks:
        return {"system": "", "user": "Inga kallor kunde extraheras."}

    sources_lines = []
    for i, u in enumerate(urls, start=1):
        sources_lines.append(f"- [{i}] {u}")
    sources_md = "\n".join(sources_lines)

    system = (
        "You are a precise research assistant. Summarize key points based only on the provided "
        "sources. Cite sources as [n] and list links clearly. Avoid speculation."
    )
    user_parts = [
        "Svarssprak: svenska (sv-SE).",
        f"Fraga: {query}",
        "",
        "Underlag (urklipp, kan innehalla brus):",
        "",
        "\n\n".join(chunks[:6]),
        "",
        "Instruktioner:",
        "- Ge en kort bullet-sammanfattning (3-7 punkter).",
        "- Skriv en avslutande kort slutsats.",
        "- Lista 3-5 kallor som klickbara lankar.",
        "- Markera osakerhet dar relevant.",
        "",
        "Kallor:",
        sources_md,
        "",
    ]
    user = "\n".join(user_parts)
    return {"system": system, "user": user}


def summarize_with_litellm(
    model: str,
    query: str,
    urls: list[str],
    items: list[dict[str, Any]],
    lang: str,
) -> str:
    prompts = _build_summary_prompt(query, urls, items)
    if prompts["user"] == "Inga kallor kunde extraheras.":
        return prompts["user"]

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompts["system"]},
            {"role": "user", "content": prompts["user"]},
        ],
        "temperature": 0.2,
        "max_tokens": 700,
    }
    try:
        r = requests.post(
            LITELLM_BASE.rstrip("/") + "/v1/chat/completions",
            json=payload,
            timeout=REQUEST_TIMEOUT * 2,
        )
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"Summarization failed: {e}"


# -----------------------------
# Research core
# -----------------------------
def _pick_model(model: str | None, lang: str) -> str:
    if model and model.strip():
        return model
    return MODEL_SV if lang.lower().startswith("sv") else MODEL_EN


def _research_core(q: str, k: int, model: str | None, lang: str):
    chosen_model = _pick_model(model, lang)
    mem_extracts: list[dict[str, Any]] = []
    mem_urls: list[str] = []
    if ENABLE_QDRANT:
        mem_extracts = qdrant_query(q, top_k=min(QDRANT_TOP_K, k))
        mem_urls = [m["url"] for m in mem_extracts if m.get("url")]

    # Web
    s = search(q=q, k=k, lang=lang)
    web_urls = [r["url"] for r in s["results"] if r.get("url")]
    web_extracts = [fetch_and_extract(u) for u in web_urls]

    # Merge, prefer memory first
    urls = mem_urls + [u for u in web_urls if u not in set(mem_urls)]
    extracts = mem_extracts + web_extracts

    summary = summarize_with_litellm(chosen_model, q, urls, extracts, lang)
    return {"query": q, "model": chosen_model, "lang": lang, "sources": urls, "summary": summary}


# -----------------------------
# API: health, extract, research
# -----------------------------
@app.get("/health")
def health():
    return {"ok": True}


@app.post("/extract")
def extract(urls: list[str] = Body(...)) -> dict[str, Any]:
    out = [fetch_and_extract(u) for u in urls]
    return {"items": out}


class FetchPayload(BaseModel):
    url: str


@app.post("/fetch")
def fetch(payload: FetchPayload) -> dict[str, Any]:
    return {"item": fetch_and_extract(payload.url)}


class ResearchPayload(BaseModel):
    query: str
    k: int = 5
    model: str | None = None
    lang: str = "sv"


@app.post("/research")
def research_post(payload: ResearchPayload, _=Depends(limiter)):
    return _research_core(payload.query, payload.k, payload.model, payload.lang)


@app.get("/research")
def research_get(
    q: str = Query(..., min_length=2),
    k: int = Query(5, ge=1, le=10),
    model: str | None = Query(None),
    lang: str = Query("sv"),
    _=Depends(limiter),
):
    return _research_core(q, k, model, lang)


@app.get("/retrieval_debug")
def retrieval_debug(q: str = Query(..., min_length=2), k: int = Query(5, ge=1, le=10)):
    mem = qdrant_query(q, top_k=min(QDRANT_TOP_K, k)) if ENABLE_QDRANT else []
    s = search(q=q, k=k, lang="sv")
    web = s.get("results", [])
    return {"query": q, "enable_qdrant": ENABLE_QDRANT, "memory": mem, "web": web}
