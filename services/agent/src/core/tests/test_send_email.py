"""Tests for send_email tool."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.protocols.email import EmailResult
from core.tools.send_email import SendEmailTool


@pytest.fixture
def email_tool() -> SendEmailTool:
    """Create SendEmailTool instance."""
    return SendEmailTool()


@pytest.fixture
def mock_email_service() -> MagicMock:
    """Create mock email service."""
    service = MagicMock()
    service.is_configured.return_value = True
    service.send = AsyncMock(return_value=EmailResult(success=True, message_id="test-123"))
    return service


class TestSendEmailTool:
    """Tests for SendEmailTool."""

    async def test_send_email_success(
        self, email_tool: SendEmailTool, mock_email_service: MagicMock
    ) -> None:
        """Test successful email send."""
        with patch(
            "core.tools.send_email.get_email_service_optional",
            return_value=mock_email_service,
        ):
            result = await email_tool.run(
                subject="Test Subject",
                body="Test body content",
                user_email="test@example.com",
            )

        assert "successfully" in result
        assert "test@example.com" in result
        mock_email_service.send.assert_called_once()

    async def test_send_email_no_service(self, email_tool: SendEmailTool) -> None:
        """Test error when email service not configured."""
        with patch("core.tools.send_email.get_email_service_optional", return_value=None):
            result = await email_tool.run(
                subject="Test",
                body="Test body",
                user_email="test@example.com",
            )

        assert "not configured" in result

    async def test_send_email_no_user_email(
        self, email_tool: SendEmailTool, mock_email_service: MagicMock
    ) -> None:
        """Test error when user email not provided."""
        with patch(
            "core.tools.send_email.get_email_service_optional",
            return_value=mock_email_service,
        ):
            result = await email_tool.run(
                subject="Test",
                body="Test body",
                user_email=None,
            )

        assert "Unable to determine" in result

    async def test_send_email_invalid_email_format(
        self, email_tool: SendEmailTool, mock_email_service: MagicMock
    ) -> None:
        """Test error for invalid email format."""
        with patch(
            "core.tools.send_email.get_email_service_optional",
            return_value=mock_email_service,
        ):
            result = await email_tool.run(
                subject="Test",
                body="Test body",
                user_email="not-an-email",
            )

        assert "Invalid email format" in result

    async def test_send_email_empty_subject(
        self, email_tool: SendEmailTool, mock_email_service: MagicMock
    ) -> None:
        """Test error for empty subject."""
        with patch(
            "core.tools.send_email.get_email_service_optional",
            return_value=mock_email_service,
        ):
            result = await email_tool.run(
                subject="",
                body="Test body",
                user_email="test@example.com",
            )

        assert "subject is required" in result

    async def test_send_email_empty_body(
        self, email_tool: SendEmailTool, mock_email_service: MagicMock
    ) -> None:
        """Test error for empty body."""
        with patch(
            "core.tools.send_email.get_email_service_optional",
            return_value=mock_email_service,
        ):
            result = await email_tool.run(
                subject="Test",
                body="",
                user_email="test@example.com",
            )

        assert "body is required" in result

    async def test_markdown_to_html_headers(self, email_tool: SendEmailTool) -> None:
        """Test markdown header conversion."""
        text = "# Heading 1\n## Heading 2\n### Heading 3"
        html = email_tool._markdown_to_html(text)

        assert "<h2>" in html
        assert "<h3>" in html
        assert "<h4>" in html

    async def test_markdown_to_html_bold_italic(self, email_tool: SendEmailTool) -> None:
        """Test markdown bold and italic conversion."""
        text = "This is **bold** and *italic*"
        html = email_tool._markdown_to_html(text)

        assert "<strong>bold</strong>" in html
        assert "<em>italic</em>" in html

    async def test_send_email_service_failure(
        self, email_tool: SendEmailTool, mock_email_service: MagicMock
    ) -> None:
        """Test handling of email service failure."""
        mock_email_service.send = AsyncMock(
            return_value=EmailResult(success=False, error="SMTP connection failed")
        )

        with patch(
            "core.tools.send_email.get_email_service_optional",
            return_value=mock_email_service,
        ):
            result = await email_tool.run(
                subject="Test",
                body="Test body",
                user_email="test@example.com",
            )

        assert "Failed to send" in result
        assert "SMTP connection failed" in result
