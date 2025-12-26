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
from sqlalchemy import text

from core.core.config import Settings
from core.core.embedder import EmbedderClient
from core.db.engine import engine

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


class TraceGroup(BaseModel):
    trace_id: str
    root: TraceSpan
    spans: list[TraceSpan]
    total_duration_ms: float
    start_time: datetime
    snippet: str
    status: str  # "OK" | "ERR"


class DiagnosticsService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._trace_log_path = Path(str(settings.trace_span_log_path or "data/spans.jsonl"))

    async def run_diagnostics(self) -> list[TestResult]:
        """Run functional health checks on system components."""
        results: list[TestResult | BaseException] = []
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # Use return_exceptions=True so one crash doesn't kill the batch
                results = await asyncio.gather(
                    self._check_ollama(client),
                    self._check_qdrant(client),
                    self._check_litellm(client),
                    self._check_embedder(client),
                    self._check_openwebui_interface(client),
                    self._check_postgres(),
                    self._check_searxng(client),
                    self._check_internet(client),
                    self._check_workspace(),
                    return_exceptions=True,
                )
        except Exception as e:
            LOGGER.error(f"Diagnostics runner crashed: {e}")
            return [
                TestResult(
                    component="Diagnostics Runner",
                    status="fail",
                    latency_ms=0,
                    message=str(e),
                )
            ]

        # Filter out exceptions if any slipped through
        # (though helpers catch Exception)
        final_results: list[TestResult] = []
        for r in results:
            if isinstance(r, TestResult):
                final_results.append(r)
            elif isinstance(r, Exception):
                final_results.append(
                    TestResult(
                        component="Unknown",
                        status="fail",
                        latency_ms=0,
                        message=f"Unhandled Error: {r}",
                    )
                )
        return final_results

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
        url = f"{str(self._settings.litellm_api_base).rstrip('/')}/health/liveness"
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
        # Use EmbedderClient to handle internal vs external logic transparently
        embedder = EmbedderClient(str(self._settings.embedder_url))
        start = time.perf_counter()
        try:
            # We don't use 'client' passed in because EmbedderClient
            # manages its own HTTP/Local switching
            await embedder.embed_one("ping")
            latency = (time.perf_counter() - start) * 1000
            return TestResult(component="Embedder", status="ok", latency_ms=latency)
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return TestResult(
                component="Embedder",
                status="fail",
                latency_ms=latency,
                message=str(e),
            )

    async def _check_openwebui_interface(self, client: httpx.AsyncClient) -> TestResult:
        # Probe ourself to see if we look like an OpenAI API
        # By default we listen on settings.port (8000).
        # We need to hit localhost:{port}/v1/models
        # This assumes the diagnostics service runs inside the same process/container as the API.

        # NOTE: self._settings.host might be 0.0.0.0, we should target localhost or 127.0.0.1
        port = self._settings.port
        url = f"http://127.0.0.1:{port}/v1/models"

        start = time.perf_counter()
        try:
            resp = await client.get(url)
            latency = (time.perf_counter() - start) * 1000

            if resp.status_code == 200:
                try:
                    data = resp.json()
                    # Validate OpenAI format: {"object": "list", "data": [...]}
                    if data.get("object") != "list":
                        return TestResult(
                            component="OpenWebUI API",
                            status="fail",
                            latency_ms=latency,
                            message="Invalid JSON: Missing object='list'",
                        )

                    if not isinstance(data.get("data"), list):
                        return TestResult(
                            component="OpenWebUI API",
                            status="fail",
                            latency_ms=latency,
                            message="Invalid JSON: 'data' is not a list",
                        )

                    if not data["data"]:
                        # It's technically valid to have empty models, but suspicious for our agent.
                        # Let's warn or pass? Pass is fine, but let's note it.
                        pass

                    return TestResult(component="OpenWebUI API", status="ok", latency_ms=latency)
                except json.JSONDecodeError:
                    return TestResult(
                        component="OpenWebUI API",
                        status="fail",
                        latency_ms=latency,
                        message="Invalid JSON response",
                    )

            return TestResult(
                component="OpenWebUI API",
                status="fail",
                latency_ms=latency,
                message=f"Status {resp.status_code}",
            )
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return TestResult(
                component="OpenWebUI API",
                status="fail",
                latency_ms=latency,
                message=str(e),
            )

    async def _check_postgres(self) -> TestResult:
        start = time.perf_counter()
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            latency = (time.perf_counter() - start) * 1000
            return TestResult(component="PostgreSQL", status="ok", latency_ms=latency)
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return TestResult(
                component="PostgreSQL",
                status="fail",
                latency_ms=latency,
                message=str(e),
            )

    async def _check_searxng(self, client: httpx.AsyncClient) -> TestResult:
        url = str(self._settings.searxng_url).rstrip("/")
        start = time.perf_counter()
        try:
            resp = await client.get(url)
            latency = (time.perf_counter() - start) * 1000
            if resp.status_code == 200:
                return TestResult(component="SearXNG", status="ok", latency_ms=latency)
            return TestResult(
                component="SearXNG",
                status="fail",
                latency_ms=latency,
                message=f"Status {resp.status_code}",
            )
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return TestResult(
                component="SearXNG",
                status="fail",
                latency_ms=latency,
                message=str(e),
            )

    async def _check_internet(self, client: httpx.AsyncClient) -> TestResult:
        # Use a reliable public site. google.com is standard.
        url = "http://www.google.com"
        start = time.perf_counter()
        try:
            # Follow redirects is important for google.com
            resp = await client.get(url, follow_redirects=True)
            latency = (time.perf_counter() - start) * 1000
            if resp.status_code == 200:
                return TestResult(component="Internet", status="ok", latency_ms=latency)
            return TestResult(
                component="Internet",
                status="fail",
                latency_ms=latency,
                message=f"Status {resp.status_code}",
            )
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return TestResult(
                component="Internet",
                status="fail",
                latency_ms=latency,
                message=str(e),
            )

    async def _check_workspace(self) -> TestResult:
        path = self._settings.contexts_dir
        start = time.perf_counter()
        try:
            if not path.exists():
                return TestResult(
                    component="Workspace",
                    status="fail",
                    latency_ms=0.0,
                    message=f"Contexts dir missing: {path}",
                )

            # Try writing a temp file
            test_file = path / ".health_check"
            try:
                # Blocking IO is acceptable for small test in diagnostics
                test_file.write_text("ok", encoding="utf-8")
                test_file.unlink()
            except Exception as e:
                return TestResult(
                    component="Workspace",
                    status="fail",
                    latency_ms=(time.perf_counter() - start) * 1000,
                    message=f"Write permission failed: {e}",
                )

            latency = (time.perf_counter() - start) * 1000
            return TestResult(component="Workspace", status="ok", latency_ms=latency)

        except Exception as e:
            return TestResult(
                component="Workspace",
                status="fail",
                latency_ms=(time.perf_counter() - start) * 1000,
                message=str(e),
            )

    def get_recent_traces(self, limit: int = 1000) -> list[TraceGroup]:
        """
        Read log, group by trace_id, and return valid trace groups.
        """
        if not self._trace_log_path.exists():
            LOGGER.warning(f"Trace log not found at {self._trace_log_path}")
            return []

        # 1. Read Raw Spans
        raw_spans: list[TraceSpan] = []
        try:
            with self._trace_log_path.open("r", encoding="utf-8") as f:
                last_lines = deque(f, maxlen=limit)

            for line in last_lines:
                try:
                    data = json.loads(line)
                    context = data.get("context", {})
                    start_str = data.get("start_time")
                    start_dt = datetime.fromisoformat(start_str) if start_str else datetime.utcnow()

                    span = TraceSpan(
                        trace_id=context.get("trace_id", "unknown"),
                        span_id=context.get("span_id", "unknown"),
                        parent_id=context.get("parent_id"),
                        name=data.get("name", "unknown"),
                        start_time=start_dt,
                        duration_ms=data.get("duration_ms", 0.0),
                        status=data.get("status", "UNSET"),
                        attributes=data.get("attributes", {}),
                    )
                    raw_spans.append(span)
                except (json.JSONDecodeError, ValueError):
                    continue
        except Exception as e:
            LOGGER.error(f"Error reading trace log: {e}")
            return []

        # 2. Group by Trace ID
        groups: dict[str, list[TraceSpan]] = {}
        for span in raw_spans:
            if span.trace_id not in groups:
                groups[span.trace_id] = []
            groups[span.trace_id].append(span)

        # 3. Construct TraceGroups
        trace_groups: list[TraceGroup] = []
        for trace_id, spans in groups.items():
            # Sort chronologically
            spans.sort(key=lambda x: x.start_time or datetime.min)

            # Find Root (no parent or first in time)
            root = next((s for s in spans if not s.parent_id), spans[0])

            # Filter Logic: discard orphans if strictly required,
            # but for now we accept best-effort roots

            # Calc Stats
            start_time = root.start_time or datetime.utcnow()
            # approximate total duration
            end_times = [
                (s.start_time or start_time).timestamp() * 1000 + s.duration_ms for s in spans
            ]
            trace_end = max(end_times) if end_times else start_time.timestamp() * 1000
            total_duration = trace_end - (start_time.timestamp() * 1000)

            # Status
            status = "OK"
            if any(s.status in ("ERROR", "fail") for s in spans):
                status = "ERR"

            # Snippet
            snippet = root.name
            attrs = root.attributes
            if "prompt" in attrs:
                snippet = str(attrs["prompt"])[:60]
            elif "http.request.body" in attrs:
                snippet = str(attrs["http.request.body"])[:60]
            elif "message" in attrs:
                snippet = str(attrs["message"])[:60]

            trace_groups.append(
                TraceGroup(
                    trace_id=trace_id,
                    root=root,
                    spans=spans,
                    total_duration_ms=max(0.0, total_duration),
                    start_time=start_time,
                    snippet=snippet,
                    status=status,
                )
            )

        # 4. Sort by Newest First
        trace_groups.sort(key=lambda g: g.start_time, reverse=True)
        return trace_groups
