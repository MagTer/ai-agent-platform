import os
import time
import json
import hashlib
import pathlib
from collections import deque
from typing import List, Dict, Any, Optional

import requests
from fastapi import FastAPI, Query, Body, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import trafilatura

# -----------------------------
# Environment / defaults
# -----------------------------
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng:8080")
LITELLM_BASE = os.getenv("LITELLM_BASE", "http://litellm:4000")

MODEL_EN = os.getenv("MODEL_EN", "local/llama3-8b")
MODEL_SV = os.getenv("MODEL_SV", "local/sv-sw3-6.7b")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", MODEL_EN)

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))
MAX_CHARS = int(os.getenv("MAX_CHARS", "12000"))

CACHE_DIR = pathlib.Path(os.getenv("CACHE_DIR", "/app/.cache"))
CACHE_TTL = int(os.getenv("CACHE_TTL", str(60 * 60 * 24)))  # 24h

RATE_WINDOW = int(os.getenv("RATE_WINDOW", "60"))    # seconds
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
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> requests.Response:
    if headers is None:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; WebFetch/0.3.2)"}
    last_exc: Optional[Exception] = None
    for i in range(tries):
        try:
            return requests.get(url, timeout=timeout, params=params, headers=headers)
        except Exception as e:
            last_exc = e
            if i < tries - 1:
                time.sleep(backoff ** i)
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

# -----------------------------
# SearxNG search
# -----------------------------
@app.get("/search")
def search(q: str = Query(..., min_length=2), k: int = 5, lang: str = "sv") -> Dict[str, Any]:
    url = SEARXNG_URL.rstrip("/") + "/search"
    params = {"q": q, "format": "json", "language": lang, "safesearch": 1}
    try:
        r = http_get(url, timeout=REQUEST_TIMEOUT, params=params)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail="searxng error: {}".format(e))
    results = []
    for item in data.get("results", [])[:k]:
        results.append({
            "title": item.get("title"),
            "url": item.get("url"),
            "snippet": item.get("content") or item.get("snippet")
        })
    return {"query": q, "lang": lang, "results": results}

# -----------------------------
# Summarization (via LiteLLM)
# -----------------------------
def _build_summary_prompt(query: str, urls: List[str], items: List[Dict[str, Any]]) -> Dict[str, str]:
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
        sources_lines.append("- [{}] {}".format(i, u))
    sources_md = "\n".join(sources_lines)

    system = (
        "You are a precise research assistant. Summarize key points based only on the provided sources. "
        "Cite sources as [n] and list links clearly. Avoid speculation."
    )
    user_parts = [
        "Svarssprak: svenska (sv-SE).",
        "Fraga: {}".format(query),
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
        ""
    ]
    user = "\n".join(user_parts)
    return {"system": system, "user": user}

def summarize_with_litellm(model: str, query: str, urls: List[str], items: List[Dict[str, Any]], lang: str) -> str:
    prompts = _build_summary_prompt(query, urls, items)
    if prompts["user"] == "Inga kallor kunde extraheras.":
        return prompts["user"]

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompts["system"]},
            {"role": "user", "content": prompts["user"]}
        ],
        "temperature": 0.2,
        "max_tokens": 700
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
        return "Summarization failed: {}".format(e)

# -----------------------------
# Research core
# -----------------------------
def _pick_model(model: Optional[str], lang: str) -> str:
    if model and model.strip():
        return model
    return MODEL_SV if lang.lower().startswith("sv") else MODEL_EN

def _research_core(q: str, k: int, model: Optional[str], lang: str):
    chosen_model = _pick_model(model, lang)
    s = search(q=q, k=k, lang=lang)
    urls = [r["url"] for r in s["results"] if r.get("url")]
    extracts = [fetch_and_extract(u) for u in urls]
    summary = summarize_with_litellm(chosen_model, q, urls, extracts, lang)
    return {"query": q, "model": chosen_model, "lang": lang, "sources": urls, "summary": summary}

# -----------------------------
# API: health, extract, research
# -----------------------------
@app.get("/health")
def health():
    return {"ok": True}

@app.post("/extract")
def extract(urls: List[str] = Body(...)) -> Dict[str, Any]:
    out = [fetch_and_extract(u) for u in urls]
    return {"items": out}

class ResearchPayload(BaseModel):
    query: str
    k: int = 5
    model: Optional[str] = None
    lang: str = "sv"

@app.post("/research")
def research_post(payload: ResearchPayload, _=Depends(limiter)):
    return _research_core(payload.query, payload.k, payload.model, payload.lang)

@app.get("/research")
def research_get(
    q: str = Query(..., min_length=2),
    k: int = Query(5, ge=1, le=10),
    model: Optional[str] = Query(None),
    lang: str = Query("sv"),
    _=Depends(limiter)
):
    return _research_core(q, k, model, lang)
