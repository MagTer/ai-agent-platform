"""Debug logging service for comprehensive request tracing.

Provides toggleable debug logging that captures:
- Incoming requests (prompt, messages, metadata)
- History source (request vs database)
- Plan generation
- Tool executions and results
- Supervisor decisions
- Completion prompts
- Final responses

Debug mode is controlled via SystemConfig in the database.
Logs are written to both:
1. OpenTelemetry spans (for trace viewing via APIs)
2. Database (for admin portal viewing and retention)
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import DebugLog, SystemConfig
from core.observability.tracing import set_span_attributes

LOGGER = logging.getLogger(__name__)

# Config keys
DEBUG_ENABLED_KEY = "debug_logging_enabled"
DEBUG_RETENTION_HOURS_KEY = "debug_retention_hours"

# In-memory cache for debug setting (avoid DB hit on every request)
_debug_enabled_cache: bool | None = None
_cache_timestamp: datetime | None = None
_cache_ttl_seconds = 30  # Re-check DB every 30 seconds


class DebugLogger:
    """Service for comprehensive debug logging.

    Usage:
        debug = DebugLogger(session)
        if await debug.is_enabled():
            await debug.log_request(trace_id, prompt, messages)
            await debug.log_history(trace_id, source, messages)
            await debug.log_plan(trace_id, plan)
            await debug.log_tool_call(trace_id, tool_name, args, result)
            await debug.log_completion(trace_id, prompt_history, response)
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize debug logger with database session."""
        self._session = session

    async def is_enabled(self) -> bool:
        """Check if debug logging is enabled.

        Uses in-memory cache with TTL to avoid DB hits on every request.
        """
        global _debug_enabled_cache, _cache_timestamp

        now = datetime.now(UTC)

        # Check cache validity
        if (
            _debug_enabled_cache is not None
            and _cache_timestamp is not None
            and (now - _cache_timestamp).total_seconds() < _cache_ttl_seconds
        ):
            return _debug_enabled_cache

        # Load from database
        stmt = select(SystemConfig).where(SystemConfig.key == DEBUG_ENABLED_KEY)
        result = await self._session.execute(stmt)
        config = result.scalar_one_or_none()

        if config and config.value.get("enabled", False):
            _debug_enabled_cache = True
        else:
            _debug_enabled_cache = False

        _cache_timestamp = now
        return _debug_enabled_cache

    async def set_enabled(self, enabled: bool) -> None:
        """Enable or disable debug logging."""
        global _debug_enabled_cache, _cache_timestamp

        stmt = select(SystemConfig).where(SystemConfig.key == DEBUG_ENABLED_KEY)
        result = await self._session.execute(stmt)
        config = result.scalar_one_or_none()

        if config:
            config.value = {"enabled": enabled}
        else:
            config = SystemConfig(
                key=DEBUG_ENABLED_KEY,
                value={"enabled": enabled},
                description="Enable comprehensive debug logging for request tracing",
            )
            self._session.add(config)

        await self._session.commit()

        # Update cache immediately
        _debug_enabled_cache = enabled
        _cache_timestamp = datetime.now(UTC)

        LOGGER.info("Debug logging %s", "enabled" if enabled else "disabled")

    async def log_event(
        self,
        trace_id: str,
        event_type: str,
        event_data: dict[str, Any],
        conversation_id: str | None = None,
    ) -> None:
        """Log a debug event to database AND OpenTelemetry spans."""
        if not await self.is_enabled():
            return

        # 1. Log to database for admin portal viewing
        log_entry = DebugLog(
            trace_id=trace_id,
            conversation_id=conversation_id,
            event_type=event_type,
            event_data=event_data,
        )
        self._session.add(log_entry)
        # Don't commit here - let the caller manage the transaction

        # 2. Add to OpenTelemetry span for API/trace viewing
        # Prefix with debug. to distinguish from regular attributes
        span_attrs = {
            f"debug.{event_type}": json.dumps(event_data, default=str, ensure_ascii=False)[:4000]
        }
        if conversation_id:
            span_attrs["debug.conversation_id"] = conversation_id
        set_span_attributes(span_attrs)

        # 3. Also log to standard logger for immediate visibility
        LOGGER.info(
            "[DEBUG TRACE %s] %s: %s",
            trace_id[:8] if trace_id else "no-trace",
            event_type,
            _truncate_data(event_data),
        )

    async def log_request(
        self,
        trace_id: str,
        prompt: str,
        messages: list[Any] | None,
        metadata: dict[str, Any] | None,
        conversation_id: str | None = None,
    ) -> None:
        """Log incoming request details."""
        await self.log_event(
            trace_id=trace_id,
            event_type="request",
            event_data={
                "prompt": prompt,
                "message_count": len(messages) if messages else 0,
                "messages": [
                    {"role": m.role, "content": (m.content or "")[:500]} for m in (messages or [])
                ],
                "metadata_keys": list((metadata or {}).keys()),
            },
            conversation_id=conversation_id,
        )

    async def log_history(
        self,
        trace_id: str,
        source: str,  # "request" or "database"
        messages: list[Any],
        conversation_id: str | None = None,
    ) -> None:
        """Log history source and contents."""
        await self.log_event(
            trace_id=trace_id,
            event_type="history",
            event_data={
                "source": source,
                "message_count": len(messages),
                "messages": [
                    {"role": m.role, "content": (m.content or "")[:500]} for m in messages
                ],
            },
            conversation_id=conversation_id,
        )

    async def log_plan(
        self,
        trace_id: str,
        plan: Any,
        conversation_id: str | None = None,
    ) -> None:
        """Log generated plan."""
        await self.log_event(
            trace_id=trace_id,
            event_type="plan",
            event_data={
                "step_count": len(plan.steps) if hasattr(plan, "steps") else 0,
                "steps": [
                    {
                        "id": s.id,
                        "label": s.label,
                        "executor": s.executor,
                        "action": s.action,
                        "tool": s.tool,
                        "args": s.args,
                    }
                    for s in (plan.steps if hasattr(plan, "steps") else [])
                ],
                "description": getattr(plan, "description", ""),
            },
            conversation_id=conversation_id,
        )

    async def log_tool_call(
        self,
        trace_id: str,
        tool_name: str,
        args: dict[str, Any],
        result: Any,
        conversation_id: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        """Log tool execution."""
        result_str = str(result)
        event_data: dict[str, Any] = {
            "tool": tool_name,
            "args": _sanitize_args(args),
            "result": result_str[:2000],
            "result_length": len(result_str),
        }
        if duration_ms is not None:
            event_data["duration_ms"] = round(duration_ms, 1)
        await self.log_event(
            trace_id=trace_id,
            event_type="tool_call",
            event_data=event_data,
            conversation_id=conversation_id,
        )

    async def log_skill_step(
        self,
        trace_id: str,
        skill_name: str,
        step_label: str,
        tool_name: str,
        outcome: str,
        duration_ms: float,
        tool_output_preview: str = "",
        conversation_id: str | None = None,
    ) -> None:
        """Log skill step execution with timing."""
        await self.log_event(
            trace_id=trace_id,
            event_type="skill_step",
            event_data={
                "skill": skill_name,
                "step": step_label,
                "tool": tool_name,
                "outcome": outcome,
                "duration_ms": round(duration_ms, 1),
                "output_preview": tool_output_preview[:500],
            },
            conversation_id=conversation_id,
        )

    async def log_supervisor(
        self,
        trace_id: str,
        step_label: str,
        outcome: str,
        reason: str,
        conversation_id: str | None = None,
    ) -> None:
        """Log supervisor decision."""
        await self.log_event(
            trace_id=trace_id,
            event_type="supervisor",
            event_data={
                "step_label": step_label,
                "outcome": outcome,
                "reason": reason,
            },
            conversation_id=conversation_id,
        )

    async def log_completion_prompt(
        self,
        trace_id: str,
        prompt_history: list[Any],
        conversation_id: str | None = None,
    ) -> None:
        """Log the full prompt sent to completion LLM."""
        await self.log_event(
            trace_id=trace_id,
            event_type="completion_prompt",
            event_data={
                "message_count": len(prompt_history),
                "messages": [
                    {
                        "index": i,
                        "role": m.role,
                        "content": (m.content or "")[:1000],
                        "content_length": len(m.content or ""),
                    }
                    for i, m in enumerate(prompt_history)
                ],
            },
            conversation_id=conversation_id,
        )

    async def log_completion_response(
        self,
        trace_id: str,
        response: str,
        model: str | None = None,
        conversation_id: str | None = None,
    ) -> None:
        """Log completion response."""
        await self.log_event(
            trace_id=trace_id,
            event_type="completion_response",
            event_data={
                "response": response[:2000],
                "response_length": len(response),
                "model": model,
            },
            conversation_id=conversation_id,
        )

    async def get_logs(
        self,
        trace_id: str | None = None,
        conversation_id: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[DebugLog]:
        """Retrieve debug logs with optional filters."""
        stmt = select(DebugLog).order_by(DebugLog.created_at.desc())

        if trace_id:
            stmt = stmt.where(DebugLog.trace_id == trace_id)
        if conversation_id:
            stmt = stmt.where(DebugLog.conversation_id == conversation_id)
        if event_type:
            stmt = stmt.where(DebugLog.event_type == event_type)

        stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def cleanup_old_logs(self, retention_hours: int = 24) -> int:
        """Delete debug logs older than retention period.

        Returns number of deleted rows.
        """
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=retention_hours)
        stmt = delete(DebugLog).where(DebugLog.created_at < cutoff)
        result = await self._session.execute(stmt)
        await self._session.commit()
        # CursorResult has rowcount, cast for type safety
        deleted = getattr(result, "rowcount", 0) or 0
        if deleted > 0:
            LOGGER.info("Cleaned up %d old debug logs", deleted)
        return deleted


def _truncate_data(data: dict[str, Any], max_length: int = 200) -> str:
    """Truncate data for log display."""
    s = str(data)
    if len(s) > max_length:
        return s[:max_length] + "..."
    return s


def _sanitize_args(args: dict[str, Any]) -> dict[str, Any]:
    """Remove sensitive data from args for logging."""
    sensitive_keys = {"password", "token", "secret", "key", "credential"}
    sanitized = {}
    for k, v in args.items():
        if any(sk in k.lower() for sk in sensitive_keys):
            sanitized[k] = "***REDACTED***"
        else:
            sanitized[k] = v
    return sanitized


def invalidate_cache() -> None:
    """Invalidate the debug enabled cache.

    Call this when debug setting is changed externally.
    """
    global _debug_enabled_cache, _cache_timestamp
    _debug_enabled_cache = None
    _cache_timestamp = None
