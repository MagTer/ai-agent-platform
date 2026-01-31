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

from core.core.config import get_settings
from core.db.engine import get_db
from core.db.models import Conversation, DebugLog, Message, Session, SystemConfig
from core.diagnostics.service import DiagnosticsService
from core.observability.security_logger import (
    AUTH_FAILURE,
    AUTH_SUCCESS,
    get_client_ip,
    log_security_event,
)
from interfaces.http.admin_auth import AdminUser, get_admin_user

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
            request=None,  # type: ignore
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
    timestamp: str
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

    # Get recent debug log errors
    recent_errors: list[dict[str, Any]] = []
    stmt = (
        select(DebugLog)
        .where(DebugLog.event_type == "supervisor")
        .order_by(DebugLog.created_at.desc())
        .limit(10)
    )
    result = await session.execute(stmt)
    for log in result.scalars():
        event_data = log.event_data or {}
        if event_data.get("outcome") in ("ABORT", "REPLAN"):
            recent_errors.append(
                {
                    "trace_id": log.trace_id,
                    "event_type": log.event_type,
                    "outcome": event_data.get("outcome"),
                    "reason": event_data.get("reason"),
                    "created_at": log.created_at.isoformat() if log.created_at else None,
                }
            )

    return SystemStatusResponse(
        status=summary.get("overall_status", "UNKNOWN"),
        timestamp=datetime.now(UTC).isoformat(),
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
    session: AsyncSession = Depends(get_db),
) -> DebugLogStats:
    """Get aggregated debug log statistics.

    Provides counts by event type, hourly distribution, and recent errors.
    """
    # Use timezone-naive datetime for database comparison
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=hours)

    # Total count
    total_stmt = select(func.count(DebugLog.id)).where(DebugLog.created_at >= cutoff)
    total_result = await session.execute(total_stmt)
    total_logs = total_result.scalar() or 0

    # Count by event type
    type_stmt = (
        select(DebugLog.event_type, func.count(DebugLog.id))
        .where(DebugLog.created_at >= cutoff)
        .group_by(DebugLog.event_type)
    )
    type_result = await session.execute(type_stmt)
    by_event_type = {row[0]: row[1] for row in type_result}

    # Hourly distribution (simplified - last 24 hours)
    by_hour: list[dict[str, Any]] = []
    for h in range(min(hours, 24)):
        now = datetime.now(UTC).replace(tzinfo=None)
        hour_start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=h)
        hour_end = hour_start + timedelta(hours=1)
        hour_stmt = (
            select(func.count(DebugLog.id))
            .where(DebugLog.created_at >= hour_start)
            .where(DebugLog.created_at < hour_end)
        )
        hour_result = await session.execute(hour_stmt)
        count = hour_result.scalar() or 0
        by_hour.append({"hour": hour_start.isoformat(), "count": count})

    # Recent errors (supervisor with ABORT/REPLAN)
    error_stmt = (
        select(DebugLog)
        .where(DebugLog.created_at >= cutoff)
        .where(DebugLog.event_type == "supervisor")
        .order_by(DebugLog.created_at.desc())
        .limit(20)
    )
    error_result = await session.execute(error_stmt)
    recent_errors = []
    for log in error_result.scalars():
        event_data = log.event_data or {}
        if event_data.get("outcome") in ("ABORT", "REPLAN"):
            recent_errors.append(
                {
                    "trace_id": log.trace_id,
                    "outcome": event_data.get("outcome"),
                    "reason": event_data.get("reason"),
                    "step": event_data.get("step_label"),
                    "created_at": log.created_at.isoformat() if log.created_at else None,
                }
            )

    return DebugLogStats(
        total_logs=total_logs,
        by_event_type=by_event_type,
        by_hour=by_hour,
        recent_errors=recent_errors,
    )


@router.get("/traces/search", response_model=list[TraceSearchResult])
async def search_traces(
    trace_id: str | None = Query(None, description="Trace ID to find (partial match)"),
    min_duration_ms: float | None = Query(None, description="Minimum duration in ms"),
    status: str | None = Query(None, description="Filter by status: OK, ERR"),
    limit: int = Query(50, le=200),
    auth: AdminUser | APIKeyUser = Depends(get_api_key_auth),
) -> list[TraceSearchResult]:
    """Search OpenTelemetry traces.

    Reads from spans.jsonl file and filters based on criteria.
    """
    settings = get_settings()
    diagnostics = DiagnosticsService(settings)

    # Get traces using the correct method
    trace_groups = diagnostics.get_recent_traces(
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

        if len(results) >= limit:
            break

    return results


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


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Simple health check endpoint (no auth required for this one)."""
    return {"status": "ok", "service": "diagnostic-api"}
