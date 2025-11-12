"""Lightweight integration checks for the AI agent stack."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

try:
    import httpx  # type: ignore[import]
except ImportError:  # pragma: no cover - optional dependency
    httpx = None

DEFAULTS: dict[str, str] = {
    "ollama": os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
    "litellm": os.environ.get("LITELLM_URL", "http://127.0.0.1:4000"),
    "agent": os.environ.get("AGENT_URL", "http://127.0.0.1:8000"),
    "qdrant": os.environ.get("QDRANT_URL", "http://127.0.0.1:6333"),
}

PRIMARY_MODEL = os.environ.get("PRIMARY_MODEL", "phi3:mini")
LITELLM_MODEL = os.environ.get("LITELLM_MODEL", "local/phi3-en")

DEFAULT_TIMEOUT = float(os.environ.get("INTEGRATION_TIMEOUT", "300.0"))
RETRY_DELAY_SEC = float(os.environ.get("INTEGRATION_RETRY_DELAY", "10.0"))


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    """Send a JSON request and decode the response."""

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("integration checks only support http/https targets")

    if httpx:
        return _request_with_httpx(url, method=method, payload=payload)

    data: bytes | None = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, headers=headers, method=method)  # noqa: S310

    try:
        with urlopen(request, timeout=DEFAULT_TIMEOUT) as response:  # noqa: S310
            body = response.read()
            if not body:
                return response.status, None
            return response.status, json.loads(body.decode("utf-8"))
    except HTTPError as exc:
        return exc.code, _safe_load(exc.read())
    except URLError as exc:
        raise RuntimeError(f"Connection failed to {url}: {exc}") from exc


def _request_with_httpx(
    url: str,
    *,
    method: str,
    payload: dict[str, Any] | None,
) -> tuple[int, Any]:
    try:
        with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
            response = client.request(method, url, json=payload)
    except httpx.HTTPError as exc:  # pragma: no cover - typed wrapper
        raise RuntimeError(f"HTTPX request to {url} failed: {exc}") from exc
    text = response.text
    if not text:
        return response.status_code, None
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        return response.status_code, response.json()
    return response.status_code, text


def _safe_load(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return body.decode("utf-8", errors="ignore")


def expect(status: int, *, url: str, expected: int = 200, payload: Any | None = None) -> None:
    if status != expected:
        raise AssertionError(f"{url} returned {status}, expected {expected} ({payload})")


def test_ollama() -> None:
    base = DEFAULTS["ollama"]
    models_url = f"{base}/v1/models"
    status, data = request_json(models_url)
    expect(status, url=models_url, payload=data)
    if not isinstance(data, dict):
        raise AssertionError("Ollama /v1/models did not return JSON")
    model_ids = [entry.get("id") for entry in data.get("data", []) if isinstance(entry, dict)]
    if PRIMARY_MODEL not in model_ids:
        raise AssertionError(f"{PRIMARY_MODEL} not listed by Ollama: {model_ids}")

    chat_url = f"{base}/v1/chat/completions"
    payload = {
        "model": PRIMARY_MODEL,
        "messages": [{"role": "user", "content": "ping"}],
    }
    status, data = request_json(chat_url, method="POST", payload=payload)
    expect(status, url=chat_url, payload=data)
    assert isinstance(data, dict) and data.get("choices"), "Missing choices from Ollama"


def test_litellm() -> None:
    base = DEFAULTS["litellm"]
    chat_url = f"{base}/v1/chat/completions"
    payload = {
        "model": LITELLM_MODEL,
        "messages": [{"role": "user", "content": "hello from integration check"}],
    }
    status, data = request_json(chat_url, method="POST", payload=payload)
    expect(status, url=chat_url, payload=data)
    assert isinstance(data, dict) and data.get("choices"), "LiteLLM response missing choices"


def test_agent() -> None:
    base = DEFAULTS["agent"]
    agent_url = f"{base}/v1/agent"
    payload = {"prompt": "integration test: confirm plan logging"}
    status, data = request_json(agent_url, method="POST", payload=payload)
    expect(status, url=agent_url, payload=data)
    assert isinstance(data, dict), "Agent response must be a dict"
    plan = data.get("metadata", {}).get("plan", {})
    steps = plan.get("steps") if isinstance(plan, dict) else None
    assert steps and any(
        step.get("action") == "completion" for step in steps
    ), "Agent plan missing completion step"


def test_qdrant_direct() -> None:
    base = DEFAULTS["qdrant"]
    url = f"{base}/collections"
    status, data = request_json(url)
    expect(status, url=url, payload=data)
    assert isinstance(data, dict), "Qdrant collections endpoint returned non-dict payload"


def test_qdrant_via_agent() -> None:
    base = DEFAULTS["agent"]
    agent_url = f"{base}/v1/agent"
    payload = {"prompt": "integration test: memory check"}
    status, data = request_json(agent_url, method="POST", payload=payload)
    expect(status, url=agent_url, payload=data)
    steps = data.get("metadata", {}).get("plan", {}).get("steps") or []
    assert any(
        step.get("action") == "memory" for step in steps
    ), "Agent plan never attempted memory lookup"


def _run_with_retries(name: str, check: Callable[[], None], attempts: int) -> None:
    """Run a check multiple times to let the Heavy LLM warm up."""

    for attempt in range(1, attempts + 1):
        try:
            check()
            if attempt > 1:
                print(f"[info] {name} succeeded on attempt {attempt}")
            return
        except Exception as exc:  # pragma: no cover - retry helper
            if attempt == attempts:
                raise
            delay = RETRY_DELAY_SEC * attempt
            print(
                f"[retry] {name} attempt {attempt} failed ({exc}); "
                f"sleeping for {delay:.0f}s before retrying."
            )
            time.sleep(delay)


def main() -> None:
    """Run all smoke checks and exit non-zero when any fail."""

    checks: list[tuple[str, Callable[[], None], int]] = [
        ("Ollama", test_ollama, 1),
        ("LiteLLM", test_litellm, 3),
        ("Agent", test_agent, 3),
        ("Qdrant direct", test_qdrant_direct, 1),
        ("Qdrant via agent", test_qdrant_via_agent, 3),
    ]

    failures: list[tuple[str, Exception]] = []
    for label, check, attempts in checks:
        try:
            _run_with_retries(label, check, attempts)
            print(f"[ok] {label}")
        except Exception as exc:  # pragma: no cover - integration helper
            failures.append((label, exc))
            print(f"[fail] {label}: {exc}", file=sys.stderr)

    if failures:
        raise SystemExit(1)


if __name__ == "__main__":  # pragma: no cover
    main()
