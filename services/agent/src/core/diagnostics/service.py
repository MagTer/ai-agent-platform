from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter, deque
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.observability.error_codes import ErrorCode

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

    def get_system_health_metrics(self, window: int = 60) -> dict[str, Any]:
        """
        Analyze recent traces to determine system health.

        Args:
            window: Number of most recent traces to analyze.

        Returns:
            Dictionary containing health status, metrics, and hotspots.
        """
        metrics = {
            "total_requests": 0,
            "error_rate": 0.0,
            "hotspots": {},
        }

        if not self._trace_log_path.exists():
            return {
                "status": "UNKNOWN",
                "metrics": metrics,
                "reason": "No trace log found",
            }

        # Read last N lines efficiently
        # We read more lines than window size to ensure we capture full traces
        # Assuming average 10 spans per trace, 3000 lines covers ~300 traces
        try:
            with self._trace_log_path.open("r", encoding="utf-8") as f:
                lines = deque(f, maxlen=3000)
        except Exception as e:
            LOGGER.error(f"Failed to read trace log: {e}")
            return {
                "status": "UNKNOWN",
                "metrics": metrics,
                "reason": f"Read error: {e}",
            }

        # Process spans
        recent_traces: dict[str, list[dict]] = {}
        hotspot_counts: Counter[str] = Counter()
        # Track reasons per hotspot: hotspot_name -> list of error descriptions
        hotspot_reasons: dict[str, list[str]] = {}
        error_traces = set()

        # Reverse iterate to get newest first
        for line in reversed(lines):
            try:
                span = json.loads(line)
                trace_id = span.get("context", {}).get("trace_id")
                if not trace_id:
                    continue

                if trace_id not in recent_traces:
                    if len(recent_traces) >= window:
                        continue
                    recent_traces[trace_id] = []

                recent_traces[trace_id].append(span)
            except json.JSONDecodeError:
                continue

        # Analyze traces
        total_requests = len(recent_traces)

        for trace_id, spans in recent_traces.items():
            has_error = False
            for span in spans:
                status = span.get("status", "UNSET")
                name = span.get("name", "unknown")

                # Check for error status
                if status in ("ERROR", "fail"):
                    has_error = True
                    # Identify hotspot using span name (e.g. tool name)
                    hotspot_counts[name] += 1

                    # Collect error reason
                    attributes = span.get("attributes", {})
                    # Prefer status_description, fall back to "status_description", then "error"
                    reason = (
                        attributes.get("status_description")
                        or attributes.get("error")
                        or "Unknown Error"
                    )

                    if name not in hotspot_reasons:
                        hotspot_reasons[name] = []
                    hotspot_reasons[name].append(str(reason))

            if has_error:
                error_traces.add(trace_id)

        error_count = len(error_traces)
        error_rate = (error_count / total_requests) if total_requests > 0 else 0.0

        status = "HEALTHY"
        if error_rate > 0.1:
            status = "DEGRADED"
        if error_rate > 0.5:
            status = "UNHEALTHY"

        # Build Insights
        insights_hotspots = []
        for name, count in hotspot_counts.most_common(5):
            reasons = hotspot_reasons.get(name, [])
            # Get top 3 reasons
            top_reasons = [f"{reason} ({cnt})" for reason, cnt in Counter(reasons).most_common(3)]
            insights_hotspots.append({"name": name, "count": count, "top_reasons": top_reasons})

        return {
            "status": status,
            "metrics": {
                "total_requests": total_requests,
                "error_rate": round(error_rate, 2),
                "error_count": error_count,
            },
            "hotspots": dict(hotspot_counts.most_common(5)),
            "insights": {"hotspots": insights_hotspots},
        }

    async def run_diagnostics(self) -> list[TestResult]:
        """Run functional health checks on system components."""
        results: list[TestResult | BaseException] = []
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # Use return_exceptions=True so one crash doesn't kill the batch
                # Note: Ollama check removed - no longer part of the architecture
                results = await asyncio.gather(
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

    async def get_diagnostics_summary(self) -> dict[str, Any]:
        """Get a machine-readable diagnostics summary for AI agent consumption.

        Returns a structured report with:
        - overall_status: HEALTHY | DEGRADED | CRITICAL
        - failed_components: List of components with failures
        - recommended_actions: Prioritized list of fixes
        - component_details: Status of each component

        This format is optimized for AI agent self-diagnosis.
        """
        from core.observability.error_codes import ErrorSeverity, get_error_info

        results = await self.run_diagnostics()
        health_metrics = self.get_system_health_metrics()

        # Classify results
        failed: list[dict[str, Any]] = []
        healthy: list[str] = []
        warnings: list[dict[str, Any]] = []

        for result in results:
            if result.status == "ok":
                healthy.append(result.component)
            else:
                # Map component to error code
                error_code = self._map_component_to_error_code(result.component)
                error_info = get_error_info(error_code)

                failure_info = {
                    "component": result.component,
                    "error_code": error_info.code,
                    "severity": error_info.severity.value,
                    "message": result.message or error_info.description,
                    "recovery_hint": error_info.recovery_hint,
                    "latency_ms": result.latency_ms,
                }

                if error_info.severity == ErrorSeverity.CRITICAL:
                    failed.append(failure_info)
                else:
                    warnings.append(failure_info)

        # Determine overall status
        if any(f["severity"] == "critical" for f in failed):
            overall_status = "CRITICAL"
        elif failed or health_metrics.get("status") == "UNHEALTHY":
            overall_status = "DEGRADED"
        elif warnings or health_metrics.get("status") == "DEGRADED":
            overall_status = "DEGRADED"
        else:
            overall_status = "HEALTHY"

        # Build recommended actions (prioritized)
        recommended_actions: list[dict[str, Any]] = []

        # Add critical failures first
        for failure in sorted(failed, key=lambda x: x["latency_ms"]):
            recommended_actions.append(
                {
                    "priority": 1,
                    "action": failure["recovery_hint"],
                    "component": failure["component"],
                    "error_code": failure["error_code"],
                }
            )

        # Add warnings
        for warning in warnings:
            recommended_actions.append(
                {
                    "priority": 2,
                    "action": warning["recovery_hint"],
                    "component": warning["component"],
                    "error_code": warning["error_code"],
                }
            )

        # Add hotspot-based recommendations
        for hotspot in health_metrics.get("insights", {}).get("hotspots", []):
            if hotspot.get("count", 0) > 2:
                recommended_actions.append(
                    {
                        "priority": 3,
                        "action": f"Investigate frequent errors in '{hotspot['name']}': "
                        f"{', '.join(hotspot.get('top_reasons', [])[:2])}",
                        "component": hotspot["name"],
                        "error_code": "HOTSPOT_DETECTED",
                    }
                )

        return {
            "overall_status": overall_status,
            "timestamp": datetime.now().isoformat(),
            "healthy_components": healthy,
            "failed_components": failed,
            "warnings": warnings,
            "recommended_actions": recommended_actions[:10],  # Limit to top 10
            "metrics": health_metrics.get("metrics", {}),
            "component_count": {
                "total": len(results),
                "healthy": len(healthy),
                "failed": len(failed),
                "warnings": len(warnings),
            },
        }

    def _map_component_to_error_code(self, component: str) -> ErrorCode:
        """Map a component name to an appropriate error code."""
        from core.observability.error_codes import ErrorCode

        component_lower = component.lower()

        mapping = {
            "ollama": ErrorCode.LLM_CONNECTION_FAILED,
            "litellm": ErrorCode.LLM_CONNECTION_FAILED,
            "qdrant": ErrorCode.RAG_QDRANT_UNAVAILABLE,
            "postgres": ErrorCode.DB_CONNECTION_FAILED,
            "embedder": ErrorCode.RAG_EMBEDDING_FAILED,
            "searxng": ErrorCode.NET_CONNECTION_REFUSED,
            "internet": ErrorCode.NET_CONNECTION_REFUSED,
            "workspace": ErrorCode.CONFIG_PERMISSION,
            "openwebui": ErrorCode.NET_CONNECTION_REFUSED,
        }

        for key, code in mapping.items():
            if key in component_lower:
                return code

        return ErrorCode.UNKNOWN

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

    # Patterns for diagnostic/health-check traces to hide by default
    _HIDE_PATTERNS = [
        "GET /diagnostics",
        "POST /diagnostics",
        "GET /v1/models",
        "/health",
        "/readiness",
        "/liveness",
    ]

    def _should_hide_trace(self, trace_group: TraceGroup) -> bool:
        """Check if a trace should be hidden from default view."""
        root_name = trace_group.root.name
        root_attrs = trace_group.root.attributes
        # Check name and http.target attribute
        target = root_attrs.get("http.target", "") or root_attrs.get("http.route", "")
        combined = f"{root_name} {target}"
        return any(pattern.lower() in combined.lower() for pattern in self._HIDE_PATTERNS)

    def get_recent_traces(self, limit: int = 1000, show_all: bool = False) -> list[TraceGroup]:
        """
        Read log, group by trace_id, and return valid trace groups.

        Args:
            limit: Maximum number of trace lines to read.
            show_all: If True, include diagnostic/health-check traces. Default False.
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

        # 5. Filter out diagnostic/health-check traces unless show_all is True
        if not show_all:
            trace_groups = [g for g in trace_groups if not self._should_hide_trace(g)]

        return trace_groups
