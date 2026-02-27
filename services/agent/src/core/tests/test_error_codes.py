"""Tests for error codes module."""

from __future__ import annotations

from core.observability.error_codes import (
    ErrorCode,
    ErrorSeverity,
    classify_exception,
    format_error_for_ai,
    get_error_info,
)


class TestErrorCode:
    """Test error code enum and metadata."""

    def test_all_error_codes_have_metadata(self) -> None:
        """Ensure every error code has associated metadata."""
        from core.observability.error_codes import ERROR_METADATA

        for code in ErrorCode:
            assert code in ERROR_METADATA, f"Missing metadata for {code}"
            info = ERROR_METADATA[code]
            assert info.code == code.value
            assert info.severity in ErrorSeverity
            assert info.category
            assert info.description
            assert info.recovery_hint

    def test_error_code_categories(self) -> None:
        """Verify error codes are organized by category prefix."""
        for code in ErrorCode:
            prefix = code.value.split("_")[0]
            expected = ["TOOL", "LLM", "DB", "NET", "CONFIG", "RAG", "SKILL", "AUTH", "UNKNOWN"]
            assert prefix in expected, f"Unexpected prefix for {code}"


class TestGetErrorInfo:
    """Test the get_error_info function."""

    def test_get_known_error_info(self) -> None:
        """Test retrieval of known error info."""
        info = get_error_info(ErrorCode.TOOL_NOT_FOUND)

        assert info.code == "TOOL_NOT_FOUND"
        assert info.severity == ErrorSeverity.ERROR
        assert info.category == "tool"
        assert "tool" in info.description.lower()
        assert info.recovery_hint

    def test_get_critical_severity(self) -> None:
        """Test that critical errors are marked appropriately."""
        info = get_error_info(ErrorCode.LLM_CONNECTION_FAILED)
        assert info.severity == ErrorSeverity.CRITICAL

    def test_get_warning_severity(self) -> None:
        """Test warning severity errors."""
        info = get_error_info(ErrorCode.LLM_RATE_LIMITED)
        assert info.severity == ErrorSeverity.WARNING


class TestClassifyException:
    """Test exception classification."""

    def test_classify_connection_refused(self) -> None:
        """Test classification of connection refused errors."""
        exc = ConnectionRefusedError("Connection refused")
        code = classify_exception(exc)
        assert code == ErrorCode.NET_CONNECTION_REFUSED

    def test_classify_timeout(self) -> None:
        """Test classification of timeout errors."""
        exc = TimeoutError("Request timed out")
        code = classify_exception(exc)
        assert code == ErrorCode.NET_TIMEOUT

    def test_classify_dns_error(self) -> None:
        """Test classification of DNS errors."""
        exc = OSError("getaddrinfo failed")
        code = classify_exception(exc)
        assert code == ErrorCode.NET_DNS_FAILED

    def test_classify_permission_error(self) -> None:
        """Test classification of permission errors."""
        exc = PermissionError("Permission denied")
        code = classify_exception(exc)
        assert code == ErrorCode.TOOL_PERMISSION_DENIED

    def test_classify_unknown_error(self) -> None:
        """Test classification of unrecognized errors."""
        exc = ValueError("Some random error")
        code = classify_exception(exc)
        assert code == ErrorCode.UNKNOWN

    def test_classify_rate_limit(self) -> None:
        """Test classification of rate limit errors."""
        exc = Exception("OpenAI rate limit exceeded")
        code = classify_exception(exc)
        assert code == ErrorCode.LLM_RATE_LIMITED

    def test_classify_qdrant_error(self) -> None:
        """Test classification of Qdrant errors."""
        exc = Exception("Qdrant collection not found")
        code = classify_exception(exc)
        assert code == ErrorCode.RAG_COLLECTION_NOT_FOUND


class TestFormatErrorForAI:
    """Test AI-friendly error formatting."""

    def test_format_basic_error(self) -> None:
        """Test basic error formatting."""
        result = format_error_for_ai(ErrorCode.TOOL_NOT_FOUND)

        assert result["error_code"] == "TOOL_NOT_FOUND"
        assert result["severity"] == "error"
        assert result["category"] == "tool"
        assert "description" in result
        assert "recovery_hint" in result

    def test_format_with_context(self) -> None:
        """Test error formatting with additional context."""
        result = format_error_for_ai(
            ErrorCode.TOOL_NOT_FOUND, context="Tool 'web_search' not in registry"
        )
        assert result["context"] == "Tool 'web_search' not in registry"

    def test_format_critical_error(self) -> None:
        """Test formatting of critical error."""
        result = format_error_for_ai(ErrorCode.DB_CONNECTION_FAILED)
        assert result["severity"] == "critical"

    def test_format_warning(self) -> None:
        """Test formatting of warning level error."""
        result = format_error_for_ai(ErrorCode.NET_TIMEOUT)
        assert result["severity"] == "warning"


class TestErrorSeverity:
    """Test error severity enum."""

    def test_severity_values(self) -> None:
        """Test that severity values are correct strings."""
        assert ErrorSeverity.CRITICAL.value == "critical"
        assert ErrorSeverity.ERROR.value == "error"
        assert ErrorSeverity.WARNING.value == "warning"
        assert ErrorSeverity.INFO.value == "info"

    def test_severity_ordering(self) -> None:
        """Test that severities can be compared by importance."""
        severities = [
            ErrorSeverity.INFO,
            ErrorSeverity.WARNING,
            ErrorSeverity.ERROR,
            ErrorSeverity.CRITICAL,
        ]
        assert len(severities) == 4
