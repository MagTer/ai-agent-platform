"""Tests for email service module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.protocols.email import EmailMessage
from modules.email.service import EmailConfig, ResendEmailService


class TestEmailConfig:
    """Tests for EmailConfig dataclass."""

    def test_default_values(self) -> None:
        """Test default configuration values."""
        config = EmailConfig(api_key="test_key", from_email="test@example.com")
        assert config.max_retries == 3
        assert config.base_delay_seconds == 1.0
        assert config.timeout_seconds == 30.0

    def test_custom_values(self) -> None:
        """Test custom configuration values."""
        config = EmailConfig(
            api_key="test_key",
            from_email="test@example.com",
            max_retries=5,
            base_delay_seconds=2.0,
            timeout_seconds=60.0,
        )
        assert config.max_retries == 5
        assert config.base_delay_seconds == 2.0
        assert config.timeout_seconds == 60.0


class TestResendEmailService:
    """Tests for ResendEmailService class."""

    def test_is_configured_with_valid_config(self) -> None:
        """Test is_configured returns True with valid config."""
        service = ResendEmailService(EmailConfig(api_key="test_key", from_email="test@example.com"))
        assert service.is_configured() is True

    def test_is_configured_missing_api_key(self) -> None:
        """Test is_configured returns False without API key."""
        service = ResendEmailService(EmailConfig(api_key="", from_email="test@example.com"))
        assert service.is_configured() is False

    def test_is_configured_missing_from_email(self) -> None:
        """Test is_configured returns False without from email."""
        service = ResendEmailService(EmailConfig(api_key="test_key", from_email=""))
        assert service.is_configured() is False

    @pytest.mark.asyncio
    async def test_send_not_configured(self) -> None:
        """Test send returns error when not configured."""
        service = ResendEmailService(EmailConfig(api_key="", from_email=""))
        message = EmailMessage(
            to=["user@example.com"],
            subject="Test",
            html_body="<p>Test</p>",
        )

        result = await service.send(message)

        assert result.success is False
        assert "not configured" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_send_success(self) -> None:
        """Test successful email send."""
        service = ResendEmailService(EmailConfig(api_key="test_key", from_email="test@example.com"))
        message = EmailMessage(
            to=["user@example.com"],
            subject="Test Subject",
            html_body="<p>Test body</p>",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "msg_123"}

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await service.send(message)

        assert result.success is True
        assert result.message_id == "msg_123"
        assert result.error is None

        # Verify API call
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["to"] == ["user@example.com"]
        assert call_kwargs["json"]["subject"] == "Test Subject"
        assert call_kwargs["json"]["html"] == "<p>Test body</p>"

    @pytest.mark.asyncio
    async def test_send_with_optional_fields(self) -> None:
        """Test send with text_body and reply_to."""
        service = ResendEmailService(EmailConfig(api_key="test_key", from_email="test@example.com"))
        message = EmailMessage(
            to=["user@example.com"],
            subject="Test",
            html_body="<p>Test</p>",
            text_body="Test plain text",
            reply_to="reply@example.com",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "msg_456"}

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await service.send(message)

        assert result.success is True
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["text"] == "Test plain text"
        assert call_kwargs["json"]["reply_to"] == "reply@example.com"

    @pytest.mark.asyncio
    async def test_send_client_error_no_retry(self) -> None:
        """Test that client errors (4xx) do not retry."""
        service = ResendEmailService(EmailConfig(api_key="test_key", from_email="test@example.com"))
        message = EmailMessage(
            to=["user@example.com"],
            subject="Test",
            html_body="<p>Test</p>",
        )

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request: invalid email"

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await service.send(message)

        assert result.success is False
        assert "400" in (result.error or "")
        # Should only be called once (no retries for client errors)
        assert mock_post.call_count == 1

    @pytest.mark.asyncio
    async def test_send_batch_success(self) -> None:
        """Test sending multiple emails."""
        service = ResendEmailService(EmailConfig(api_key="test_key", from_email="test@example.com"))
        messages = [
            EmailMessage(to=["user1@example.com"], subject="Test 1", html_body="<p>1</p>"),
            EmailMessage(to=["user2@example.com"], subject="Test 2", html_body="<p>2</p>"),
        ]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "msg_batch"}

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            results = await service.send_batch(messages)

        assert len(results) == 2
        assert all(r.success for r in results)
        assert mock_post.call_count == 2


class TestEmailMessage:
    """Tests for EmailMessage dataclass."""

    def test_required_fields_only(self) -> None:
        """Test creating message with required fields only."""
        msg = EmailMessage(
            to=["user@example.com"],
            subject="Test",
            html_body="<p>Body</p>",
        )
        assert msg.to == ["user@example.com"]
        assert msg.text_body is None
        assert msg.reply_to is None

    def test_all_fields(self) -> None:
        """Test creating message with all fields."""
        msg = EmailMessage(
            to=["user@example.com", "user2@example.com"],
            subject="Test",
            html_body="<p>Body</p>",
            text_body="Plain body",
            reply_to="reply@example.com",
        )
        assert len(msg.to) == 2
        assert msg.text_body == "Plain body"
        assert msg.reply_to == "reply@example.com"
