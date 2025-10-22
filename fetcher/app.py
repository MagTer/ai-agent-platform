import os
import time
import json
import hashlib
import pathlib
from typing import List, Dict, Any

import requests
from fastapi import FastAPI, Query, Body, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
import trafilatura

# ---- Config (env with sensible defaults) ----
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng:8080")
LITELLM_BASE = os.getenv("LITELLM_BASE", "http://litellm:4000")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "local/llama3-8b")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "4"))  # reserved for future parallel fetch
MAX_CHARS = int(os.getenv("MAX_CHARS", "12000"))

CACHE_DIR = pathlib.Path(os.getenv("CACHE_DIR", "/app/.cache"))
CACHE_TTL = int(os.getenv("CACHE_TTL", str(60 * 60 * 24)))  # 24h
RATE_WINDOW = int(os.getenv("RATE_WINDOW", "60"))  # seconds
RATE_MAX_REQ = int(os.getenv("RATE_MAX_REQ", "60"))

CACHE_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Web Fetcher", version="0.2.0")

# ---- CORS ----
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Simple in-memory rate limiter (per-instance) ----
from collections import deque
_hits = deque()

def limiter():
    now = time.time()
    while _hits and now - _hits[0] > RATE_WINDOW:
        _hits.popleft()
    if len(_hits) >= RATE_MAX_REQ:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    _hits.append(now)

# ---- Helpers ----
def http_get(url: str, timeout: int, tries: int = 3, backoff: float = 1.5) -> requests.Response:
    last_exc = None
    for i in range(tries):
        try:
            return requests.get(
                url,
                timeout=timeout,
                headers={"User-Agent": "Mozilla/5.0 (compatible; WebFetch/0.2)"},
            )
        except Exception as e:
            last_exc = e
            if i < tries - 1:
                time.sleep(backoff ** i)
    raise last_exc  # type: ignore

def cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()

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

def fetch_and_extract(url: str) -> Dict[str, Any]:
    cached = cache_get(url)
    if cached:
        return cached
    try:
        r = http_get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        text = trafilatura.extract(
            r.text, include_images=False, include_tables=False
        ) or ""
        text = text.strip()
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS] + "\n...\n"
        data = {"url": url, "ok": True, "text": text}
        cache_set(url, data)
        return data
    except Exception as e:
        data = {"url": url, "ok": False, "error": str(e), "text": ""}
        cache_set(url, data)
        return data

def summarize_with_litellm(model: str, query: str, urls: List[str], items: List[Dict[str, Any]], lang: str) -> str:
    chunks = []
    for i, it in enumerate(items, start=1):
        if not it.get("ok") or not it.get("text"):
            continue
        chunks.append(f"Source [{i}] {it['url']}\n{it['text']}\n")

    if not chunks:
        return "Inga källor kunde extraheras."

    # Build a clean Swedish prompt with sources list
    sources_markdown = "\n".join([f"- [{i+1}] {u}" for i, u in enumerate(urls)])
    system = (
        "You are a precise research assistant. Summarize key points based only on the provided sources. "
        "Cite sources as [n] and list links clearly. Avoid speculation."
    )
    user = (
        f"Svarsspråk: svenska (sv-SE).\n"
        f"Fråga: {query}\n\n"
        "Underlag (urklipp, kan innehålla brus):\n\n" +
        "\n\n".join(chunks[:6]) +
        "\n\nInstruktioner:\n"
        "- Ge en kort bullet-sammanfattning (3–7 punkter).\n"
        "- Skriv en avslutande kort slutsats.\n"
        "- Lista 3–5 källor som klickbara länkar.\n"
        "- Markera osäkerhet där relevant.\n\n"
        "Källor:\n" + sources_markdown + "\n"
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ],
        "temperature": 0.2,
        "max_tokens": 700
    }
    try:
        r = requests.post(
            f"{LITELLM_BASE}/v1/chat/completions",
            json=payload,
            timeout=REQUEST_TIMEOUT * 2,
        )
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"Summarization failed: {e}"

# ---- Routes ----
@app.get("/health")
def health():
    return {"ok": True}

@app.get("/search")
def search(q: str = Query(..., min_length=2), k: int = 5, lang: str = "sv") -> Dict[str, Any]:
    url = f"{SEARXNG_URL}/search"
    params = {"q": q, "format": "json", "language": lang, "safesearch": 1}
    try:
        r = http_get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"searxng error: {e}")

    results = []
    for item in data.get("results", [])[:k]:
        results.append({
            "title": item.get("title"),
            "url": item.get("url"),
            "snippet": item.get("content") or item.get("snippet")
        })
    return {"query": q, "lang": lang, "results": results}

@app.post("/extract")
def extract(urls: List[str] = Body(...)) -> Dict[str, Any]:
    out = [fetch_and_extract(u) for u in urls]
    return {"items": out}

@app.post("/research")
def research(
    q: str = Query(..., min_length=2),
    k: int = Query(5, ge=1, le=10),
    model: str = Query(DEFAULT_MODEL),
    lang: str = Query("sv"),
    _=Depends(limiter)
) -> Dict[str, Any]:
    # 1) search
    s = search(q=q, k=k, lang=lang)
    urls = [r["url"] for r in s["results"] if r.get("url")]
    # 2) extract
    extracts = [fetch_and_extract(u) for u in urls]
    # 3) summarize
    summary = summarize_with_litellm(model, q, urls, extracts, lang)
    return {"query": q, "model": model, "lang": lang, "sources": urls, "summary": summary}
