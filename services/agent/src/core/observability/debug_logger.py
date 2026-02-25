"""OTel-based debug logger for verbose platform tracing.

Debug events are emitted as OTel span events via span.add_event().
The _FileSpanExporter in tracing.py captures these events alongside span
attributes and writes them to spans.jsonl.

The Diagnostic API reads debug events from spans.jsonl instead of the
old debug_logs.jsonl file.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from opentelemetry import trace
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import SystemConfig

# Toggle cache with TTL
_debug_enabled_cache: tuple[bool, float] | None = None
_DEBUG_CACHE_TTL = 30.0  # seconds

# Skill quality evaluation toggle cache
_quality_eval_enabled_cache: tuple[bool, float] | None = None
_QUALITY_EVAL_CACHE_TTL = 30.0  # seconds


def invalidate_quality_eval_cache() -> None:
    """Invalidate the skill quality evaluation enabled cache."""
    global _quality_eval_enabled_cache
    _quality_eval_enabled_cache = None


async def is_quality_eval_enabled(session: AsyncSession) -> bool:
    """Check if skill quality evaluation is enabled (with caching).

    Both debug_enabled AND skill_quality_evaluation_enabled must be true.

    Args:
        session: Database session.

    Returns:
        True if quality evaluation is enabled, False otherwise.
    """
    global _quality_eval_enabled_cache

    now = time.time()
    if _quality_eval_enabled_cache is not None:
        cached_value, cached_time = _quality_eval_enabled_cache
        if now - cached_time < _QUALITY_EVAL_CACHE_TTL:
            return cached_value

    # Check debug_enabled first (prerequisite)
    debug_logger = DebugLogger(session)
    if not await debug_logger.is_enabled():
        _quality_eval_enabled_cache = (False, now)
        return False

    # Check quality eval toggle
    stmt = select(SystemConfig).where(SystemConfig.key == "skill_quality_evaluation_enabled")
    result = await session.execute(stmt)
    config = result.scalar_one_or_none()

    enabled = config.value == "true" if config else False
    _quality_eval_enabled_cache = (enabled, now)
    return enabled


def invalidate_debug_cache() -> None:
    """Invalidate the debug enabled cache.

    Forces the next is_enabled() check to re-read from the database.
    This should be called after updating the SystemConfig.debug_enabled value.
    """
    global _debug_enabled_cache
    _debug_enabled_cache = None


def configure_debug_log_handler(
    log_path: str | Path = Path("data/debug_logs.jsonl"),
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 3,
) -> None:
    """No-op. Debug logs are now emitted as OTel span events.

    Kept for backward compatibility during migration.

    Args:
        log_path: Ignored. Previously the path to the debug log file.
        max_bytes: Ignored. Previously the maximum file size before rotation.
        backup_count: Ignored. Previously the number of backup files to keep.
    """
    logger_mod = logging.getLogger(__name__)
    logger_mod.info("configure_debug_log_handler is deprecated; debug events use OTel span events")


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


def _get_spans_path() -> Path:
    """Get the path to the spans JSONL file."""
    try:
        from core.runtime.config import get_settings

        settings = get_settings()
        return Path(str(settings.trace_span_log_path or "data/spans.jsonl"))
    except Exception:
        return Path("data/spans.jsonl")


def _extract_debug_events_from_spans_file(
    spans_path: Path,
    trace_id: str | None,
    event_type: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Extract debug events from span events in spans.jsonl (blocking I/O).

    Each span record in spans.jsonl has an "events" list. Debug events
    have names starting with "debug." and contain structured attributes.

    Args:
        spans_path: Path to spans.jsonl file.
        trace_id: Optional trace_id filter.
        event_type: Optional event_type filter (e.g., "tool_call").
        limit: Maximum results to return.

    Returns:
        List of debug event dicts, newest first.
    """
    if not spans_path.exists():
        return []

    results: list[dict[str, Any]] = []

    try:
        with spans_path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return []

    # Process in reverse (newest spans first)
    for line in reversed(lines):
        if not line.strip():
            continue
        if len(results) >= limit:
            break

        try:
            span_record = json.loads(line)
        except json.JSONDecodeError:
            continue

        span_trace_id = span_record.get("context", {}).get("trace_id", "")

        # If filtering by trace_id and this span doesn't match, skip
        if trace_id and span_trace_id != trace_id:
            continue

        events = span_record.get("events", [])
        for evt in reversed(events):  # newest events first within span
            evt_name = evt.get("name", "")
            if not evt_name.startswith("debug."):
                continue

            evt_attrs = evt.get("attributes", {})
            evt_event_type = evt_attrs.get("debug.event_type", "")

            # Apply event_type filter
            if event_type and evt_event_type != event_type:
                continue

            # Reconstruct the legacy debug log format for API compatibility
            event_data_str = evt_attrs.get("debug.event_data", "{}")
            try:
                event_data = json.loads(event_data_str)
            except (json.JSONDecodeError, TypeError):
                event_data = {}

            result_entry = {
                "trace_id": evt_attrs.get("debug.trace_id", span_trace_id),
                "event_type": evt_event_type,
                "conversation_id": evt_attrs.get("debug.conversation_id"),
                "event_data": event_data,
                "timestamp": evt.get("timestamp", span_record.get("start_time")),
            }
            results.append(result_entry)

            if len(results) >= limit:
                break

    return results


async def _read_debug_events_from_spans(
    trace_id: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Async wrapper for reading debug events from spans.jsonl.

    Args:
        trace_id: Filter by trace ID.
        event_type: Filter by event type.
        limit: Maximum number of entries to return.

    Returns:
        List of debug log entries (newest first).
    """
    spans_path = _get_spans_path()
    return await asyncio.to_thread(
        _extract_debug_events_from_spans_file,
        spans_path,
        trace_id,
        event_type,
        limit,
    )


async def read_debug_logs(
    trace_id: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Read debug events from span events in spans.jsonl.

    Args:
        trace_id: Filter by trace ID.
        event_type: Filter by event type.
        limit: Maximum number of entries to return.

    Returns:
        List of debug log entries (newest first).
    """
    return await _read_debug_events_from_spans(
        trace_id=trace_id, event_type=event_type, limit=limit
    )


def _extract_supervisor_events_for_context(
    spans_path: Path,
    context_id: str,
    since_iso: str,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Extract supervisor REPLAN/ABORT events for a specific context.

    Reads spans.jsonl and filters by:
    1. Span attributes.context_id == context_id
    2. Events with name starting with "debug."
    3. Event attribute debug.event_type == "supervisor"
    4. Event timestamp >= since_iso
    5. Event data outcome in (REPLAN, ABORT)

    Args:
        spans_path: Path to spans.jsonl file.
        context_id: Context UUID string to filter by.
        since_iso: ISO timestamp cutoff (only events after this).
        limit: Maximum results to return.

    Returns:
        List of dicts with keys: trace_id, outcome, reason, step_label, timestamp,
        conversation_id, skill_name
    """
    if not spans_path.exists():
        return []

    results: list[dict[str, Any]] = []

    try:
        with spans_path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return []

    for line in reversed(lines):
        if not line.strip():
            continue
        if len(results) >= limit:
            break

        try:
            span_record = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Filter by context_id (set as root span attribute)
        span_attrs = span_record.get("attributes", {})
        if span_attrs.get("context_id") != context_id:
            continue

        span_trace_id = span_record.get("context", {}).get("trace_id", "")

        events = span_record.get("events", [])
        for evt in reversed(events):
            evt_name = evt.get("name", "")
            if not evt_name.startswith("debug."):
                continue

            evt_attrs = evt.get("attributes", {})
            if evt_attrs.get("debug.event_type") != "supervisor":
                continue

            # Parse event data
            event_data_str = evt_attrs.get("debug.event_data", "{}")
            try:
                event_data = json.loads(event_data_str)
            except (json.JSONDecodeError, TypeError):
                event_data = {}

            outcome = event_data.get("outcome", "")
            if outcome not in ("REPLAN", "ABORT"):
                continue

            # Check timestamp
            evt_ts = evt.get("timestamp", "")
            if evt_ts and evt_ts < since_iso:
                continue

            skill_name_evt = event_data.get("skill_name")

            results.append(
                {
                    "trace_id": span_trace_id,
                    "outcome": outcome,
                    "reason": event_data.get("reason", ""),
                    "step_label": event_data.get("step_label", ""),
                    "timestamp": evt_ts,
                    "conversation_id": evt_attrs.get("debug.conversation_id"),
                    "skill_name": skill_name_evt,
                }
            )

            if len(results) >= limit:
                break

    return results


async def read_supervisor_events_for_context(
    context_id: str,
    since_iso: str,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Async wrapper for reading supervisor failure events for a context.

    Args:
        context_id: Context UUID string.
        since_iso: ISO timestamp cutoff.
        limit: Maximum number of entries.

    Returns:
        List of supervisor failure events (REPLAN/ABORT only).
    """
    spans_path = _get_spans_path()
    return await asyncio.to_thread(
        _extract_supervisor_events_for_context,
        spans_path,
        context_id,
        since_iso,
        limit,
    )


def _count_skill_executions_for_context(
    spans_path: Path,
    context_id: str,
    since_iso: str,
) -> dict[str, dict[str, int]]:
    """Count skill executions by outcome for a context.

    Returns:
        Dict mapping skill_name -> {"total": N, "SUCCESS": N, "REPLAN": N, "ABORT": N, "RETRY": N}
    """
    if not spans_path.exists():
        return {}

    counts: dict[str, dict[str, int]] = {}

    try:
        with spans_path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return {}

    for line in lines:
        if not line.strip():
            continue

        try:
            span_record = json.loads(line)
        except json.JSONDecodeError:
            continue

        span_attrs = span_record.get("attributes", {})
        if span_attrs.get("context_id") != context_id:
            continue

        events = span_record.get("events", [])
        for evt in events:
            evt_name = evt.get("name", "")
            if not evt_name.startswith("debug."):
                continue

            evt_attrs = evt.get("attributes", {})
            if evt_attrs.get("debug.event_type") != "skill_step":
                continue

            evt_ts = evt.get("timestamp", "")
            if evt_ts and evt_ts < since_iso:
                continue

            event_data_str = evt_attrs.get("debug.event_data", "{}")
            try:
                event_data = json.loads(event_data_str)
            except (json.JSONDecodeError, TypeError):
                continue

            skill_name = event_data.get("skill_name", "unknown")
            outcome = event_data.get("outcome", "unknown")

            if skill_name not in counts:
                counts[skill_name] = {"total": 0, "SUCCESS": 0, "REPLAN": 0, "ABORT": 0, "RETRY": 0}

            counts[skill_name]["total"] += 1
            if outcome in counts[skill_name]:
                counts[skill_name][outcome] += 1

    return counts


async def count_skill_executions_for_context(
    context_id: str,
    since_iso: str,
) -> dict[str, dict[str, int]]:
    """Async wrapper for counting skill executions by outcome for a context."""
    spans_path = _get_spans_path()
    return await asyncio.to_thread(
        _count_skill_executions_for_context,
        spans_path,
        context_id,
        since_iso,
    )


class DebugLogger:
    """Emits debug events as OTel span events.

    Events are added to the current active OTel span via span.add_event().
    The _FileSpanExporter captures events alongside span attributes and
    writes them to spans.jsonl (the single source of truth).
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
        """Log a debug event as an OTel span event.

        Args:
            trace_id: Trace ID for correlation.
            event_type: Type of event (request, plan, tool_call, etc.).
            event_data: Event-specific data.
            conversation_id: Optional conversation ID.
        """
        if not await self.is_enabled():
            return

        # Sanitize before attaching to span
        sanitized_data = _sanitize_args(event_data)

        # Build flat attributes dict for OTel event.
        # OTel event attributes must be primitive types (str, int, float, bool)
        # or sequences of primitives. Complex dicts must be JSON-serialized.
        attrs: dict[str, Any] = {
            "debug.trace_id": trace_id,
            "debug.event_type": event_type,
        }
        if conversation_id:
            attrs["debug.conversation_id"] = conversation_id

        # Serialize event_data as JSON string (OTel doesn't support nested dicts)
        attrs["debug.event_data"] = json.dumps(sanitized_data, default=str)[:4000]

        # Add as span event on the current active span
        span = trace.get_current_span()
        if span.is_recording():
            span.add_event(f"debug.{event_type}", attributes=attrs)

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
        skill_name: str | None = None,
    ) -> None:
        """Log a supervisor decision.

        Args:
            trace_id: Trace ID.
            step_label: Step label being reviewed.
            outcome: Outcome (SUCCESS, RETRY, REPLAN, ABORT).
            reason: Reason for the decision.
            conversation_id: Optional conversation ID.
            skill_name: Optional skill name (tool field of the plan step).
        """
        await self.log_event(
            trace_id=trace_id,
            event_type="supervisor",
            event_data={
                "step_label": step_label,
                "outcome": outcome,
                "reason": reason[:500],  # Truncate
                "skill_name": skill_name,
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
