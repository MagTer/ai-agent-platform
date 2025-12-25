import asyncio
import json
import logging
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

from core.core.config import Settings

LOGGER = logging.getLogger(__name__)


class TraceSpan(BaseModel):
    trace_id: str
    span_id: str
    parent_id: str | None = None
    name: str
    start_time: datetime | None = None
    duration_ms: float
    status: str
    attributes: dict[str, Any]


class TestResult(BaseModel):
    component: str
    status: str  # "ok" | "fail"
    latency_ms: float
    message: str | None = None


class DiagnosticsService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._trace_log_path = Path(str(settings.trace_span_log_path or "data/spans.jsonl"))

    async def run_diagnostics(self) -> list[TestResult]:
        """Run functional health checks on system components."""
        async with httpx.AsyncClient(timeout=2.0) as client:
            results = await asyncio.gather(
                self._check_ollama(client),
                self._check_qdrant(client),
                self._check_litellm(client),
                self._check_embedder(client),
            )
        return list(results)

    async def _check_ollama(self, client: httpx.AsyncClient) -> TestResult:
        # Default Ollama is host:11434 usually.
        # We can infer from settings, but config splits litellm from ollama.
        # Docker usually uses 'ollama:11434'.
        url = "http://ollama:11434/api/tags"
        start = time.perf_counter()
        try:
            resp = await client.get(url)
            latency = (time.perf_counter() - start) * 1000
            if resp.status_code == 200:
                return TestResult(component="Ollama", status="ok", latency_ms=latency)
            return TestResult(
                component="Ollama",
                status="fail",
                latency_ms=latency,
                message=f"Status {resp.status_code}",
            )
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return TestResult(
                component="Ollama",
                status="fail",
                latency_ms=latency,
                message=str(e),
            )

    async def _check_qdrant(self, client: httpx.AsyncClient) -> TestResult:
        url = f"{self._settings.qdrant_url}/collections"
        start = time.perf_counter()
        try:
            resp = await client.get(url)
            latency = (time.perf_counter() - start) * 1000
            if resp.status_code == 200:
                return TestResult(component="Qdrant", status="ok", latency_ms=latency)
            return TestResult(
                component="Qdrant",
                status="fail",
                latency_ms=latency,
                message=f"Status {resp.status_code}",
            )
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return TestResult(
                component="Qdrant",
                status="fail",
                latency_ms=latency,
                message=str(e),
            )

    async def _check_litellm(self, client: httpx.AsyncClient) -> TestResult:
        url = f"{self._settings.litellm_api_base}/health/liveness"
        start = time.perf_counter()
        try:
            resp = await client.get(url)
            latency = (time.perf_counter() - start) * 1000
            if resp.status_code == 200:
                return TestResult(component="LiteLLM", status="ok", latency_ms=latency)
            return TestResult(
                component="LiteLLM",
                status="fail",
                latency_ms=latency,
                message=f"Status {resp.status_code}",
            )
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return TestResult(
                component="LiteLLM",
                status="fail",
                latency_ms=latency,
                message=str(e),
            )

    async def _check_embedder(self, client: httpx.AsyncClient) -> TestResult:
        url = f"{self._settings.embedder_url}/health"
        start = time.perf_counter()
        try:
            resp = await client.get(url)
            latency = (time.perf_counter() - start) * 1000
            if resp.status_code == 200:
                return TestResult(component="Embedder", status="ok", latency_ms=latency)
            return TestResult(
                component="Embedder",
                status="fail",
                latency_ms=latency,
                message=f"Status {resp.status_code}",
            )
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return TestResult(
                component="Embedder",
                status="fail",
                latency_ms=latency,
                message=str(e),
            )

    def get_recent_traces(self, limit: int = 50) -> list[TraceSpan]:
        """
        Efficiently read the last N lines from the trace log and return parsed spans.
        """
        if not self._trace_log_path.exists():
            LOGGER.warning(f"Trace log not found at {self._trace_log_path}")
            return []

        spans: list[TraceSpan] = []

        try:
            # Efficiently read last N lines using deque
            with self._trace_log_path.open("r", encoding="utf-8") as f:
                # deque(f, limit) keeps only the last 'limit' lines in memory
                last_lines = deque(f, maxlen=limit)

            for line in last_lines:
                try:
                    data = json.loads(line)
                    context = data.get("context", {})

                    # Parse timestamp (already ISO or raw?)
                    # Tracing export now writes ISO string for start_time
                    start_str = data.get("start_time")
                    start_dt = datetime.fromisoformat(start_str) if start_str else None

                    span = TraceSpan(
                        trace_id=context.get("trace_id", ""),
                        span_id=context.get("span_id", ""),
                        parent_id=context.get("parent_id"),
                        name=data.get("name", "unknown"),
                        start_time=start_dt,
                        duration_ms=data.get("duration_ms", 0.0),
                        status=data.get("status", "UNSET"),
                        attributes=data.get("attributes", {}),
                    )
                    spans.append(span)
                except (json.JSONDecodeError, ValueError) as e:
                    LOGGER.warning(f"Failed to parse trace log line: {e}")
                    continue

        except Exception as e:
            LOGGER.error(f"Error reading trace log: {e}")

        # Return reversed so newest keys are top (if read order is oldest-first)
        # deque(f) reads whole file. Order is oldest -> newest.
        # We usually want dashboard to show Newest First.
        return list(reversed(spans))
