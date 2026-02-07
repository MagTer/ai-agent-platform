"""Resend email service implementation."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

from core.protocols.email import EmailMessage, EmailResult, IEmailService

logger = logging.getLogger(__name__)


@dataclass
class EmailConfig:
    """Configuration for the email service."""

    api_key: str
    from_email: str
    max_retries: int = 3
    base_delay_seconds: float = 1.0
    timeout_seconds: float = 30.0


class ResendEmailService:
    """Email service implementation using Resend API.

    Implements the IEmailService protocol with:
    - Exponential backoff retry logic
    - Connection pooling via shared httpx client
    - Rate limit awareness
    """

    RESEND_API_URL = "https://api.resend.com/emails"

    def __init__(self, config: EmailConfig) -> None:
        """Initialize the email service.

        Args:
            config: Email service configuration.
        """
        self._config = config
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._config.timeout_seconds,
                headers={
                    "Authorization": f"Bearer {self._config.api_key}",
                    "Content-Type": "application/json",
                },
                limits=httpx.Limits(
                    max_connections=10,
                    max_keepalive_connections=5,
                ),
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def is_configured(self) -> bool:
        """Check if the email service is properly configured."""
        return bool(self._config.api_key and self._config.from_email)

    async def send(self, message: EmailMessage) -> EmailResult:
        """Send a single email with retry logic.

        Args:
            message: The email message to send.

        Returns:
            EmailResult with success status.
        """
        if not self.is_configured():
            return EmailResult(
                success=False,
                error="Email service not configured (missing API key or from address)",
            )

        payload = {
            "from": self._config.from_email,
            "to": message.to,
            "subject": message.subject,
            "html": message.html_body,
        }

        if message.text_body:
            payload["text"] = message.text_body

        if message.reply_to:
            payload["reply_to"] = message.reply_to

        last_error: str | None = None

        for attempt in range(self._config.max_retries):
            try:
                client = await self._get_client()
                response = await client.post(self.RESEND_API_URL, json=payload)

                if response.status_code == 200:
                    data = response.json()
                    message_id = data.get("id")
                    logger.info(
                        "Email sent successfully",
                        extra={
                            "to": message.to,
                            "subject": message.subject[:50],
                            "message_id": message_id,
                        },
                    )
                    return EmailResult(success=True, message_id=message_id)

                # Rate limited - wait and retry
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", "60"))
                    logger.warning(
                        f"Rate limited, waiting {retry_after}s before retry",
                        extra={"attempt": attempt + 1},
                    )
                    await asyncio.sleep(retry_after)
                    continue

                # Server error - retry with backoff
                if response.status_code >= 500:
                    last_error = f"Server error: {response.status_code} - {response.text}"
                    logger.warning(
                        last_error,
                        extra={"attempt": attempt + 1},
                    )
                    delay = self._config.base_delay_seconds * (2**attempt)
                    await asyncio.sleep(delay)
                    continue

                # Client error - do not retry
                error_text = response.text
                logger.error(
                    f"Email send failed: {response.status_code} - {error_text}",
                    extra={"to": message.to, "subject": message.subject[:50]},
                )
                return EmailResult(
                    success=False,
                    error=f"HTTP {response.status_code}: {error_text}",
                )

            except httpx.TimeoutException:
                last_error = "Request timed out"
                logger.warning(last_error, extra={"attempt": attempt + 1})
                delay = self._config.base_delay_seconds * (2**attempt)
                await asyncio.sleep(delay)

            except httpx.RequestError as e:
                last_error = f"Request error: {e}"
                logger.warning(last_error, extra={"attempt": attempt + 1})
                delay = self._config.base_delay_seconds * (2**attempt)
                await asyncio.sleep(delay)

        logger.error(
            f"Email send failed after {self._config.max_retries} attempts",
            extra={"to": message.to, "last_error": last_error},
        )
        return EmailResult(success=False, error=last_error or "Max retries exceeded")

    async def send_batch(self, messages: list[EmailMessage]) -> list[EmailResult]:
        """Send multiple emails.

        Args:
            messages: List of email messages to send.

        Returns:
            List of EmailResult, one per message.
        """
        results = []
        for message in messages:
            result = await self.send(message)
            results.append(result)
            # Small delay between messages to avoid rate limiting
            await asyncio.sleep(0.1)
        return results


# Type assertion to verify protocol compliance
def _verify_protocol() -> None:
    """Verify that ResendEmailService implements IEmailService."""
    service: IEmailService = ResendEmailService(
        EmailConfig(api_key="test", from_email="test@example.com")
    )
    assert isinstance(service, IEmailService)


__all__ = ["EmailConfig", "ResendEmailService"]
