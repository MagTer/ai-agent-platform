"""DEPRECATED: Debug logging service moved to core.observability.debug_logger.

This module is kept for backward compatibility.
All new code should import from core.observability.debug_logger instead.
"""

from __future__ import annotations

# Re-export everything from the new location
from core.observability.debug_logger import (
    DEBUG_LOGS_PATH,
    DebugLogger,
    configure_debug_log_handler,
    read_debug_logs,
)

__all__ = [
    "DebugLogger",
    "configure_debug_log_handler",
    "read_debug_logs",
    "DEBUG_LOGS_PATH",
]
