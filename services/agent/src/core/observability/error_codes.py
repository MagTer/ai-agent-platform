"""Standardized error codes for the AI agent platform.

This module defines structured error codes that:
1. Are machine-parseable for AI agent self-diagnosis
2. Include severity levels for prioritization
3. Provide recovery hints for self-healing
"""

from __future__ import annotations

from enum import Enum
from typing import NamedTuple


class ErrorSeverity(str, Enum):
    """Severity levels for errors."""

    CRITICAL = "critical"  # System unusable, requires immediate attention
    ERROR = "error"  # Operation failed, but system continues
    WARNING = "warning"  # Degraded functionality, non-blocking
    INFO = "info"  # Informational, no action needed


class ErrorInfo(NamedTuple):
    """Structured information about an error code."""

    code: str
    severity: ErrorSeverity
    category: str
    description: str
    recovery_hint: str


class ErrorCode(str, Enum):
    """Standardized error codes for the agent platform.

    Format: CATEGORY_SPECIFIC_ERROR
    Categories:
    - TOOL: Tool execution errors
    - LLM: LLM provider errors
    - DB: Database errors
    - NET: Network/connectivity errors
    - CONFIG: Configuration errors
    - RAG: Vector store/retrieval errors
    - SKILL: Skill execution errors
    """

    # ---- Tool Errors ----
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    TOOL_INVALID_ARGS = "TOOL_INVALID_ARGS"
    TOOL_PERMISSION_DENIED = "TOOL_PERMISSION_DENIED"

    # ---- LLM Errors ----
    LLM_CONNECTION_FAILED = "LLM_CONNECTION_FAILED"
    LLM_RATE_LIMITED = "LLM_RATE_LIMITED"
    LLM_CONTEXT_OVERFLOW = "LLM_CONTEXT_OVERFLOW"
    LLM_INVALID_RESPONSE = "LLM_INVALID_RESPONSE"
    LLM_MODEL_NOT_FOUND = "LLM_MODEL_NOT_FOUND"
    LLM_AUTH_FAILED = "LLM_AUTH_FAILED"

    # ---- Database Errors ----
    DB_CONNECTION_FAILED = "DB_CONNECTION_FAILED"
    DB_QUERY_FAILED = "DB_QUERY_FAILED"
    DB_TRANSACTION_FAILED = "DB_TRANSACTION_FAILED"
    DB_NOT_FOUND = "DB_NOT_FOUND"

    # ---- Network Errors ----
    NET_CONNECTION_REFUSED = "NET_CONNECTION_REFUSED"
    NET_TIMEOUT = "NET_TIMEOUT"
    NET_DNS_FAILED = "NET_DNS_FAILED"
    NET_SSL_ERROR = "NET_SSL_ERROR"

    # ---- Configuration Errors ----
    CONFIG_MISSING = "CONFIG_MISSING"
    CONFIG_INVALID = "CONFIG_INVALID"
    CONFIG_PERMISSION = "CONFIG_PERMISSION"

    # ---- RAG/Vector Store Errors ----
    RAG_QDRANT_UNAVAILABLE = "RAG_QDRANT_UNAVAILABLE"
    RAG_COLLECTION_NOT_FOUND = "RAG_COLLECTION_NOT_FOUND"
    RAG_EMBEDDING_FAILED = "RAG_EMBEDDING_FAILED"
    RAG_SEARCH_FAILED = "RAG_SEARCH_FAILED"

    # ---- Skill Errors ----
    SKILL_NOT_FOUND = "SKILL_NOT_FOUND"
    SKILL_PARSE_ERROR = "SKILL_PARSE_ERROR"
    SKILL_EXECUTION_FAILED = "SKILL_EXECUTION_FAILED"
    SKILL_MAX_TURNS = "SKILL_MAX_TURNS"

    # ---- Auth Errors ----
    AUTH_TOKEN_EXPIRED = "AUTH_TOKEN_EXPIRED"

    # ---- Generic Errors ----
    UNKNOWN = "UNKNOWN"


# Error code metadata for AI consumption
ERROR_METADATA: dict[ErrorCode, ErrorInfo] = {
    # Tool Errors
    ErrorCode.TOOL_NOT_FOUND: ErrorInfo(
        code="TOOL_NOT_FOUND",
        severity=ErrorSeverity.ERROR,
        category="tool",
        description="Requested tool does not exist in the registry",
        recovery_hint="Check tool name spelling or list available tools with list_tools()",
    ),
    ErrorCode.TOOL_EXECUTION_FAILED: ErrorInfo(
        code="TOOL_EXECUTION_FAILED",
        severity=ErrorSeverity.ERROR,
        category="tool",
        description="Tool execution raised an exception",
        recovery_hint="Check tool arguments and retry with corrected parameters",
    ),
    ErrorCode.TOOL_TIMEOUT: ErrorInfo(
        code="TOOL_TIMEOUT",
        severity=ErrorSeverity.WARNING,
        category="tool",
        description="Tool execution exceeded time limit",
        recovery_hint="Simplify the request or increase timeout if possible",
    ),
    ErrorCode.TOOL_INVALID_ARGS: ErrorInfo(
        code="TOOL_INVALID_ARGS",
        severity=ErrorSeverity.ERROR,
        category="tool",
        description="Invalid arguments provided to tool",
        recovery_hint="Check required parameters and their types",
    ),
    ErrorCode.TOOL_PERMISSION_DENIED: ErrorInfo(
        code="TOOL_PERMISSION_DENIED",
        severity=ErrorSeverity.ERROR,
        category="tool",
        description="Insufficient permissions to execute tool",
        recovery_hint="Verify file/directory permissions or choose a different path",
    ),
    # LLM Errors
    ErrorCode.LLM_CONNECTION_FAILED: ErrorInfo(
        code="LLM_CONNECTION_FAILED",
        severity=ErrorSeverity.CRITICAL,
        category="llm",
        description="Cannot connect to LLM provider",
        recovery_hint="Check LITELLM_API_BASE setting and network connectivity",
    ),
    ErrorCode.LLM_RATE_LIMITED: ErrorInfo(
        code="LLM_RATE_LIMITED",
        severity=ErrorSeverity.WARNING,
        category="llm",
        description="LLM provider rate limit exceeded",
        recovery_hint="Wait and retry with exponential backoff",
    ),
    ErrorCode.LLM_CONTEXT_OVERFLOW: ErrorInfo(
        code="LLM_CONTEXT_OVERFLOW",
        severity=ErrorSeverity.ERROR,
        category="llm",
        description="Input exceeds model context window",
        recovery_hint="Reduce message history or summarize earlier context",
    ),
    ErrorCode.LLM_INVALID_RESPONSE: ErrorInfo(
        code="LLM_INVALID_RESPONSE",
        severity=ErrorSeverity.ERROR,
        category="llm",
        description="LLM returned unparseable response",
        recovery_hint="Retry the request; if persistent, check model configuration",
    ),
    ErrorCode.LLM_MODEL_NOT_FOUND: ErrorInfo(
        code="LLM_MODEL_NOT_FOUND",
        severity=ErrorSeverity.ERROR,
        category="llm",
        description="Requested model not available",
        recovery_hint="Check model name or use /models endpoint to list available models",
    ),
    ErrorCode.LLM_AUTH_FAILED: ErrorInfo(
        code="LLM_AUTH_FAILED",
        severity=ErrorSeverity.CRITICAL,
        category="llm",
        description="Authentication to LLM provider failed",
        recovery_hint="Check API key configuration in environment variables",
    ),
    # Database Errors
    ErrorCode.DB_CONNECTION_FAILED: ErrorInfo(
        code="DB_CONNECTION_FAILED",
        severity=ErrorSeverity.CRITICAL,
        category="database",
        description="Cannot connect to PostgreSQL database",
        recovery_hint="Check DATABASE_URL setting and database server status",
    ),
    ErrorCode.DB_QUERY_FAILED: ErrorInfo(
        code="DB_QUERY_FAILED",
        severity=ErrorSeverity.ERROR,
        category="database",
        description="Database query execution failed",
        recovery_hint="Check query syntax and database logs",
    ),
    ErrorCode.DB_TRANSACTION_FAILED: ErrorInfo(
        code="DB_TRANSACTION_FAILED",
        severity=ErrorSeverity.ERROR,
        category="database",
        description="Database transaction could not be committed",
        recovery_hint="Check for constraint violations or deadlocks",
    ),
    ErrorCode.DB_NOT_FOUND: ErrorInfo(
        code="DB_NOT_FOUND",
        severity=ErrorSeverity.WARNING,
        category="database",
        description="Requested record not found",
        recovery_hint="Verify the ID or create the record if it should exist",
    ),
    # Network Errors
    ErrorCode.NET_CONNECTION_REFUSED: ErrorInfo(
        code="NET_CONNECTION_REFUSED",
        severity=ErrorSeverity.ERROR,
        category="network",
        description="Remote service refused connection",
        recovery_hint="Check if the service is running and the port is correct",
    ),
    ErrorCode.NET_TIMEOUT: ErrorInfo(
        code="NET_TIMEOUT",
        severity=ErrorSeverity.WARNING,
        category="network",
        description="Network request timed out",
        recovery_hint="Retry the request or check network connectivity",
    ),
    ErrorCode.NET_DNS_FAILED: ErrorInfo(
        code="NET_DNS_FAILED",
        severity=ErrorSeverity.ERROR,
        category="network",
        description="DNS resolution failed",
        recovery_hint="Check hostname spelling or DNS server configuration",
    ),
    ErrorCode.NET_SSL_ERROR: ErrorInfo(
        code="NET_SSL_ERROR",
        severity=ErrorSeverity.ERROR,
        category="network",
        description="SSL/TLS handshake failed",
        recovery_hint="Check certificate validity or disable SSL verification if internal",
    ),
    # Configuration Errors
    ErrorCode.CONFIG_MISSING: ErrorInfo(
        code="CONFIG_MISSING",
        severity=ErrorSeverity.CRITICAL,
        category="config",
        description="Required configuration value not set",
        recovery_hint="Set the missing environment variable or config file value",
    ),
    ErrorCode.CONFIG_INVALID: ErrorInfo(
        code="CONFIG_INVALID",
        severity=ErrorSeverity.CRITICAL,
        category="config",
        description="Configuration value has invalid format",
        recovery_hint="Check the expected format for this configuration",
    ),
    ErrorCode.CONFIG_PERMISSION: ErrorInfo(
        code="CONFIG_PERMISSION",
        severity=ErrorSeverity.ERROR,
        category="config",
        description="Cannot read configuration file due to permissions",
        recovery_hint="Check file permissions on configuration files",
    ),
    # RAG Errors
    ErrorCode.RAG_QDRANT_UNAVAILABLE: ErrorInfo(
        code="RAG_QDRANT_UNAVAILABLE",
        severity=ErrorSeverity.CRITICAL,
        category="rag",
        description="Cannot connect to Qdrant vector database",
        recovery_hint="Check QDRANT_URL setting and Qdrant service status",
    ),
    ErrorCode.RAG_COLLECTION_NOT_FOUND: ErrorInfo(
        code="RAG_COLLECTION_NOT_FOUND",
        severity=ErrorSeverity.WARNING,
        category="rag",
        description="Vector collection does not exist",
        recovery_hint="Run indexing to create the collection or check collection name",
    ),
    ErrorCode.RAG_EMBEDDING_FAILED: ErrorInfo(
        code="RAG_EMBEDDING_FAILED",
        severity=ErrorSeverity.ERROR,
        category="rag",
        description="Text embedding generation failed",
        recovery_hint="Check embedder service status and input text validity",
    ),
    ErrorCode.RAG_SEARCH_FAILED: ErrorInfo(
        code="RAG_SEARCH_FAILED",
        severity=ErrorSeverity.ERROR,
        category="rag",
        description="Vector similarity search failed",
        recovery_hint="Check collection exists and query is valid",
    ),
    # Skill Errors
    ErrorCode.SKILL_NOT_FOUND: ErrorInfo(
        code="SKILL_NOT_FOUND",
        severity=ErrorSeverity.ERROR,
        category="skill",
        description="Skill definition file not found",
        recovery_hint="Check skill name or list available skills",
    ),
    ErrorCode.SKILL_PARSE_ERROR: ErrorInfo(
        code="SKILL_PARSE_ERROR",
        severity=ErrorSeverity.ERROR,
        category="skill",
        description="Skill definition could not be parsed",
        recovery_hint="Check skill file YAML/markdown syntax",
    ),
    ErrorCode.SKILL_EXECUTION_FAILED: ErrorInfo(
        code="SKILL_EXECUTION_FAILED",
        severity=ErrorSeverity.ERROR,
        category="skill",
        description="Skill worker execution failed",
        recovery_hint="Check skill tool dependencies and LLM availability",
    ),
    ErrorCode.SKILL_MAX_TURNS: ErrorInfo(
        code="SKILL_MAX_TURNS",
        severity=ErrorSeverity.WARNING,
        category="skill",
        description="Skill reached maximum turn limit without completing",
        recovery_hint="Simplify the goal or increase max_turns if appropriate",
    ),
    # Auth Errors
    ErrorCode.AUTH_TOKEN_EXPIRED: ErrorInfo(
        code="AUTH_TOKEN_EXPIRED",
        severity=ErrorSeverity.WARNING,
        category="auth",
        description="OAuth token has expired and needs re-authorization",
        recovery_hint="Re-authorize the integration via Admin Portal -> Context -> OAuth",
    ),
    # Generic
    ErrorCode.UNKNOWN: ErrorInfo(
        code="UNKNOWN",
        severity=ErrorSeverity.ERROR,
        category="unknown",
        description="An unexpected error occurred",
        recovery_hint="Check logs for detailed error message",
    ),
}


def get_error_info(code: ErrorCode) -> ErrorInfo:
    """Get metadata for an error code."""
    return ERROR_METADATA.get(code, ERROR_METADATA[ErrorCode.UNKNOWN])


def classify_exception(exc: Exception) -> ErrorCode:
    """Classify an exception into a standardized error code.

    Args:
        exc: The exception to classify.

    Returns:
        The most appropriate ErrorCode.
    """
    exc_str = str(exc).lower()
    exc_type = type(exc).__name__.lower()

    # Check exception type directly
    if isinstance(exc, TimeoutError):
        return ErrorCode.NET_TIMEOUT
    if isinstance(exc, ConnectionRefusedError):
        return ErrorCode.NET_CONNECTION_REFUSED
    if isinstance(exc, PermissionError):
        return ErrorCode.TOOL_PERMISSION_DENIED

    # Connection errors
    if "connection refused" in exc_str or "connectionrefused" in exc_type:
        return ErrorCode.NET_CONNECTION_REFUSED
    if "timeout" in exc_str or "timed out" in exc_str:
        return ErrorCode.NET_TIMEOUT
    if "dns" in exc_str or "getaddrinfo" in exc_str:
        return ErrorCode.NET_DNS_FAILED
    if "ssl" in exc_str or "certificate" in exc_str:
        return ErrorCode.NET_SSL_ERROR

    # Rate limit errors (check before other LLM errors)
    if "rate" in exc_str and "limit" in exc_str:
        return ErrorCode.LLM_RATE_LIMITED

    # Database errors
    if "asyncpg" in exc_type or "postgresql" in exc_str or "database" in exc_str:
        if "connect" in exc_str:
            return ErrorCode.DB_CONNECTION_FAILED
        return ErrorCode.DB_QUERY_FAILED

    # LLM errors
    if "litellm" in exc_type or "openai" in exc_type or "openai" in exc_str:
        if "context" in exc_str and "length" in exc_str:
            return ErrorCode.LLM_CONTEXT_OVERFLOW
        if "auth" in exc_str or "unauthorized" in exc_str:
            return ErrorCode.LLM_AUTH_FAILED
        if "model" in exc_str and "not found" in exc_str:
            return ErrorCode.LLM_MODEL_NOT_FOUND
        return ErrorCode.LLM_CONNECTION_FAILED

    # Qdrant errors
    if "qdrant" in exc_str:
        if "collection" in exc_str and "not found" in exc_str:
            return ErrorCode.RAG_COLLECTION_NOT_FOUND
        return ErrorCode.RAG_QDRANT_UNAVAILABLE

    # File/permission errors
    if "permission" in exc_str or "access denied" in exc_str:
        return ErrorCode.TOOL_PERMISSION_DENIED
    if "not found" in exc_str and "file" in exc_str:
        return ErrorCode.TOOL_NOT_FOUND

    return ErrorCode.UNKNOWN


def format_error_for_ai(code: ErrorCode, context: str | None = None) -> dict[str, str]:
    """Format an error for AI agent consumption.

    Args:
        code: The error code.
        context: Optional additional context.

    Returns:
        Machine-readable error dictionary.
    """
    info = get_error_info(code)
    result = {
        "error_code": info.code,
        "severity": info.severity.value,
        "category": info.category,
        "description": info.description,
        "recovery_hint": info.recovery_hint,
    }
    if context:
        result["context"] = context
    return result


__all__ = [
    "ErrorCode",
    "ErrorInfo",
    "ErrorSeverity",
    "classify_exception",
    "format_error_for_ai",
    "get_error_info",
]
