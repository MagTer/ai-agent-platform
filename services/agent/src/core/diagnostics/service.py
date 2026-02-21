from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter, deque
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.observability.error_codes import ErrorCode

import httpx
from cryptography.fernet import Fernet
from pydantic import BaseModel
from sqlalchemy import text

from core.db.engine import engine
from core.providers import get_embedder
from core.runtime.config import Settings

LOGGER = logging.getLogger(__name__)

# Test data for encryption round-trip
_ENCRYPTION_TEST_VALUE = "diagnostic_test_value_12345"


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
    # Components whose failures cap at WARNING (never CRITICAL).
    # These are third-party services or user-configured integrations:
    # platform degraded but still functional without them.
    EXTERNAL_COMPONENTS: frozenset[str] = frozenset({
        "SearXNG Search",  # actual web queries via SearXNG (internet-dependent)
        "Internet",        # raw internet connectivity
        "OAuth Tokens",    # third-party OAuth (Homey, etc.)
        "MCP",             # user-configured MCP servers
        "Azure DevOps",    # Microsoft ADO (user integration)
        "OpenRouter",      # upstream LLM API provider
    })

    def __init__(self, settings: Settings):
        self._settings = settings
        self._trace_log_path = Path(str(settings.trace_span_log_path or "data/spans.jsonl"))

    def _read_trace_lines(self) -> deque[str]:
        """Read trace log lines synchronously (called via asyncio.to_thread)."""
        with self._trace_log_path.open("r", encoding="utf-8") as f:
            return deque(f, maxlen=3000)

    def _calculate_percentiles(self, durations: list[float]) -> dict[str, float]:
        """Calculate p50, p95, p99 latency percentiles from duration list.

        Args:
            durations: List of duration values in milliseconds.

        Returns:
            Dictionary with p50, p95, p99 keys (rounded to 1 decimal).
        """
        if not durations:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0}

        sorted_durations = sorted(durations)
        count = len(sorted_durations)

        def percentile(p: float) -> float:
            """Calculate percentile value."""
            k = (count - 1) * p
            f = int(k)
            c = k - f
            if f + 1 < count:
                return sorted_durations[f] * (1 - c) + sorted_durations[f + 1] * c
            return sorted_durations[f]

        return {
            "p50": round(percentile(0.50), 1),
            "p95": round(percentile(0.95), 1),
            "p99": round(percentile(0.99), 1),
        }

    async def get_system_health_metrics(self, window: int = 60) -> dict[str, Any]:
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
            "latency_percentiles": {"p50": 0.0, "p95": 0.0, "p99": 0.0},
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
            lines = await asyncio.to_thread(self._read_trace_lines)
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
        trace_durations: list[float] = []

        for trace_id, spans in recent_traces.items():
            has_error = False
            trace_duration_ms = 0.0

            for span in spans:
                status = span.get("status", "UNSET")
                name = span.get("name", "unknown")
                duration_ms = span.get("duration_ms", 0.0)

                # Track total trace duration (sum of all spans)
                trace_duration_ms += duration_ms

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

            if trace_duration_ms > 0:
                trace_durations.append(trace_duration_ms)

        error_count = len(error_traces)
        error_rate = (error_count / total_requests) if total_requests > 0 else 0.0

        # Calculate latency percentiles
        latency_percentiles = self._calculate_percentiles(trace_durations)

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
                "latency_percentiles": latency_percentiles,
            },
            "hotspots": dict(hotspot_counts.most_common(5)),
            "insights": {"hotspots": insights_hotspots},
        }

    async def run_diagnostics(self) -> list[TestResult]:
        """Run functional health checks on system components.

        Tests both basic connectivity (health checks) and functional tests
        that verify components work correctly end-to-end.
        """
        results: list[TestResult | BaseException] = []
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Use return_exceptions=True so one crash doesn't kill the batch
                # Core infrastructure tests
                results = await asyncio.gather(
                    self._check_qdrant(client),
                    self._check_litellm(client),
                    self._check_openrouter(client),
                    self._check_embedder(client),
                    self._check_openwebui_interface(client),
                    self._check_postgres(),
                    self._check_searxng(client),
                    self._check_internet(client),
                    self._check_workspace(),
                    # New functional/integration tests
                    self._check_mcp_connections(),
                    self._check_oauth_tokens(),
                    self._check_azure_devops(client),
                    self._check_searxng_functional(client),
                    self._check_credential_encryption(),
                    self._check_price_tracker(),
                    self._check_user_context_integrity(),
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
        health_metrics = await self.get_system_health_metrics()

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

                # External components are capped at WARNING regardless of
                # their generic error code severity -- a third-party service
                # being down degrades optional features, not the core platform.
                is_external = any(
                    ext in result.component for ext in self.EXTERNAL_COMPONENTS
                )
                effective_severity = (
                    ErrorSeverity.WARNING
                    if is_external and error_info.severity == ErrorSeverity.CRITICAL
                    else error_info.severity
                )

                failure_info = {
                    "component": result.component,
                    "error_code": error_info.code,
                    "severity": effective_severity.value,
                    "message": result.message or error_info.description,
                    "recovery_hint": error_info.recovery_hint,
                    "latency_ms": result.latency_ms,
                    "is_external": is_external,
                }

                if effective_severity == ErrorSeverity.CRITICAL:
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
            "litellm": ErrorCode.LLM_CONNECTION_FAILED,
            "openrouter": ErrorCode.NET_CONNECTION_REFUSED,
            "qdrant": ErrorCode.RAG_QDRANT_UNAVAILABLE,
            "postgres": ErrorCode.DB_CONNECTION_FAILED,
            "embedder": ErrorCode.RAG_EMBEDDING_FAILED,
            "searxng": ErrorCode.NET_CONNECTION_REFUSED,
            "internet": ErrorCode.NET_CONNECTION_REFUSED,
            "workspace": ErrorCode.CONFIG_PERMISSION,
            "openwebui": ErrorCode.NET_CONNECTION_REFUSED,
            # New integration test components
            "mcp": ErrorCode.NET_CONNECTION_REFUSED,
            "oauth": ErrorCode.AUTH_TOKEN_EXPIRED,
            "azure": ErrorCode.NET_CONNECTION_REFUSED,
            "credential": ErrorCode.CONFIG_INVALID,
            "price": ErrorCode.DB_CONNECTION_FAILED,
        }

        for key, code in mapping.items():
            if key in component_lower:
                return code

        return ErrorCode.UNKNOWN

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

    async def _check_openrouter(self, client: httpx.AsyncClient) -> TestResult:
        """Check connectivity to the OpenRouter upstream LLM API.

        A 401 response still proves the endpoint is reachable -- we just
        haven't authenticated, which is expected without a key here.
        """
        url = "https://openrouter.ai/api/v1/models"
        start = time.perf_counter()
        try:
            resp = await client.get(url, timeout=10.0)
            latency = (time.perf_counter() - start) * 1000
            if resp.status_code in (200, 401, 403):
                return TestResult(
                    component="OpenRouter",
                    status="ok",
                    latency_ms=latency,
                    message=f"Reachable (HTTP {resp.status_code})",
                )
            return TestResult(
                component="OpenRouter",
                status="fail",
                latency_ms=latency,
                message=f"HTTP {resp.status_code}",
            )
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return TestResult(
                component="OpenRouter",
                status="fail",
                latency_ms=latency,
                message=str(e),
            )

    async def _check_embedder(self, client: httpx.AsyncClient) -> TestResult:
        start = time.perf_counter()
        try:
            await get_embedder().embed(["ping"])
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

        headers: dict[str, str] = {}
        if self._settings.internal_api_key:
            headers["Authorization"] = f"Bearer {self._settings.internal_api_key}"

        start = time.perf_counter()
        try:
            resp = await client.get(url, headers=headers)
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

    def _workspace_write_test(self, test_file: Path) -> None:
        """Synchronous workspace write test (called via asyncio.to_thread)."""
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()

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
                await asyncio.to_thread(self._workspace_write_test, test_file)
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

    # -------------------------------------------------------------------------
    # New Integration Tests (Functional Validation)
    # -------------------------------------------------------------------------

    async def _check_mcp_connections(self) -> TestResult:
        """Check MCP server connections and tool availability.

        Tests:
        - MCP client pool is initialized
        - Configured servers are reachable
        - Tools are discoverable
        """
        start = time.perf_counter()
        try:
            from core.tools.mcp_loader import get_mcp_health, get_mcp_stats

            stats = get_mcp_stats()
            health = await get_mcp_health()
            latency = (time.perf_counter() - start) * 1000

            total_servers = stats.get("total_clients", 0)
            connected = stats.get("connected_clients", 0)

            if total_servers == 0:
                return TestResult(
                    component="MCP Servers",
                    status="ok",
                    latency_ms=latency,
                    message="No MCP servers configured",
                )

            if connected == total_servers:
                total_tools = sum(s.get("tools_count", 0) for s in health.values())
                return TestResult(
                    component="MCP Servers",
                    status="ok",
                    latency_ms=latency,
                    message=f"{connected}/{total_servers} connected, {total_tools} tools",
                )

            # Some servers disconnected
            disconnected_names = [
                name for name, info in health.items() if not info.get("connected")
            ]
            disconnected_str = ", ".join(disconnected_names)
            return TestResult(
                component="MCP Servers",
                status="fail",
                latency_ms=latency,
                message=f"{connected}/{total_servers} connected. Down: {disconnected_str}",
            )
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return TestResult(
                component="MCP Servers",
                status="fail",
                latency_ms=latency,
                message=str(e),
            )

    async def _check_oauth_tokens(self) -> TestResult:
        """Check OAuth token validity and expiration status.

        Tests:
        - Tokens exist in database
        - Tokens are not expired
        - Warns about tokens expiring soon (within 1 hour)
        """
        from datetime import timedelta

        from sqlalchemy import func, select

        from core.db.oauth_models import OAuthToken

        start = time.perf_counter()
        try:
            async with engine.connect() as conn:
                # Count total tokens
                total_result = await conn.execute(select(func.count()).select_from(OAuthToken))
                total_tokens = total_result.scalar() or 0

                if total_tokens == 0:
                    latency = (time.perf_counter() - start) * 1000
                    return TestResult(
                        component="OAuth Tokens",
                        status="ok",
                        latency_ms=latency,
                        message="No OAuth tokens configured",
                    )

                # Count expired tokens
                now = datetime.now(UTC).replace(tzinfo=None)
                expired_result = await conn.execute(
                    select(func.count()).select_from(OAuthToken).where(OAuthToken.expires_at < now)
                )
                expired_count = expired_result.scalar() or 0

                # Count tokens expiring within 1 hour
                soon = now + timedelta(hours=1)
                expiring_result = await conn.execute(
                    select(func.count())
                    .select_from(OAuthToken)
                    .where(OAuthToken.expires_at >= now)
                    .where(OAuthToken.expires_at < soon)
                )
                expiring_count = expiring_result.scalar() or 0

                latency = (time.perf_counter() - start) * 1000

                if expired_count > 0:
                    return TestResult(
                        component="OAuth Tokens",
                        status="fail",
                        latency_ms=latency,
                        message=f"{expired_count}/{total_tokens} tokens expired",
                    )

                if expiring_count > 0:
                    return TestResult(
                        component="OAuth Tokens",
                        status="ok",
                        latency_ms=latency,
                        message=f"{expiring_count}/{total_tokens} tokens expiring soon",
                    )

                return TestResult(
                    component="OAuth Tokens",
                    status="ok",
                    latency_ms=latency,
                    message=f"{total_tokens} tokens valid",
                )
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return TestResult(
                component="OAuth Tokens",
                status="fail",
                latency_ms=latency,
                message=str(e),
            )

    async def _check_azure_devops(self, client: httpx.AsyncClient) -> TestResult:
        """Check Azure DevOps connectivity using configured PAT.

        Tests:
        - PAT is configured
        - PAT is valid (can authenticate)
        - API is reachable
        """
        import os

        start = time.perf_counter()
        try:
            # Get config from environment (same as AzureDevOpsTool)
            org_url = os.environ.get("AZURE_DEVOPS_ORG_URL")
            if not org_url:
                org = os.environ.get("AZURE_DEVOPS_ORG")
                if org:
                    org_url = f"https://dev.azure.com/{org}"
            pat = os.environ.get("AZURE_DEVOPS_PAT")

            if not org_url or not pat:
                latency = (time.perf_counter() - start) * 1000
                return TestResult(
                    component="Azure DevOps",
                    status="ok",
                    latency_ms=latency,
                    message="Not configured (no org URL or PAT)",
                )

            # Test API connectivity with projects endpoint
            import base64

            auth_str = base64.b64encode(f":{pat}".encode()).decode()
            headers = {"Authorization": f"Basic {auth_str}"}

            # Use _apis/projects endpoint to verify authentication
            api_url = f"{org_url.rstrip('/')}/_apis/projects?api-version=7.0&$top=1"

            resp = await client.get(api_url, headers=headers, timeout=10.0)
            latency = (time.perf_counter() - start) * 1000

            if resp.status_code == 200:
                data = resp.json()
                project_count = data.get("count", 0)
                return TestResult(
                    component="Azure DevOps",
                    status="ok",
                    latency_ms=latency,
                    message=f"Connected ({project_count} projects accessible)",
                )
            elif resp.status_code == 401:
                return TestResult(
                    component="Azure DevOps",
                    status="fail",
                    latency_ms=latency,
                    message="PAT authentication failed (401 Unauthorized)",
                )
            elif resp.status_code == 403:
                return TestResult(
                    component="Azure DevOps",
                    status="fail",
                    latency_ms=latency,
                    message="PAT lacks permissions (403 Forbidden)",
                )
            else:
                return TestResult(
                    component="Azure DevOps",
                    status="fail",
                    latency_ms=latency,
                    message=f"API error: HTTP {resp.status_code}",
                )
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return TestResult(
                component="Azure DevOps",
                status="fail",
                latency_ms=latency,
                message=str(e),
            )

    async def _check_searxng_functional(self, client: httpx.AsyncClient) -> TestResult:
        """Run an actual search query against SearXNG.

        Tests:
        - Search endpoint is functional
        - Returns valid results
        - Response time is acceptable
        """
        start = time.perf_counter()
        try:
            base_url = str(self._settings.searxng_url).rstrip("/")
            search_url = f"{base_url}/search"

            # Use a simple, fast query
            params = {
                "q": "test",
                "format": "json",
                "categories": "general",
            }

            resp = await client.get(search_url, params=params, timeout=10.0)
            latency = (time.perf_counter() - start) * 1000

            if resp.status_code == 200:
                try:
                    data = resp.json()
                    result_count = len(data.get("results", []))
                    return TestResult(
                        component="SearXNG Search",
                        status="ok",
                        latency_ms=latency,
                        message=f"Search returned {result_count} results",
                    )
                except Exception:
                    return TestResult(
                        component="SearXNG Search",
                        status="fail",
                        latency_ms=latency,
                        message="Invalid JSON response",
                    )
            else:
                return TestResult(
                    component="SearXNG Search",
                    status="fail",
                    latency_ms=latency,
                    message=f"HTTP {resp.status_code}",
                )
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return TestResult(
                component="SearXNG Search",
                status="fail",
                latency_ms=latency,
                message=str(e),
            )

    async def _check_credential_encryption(self) -> TestResult:
        """Test credential encryption/decryption round-trip.

        Tests:
        - Encryption key is configured
        - Fernet encryption works correctly
        - Round-trip produces same value
        """
        start = time.perf_counter()
        try:
            encryption_key = self._settings.credential_encryption_key

            if not encryption_key:
                latency = (time.perf_counter() - start) * 1000
                return TestResult(
                    component="Credential Encryption",
                    status="ok",
                    latency_ms=latency,
                    message="Not configured (no encryption key)",
                )

            # Test encryption round-trip
            key_bytes = (
                encryption_key.encode() if isinstance(encryption_key, str) else encryption_key
            )
            fernet = Fernet(key_bytes)

            # Encrypt and decrypt test value
            encrypted = fernet.encrypt(_ENCRYPTION_TEST_VALUE.encode())
            decrypted = fernet.decrypt(encrypted).decode()

            latency = (time.perf_counter() - start) * 1000

            if decrypted == _ENCRYPTION_TEST_VALUE:
                return TestResult(
                    component="Credential Encryption",
                    status="ok",
                    latency_ms=latency,
                    message="Encryption round-trip successful",
                )
            else:
                return TestResult(
                    component="Credential Encryption",
                    status="fail",
                    latency_ms=latency,
                    message="Decrypted value does not match original",
                )
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return TestResult(
                component="Credential Encryption",
                status="fail",
                latency_ms=latency,
                message=f"Encryption error: {e}",
            )

    async def _check_price_tracker(self) -> TestResult:
        """Check price tracker module status.

        Tests:
        - Database table is accessible
        - Checks if any tracked products exist
        """
        start = time.perf_counter()
        try:
            from sqlalchemy import text

            # Check if price_tracker_products table exists and is accessible
            async with engine.connect() as conn:
                # First check if table exists
                table_check = await conn.execute(
                    text(
                        "SELECT EXISTS ("
                        "SELECT FROM information_schema.tables "
                        "WHERE table_name = 'price_tracker_products'"
                        ")"
                    )
                )
                table_exists = table_check.scalar()

                if not table_exists:
                    latency = (time.perf_counter() - start) * 1000
                    return TestResult(
                        component="Price Tracker",
                        status="ok",
                        latency_ms=latency,
                        message="Not configured (table not found)",
                    )

                # Count tracked products
                count_result = await conn.execute(
                    text("SELECT COUNT(*) FROM price_tracker_products")
                )
                product_count = count_result.scalar() or 0

                latency = (time.perf_counter() - start) * 1000

                return TestResult(
                    component="Price Tracker",
                    status="ok",
                    latency_ms=latency,
                    message=f"{product_count} products tracked",
                )
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return TestResult(
                component="Price Tracker",
                status="fail",
                latency_ms=latency,
                message=str(e),
            )

    async def _check_user_context_integrity(self) -> TestResult:
        """Check user-context data integrity.

        Tests:
        - All users have at least one linked context
        - No orphaned personal contexts (contexts without user links)
        """
        start = time.perf_counter()
        try:
            async with engine.connect() as conn:
                # Check for users without any context
                users_without_context = await conn.execute(
                    text(
                        "SELECT u.id, u.email FROM users u "
                        "LEFT JOIN user_contexts uc ON uc.user_id = u.id "
                        "WHERE uc.id IS NULL"
                    )
                )
                orphaned_users = users_without_context.fetchall()

                # Check for personal contexts without user links
                orphaned_contexts = await conn.execute(
                    text(
                        "SELECT c.id, c.name FROM contexts c "
                        "LEFT JOIN user_contexts uc ON uc.context_id = c.id "
                        "WHERE c.type = 'personal' AND uc.id IS NULL"
                    )
                )
                orphaned_ctx = orphaned_contexts.fetchall()

                latency = (time.perf_counter() - start) * 1000

                issues = []
                if orphaned_users:
                    emails = [row[1] for row in orphaned_users]
                    issues.append(f"{len(orphaned_users)} users without context: {emails[:3]}")
                if orphaned_ctx:
                    names = [row[1] for row in orphaned_ctx]
                    issues.append(f"{len(orphaned_ctx)} orphaned contexts: {names[:3]}")

                if issues:
                    return TestResult(
                        component="User-Context Integrity",
                        status="fail",
                        latency_ms=latency,
                        message="; ".join(issues),
                    )

                return TestResult(
                    component="User-Context Integrity",
                    status="ok",
                    latency_ms=latency,
                    message="All users have linked contexts",
                )
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return TestResult(
                component="User-Context Integrity",
                status="fail",
                latency_ms=latency,
                message=str(e),
            )

    # -------------------------------------------------------------------------
    # Trace Analysis Methods
    # -------------------------------------------------------------------------

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

    def _parse_span(self, data: dict[str, Any]) -> TraceSpan | None:
        """Parse a span from JSON data."""
        try:
            context = data.get("context", {})
            start_str = data.get("start_time")
            if start_str:
                start_dt = datetime.fromisoformat(start_str)
            else:
                start_dt = datetime.now(UTC).replace(tzinfo=None)

            return TraceSpan(
                trace_id=context.get("trace_id", "unknown"),
                span_id=context.get("span_id", "unknown"),
                parent_id=context.get("parent_id"),
                name=data.get("name", "unknown"),
                start_time=start_dt,
                duration_ms=data.get("duration_ms", 0.0),
                status=data.get("status", "UNSET"),
                attributes=data.get("attributes", {}),
            )
        except (KeyError, ValueError):
            return None

    def _read_trace_spans_by_id(self, trace_id: str) -> list[TraceSpan]:
        """Search entire file for spans matching trace_id (called via asyncio.to_thread)."""
        raw_spans: list[TraceSpan] = []
        with self._trace_log_path.open("r", encoding="utf-8") as f:
            for line in f:
                if trace_id not in line:
                    continue
                try:
                    data = json.loads(line)
                    span = self._parse_span(data)
                    if span:
                        raw_spans.append(span)
                except (json.JSONDecodeError, ValueError):
                    continue
        return raw_spans

    def _read_recent_trace_spans(self, limit: int) -> list[TraceSpan]:
        """Read recent trace spans from tail of file (called via asyncio.to_thread)."""
        raw_spans: list[TraceSpan] = []
        with self._trace_log_path.open("r", encoding="utf-8") as f:
            last_lines = deque(f, maxlen=limit)

        for line in last_lines:
            try:
                data = json.loads(line)
                span = self._parse_span(data)
                if span:
                    raw_spans.append(span)
            except (json.JSONDecodeError, ValueError):
                continue
        return raw_spans

    async def get_recent_traces(
        self, limit: int = 1000, show_all: bool = False, trace_id: str | None = None
    ) -> list[TraceGroup]:
        """
        Read log, group by trace_id, and return valid trace groups.

        Args:
            limit: Maximum number of trace lines to read.
            show_all: If True, include diagnostic/health-check traces. Default False.
            trace_id: If provided, filter to traces containing this ID (partial match).
        """
        if not self._trace_log_path.exists():
            LOGGER.warning(f"Trace log not found at {self._trace_log_path}")
            return []

        # 1. Read Raw Spans
        # When trace_id is specified, search the entire file for matching spans
        # Otherwise, read only the last N lines for performance
        raw_spans: list[TraceSpan] = []
        try:
            if trace_id:
                # Full-file search for specific trace_id
                raw_spans = await asyncio.to_thread(self._read_trace_spans_by_id, trace_id)
            else:
                # Tail-based reading for recent traces
                raw_spans = await asyncio.to_thread(self._read_recent_trace_spans, limit)
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
        for tid, spans in groups.items():
            # Sort chronologically
            spans.sort(key=lambda x: x.start_time or datetime.min)

            # Find Root (no parent or first in time)
            root = next((s for s in spans if not s.parent_id), spans[0])

            # Filter Logic: discard orphans if strictly required,
            # but for now we accept best-effort roots

            # Calc Stats
            start_time = root.start_time or datetime.now(UTC).replace(tzinfo=None)
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
                    trace_id=tid,
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
        #    OR when a specific trace_id is requested (always show explicit requests)
        if not show_all and not trace_id:
            trace_groups = [g for g in trace_groups if not self._should_hide_trace(g)]

        # 6. Filter by specific trace_id if provided (partial match)
        if trace_id:
            trace_groups = [g for g in trace_groups if trace_id in g.trace_id]

        return trace_groups
