"""OTel-based debug logger for verbose platform tracing.

Replaces the DB-backed DebugLog model with JSONL file storage.
Debug events are emitted as structured log records at DEBUG level,
written to data/debug_logs.jsonl with automatic rotation.

When OTEL_EXPORTER_OTLP_ENDPOINT is configured, debug logs also
flow to the external collector via the LoggerProvider bridge.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from opentelemetry import trace
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import SystemConfig

# Dedicated logger for debug events (separate from root logger)
_debug_log = logging.getLogger("agent.debug")

# Path for debug log storage
DEBUG_LOGS_PATH = Path("data/debug_logs.jsonl")

# Toggle cache with TTL
_debug_enabled_cache: tuple[bool, float] | None = None
_DEBUG_CACHE_TTL = 30.0  # seconds


def configure_debug_log_handler(
    log_path: str | Path = DEBUG_LOGS_PATH,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 3,
) -> None:
    """Set up rotating file handler for debug logs.

    Args:
        log_path: Path to the debug log file.
        max_bytes: Maximum file size before rotation.
        backup_count: Number of backup files to keep.
    """
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Create custom formatter that outputs raw JSON (no wrapper)
    class RawJsonFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            # The message is already a JSON string from log_event
            return record.getMessage()

    handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(RawJsonFormatter())
    _debug_log.addHandler(handler)
    _debug_log.setLevel(logging.DEBUG)
    _debug_log.propagate = False  # Don't propagate to root logger


def _sanitize_args(args: dict[str, Any] | Any) -> dict[str, Any]:
    """Redact sensitive keys from tool arguments or event data.

    Args:
        args: Dictionary of arguments or any value.

    Returns:
        Sanitized dict with sensitive values redacted.
    """
    if not isinstance(args, dict):
        return {}

    sensitive_keys = {"password", "token", "secret", "key", "credential", "api_key"}
    sanitized: dict[str, Any] = {}
    for k, v in args.items():
        if any(sk in k.lower() for sk in sensitive_keys):
            sanitized[k] = "***REDACTED***"
        elif isinstance(v, dict):
            sanitized[k] = _sanitize_args(v)
        else:
            sanitized[k] = v
    return sanitized


async def read_debug_logs(
    trace_id: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Read debug logs from JSONL file with optional filters.

    Args:
        trace_id: Filter by trace ID.
        event_type: Filter by event type.
        limit: Maximum number of entries to return.

    Returns:
        List of debug log entries (newest first).
    """
    log_path = DEBUG_LOGS_PATH
    if not log_path.exists():
        return []

    # Read file in background thread to avoid blocking
    lines = await asyncio.to_thread(_read_lines, log_path)

    results = []
    for line in reversed(lines):  # newest first
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        if trace_id and entry.get("trace_id") != trace_id:
            continue
        if event_type and entry.get("event_type") != event_type:
            continue

        results.append(entry)
        if len(results) >= limit:
            break

    return results


def _read_lines(path: Path) -> list[str]:
    """Read all lines from a file (blocking I/O).

    Args:
        path: Path to the file.

    Returns:
        List of lines.
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            return f.readlines()
    except Exception:
        return []


class DebugLogger:
    """Emits debug events as OTel-correlated structured log records.

    Replaces the DB-backed DebugLogger. Events are written to
    data/debug_logs.jsonl with automatic rotation and optional
    OTLP export when a collector is configured.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the debug logger.

        Args:
            session: Database session (needed for SystemConfig toggle reads).
        """
        self._session = session

    async def is_enabled(self) -> bool:
        """Check if debug logging is enabled (with caching).

        Returns:
            True if debug logging is enabled, False otherwise.
        """
        global _debug_enabled_cache

        # Check cache
        now = time.time()
        if _debug_enabled_cache is not None:
            cached_value, cached_time = _debug_enabled_cache
            if now - cached_time < _DEBUG_CACHE_TTL:
                return cached_value

        # Query database
        stmt = select(SystemConfig).where(SystemConfig.key == "debug_enabled")
        result = await self._session.execute(stmt)
        config = result.scalar_one_or_none()

        enabled = config.value == "true" if config else False
        _debug_enabled_cache = (enabled, now)
        return enabled

    async def log_event(
        self,
        trace_id: str,
        event_type: str,
        event_data: dict[str, Any],
        conversation_id: str | None = None,
    ) -> None:
        """Log a debug event to JSONL file and OTel span attributes.

        Args:
            trace_id: Trace ID for correlation.
            event_type: Type of event (request, plan, tool_call, etc.).
            event_data: Event-specific data.
            conversation_id: Optional conversation ID.
        """
        if not await self.is_enabled():
            return

        # 1. Write structured JSON to debug_logs.jsonl
        record = {
            "trace_id": trace_id,
            "event_type": event_type,
            "conversation_id": conversation_id,
            "event_data": _sanitize_args(event_data),
            "timestamp": datetime.now(UTC).isoformat(),
        }
        _debug_log.debug(json.dumps(record, default=str))

        # 2. Set OTel span attributes (for trace waterfall enrichment)
        span = trace.get_current_span()
        if span.is_recording():
            # Truncate to avoid excessive span attribute sizes
            span.set_attribute(
                f"debug.{event_type}",
                json.dumps(event_data, default=str)[:4000],
            )

    async def log_request(
        self,
        trace_id: str,
        conversation_id: str,
        prompt: str | None = None,
        messages: list[Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log an incoming agent request.

        Args:
            trace_id: Trace ID.
            conversation_id: Conversation ID.
            prompt: Optional user prompt.
            messages: Optional list of messages.
            metadata: Optional request metadata.
        """
        await self.log_event(
            trace_id=trace_id,
            event_type="request",
            event_data={
                "prompt": prompt[:500] if prompt else None,
                "message_count": len(messages) if messages else 0,
                "metadata": metadata or {},
            },
            conversation_id=conversation_id,
        )

    async def log_history(
        self,
        trace_id: str,
        conversation_id: str,
        source: str | None = None,
        messages: list[Any] | None = None,
    ) -> None:
        """Log conversation history retrieval.

        Args:
            trace_id: Trace ID.
            conversation_id: Conversation ID.
            source: Optional source of history (db, cache, etc.).
            messages: Optional list of messages.
        """
        await self.log_event(
            trace_id=trace_id,
            event_type="history",
            event_data={
                "source": source,
                "message_count": len(messages) if messages else 0,
            },
            conversation_id=conversation_id,
        )

    async def log_plan(
        self,
        trace_id: str,
        conversation_id: str,
        plan: Any,
    ) -> None:
        """Log a generated execution plan.

        Args:
            trace_id: Trace ID.
            conversation_id: Conversation ID.
            plan: The plan object (will be converted to string).
        """
        plan_text = str(plan)
        await self.log_event(
            trace_id=trace_id,
            event_type="plan",
            event_data={
                "plan": plan_text[:2000],  # Truncate
                "step_count": plan_text.count("Step") if plan_text else 0,
            },
            conversation_id=conversation_id,
        )

    async def log_tool_call(
        self,
        trace_id: str,
        conversation_id: str,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any] | str | None = None,
        error: str | None = None,
    ) -> None:
        """Log a tool execution.

        Args:
            trace_id: Trace ID.
            conversation_id: Conversation ID.
            tool_name: Name of the tool.
            args: Tool arguments.
            result: Tool result (dict or string, truncated).
            error: Error message if failed.
        """
        result_str = str(result) if result is not None else None
        await self.log_event(
            trace_id=trace_id,
            event_type="tool_call",
            event_data={
                "tool_name": tool_name,
                "args": _sanitize_args(args),
                "result": result_str[:1000] if result_str else None,
                "error": error,
            },
            conversation_id=conversation_id,
        )

    async def log_skill_step(
        self,
        trace_id: str,
        conversation_id: str,
        skill_name: str,
        step_label: str,
        outcome: str,
    ) -> None:
        """Log a skill step execution.

        Args:
            trace_id: Trace ID.
            conversation_id: Conversation ID.
            skill_name: Name of the skill.
            step_label: Step label.
            outcome: Outcome (SUCCESS, RETRY, REPLAN, ABORT).
        """
        await self.log_event(
            trace_id=trace_id,
            event_type="skill_step",
            event_data={
                "skill_name": skill_name,
                "step_label": step_label,
                "outcome": outcome,
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
        """Log a supervisor decision.

        Args:
            trace_id: Trace ID.
            step_label: Step label being reviewed.
            outcome: Outcome (SUCCESS, RETRY, REPLAN, ABORT).
            reason: Reason for the decision.
            conversation_id: Optional conversation ID.
        """
        await self.log_event(
            trace_id=trace_id,
            event_type="supervisor",
            event_data={
                "step_label": step_label,
                "outcome": outcome,
                "reason": reason[:500],  # Truncate
            },
            conversation_id=conversation_id,
        )

    async def log_completion_prompt(
        self,
        trace_id: str,
        conversation_id: str,
        prompt_history: list[Any] | None = None,
    ) -> None:
        """Log an LLM completion prompt.

        Args:
            trace_id: Trace ID.
            conversation_id: Conversation ID.
            prompt_history: The prompt history sent to the LLM.
        """
        prompt_str = str(prompt_history) if prompt_history else ""
        await self.log_event(
            trace_id=trace_id,
            event_type="completion_prompt",
            event_data={
                "prompt": prompt_str[:2000],  # Truncate
                "message_count": len(prompt_history) if prompt_history else 0,
            },
            conversation_id=conversation_id,
        )

    async def log_completion_response(
        self,
        trace_id: str,
        conversation_id: str,
        response: str,
        model: str | None = None,
    ) -> None:
        """Log an LLM completion response.

        Args:
            trace_id: Trace ID.
            conversation_id: Conversation ID.
            response: The LLM response.
            model: Optional model name.
        """
        await self.log_event(
            trace_id=trace_id,
            event_type="completion_response",
            event_data={
                "response": response[:2000],  # Truncate
                "model": model,
            },
            conversation_id=conversation_id,
        )
