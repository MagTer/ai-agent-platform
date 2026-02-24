"""Diagnostic API for AI/programmatic access.

This module provides REST API endpoints for AI agents and scripts to access
diagnostic data without browser-based Entra ID authentication.

Authentication: X-API-Key header OR Entra ID admin session.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_db
from core.db.models import Conversation, Message, Session, SystemConfig
from core.diagnostics.service import DiagnosticsService
from core.observability.security_logger import (
    AUTH_FAILURE,
    AUTH_SUCCESS,
    get_client_ip,
    log_security_event,
)
from core.runtime.config import get_settings
from interfaces.http.admin_auth import AdminUser, get_admin_user
from interfaces.http.csrf import require_csrf

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/platformadmin/api", tags=["diagnostic-api"])


# =============================================================================
# Authentication
# =============================================================================


class APIKeyUser:
    """Represents an API key authenticated request."""

    def __init__(self, key_type: str = "diagnostic") -> None:
        self.key_type = key_type
        self.email = "api-key-user"
        self.user_id = None


async def verify_api_key_or_admin(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    admin_user: AdminUser | None = Depends(lambda: None),  # Optional admin
    session: AsyncSession = Depends(get_db),
) -> AdminUser | APIKeyUser:
    """Verify either API key or admin authentication.

    Priority:
    1. X-API-Key header (for AI/programmatic access)
    2. Entra ID admin session (for browser access)

    Returns:
        AdminUser or APIKeyUser depending on auth method.

    Raises:
        HTTPException 401: If neither auth method succeeds.
    """
    settings = get_settings()

    # Try API key first (use constant-time comparison to prevent timing attacks)
    if x_api_key:
        if settings.diagnostic_api_key and secrets.compare_digest(
            x_api_key.encode(), settings.diagnostic_api_key.encode()
        ):
            LOGGER.info("API key authentication successful")
            return APIKeyUser(key_type="diagnostic")
        # Also accept admin_api_key for backward compatibility
        if settings.admin_api_key and secrets.compare_digest(
            x_api_key.encode(), settings.admin_api_key.encode()
        ):
            LOGGER.info("Admin API key authentication successful")
            return APIKeyUser(key_type="admin")

    # Try admin session
    try:
        admin = await get_admin_user(
            # Create a minimal request object - this is a workaround
            # In practice, the dependency injection will handle this
            request=None,  # type: ignore[arg-type]
            session=session,
        )
        return admin
    except HTTPException:
        pass  # Admin auth failed, continue to error

    # Neither auth method worked
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required. Provide X-API-Key header or login via Entra ID.",
        headers={"WWW-Authenticate": "ApiKey"},
    )


# Simpler approach: separate dependency that checks API key only
async def get_api_key_auth(
    request: Request,
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    session: AsyncSession = Depends(get_db),
) -> AdminUser | APIKeyUser:
    """Authenticate via API key or fall back to admin session."""
    settings = get_settings()

    # Check API key (use constant-time comparison to prevent timing attacks)
    if x_api_key:
        if settings.diagnostic_api_key and secrets.compare_digest(
            x_api_key.encode(), settings.diagnostic_api_key.encode()
        ):
            log_security_event(
                event_type=AUTH_SUCCESS,
                ip_address=get_client_ip(request),
                endpoint=request.url.path,
                details={"auth_method": "diagnostic_api_key"},
                severity="INFO",
            )
            return APIKeyUser(key_type="diagnostic")

        if settings.admin_api_key and secrets.compare_digest(
            x_api_key.encode(), settings.admin_api_key.encode()
        ):
            log_security_event(
                event_type=AUTH_SUCCESS,
                ip_address=get_client_ip(request),
                endpoint=request.url.path,
                details={"auth_method": "admin_api_key"},
                severity="INFO",
            )
            return APIKeyUser(key_type="admin")

        # Invalid API key
        log_security_event(
            event_type=AUTH_FAILURE,
            ip_address=get_client_ip(request),
            endpoint=request.url.path,
            details={"reason": "Invalid API key"},
            severity="WARNING",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )

    # No API key - try admin session
    try:
        return await get_admin_user(request, session)
    except HTTPException as e:
        # Re-raise with API key hint
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide X-API-Key header or login via Entra ID.",
        ) from e


# =============================================================================
# Response Models
# =============================================================================


class RecommendedAction(BaseModel):
    """A recommended action from diagnostics."""

    priority: int
    action: str
    component: str
    error_code: str


class SystemStatusResponse(BaseModel):
    """Aggregated system status for AI diagnosis."""

    status: str  # HEALTHY, DEGRADED, CRITICAL
    environment: str
    timestamp: str
    system_context_id: str | None
    healthy_components: list[str]
    failed_components: list[dict[str, Any]]
    recent_errors: list[dict[str, Any]]
    metrics: dict[str, Any]
    recommended_actions: list[RecommendedAction]


class ConversationSummary(BaseModel):
    """Summary of a conversation."""

    id: UUID
    context_id: UUID | None
    created_at: datetime
    updated_at: datetime | None
    message_count: int
    metadata: dict[str, Any] | None


class MessageResponse(BaseModel):
    """A single message in a conversation."""

    id: str  # UUID as string
    role: str
    content: str
    created_at: datetime
    trace_id: str | None = None


class ConversationMessagesResponse(BaseModel):
    """Full conversation with messages."""

    conversation_id: UUID
    messages: list[MessageResponse]
    total_count: int


class DebugLogStats(BaseModel):
    """Aggregated debug log statistics."""

    total_logs: int
    by_event_type: dict[str, int]
    by_hour: list[dict[str, Any]]
    recent_errors: list[dict[str, Any]]


class TraceSearchResult(BaseModel):
    """Result from trace search."""

    trace_id: str
    start_time: str
    duration_ms: float
    status: str
    root_name: str
    span_count: int


class SpanDetail(BaseModel):
    """Detail of a single span within a trace."""

    span_id: str
    parent_id: str | None
    name: str
    start_time: str | None
    duration_ms: float
    status: str
    attributes: dict[str, Any]
    events: list[dict[str, Any]] = []  # Span events (debug events, exceptions, etc.)


class TraceDetail(BaseModel):
    """Full trace with all span details."""

    trace_id: str
    start_time: str
    duration_ms: float
    status: str
    root_name: str
    span_count: int
    spans: list[SpanDetail]


class SystemConfigResponse(BaseModel):
    """System configuration entry."""

    key: str
    value: Any
    description: str | None
    updated_at: datetime | None


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/status", response_model=SystemStatusResponse)
async def get_system_status(
    request: Request,
    auth: AdminUser | APIKeyUser = Depends(get_api_key_auth),
    session: AsyncSession = Depends(get_db),
) -> SystemStatusResponse:
    """Get aggregated system status optimized for AI diagnosis.

    Returns a comprehensive status including:
    - Overall health status (HEALTHY/DEGRADED/CRITICAL)
    - Component health checks
    - Recent errors from logs
    - Key metrics
    - Recommended actions
    """
    settings = get_settings()
    diagnostics = DiagnosticsService(settings)

    # Get diagnostics summary
    summary = await diagnostics.get_diagnostics_summary()

    # Get recent debug log errors from JSONL
    from core.observability.debug_logger import read_debug_logs

    recent_errors: list[dict[str, Any]] = []
    supervisor_logs = await read_debug_logs(event_type="supervisor", limit=20)
    for log in supervisor_logs:
        event_data = log.get("event_data", {})
        if event_data.get("outcome") in ("ABORT", "REPLAN"):
            recent_errors.append(
                {
                    "trace_id": log.get("trace_id"),
                    "event_type": log.get("event_type"),
                    "outcome": event_data.get("outcome"),
                    "reason": event_data.get("reason"),
                    "created_at": log.get("timestamp"),
                }
            )
            if len(recent_errors) >= 10:
                break

    # Retrieve system_context_id from app state if available
    system_context_id: str | None = None
    if hasattr(request.app.state, "system_context_id"):
        system_context_id = str(request.app.state.system_context_id)

    return SystemStatusResponse(
        status=summary.get("overall_status", "UNKNOWN"),
        environment=settings.environment,
        timestamp=datetime.now(UTC).isoformat(),
        system_context_id=system_context_id,
        healthy_components=summary.get("healthy_components", []),
        failed_components=summary.get("failed_components", []),
        recent_errors=recent_errors,
        metrics=summary.get("metrics", {}),
        recommended_actions=[
            RecommendedAction(**action) for action in summary.get("recommended_actions", [])
        ],
    )


@router.get("/conversations", response_model=list[ConversationSummary])
async def list_conversations(
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    context_id: UUID | None = Query(None),
    auth: AdminUser | APIKeyUser = Depends(get_api_key_auth),
    session: AsyncSession = Depends(get_db),
) -> list[ConversationSummary]:
    """List conversations with message counts.

    Args:
        limit: Maximum number of conversations to return.
        offset: Number of conversations to skip.
        context_id: Optional filter by context.
    """
    # Build query
    stmt = select(Conversation).order_by(Conversation.created_at.desc())

    if context_id:
        stmt = stmt.where(Conversation.context_id == context_id)

    stmt = stmt.offset(offset).limit(limit)

    result = await session.execute(stmt)
    conversations = result.scalars().all()

    # Get message counts
    summaries = []
    for conv in conversations:
        # Count messages for this conversation's sessions
        count_stmt = (
            select(func.count(Message.id))
            .join(Session, Message.session_id == Session.id)
            .where(Session.conversation_id == conv.id)
        )
        count_result = await session.execute(count_stmt)
        message_count = count_result.scalar() or 0

        summaries.append(
            ConversationSummary(
                id=conv.id,
                context_id=conv.context_id,
                created_at=conv.created_at,
                updated_at=conv.updated_at,
                message_count=message_count,
                metadata=conv.conversation_metadata,
            )
        )

    return summaries


@router.get(
    "/conversations/{conversation_id}/traces",
    response_model=list[TraceSearchResult],
)
async def get_conversation_traces(
    conversation_id: UUID,
    limit: int = Query(50, le=200, description="Max traces to return"),
    auth: AdminUser | APIKeyUser = Depends(get_api_key_auth),
    session: AsyncSession = Depends(get_db),
) -> list[TraceSearchResult]:
    """Get all traces for a specific conversation.

    Aggregates all trace_ids from messages in the conversation's sessions,
    then loads trace details for each one.

    Args:
        conversation_id: The conversation UUID.
        limit: Maximum number of traces to return (default 50, max 200).
    """
    # Verify conversation exists
    conv_stmt = select(Conversation).where(Conversation.id == conversation_id)
    conv_result = await session.execute(conv_stmt)
    if not conv_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Get all trace_ids from messages in this conversation (with limit)
    trace_id_stmt = (
        select(Message.trace_id)
        .join(Session, Message.session_id == Session.id)
        .where(Session.conversation_id == conversation_id)
        .where(Message.trace_id.isnot(None))
        .distinct()
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    trace_id_result = await session.execute(trace_id_stmt)
    trace_ids = [row[0] for row in trace_id_result.fetchall()]

    if not trace_ids:
        return []

    # Load trace details only for the requested trace_ids
    settings = get_settings()
    diagnostics = DiagnosticsService(settings)

    # Convert trace_ids to a set for faster lookups
    trace_id_set = set(trace_ids)

    # Read a reasonable window of traces and filter
    # Read 2x the limit to account for non-matching traces
    all_traces = await diagnostics.get_recent_traces(limit=limit * 2, show_all=True)

    results = []
    for trace_group in all_traces:
        if trace_group.trace_id in trace_id_set:
            results.append(
                TraceSearchResult(
                    trace_id=trace_group.trace_id,
                    start_time=trace_group.start_time.isoformat(),
                    duration_ms=trace_group.total_duration_ms,
                    status=trace_group.status,
                    root_name=trace_group.root.name,
                    span_count=len(trace_group.spans),
                )
            )
            # Stop if we found all traces
            if len(results) >= len(trace_ids):
                break

    # Sort by start time descending (most recent first)
    results.sort(key=lambda x: x.start_time, reverse=True)

    return results


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=ConversationMessagesResponse,
)
async def get_conversation_messages(
    conversation_id: UUID,
    limit: int = Query(100, le=500),
    offset: int = Query(0),
    role: str | None = Query(None, description="Filter by role: user, assistant, system"),
    auth: AdminUser | APIKeyUser = Depends(get_api_key_auth),
    session: AsyncSession = Depends(get_db),
) -> ConversationMessagesResponse:
    """Get messages for a specific conversation.

    Args:
        conversation_id: The conversation UUID.
        limit: Maximum messages to return.
        offset: Number of messages to skip.
        role: Optional filter by message role.
    """
    # Verify conversation exists
    conv_stmt = select(Conversation).where(Conversation.id == conversation_id)
    conv_result = await session.execute(conv_stmt)
    if not conv_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Get messages through sessions
    stmt = (
        select(Message)
        .join(Session, Message.session_id == Session.id)
        .where(Session.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )

    if role:
        stmt = stmt.where(Message.role == role)

    # Get total count
    count_stmt = (
        select(func.count(Message.id))
        .join(Session, Message.session_id == Session.id)
        .where(Session.conversation_id == conversation_id)
    )
    if role:
        count_stmt = count_stmt.where(Message.role == role)
    count_result = await session.execute(count_stmt)
    total_count = count_result.scalar() or 0

    # Apply pagination
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    messages = result.scalars().all()

    return ConversationMessagesResponse(
        conversation_id=conversation_id,
        messages=[
            MessageResponse(
                id=str(msg.id),
                role=msg.role,
                content=msg.content or "",
                created_at=msg.created_at,
                trace_id=msg.trace_id,
            )
            for msg in messages
        ],
        total_count=total_count,
    )


@router.get("/debug/stats", response_model=DebugLogStats)
async def get_debug_stats(
    hours: int = Query(24, le=168, description="Hours of data to analyze"),
    auth: AdminUser | APIKeyUser = Depends(get_api_key_auth),
) -> DebugLogStats:
    """Get aggregated debug log statistics from JSONL file.

    Provides counts by event type, hourly distribution, and recent errors.
    """
    from collections import defaultdict

    from core.observability.debug_logger import read_debug_logs

    cutoff = datetime.now(UTC) - timedelta(hours=hours)

    # Read all logs (limited to reasonable number)
    logs = await read_debug_logs(limit=10000)

    # Filter by time window
    filtered_logs = []
    for log in logs:
        timestamp_str = log.get("timestamp", "")
        if timestamp_str:
            try:
                log_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                if log_time >= cutoff:
                    filtered_logs.append(log)
            except ValueError:
                continue

    total_logs = len(filtered_logs)

    # Count by event type
    by_event_type_dict: dict[str, int] = defaultdict(int)
    for log in filtered_logs:
        event_type = log.get("event_type", "unknown")
        by_event_type_dict[event_type] += 1

    # Hourly distribution
    by_hour: list[dict[str, Any]] = []
    for h in range(min(hours, 24)):
        now = datetime.now(UTC)
        hour_start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=h)
        hour_end = hour_start + timedelta(hours=1)
        count = sum(
            1
            for log in filtered_logs
            if hour_start
            <= datetime.fromisoformat(log.get("timestamp", "").replace("Z", "+00:00"))
            < hour_end
        )
        by_hour.append({"hour": hour_start.isoformat(), "count": count})

    # Recent errors (supervisor with ABORT/REPLAN)
    recent_errors = []
    for log in filtered_logs:
        if log.get("event_type") == "supervisor":
            event_data = log.get("event_data", {})
            outcome = event_data.get("outcome")
            if outcome in ("ABORT", "REPLAN"):
                recent_errors.append(
                    {
                        "trace_id": log.get("trace_id"),
                        "outcome": outcome,
                        "reason": event_data.get("reason"),
                        "step": event_data.get("step_label"),
                        "created_at": log.get("timestamp"),
                    }
                )
                if len(recent_errors) >= 20:
                    break

    return DebugLogStats(
        total_logs=total_logs,
        by_event_type=dict(by_event_type_dict),
        by_hour=by_hour,
        recent_errors=recent_errors,
    )


@router.get("/otel-metrics")
async def get_otel_metrics_api(
    auth: AdminUser | APIKeyUser = Depends(get_api_key_auth),
) -> dict[str, Any]:
    """Get OpenTelemetry metrics with computed insights.

    Returns raw counters plus computed fields useful for AI diagnosis:
    - error_rate_pct: Error rate as percentage
    - avg_request_duration_ms: Average request latency
    - avg_llm_duration_ms: Average LLM call latency
    """
    from core.observability.metrics import get_metric_snapshot

    snapshot = get_metric_snapshot()

    # Compute derived insights
    total_req = snapshot.get("requests.total", 0)
    total_errors = snapshot.get("requests.errors", 0)
    duration_sum = snapshot.get("requests.duration_ms_sum", 0)
    llm_calls = snapshot.get("llm.calls.total", 0)
    llm_duration_sum = snapshot.get("llm.duration_ms_sum", 0)

    return {
        "counters": snapshot,
        "insights": {
            "error_rate_pct": round((total_errors / total_req * 100), 2) if total_req > 0 else 0.0,
            "avg_request_duration_ms": round(duration_sum / total_req, 1) if total_req > 0 else 0.0,
            "avg_llm_duration_ms": round(llm_duration_sum / llm_calls, 1) if llm_calls > 0 else 0.0,
            "total_requests": int(total_req),
            "total_errors": int(total_errors),
            "total_llm_tokens": int(snapshot.get("llm.tokens.total", 0)),
            "total_tool_calls": int(snapshot.get("tools.calls.total", 0)),
            "total_tool_errors": int(snapshot.get("tools.errors", 0)),
            "active_requests": int(snapshot.get("requests.active", 0)),
        },
    }


@router.get("/debug/logs")
async def get_debug_logs_api(
    trace_id: str | None = Query(None, description="Filter by trace ID"),
    event_type: str | None = Query(None, description="Filter by event type"),
    offset: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(50, ge=1, le=500, description="Max entries to return"),
    auth: AdminUser | APIKeyUser = Depends(get_api_key_auth),
) -> list[dict[str, Any]]:
    """Query debug log entries from JSONL file.

    Returns structured debug events with full event_data.
    Filter by trace_id to get all events for a specific request,
    or by event_type to find specific event categories.

    Event types: request, history, plan, tool_call, skill_step,
    supervisor, completion_prompt, completion_response
    """
    from core.observability.debug_logger import read_debug_logs

    # Read logs (we need offset + limit entries to apply offset)
    logs = await read_debug_logs(
        trace_id=trace_id,
        event_type=event_type,
        limit=offset + limit,
    )

    # Apply pagination
    return logs[offset:]


@router.get("/investigate/{trace_id}")
async def investigate_trace(
    trace_id: str,
    auth: AdminUser | APIKeyUser = Depends(get_api_key_auth),
) -> dict[str, Any]:
    """Get all observability data for a single trace in one call.

    Returns trace spans, debug log entries, and metrics context
    for the given trace_id. This is the primary endpoint for
    AI-driven diagnosis of individual requests.

    Response structure:
    {
        "trace_id": "abc123...",
        "spans": [...],           // All spans in this trace
        "debug_logs": [...],      // All debug events for this trace
        "summary": {              // Computed summary
            "duration_ms": 1234,
            "span_count": 8,
            "error_spans": 1,
            "tools_used": ["search", "homey"],
            "llm_calls": 2,
            "outcome": "SUCCESS" | "ABORT" | "REPLAN" | null
        }
    }
    """
    from core.observability.debug_logger import read_debug_logs

    settings = get_settings()
    diag_service = DiagnosticsService(settings)

    # Get trace spans
    traces = await diag_service.get_recent_traces(limit=5000, show_all=True)
    trace_spans = []
    for tg in traces:
        if tg.trace_id == trace_id:
            trace_spans = [
                {
                    "name": s.name,
                    "duration_ms": s.duration_ms,
                    "status": s.status,
                    "attributes": s.attributes,
                    "start_time": s.start_time,
                    "events": s.events,
                }
                for s in tg.spans
            ]
            break

    # Get debug logs for this trace
    debug_logs = await read_debug_logs(trace_id=trace_id, limit=500)

    # Compute summary
    durations = [s.get("duration_ms", 0) for s in trace_spans]
    total_duration = max((d for d in durations if isinstance(d, (int, float))), default=0)
    error_spans = sum(1 for s in trace_spans if s.get("status") == "ERROR")
    tools_used = list(
        {
            dl.get("event_data", {}).get("tool_name", "")
            for dl in debug_logs
            if dl.get("event_type") == "tool_call"
        }
        - {""}
    )
    llm_calls = sum(1 for dl in debug_logs if dl.get("event_type") in ("completion_prompt",))

    # Find outcome from supervisor events
    outcome = None
    for dl in debug_logs:
        if dl.get("event_type") == "supervisor":
            outcome = dl.get("event_data", {}).get("outcome")

    return {
        "trace_id": trace_id,
        "spans": trace_spans,
        "debug_logs": debug_logs,
        "summary": {
            "duration_ms": total_duration,
            "span_count": len(trace_spans),
            "error_spans": error_spans,
            "tools_used": tools_used,
            "llm_calls": llm_calls,
            "outcome": outcome,
        },
    }


@router.get("/traces/search", response_model=list[TraceSearchResult])
async def search_traces(
    trace_id: str | None = Query(None, description="Trace ID to find (partial match)"),
    min_duration_ms: float | None = Query(None, description="Minimum duration in ms"),
    status: str | None = Query(None, description="Filter by status: OK, ERR"),
    offset: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(50, ge=1, le=200, description="Max items to return"),
    auth: AdminUser | APIKeyUser = Depends(get_api_key_auth),
) -> list[TraceSearchResult]:
    """Search OpenTelemetry traces.

    Reads from spans.jsonl file and filters based on criteria.
    """
    settings = get_settings()
    diagnostics = DiagnosticsService(settings)

    # Get traces using the correct method
    trace_groups = await diagnostics.get_recent_traces(
        limit=limit * 10,  # Read more to allow filtering
        show_all=True,
        trace_id=trace_id,
    )

    # Filter and transform
    results = []
    for trace_group in trace_groups:
        # Apply filters
        if min_duration_ms and trace_group.total_duration_ms < min_duration_ms:
            continue
        if status and trace_group.status != status:
            continue

        results.append(
            TraceSearchResult(
                trace_id=trace_group.trace_id,
                start_time=trace_group.start_time.isoformat(),
                duration_ms=trace_group.total_duration_ms,
                status=trace_group.status,
                root_name=trace_group.root.name,
                span_count=len(trace_group.spans),
            )
        )

    # Apply pagination
    return results[offset : offset + limit]


@router.get("/traces/{trace_id}", response_model=TraceDetail)
async def get_trace_detail(
    trace_id: str,
    auth: AdminUser | APIKeyUser = Depends(get_api_key_auth),
) -> TraceDetail:
    """Get full detail for a specific trace, including all spans.

    Args:
        trace_id: The full trace ID string.
    """
    settings = get_settings()
    diagnostics = DiagnosticsService(settings)

    trace_groups = await diagnostics.get_recent_traces(
        limit=100,
        show_all=True,
        trace_id=trace_id,
    )

    # Find exact match (get_recent_traces does partial matching)
    match = None
    for group in trace_groups:
        if group.trace_id == trace_id:
            match = group
            break

    if not match:
        raise HTTPException(status_code=404, detail="Trace not found")

    # Sort spans by start_time
    sorted_spans = sorted(
        match.spans,
        key=lambda s: s.start_time if s.start_time else datetime.min,
    )

    return TraceDetail(
        trace_id=match.trace_id,
        start_time=match.start_time.isoformat(),
        duration_ms=match.total_duration_ms,
        status=match.status,
        root_name=match.root.name,
        span_count=len(match.spans),
        spans=[
            SpanDetail(
                span_id=span.span_id,
                parent_id=span.parent_id,
                name=span.name,
                start_time=span.start_time.isoformat() if span.start_time else None,
                duration_ms=span.duration_ms,
                status=span.status,
                attributes=span.attributes,
                events=span.events,
            )
            for span in sorted_spans
        ],
    )


@router.get("/config", response_model=list[SystemConfigResponse])
async def get_system_config(
    auth: AdminUser | APIKeyUser = Depends(get_api_key_auth),
    session: AsyncSession = Depends(get_db),
) -> list[SystemConfigResponse]:
    """Get all system configuration entries.

    Returns non-sensitive configuration stored in the database.
    """
    stmt = select(SystemConfig)
    result = await session.execute(stmt)
    configs = result.scalars().all()

    return [
        SystemConfigResponse(
            key=cfg.key,
            value=cfg.value,
            description=cfg.description,
            updated_at=cfg.updated_at,
        )
        for cfg in configs
    ]


@router.get("/tools/stats")
async def get_tool_stats(
    hours: int = Query(24, ge=1, le=168),
    auth: AdminUser | APIKeyUser = Depends(get_api_key_auth),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get tool execution statistics from debug logs.

    Returns aggregated metrics for each tool including:
    - Total call count
    - Execution timing (avg, min, max, total)
    - Calls with timing data vs total calls

    Args:
        hours: Number of hours to analyze (1-168).
    """
    from core.observability.debug_logger import read_debug_logs

    cutoff = datetime.now(UTC) - timedelta(hours=hours)

    # Get all tool_call events from JSONL
    logs = await read_debug_logs(event_type="tool_call", limit=10000)

    # Filter by time window
    filtered_logs = []
    for log in logs:
        timestamp_str = log.get("timestamp", "")
        if timestamp_str:
            try:
                log_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                if log_time >= cutoff:
                    filtered_logs.append(log)
            except ValueError:
                continue

    # Aggregate by tool name
    tool_stats: dict[str, dict[str, Any]] = {}
    for log in filtered_logs:
        data = log.get("event_data", {})
        tool_name = data.get("tool_name", data.get("tool", "unknown"))
        duration = data.get("duration_ms")

        if tool_name not in tool_stats:
            tool_stats[tool_name] = {
                "count": 0,
                "total_duration_ms": 0.0,
                "avg_duration_ms": 0.0,
                "max_duration_ms": 0.0,
                "min_duration_ms": float("inf"),
                "timed_count": 0,
            }

        stats = tool_stats[tool_name]
        stats["count"] += 1
        if duration is not None:
            stats["timed_count"] += 1
            stats["total_duration_ms"] += duration
            stats["max_duration_ms"] = max(stats["max_duration_ms"], duration)
            stats["min_duration_ms"] = min(stats["min_duration_ms"], duration)

    # Calculate averages and fix min for tools with no timing
    for stats in tool_stats.values():
        if stats["timed_count"] > 0:
            stats["avg_duration_ms"] = round(stats["total_duration_ms"] / stats["timed_count"], 1)
        if stats["min_duration_ms"] == float("inf"):
            stats["min_duration_ms"] = 0.0
        stats["total_duration_ms"] = round(stats["total_duration_ms"], 1)

    return {
        "period_hours": hours,
        "tools": tool_stats,
        "total_tool_calls": sum(s["count"] for s in tool_stats.values()),
    }


@router.get("/skills/stats")
async def get_skill_stats(
    hours: int = Query(24, ge=1, le=168),
    auth: AdminUser | APIKeyUser = Depends(get_api_key_auth),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get skill step execution statistics from debug logs.

    Returns aggregated metrics for each skill including:
    - Total step count
    - Execution timing (avg, min, max, total)
    - Outcome breakdown (success, retry, replan, abort)

    Args:
        hours: Number of hours to analyze (1-168).
    """
    from core.observability.debug_logger import read_debug_logs

    cutoff = datetime.now(UTC) - timedelta(hours=hours)

    # Get all skill_step events from JSONL
    logs = await read_debug_logs(event_type="skill_step", limit=10000)

    # Filter by time window
    filtered_logs = []
    for log in logs:
        timestamp_str = log.get("timestamp", "")
        if timestamp_str:
            try:
                log_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                if log_time >= cutoff:
                    filtered_logs.append(log)
            except ValueError:
                continue

    # Aggregate by skill name
    skill_stats: dict[str, dict[str, Any]] = {}
    for log in filtered_logs:
        data = log.get("event_data", {})
        skill_name = data.get("skill_name", data.get("skill", "unknown"))
        duration = data.get("duration_ms")
        outcome = data.get("outcome", "unknown")

        if skill_name not in skill_stats:
            skill_stats[skill_name] = {
                "count": 0,
                "total_duration_ms": 0.0,
                "avg_duration_ms": 0.0,
                "max_duration_ms": 0.0,
                "min_duration_ms": float("inf"),
                "timed_count": 0,
                "outcomes": {
                    "SUCCESS": 0,
                    "RETRY": 0,
                    "REPLAN": 0,
                    "ABORT": 0,
                    "unknown": 0,
                },
            }

        stats = skill_stats[skill_name]
        stats["count"] += 1

        # Track outcome
        if outcome in stats["outcomes"]:
            stats["outcomes"][outcome] += 1
        else:
            stats["outcomes"]["unknown"] += 1

        # Track timing
        if duration is not None:
            stats["timed_count"] += 1
            stats["total_duration_ms"] += duration
            stats["max_duration_ms"] = max(stats["max_duration_ms"], duration)
            stats["min_duration_ms"] = min(stats["min_duration_ms"], duration)

    # Calculate averages and fix min for skills with no timing
    for stats in skill_stats.values():
        if stats["timed_count"] > 0:
            stats["avg_duration_ms"] = round(stats["total_duration_ms"] / stats["timed_count"], 1)
        if stats["min_duration_ms"] == float("inf"):
            stats["min_duration_ms"] = 0.0
        stats["total_duration_ms"] = round(stats["total_duration_ms"], 1)

    return {
        "period_hours": hours,
        "skills": skill_stats,
        "total_skill_steps": sum(s["count"] for s in skill_stats.values()),
    }


@router.get("/requests/stats")
async def get_request_stats(
    hours: int = Query(24, ge=1, le=168),
    auth: AdminUser | APIKeyUser = Depends(get_api_key_auth),
) -> dict[str, Any]:
    """Get HTTP request timing statistics from OpenTelemetry spans.

    Returns aggregated metrics for each endpoint including:
    - Total request count
    - Execution timing (avg, max, total)

    Args:
        hours: Number of hours to analyze (1-168).

    Note:
        This reads from the spans.jsonl file. If the file doesn't exist
        or is empty, returns empty stats.
    """
    import json
    from pathlib import Path

    spans_file = Path("data/spans.jsonl")
    if not spans_file.exists():
        return {"period_hours": hours, "endpoints": {}, "total_requests": 0}

    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    endpoint_stats: dict[str, dict[str, Any]] = {}
    endpoint_durations: dict[str, list[float]] = {}

    try:
        for line in spans_file.read_text().splitlines():
            if not line.strip():
                continue

            try:
                span = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Parse timestamp
            start_time_str = span.get("start_time")
            if not start_time_str:
                continue

            try:
                start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue

            # Filter by time range
            if start_time < cutoff:
                continue

            attrs = span.get("attributes", {})
            route = attrs.get("http.route") or attrs.get("http.target", "")
            duration = span.get("duration_ms", 0)

            if not route or not route.startswith("/"):
                continue

            # Initialize stats for this endpoint
            if route not in endpoint_stats:
                endpoint_stats[route] = {
                    "count": 0,
                    "avg_duration_ms": 0.0,
                    "max_duration_ms": 0.0,
                    "total_duration_ms": 0.0,
                }
                endpoint_durations[route] = []

            stats = endpoint_stats[route]
            stats["count"] += 1
            stats["total_duration_ms"] += duration
            stats["max_duration_ms"] = max(stats["max_duration_ms"], duration)
            endpoint_durations[route].append(duration)

    except Exception:
        LOGGER.exception("Error reading spans file")
        return {
            "period_hours": hours,
            "endpoints": {},
            "total_requests": 0,
            "error": "Failed to read spans data",
        }

    # Calculate averages and percentiles
    for route, stats in endpoint_stats.items():
        if stats["count"] > 0:
            stats["avg_duration_ms"] = round(stats["total_duration_ms"] / stats["count"], 1)
        stats["total_duration_ms"] = round(stats["total_duration_ms"], 1)

        # Add percentiles
        durations = endpoint_durations.get(route, [])
        if durations:
            settings = get_settings()
            diagnostics = DiagnosticsService(settings)
            stats["latency_percentiles"] = diagnostics._calculate_percentiles(durations)
        else:
            stats["latency_percentiles"] = {"p50": 0.0, "p95": 0.0, "p99": 0.0}

    return {
        "period_hours": hours,
        "endpoints": endpoint_stats,
        "total_requests": sum(s["count"] for s in endpoint_stats.values()),
    }


@router.post("/debug/toggle")
async def toggle_debug_logging(
    request: Request,
    enabled: bool,
    auth: AdminUser | APIKeyUser = Depends(get_api_key_auth),
    session: AsyncSession = Depends(get_db),
    _csrf: None = Depends(require_csrf),
) -> dict[str, Any]:
    """Toggle debug logging on or off.

    Updates the SystemConfig table and invalidates the debug logger cache.

    Args:
        enabled: True to enable debug logging, False to disable.

    Returns:
        Status message and current debug state.
    """
    # Update SystemConfig
    stmt = select(SystemConfig).where(SystemConfig.key == "debug_enabled")
    result = await session.execute(stmt)
    config = result.scalar_one_or_none()

    value_dict: dict[str, Any] = {"enabled": enabled}

    if config:
        config.value = value_dict
        config.updated_at = datetime.now(UTC)
    else:
        new_config = SystemConfig(
            key="debug_enabled",
            value=value_dict,
            description="Enable or disable debug logging",
        )
        session.add(new_config)

    await session.commit()

    # Invalidate debug logger cache using the public API
    # This forces the next is_enabled() call to re-read from the database
    from core.observability.debug_logger import invalidate_debug_cache

    invalidate_debug_cache()

    LOGGER.info("Debug logging %s by %s", "enabled" if enabled else "disabled", auth.email)

    return {
        "status": "ok",
        "debug_enabled": enabled,
        "message": f"Debug logging {'enabled' if enabled else 'disabled'}",
    }


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Simple health check endpoint (no auth required for this one)."""
    settings = get_settings()
    return {"status": "ok", "service": "diagnostic-api", "environment": settings.environment}
