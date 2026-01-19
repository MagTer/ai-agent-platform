# Platform Email Service - Refactor from Price Tracker

**Created:** 2026-01-19
**Planner:** Opus 4.5
**Status:** Planning Complete
**Implementer:** Sonnet 4.5

---

## 1. Executive Summary

**Problem Statement:**
The email sending functionality is currently embedded in `modules/price_tracker/notifier.py` and is tightly coupled to price alert use cases. Other parts of the platform (orchestrator, other modules, admin notifications, user alerts) need email capabilities but cannot access the Price Tracker's notifier without violating architecture rules.

**Solution Approach:**
Extract the core email sending logic into a new `modules/email/` module with a protocol-based interface (`IEmailService`). The module will implement retry logic, rate limiting, and templating. Price Tracker will be updated to use this shared service via dependency injection.

**Success Criteria:**
- [ ] New `IEmailService` protocol defined in `core/protocols/`
- [ ] Email module implementation in `modules/email/`
- [ ] Price Tracker refactored to use the shared email service
- [ ] All existing tests pass
- [ ] New unit tests for email service
- [ ] `python scripts/code_check.py` passes

---

## 2. Codebase Context

### 2.1 Relevant Architecture

**Layers Involved:**
- `core/` - New protocol definition (`IEmailService`)
- `modules/` - New `email/` module + update `price_tracker/`
- `interfaces/` - Startup wiring in `app.py`

**Key Files:**
```
services/agent/src/
├── core/
│   ├── protocols/
│   │   ├── __init__.py          # Add IEmailService export
│   │   └── email.py             # NEW: IEmailService protocol
│   ├── providers.py             # Add email service provider
│   └── core/
│       └── config.py            # Already has resend_api_key
├── modules/
│   ├── email/                   # NEW directory
│   │   ├── __init__.py
│   │   ├── service.py           # ResendEmailService implementation
│   │   ├── templates.py         # Email template helpers
│   │   └── tests/
│   │       ├── __init__.py
│   │       └── test_service.py
│   └── price_tracker/
│       ├── notifier.py          # MODIFY: Use IEmailService
│       ├── scheduler.py         # MODIFY: Inject email service
│       └── tests/
│           └── test_notifier.py # UPDATE tests
└── interfaces/
    └── http/
        └── admin_portal.py      # (future) Admin email notifications
```

### 2.2 Current Implementation Analysis

**Current Email Implementation (modules/price_tracker/notifier.py):**
```python
class PriceNotifier:
    """Send price alerts via Resend email API."""

    RESEND_API_URL = "https://api.resend.com/emails"

    def __init__(self, api_key: str, from_email: str) -> None:
        self.api_key = api_key
        self.from_email = from_email

    async def _send_email(self, to_email: str, subject: str, html_body: str) -> bool:
        """Send email via Resend API."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.RESEND_API_URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "from": self.from_email,
                        "to": [to_email],
                        "subject": subject,
                        "html": html_body,
                    },
                    timeout=30.0,
                )

                if response.status_code == 200:
                    logger.info(f"Email sent successfully to {to_email}")
                    return True
                else:
                    logger.error(f"Failed to send email: {response.status_code} - {response.text}")
                    return False

        except Exception as e:
            logger.error(f"Email send error: {e}")
            return False
```

**Issues with current implementation:**
1. No retry logic for transient failures
2. No rate limiting
3. Creates new httpx.AsyncClient per request (inefficient)
4. Tightly coupled to price tracker domain (Swedish email content)
5. Cannot be used by other modules without architecture violation

### 2.3 Configuration (Already Exists)

**In `services/agent/src/core/core/config.py` (lines 197-209):**
```python
# Price Tracker Settings
resend_api_key: str | None = Field(
    default=None,
    description="Resend API key for email notifications.",
)
price_tracker_from_email: str = Field(
    default="prisspaning@noreply.local",
    description="From email address for price alerts.",
)
```

We will generalize this to a platform-wide email configuration.

### 2.4 Provider Pattern (Existing)

**In `services/agent/src/core/providers.py`:**
```python
# --- Embedder ---
def set_embedder(embedder: IEmbedder) -> None:
    """Register the embedder implementation."""
    global _embedder
    _embedder = embedder
    LOGGER.info("Embedder provider registered")


def get_embedder() -> IEmbedder:
    """Get the registered embedder. Raises if not configured."""
    if _embedder is None:
        raise ProviderError("Embedder not configured. Call set_embedder() at startup.")
    return _embedder
```

We will follow this exact pattern for the email service.

---

## 3. Architecture Decisions

### Decision 1: Where to Place Email Service

**Options Considered:**
1. **Option A:** In `core/` - Email as fundamental infrastructure
   - Pros: Simplest access from anywhere
   - Cons: Violates architecture - core cannot have external dependencies (httpx calls)

2. **Option B:** In `modules/email/` - Email as a module ✅ CHOSEN
   - Pros: Follows architecture rules, can be independently tested
   - Cons: Must use protocol/DI pattern for core tools

**Rationale:** Email sending is an external integration (Resend API). Following the existing pattern for fetcher/embedder, external integrations belong in modules with protocol-based interfaces in core.

### Decision 2: Email Template Handling

**Options Considered:**
1. **Option A:** Generic template engine (Jinja2)
   - Pros: Flexible, powerful
   - Cons: Over-engineered for current needs, new dependency

2. **Option B:** Simple string formatting with helper functions ✅ CHOSEN
   - Pros: No new dependencies, matches existing code style
   - Cons: Less flexible

**Rationale:** The current codebase uses simple f-string formatting. We keep template helpers as pure functions that return HTML strings. If Jinja2 is needed later, it can be added.

### Decision 3: Retry Strategy

**Chosen Approach:** Exponential backoff with max 3 retries

**Rationale:**
- Resend API can have transient failures
- Exponential backoff prevents overwhelming the API
- 3 retries is sufficient for transient issues
- If still failing, log and let caller decide (some emails are optional)

---

## 4. Implementation Roadmap

### Phase 1: Core Protocol and Provider

**File 1: `services/agent/src/core/protocols/email.py`** (NEW)
```python
"""Protocol for email services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class EmailMessage:
    """Represents an email message to be sent."""

    to: list[str]
    subject: str
    html_body: str
    text_body: str | None = None
    reply_to: str | None = None


@dataclass
class EmailResult:
    """Result of an email send operation."""

    success: bool
    message_id: str | None = None
    error: str | None = None


@runtime_checkable
class IEmailService(Protocol):
    """Abstract interface for email services.

    This protocol defines the contract for sending emails.
    Implementations can use different providers (Resend, SendGrid, SMTP, etc.).
    """

    async def send(self, message: EmailMessage) -> EmailResult:
        """Send a single email.

        Args:
            message: The email message to send.

        Returns:
            EmailResult with success status and optional message_id or error.
        """
        ...

    async def send_batch(self, messages: list[EmailMessage]) -> list[EmailResult]:
        """Send multiple emails.

        Args:
            messages: List of email messages to send.

        Returns:
            List of EmailResult, one per message.
        """
        ...

    def is_configured(self) -> bool:
        """Check if the email service is properly configured.

        Returns:
            True if API key and from address are set.
        """
        ...


__all__ = ["EmailMessage", "EmailResult", "IEmailService"]
```

**File 2: `services/agent/src/core/protocols/__init__.py`** (MODIFY)

Add these lines to the existing file:
```python
# Add import
from .email import EmailMessage, EmailResult, IEmailService

# Add to __all__
__all__ = [
    "IEmbedder",
    "IFetcher",
    "ICodeIndexer",
    "IOAuthClient",
    "IPriceTracker",
    "IRAGManager",
    # Add these:
    "EmailMessage",
    "EmailResult",
    "IEmailService",
]
```

**File 3: `services/agent/src/core/providers.py`** (ADD to existing)

Add after the existing Price Tracker section (after line 129):
```python
# --- Email Service ---
_email_service: IEmailService | None = None


def set_email_service(service: IEmailService) -> None:
    """Register the email service implementation."""
    global _email_service
    _email_service = service
    LOGGER.info("Email Service provider registered")


def get_email_service() -> IEmailService:
    """Get the registered email service. Raises if not configured."""
    if _email_service is None:
        raise ProviderError("Email Service not configured. Call set_email_service() at startup.")
    return _email_service


def get_email_service_optional() -> IEmailService | None:
    """Get the email service if configured, or None.

    Use this when email is optional (e.g., notifications that can be skipped).
    """
    return _email_service
```

Also add to `__all__`:
```python
__all__ = [
    # ... existing exports ...
    "set_email_service",
    "get_email_service",
    "get_email_service_optional",
]
```

Add TYPE_CHECKING import:
```python
if TYPE_CHECKING:
    from core.auth.token_manager import TokenManager
    from core.protocols import ICodeIndexer, IEmbedder, IFetcher, IPriceTracker, IRAGManager
    from core.protocols.email import IEmailService  # Add this
```

---

### Phase 2: Email Module Implementation

**Directory Structure:**
```
services/agent/src/modules/email/
├── __init__.py
├── service.py         # ResendEmailService implementation
├── templates.py       # Common email template helpers
└── tests/
    ├── __init__.py
    └── test_service.py
```

**File 1: `services/agent/src/modules/email/__init__.py`** (NEW)
```python
"""Email service module.

Provides platform-wide email sending capabilities via Resend API.
"""

from .service import ResendEmailService

__all__ = ["ResendEmailService"]
```

**File 2: `services/agent/src/modules/email/service.py`** (NEW)
```python
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
```

**File 3: `services/agent/src/modules/email/templates.py`** (NEW)
```python
"""Common email template helpers.

These helpers provide reusable HTML templates for platform emails.
Each function returns an HTML string ready for sending.
"""

from __future__ import annotations


def wrap_html_email(title: str, body_content: str, footer_text: str = "") -> str:
    """Wrap content in a standard HTML email template.

    Args:
        title: Email title (shown in header).
        body_content: Main HTML content for the email body.
        footer_text: Optional footer text.

    Returns:
        Complete HTML document string.
    """
    footer_html = ""
    if footer_text:
        footer_html = f"""
            <hr style="margin-top: 30px; border: none; border-top: 1px solid #eee;">
            <p style="color: #666; font-size: 0.9em;">
                {footer_text}
            </p>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
</head>
<body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #333;">
    <h2 style="color: #1e3a5f;">{title}</h2>
    {body_content}
    {footer_html}
</body>
</html>"""


def create_notification_email(
    title: str,
    message: str,
    action_url: str | None = None,
    action_text: str = "View Details",
) -> str:
    """Create a simple notification email.

    Args:
        title: Notification title.
        message: Main message text (can include HTML).
        action_url: Optional URL for a call-to-action button.
        action_text: Text for the action button.

    Returns:
        Complete HTML email string.
    """
    action_html = ""
    if action_url:
        action_html = f"""
            <p style="margin-top: 20px;">
                <a href="{action_url}"
                   style="background: #2563eb; color: white; padding: 10px 20px;
                          text-decoration: none; border-radius: 4px; display: inline-block;">
                    {action_text}
                </a>
            </p>"""

    body = f"""
        <p>{message}</p>
        {action_html}"""

    return wrap_html_email(title, body, "This email was sent by AI Agent Platform.")


def create_table_email(
    title: str,
    intro_text: str,
    headers: list[str],
    rows: list[list[str]],
    footer_text: str = "This email was sent by AI Agent Platform.",
) -> str:
    """Create an email with a data table.

    Args:
        title: Email title.
        intro_text: Introductory text before the table.
        headers: Table column headers.
        rows: List of rows, each row is a list of cell values.
        footer_text: Footer text.

    Returns:
        Complete HTML email string.
    """
    header_cells = "".join(
        f'<th style="padding: 8px; text-align: left; border-bottom: 2px solid #ddd;">{h}</th>'
        for h in headers
    )

    row_html = ""
    for row in rows:
        cells = "".join(
            f'<td style="padding: 8px; border-bottom: 1px solid #eee;">{cell}</td>'
            for cell in row
        )
        row_html += f"<tr>{cells}</tr>"

    table = f"""
        <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
            <thead>
                <tr style="background: #f3f4f6;">
                    {header_cells}
                </tr>
            </thead>
            <tbody>
                {row_html}
            </tbody>
        </table>"""

    body = f"""
        <p>{intro_text}</p>
        {table}"""

    return wrap_html_email(title, body, footer_text)


__all__ = ["wrap_html_email", "create_notification_email", "create_table_email"]
```

**File 4: `services/agent/src/modules/email/tests/__init__.py`** (NEW)
```python
"""Tests for email module."""
```

**File 5: `services/agent/src/modules/email/tests/test_service.py`** (NEW)
```python
"""Tests for email service module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.protocols.email import EmailMessage, EmailResult
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
        service = ResendEmailService(
            EmailConfig(api_key="test_key", from_email="test@example.com")
        )
        assert service.is_configured() is True

    def test_is_configured_missing_api_key(self) -> None:
        """Test is_configured returns False without API key."""
        service = ResendEmailService(
            EmailConfig(api_key="", from_email="test@example.com")
        )
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
        service = ResendEmailService(
            EmailConfig(api_key="test_key", from_email="test@example.com")
        )
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
        service = ResendEmailService(
            EmailConfig(api_key="test_key", from_email="test@example.com")
        )
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
        service = ResendEmailService(
            EmailConfig(api_key="test_key", from_email="test@example.com")
        )
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
        service = ResendEmailService(
            EmailConfig(api_key="test_key", from_email="test@example.com")
        )
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
```

---

### Phase 3: Update Price Tracker to Use Email Service

**File 1: `services/agent/src/modules/price_tracker/notifier.py`** (MODIFY)

Replace the entire file with:
```python
"""Price tracker notifications using platform email service."""

from __future__ import annotations

import logging
from decimal import Decimal

from core.protocols.email import EmailMessage, IEmailService

logger = logging.getLogger(__name__)


class PriceNotifier:
    """Send price alerts using the platform email service.

    This class handles price-specific email formatting and delegates
    actual sending to the injected IEmailService.
    """

    def __init__(self, email_service: IEmailService) -> None:
        """Initialize the price notifier.

        Args:
            email_service: The email service to use for sending.
        """
        self._email_service = email_service

    async def send_price_alert(
        self,
        to_email: str,
        product_name: str,
        store_name: str,
        current_price: Decimal,
        target_price: Decimal | None,
        offer_type: str | None,
        offer_details: str | None,
        product_url: str | None = None,
        price_drop_percent: float | None = None,
        unit_price_sek: Decimal | None = None,
        unit_price_drop_percent: float | None = None,
    ) -> bool:
        """Send price drop alert email.

        Returns:
            True if email was sent successfully.
        """
        subject = f"Prisvarning: {product_name} hos {store_name}"
        html_body = self._build_alert_html(
            product_name=product_name,
            store_name=store_name,
            current_price=current_price,
            target_price=target_price,
            offer_type=offer_type,
            offer_details=offer_details,
            product_url=product_url,
            price_drop_percent=price_drop_percent,
            unit_price_sek=unit_price_sek,
            unit_price_drop_percent=unit_price_drop_percent,
        )

        message = EmailMessage(
            to=[to_email],
            subject=subject,
            html_body=html_body,
        )

        result = await self._email_service.send(message)
        return result.success

    async def send_weekly_summary(
        self,
        to_email: str,
        deals: list[dict[str, str | Decimal | None]],
        watched_products: list[dict[str, str | Decimal | None]],
    ) -> bool:
        """Send weekly price summary email.

        Returns:
            True if email was sent successfully.
        """
        subject = "Veckans prisoversikt - Prisspaning"
        html_body = self._build_summary_html(deals, watched_products)

        message = EmailMessage(
            to=[to_email],
            subject=subject,
            html_body=html_body,
        )

        result = await self._email_service.send(message)
        return result.success

    def _build_alert_html(
        self,
        product_name: str,
        store_name: str,
        current_price: Decimal,
        target_price: Decimal | None,
        offer_type: str | None,
        offer_details: str | None,
        product_url: str | None,
        price_drop_percent: float | None = None,
        unit_price_sek: Decimal | None = None,
        unit_price_drop_percent: float | None = None,
    ) -> str:
        """Build HTML for price alert email."""
        target_row = ""
        if target_price:
            target_row = f"""
            <tr>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">Ditt malpris:</td>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">{target_price} kr</td>
            </tr>"""

        price_drop_row = ""
        if price_drop_percent is not None:
            price_drop_row = f"""
            <tr>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">Prisfall:</td>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">
                    <strong style="color: #22c55e;">
                        {price_drop_percent:.1f}% under ordinarie pris
                    </strong>
                </td>
            </tr>"""

        unit_price_row = ""
        if unit_price_sek is not None:
            unit_price_row = f"""
            <tr>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">Jamforelsepris:</td>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">
                    <strong>{unit_price_sek} kr/enhet</strong>
                </td>
            </tr>"""

        unit_price_drop_row = ""
        if unit_price_drop_percent is not None:
            unit_price_drop_row = f"""
            <tr>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">Jamforelsepris-fall:</td>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">
                    <strong style="color: #22c55e;">
                        {unit_price_drop_percent:.1f}% under ordinarie jamforelsepris
                    </strong>
                </td>
            </tr>"""

        offer_row = ""
        if offer_type:
            details = f" - {offer_details}" if offer_details else ""
            offer_row = f"""
            <tr>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">Erbjudande:</td>
                <td style="padding: 8px; border-bottom: 1px solid #eee;">
                    <span style="background: #22c55e; color: white; padding: 2px 8px;
                                border-radius: 4px;">
                        {offer_type}
                    </span>{details}
                </td>
            </tr>"""

        link_section = ""
        if product_url:
            link_section = f"""
            <p style="margin-top: 20px;">
                <a href="{product_url}"
                   style="background: #2563eb; color: white; padding: 10px 20px;
                          text-decoration: none; border-radius: 4px;">Se produkten</a>
            </p>"""

        return f"""
        <!DOCTYPE html>
        <html lang="sv">
        <head><meta charset="UTF-8"></head>
        <body style="font-family: Arial, sans-serif; max-width: 600px;
                     margin: 0 auto; padding: 20px;">
            <h2 style="color: #1e3a5f;">Prisvarning!</h2>
            <p><strong>{product_name}</strong> hos <strong>{store_name}</strong>
               har ett bra pris.</p>

            <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
                <tr>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">Aktuellt pris:</td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee; font-size: 1.2em;">
                        <strong style="color: #22c55e;">{current_price} kr</strong>
                    </td>
                </tr>
                {target_row}
                {price_drop_row}
                {unit_price_row}
                {unit_price_drop_row}
                {offer_row}
            </table>
            {link_section}

            <hr style="margin-top: 30px; border: none; border-top: 1px solid #eee;">
            <p style="color: #666; font-size: 0.9em;">
                Detta mail skickades av Prisspaning. Du far detta for att du bevakar produkten.
            </p>
        </body>
        </html>
        """

    def _build_summary_html(
        self,
        deals: list[dict[str, str | Decimal | None]],
        watched_products: list[dict[str, str | Decimal | None]],
    ) -> str:
        """Build HTML for weekly summary email."""
        # Build deals section
        deals_html = ""
        if deals:
            deals_rows = ""
            for deal in deals[:10]:  # Limit to top 10
                product_name = deal.get("product_name", "")
                store_name = deal.get("store_name", "")
                offer_price = deal.get("offer_price_sek", "")
                offer_type = deal.get("offer_type", "")
                deals_rows += f"""
                <tr>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">
                        {product_name}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">
                        {store_name}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;
                               color: #22c55e; font-weight: bold;">
                        {offer_price} kr
                    </td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">
                        <span style="background: #f59e0b; color: white;
                                     padding: 2px 6px; border-radius: 3px;
                                     font-size: 0.8em;">
                            {offer_type}
                        </span>
                    </td>
                </tr>"""

            deals_html = f"""
            <h3 style="color: #1e3a5f; margin-top: 30px;">Aktuella erbjudanden</h3>
            <table style="width: 100%; border-collapse: collapse;">
                <thead>
                    <tr style="background: #f3f4f6;">
                        <th style="padding: 8px; text-align: left;">Produkt</th>
                        <th style="padding: 8px; text-align: left;">Butik</th>
                        <th style="padding: 8px; text-align: left;">Pris</th>
                        <th style="padding: 8px; text-align: left;">Typ</th>
                    </tr>
                </thead>
                <tbody>{deals_rows}</tbody>
            </table>"""

        # Build watched products section
        watched_html = ""
        if watched_products:
            watched_rows = ""
            for product in watched_products:
                name = product.get("name", "")
                lowest_price = product.get("lowest_price", "N/A")
                store_name = product.get("store_name", "")
                watched_rows += f"""
                <tr>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">
                        {name}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">
                        {lowest_price} kr</td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">
                        {store_name}</td>
                </tr>"""

            watched_html = f"""
            <h3 style="color: #1e3a5f; margin-top: 30px;">Dina bevakade produkter</h3>
            <table style="width: 100%; border-collapse: collapse;">
                <thead>
                    <tr style="background: #f3f4f6;">
                        <th style="padding: 8px; text-align: left;">Produkt</th>
                        <th style="padding: 8px; text-align: left;">Lagsta pris</th>
                        <th style="padding: 8px; text-align: left;">Butik</th>
                    </tr>
                </thead>
                <tbody>{watched_rows}</tbody>
            </table>"""

        return f"""
        <!DOCTYPE html>
        <html lang="sv">
        <head><meta charset="UTF-8"></head>
        <body style="font-family: Arial, sans-serif; max-width: 600px;
                     margin: 0 auto; padding: 20px;">
            <h2 style="color: #1e3a5f;">Veckans prisoversikt</h2>
            <p>Har ar en sammanfattning av priser och erbjudanden denna vecka.</p>
            {deals_html}
            {watched_html}

            <hr style="margin-top: 30px; border: none; border-top: 1px solid #eee;">
            <p style="color: #666; font-size: 0.9em;">
                Detta mail skickades av Prisspaning.
            </p>
        </body>
        </html>
        """
```

**File 2: `services/agent/src/modules/price_tracker/scheduler.py`** (MODIFY)

Update the `__init__` method to accept `IEmailService` instead of `PriceNotifier`, and update the constructor to create `PriceNotifier` internally.

Find and replace the `__init__` method signature and body (around lines 28-39):

**OLD:**
```python
def __init__(
    self,
    session_factory: async_sessionmaker[AsyncSession],
    fetcher: IFetcher,
    notifier: PriceNotifier | None = None,
) -> None:
    self.session_factory = session_factory
    self.fetcher = fetcher
    self.parser = PriceParser()
    self.notifier = notifier
    self._running = False
    self._task: asyncio.Task[None] | None = None
```

**NEW:**
```python
def __init__(
    self,
    session_factory: async_sessionmaker[AsyncSession],
    fetcher: IFetcher,
    email_service: IEmailService | None = None,
) -> None:
    self.session_factory = session_factory
    self.fetcher = fetcher
    self.parser = PriceParser()
    # Create notifier wrapper if email service is provided
    self.notifier: PriceNotifier | None = None
    if email_service is not None:
        self.notifier = PriceNotifier(email_service)
    self._running = False
    self._task: asyncio.Task[None] | None = None
```

Also update the imports at the top of the file. Replace:
```python
from core.protocols import IFetcher
```

With:
```python
from core.protocols import IEmailService, IFetcher
```

**File 3: `services/agent/src/modules/price_tracker/tests/test_notifier.py`** (MODIFY)

Update tests to use the new interface. Replace the entire file with:
```python
"""Tests for price notifier module."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from core.protocols.email import EmailMessage, EmailResult, IEmailService
from modules.price_tracker.notifier import PriceNotifier


class MockEmailService:
    """Mock email service for testing."""

    def __init__(self, should_succeed: bool = True) -> None:
        self.should_succeed = should_succeed
        self.sent_messages: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> EmailResult:
        self.sent_messages.append(message)
        if self.should_succeed:
            return EmailResult(success=True, message_id="test_id")
        return EmailResult(success=False, error="Mock error")

    async def send_batch(self, messages: list[EmailMessage]) -> list[EmailResult]:
        results = []
        for msg in messages:
            results.append(await self.send(msg))
        return results

    def is_configured(self) -> bool:
        return True


class TestPriceNotifier:
    """Tests for PriceNotifier class."""

    def test_build_alert_html_contains_expected_content(self) -> None:
        """Test _build_alert_html generates valid HTML with all fields."""
        mock_service = MockEmailService()
        notifier = PriceNotifier(email_service=mock_service)

        html = notifier._build_alert_html(
            product_name="Mjolk Arla Standard 3%",
            store_name="ICA Maxi",
            current_price=Decimal("19.90"),
            target_price=Decimal("25.00"),
            offer_type="stammispris",
            offer_details="Kop 2 betala for 1",
            product_url="https://www.ica.se/handla/produkt/test-123",
        )

        # Check basic structure
        assert "<!DOCTYPE html>" in html
        assert '<html lang="sv">' in html
        assert "</html>" in html

        # Check content elements
        assert "Prisvarning!" in html
        assert "Mjolk Arla Standard 3%" in html
        assert "ICA Maxi" in html
        assert "19.90 kr" in html
        assert "25.00 kr" in html  # Target price
        assert "stammispris" in html
        assert "Kop 2 betala for 1" in html
        assert "https://www.ica.se/handla/produkt/test-123" in html
        assert "Se produkten" in html  # Link button

    def test_build_alert_html_without_optional_fields(self) -> None:
        """Test _build_alert_html without target price and offer."""
        mock_service = MockEmailService()
        notifier = PriceNotifier(email_service=mock_service)

        html = notifier._build_alert_html(
            product_name="Smor Bregott",
            store_name="Willys",
            current_price=Decimal("29.90"),
            target_price=None,
            offer_type=None,
            offer_details=None,
            product_url=None,
        )

        # Should still have basic content
        assert "Smor Bregott" in html
        assert "Willys" in html
        assert "29.90 kr" in html

        # Should NOT have optional fields
        assert "Ditt malpris:" not in html
        assert "Erbjudande:" not in html
        assert "Se produkten" not in html

    def test_build_alert_html_with_offer_but_no_details(self) -> None:
        """Test _build_alert_html with offer type but no details."""
        mock_service = MockEmailService()
        notifier = PriceNotifier(email_service=mock_service)

        html = notifier._build_alert_html(
            product_name="Yoghurt",
            store_name="Coop",
            current_price=Decimal("12.50"),
            target_price=None,
            offer_type="extrapris",
            offer_details=None,
            product_url=None,
        )

        assert "extrapris" in html
        # Offer type should be in badge
        assert 'style="background: #22c55e' in html

    def test_build_summary_html_handles_empty_lists(self) -> None:
        """Test _build_summary_html with empty deals and watched products."""
        mock_service = MockEmailService()
        notifier = PriceNotifier(email_service=mock_service)

        html = notifier._build_summary_html(deals=[], watched_products=[])

        # Should have basic structure
        assert "<!DOCTYPE html>" in html
        assert "Veckans prisoversikt" in html
        assert "Har ar en sammanfattning" in html

        # Should NOT have tables or sections for empty data
        assert "Aktuella erbjudanden" not in html
        assert "Dina bevakade produkter" not in html

    def test_build_summary_html_with_deals(self) -> None:
        """Test _build_summary_html with deals."""
        mock_service = MockEmailService()
        notifier = PriceNotifier(email_service=mock_service)

        deals: list[dict[str, str | Decimal | None]] = [
            {
                "product_name": "Mjolk Arla",
                "store_name": "ICA Maxi",
                "offer_price_sek": Decimal("19.90"),
                "offer_type": "stammispris",
            },
            {
                "product_name": "Smor Bregott",
                "store_name": "Willys",
                "offer_price_sek": Decimal("29.90"),
                "offer_type": "kampanj",
            },
        ]

        html = notifier._build_summary_html(deals=deals, watched_products=[])

        # Should have deals section
        assert "Aktuella erbjudanden" in html
        assert "Mjolk Arla" in html
        assert "ICA Maxi" in html
        assert "19.90 kr" in html
        assert "stammispris" in html
        assert "Smor Bregott" in html
        assert "Willys" in html
        assert "kampanj" in html

        # Should NOT have watched products section
        assert "Dina bevakade produkter" not in html

    def test_build_summary_html_with_watched_products(self) -> None:
        """Test _build_summary_html with watched products."""
        mock_service = MockEmailService()
        notifier = PriceNotifier(email_service=mock_service)

        watched: list[dict[str, str | Decimal | None]] = [
            {
                "name": "Mjolk Arla Standard 3%",
                "lowest_price": Decimal("19.90"),
                "store_name": "ICA Maxi",
            },
            {
                "name": "Smor Bregott Original",
                "lowest_price": Decimal("29.90"),
                "store_name": "Coop",
            },
        ]

        html = notifier._build_summary_html(deals=[], watched_products=watched)

        # Should have watched products section
        assert "Dina bevakade produkter" in html
        assert "Mjolk Arla Standard 3%" in html
        assert "19.90 kr" in html
        assert "ICA Maxi" in html
        assert "Smor Bregott Original" in html
        assert "Coop" in html

        # Should NOT have deals section
        assert "Aktuella erbjudanden" not in html

    def test_build_summary_html_limits_deals_to_top_10(self) -> None:
        """Test _build_summary_html limits deals to top 10."""
        mock_service = MockEmailService()
        notifier = PriceNotifier(email_service=mock_service)

        deals: list[dict[str, str | Decimal | None]] = [
            {
                "product_name": f"Product {i}",
                "store_name": "Store",
                "offer_price_sek": Decimal("10.00"),
                "offer_type": "kampanj",
            }
            for i in range(20)
        ]

        html = notifier._build_summary_html(deals=deals, watched_products=[])

        # Should contain first 10 products
        for i in range(10):
            assert f"Product {i}" in html

        # Should NOT contain products beyond 10
        for i in range(10, 20):
            assert f"Product {i}" not in html

    @pytest.mark.asyncio
    async def test_send_price_alert_success(self) -> None:
        """Test send_price_alert with successful send."""
        mock_service = MockEmailService(should_succeed=True)
        notifier = PriceNotifier(email_service=mock_service)

        result = await notifier.send_price_alert(
            to_email="user@example.com",
            product_name="Mjolk Arla",
            store_name="ICA Maxi",
            current_price=Decimal("19.90"),
            target_price=Decimal("25.00"),
            offer_type="stammispris",
            offer_details="Kop 2 betala for 1",
            product_url="https://www.ica.se/test",
        )

        assert result is True
        assert len(mock_service.sent_messages) == 1

        sent_msg = mock_service.sent_messages[0]
        assert sent_msg.to == ["user@example.com"]
        assert "Prisvarning: Mjolk Arla hos ICA Maxi" in sent_msg.subject
        assert "Mjolk Arla" in sent_msg.html_body

    @pytest.mark.asyncio
    async def test_send_price_alert_failure(self) -> None:
        """Test send_price_alert with failed send."""
        mock_service = MockEmailService(should_succeed=False)
        notifier = PriceNotifier(email_service=mock_service)

        result = await notifier.send_price_alert(
            to_email="user@example.com",
            product_name="Mjolk",
            store_name="ICA",
            current_price=Decimal("19.90"),
            target_price=None,
            offer_type=None,
            offer_details=None,
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_send_weekly_summary_success(self) -> None:
        """Test send_weekly_summary with successful send."""
        mock_service = MockEmailService(should_succeed=True)
        notifier = PriceNotifier(email_service=mock_service)

        deals: list[dict[str, str | Decimal | None]] = [
            {
                "product_name": "Mjolk",
                "store_name": "ICA",
                "offer_price_sek": Decimal("19.90"),
                "offer_type": "kampanj",
            }
        ]
        watched: list[dict[str, str | Decimal | None]] = [
            {"name": "Smor", "lowest_price": Decimal("29.90"), "store_name": "Coop"}
        ]

        result = await notifier.send_weekly_summary(
            to_email="user@example.com",
            deals=deals,
            watched_products=watched,
        )

        assert result is True
        assert len(mock_service.sent_messages) == 1

        sent_msg = mock_service.sent_messages[0]
        assert sent_msg.to == ["user@example.com"]
        assert "Veckans prisoversikt" in sent_msg.subject
        assert "Mjolk" in sent_msg.html_body
        assert "Smor" in sent_msg.html_body
```

---

### Phase 4: Startup Wiring

**File: `services/agent/src/core/core/app.py`** (MODIFY)

Find the price tracker section in the lifespan function (around lines 310-331) and update it.

**OLD (approximately lines 310-331):**
```python
# Price Tracker Scheduler - runs background price checks
from modules.price_tracker.scheduler import PriceCheckScheduler

# Create notifier if API key is configured
notifier = None
if settings.resend_api_key:
    from modules.price_tracker.notifier import PriceNotifier

    notifier = PriceNotifier(
        api_key=settings.resend_api_key,
        from_email=settings.price_tracker_from_email,
    )
    LOGGER.info("Price alert notifier initialized")

# Create and start scheduler
scheduler = PriceCheckScheduler(
    session_factory=AsyncSessionLocal,
    fetcher=get_fetcher(),
    notifier=notifier,
)
await scheduler.start()
LOGGER.info("Price check scheduler started")
```

**NEW:**
```python
# Email Service - platform-wide email capability
from core.providers import set_email_service, get_email_service_optional
from modules.email.service import EmailConfig, ResendEmailService

email_service = None
if settings.resend_api_key:
    email_config = EmailConfig(
        api_key=settings.resend_api_key,
        from_email=settings.price_tracker_from_email,
    )
    email_service = ResendEmailService(email_config)
    set_email_service(email_service)
    LOGGER.info("Email service initialized")

# Price Tracker Scheduler - runs background price checks
from modules.price_tracker.scheduler import PriceCheckScheduler

# Create and start scheduler (pass email service directly)
scheduler = PriceCheckScheduler(
    session_factory=AsyncSessionLocal,
    fetcher=get_fetcher(),
    email_service=email_service,
)
await scheduler.start()
LOGGER.info("Price check scheduler started")
```

Also add cleanup in the shutdown section. Find the shutdown section (around line 336) and add email service cleanup:

**After line 336 (`await scheduler.stop()`), add:**
```python
# Clean up email service
if email_service is not None:
    await email_service.close()
```

---

### Phase 5: Configuration Update

**File: `services/agent/src/core/core/config.py`** (MODIFY)

Update the comment for the email settings to be more generic (around lines 197-209):

**OLD:**
```python
# Price Tracker Settings
resend_api_key: str | None = Field(
    default=None,
    description="Resend API key for email notifications.",
)
price_tracker_from_email: str = Field(
    default="prisspaning@noreply.local",
    description="From email address for price alerts.",
)
```

**NEW:**
```python
# Email Service Settings
resend_api_key: str | None = Field(
    default=None,
    description="Resend API key for platform email notifications.",
)
email_from_address: str = Field(
    default="noreply@ai-agent-platform.local",
    description="Default from email address for platform notifications.",
)
# Backward compatibility alias
price_tracker_from_email: str = Field(
    default="",
    description="DEPRECATED: Use email_from_address instead.",
)
```

Also add a model_validator to handle the backward compatibility:

**Add after line 220 (after `validate_production_secrets`):**
```python
@model_validator(mode="after")
def handle_email_backward_compat(self) -> Settings:
    """Handle backward compatibility for email settings."""
    # If old setting is used but new one is default, use old value
    if self.price_tracker_from_email and self.email_from_address == "noreply@ai-agent-platform.local":
        object.__setattr__(self, "email_from_address", self.price_tracker_from_email)
    return self
```

**NOTE:** Since this introduces breaking changes, update the app.py to use the new field:

In `app.py`, change:
```python
from_email=settings.price_tracker_from_email,
```
to:
```python
from_email=settings.email_from_address,
```

---

## 5. Quality Checks

### 5.1 Architecture Compliance

**Verify:**
- [ ] `modules/email/` only imports from `core/`
- [ ] `core/protocols/email.py` has no external dependencies
- [ ] Price tracker imports `IEmailService` from `core/protocols`
- [ ] No circular dependencies

### 5.2 Code Quality

**Run quality check:**
```bash
cd /home/magnus/dev/ai-agent-platform/services/agent
python scripts/code_check.py
```

**Expected checks:**
- [ ] Ruff linting passes
- [ ] Black formatting passes
- [ ] Mypy type checking passes (strict mode)
- [ ] All tests pass

### 5.3 Security Review

**Check for:**
- [ ] API key not hardcoded (uses settings)
- [ ] No sensitive data logged (email addresses truncated in logs)
- [ ] Input validation on email addresses (Resend handles this)
- [ ] Timeout on HTTP requests (30s default)

---

## 6. Testing Strategy

### Unit Tests
- [ ] `test_service.py` - ResendEmailService in isolation
- [ ] `test_notifier.py` - PriceNotifier with mock email service
- [ ] Test retry logic (mock failures)
- [ ] Test rate limit handling

### Integration Tests
```bash
# Run all tests
cd /home/magnus/dev/ai-agent-platform/services/agent
pytest src/modules/email/tests/ -v
pytest src/modules/price_tracker/tests/test_notifier.py -v
```

### Manual Testing
```python
# In Python REPL or test script
import asyncio
from modules.email.service import EmailConfig, ResendEmailService
from core.protocols.email import EmailMessage

async def test_send():
    config = EmailConfig(
        api_key="re_xxxxx",  # Your Resend API key
        from_email="test@yourdomain.com",
    )
    service = ResendEmailService(config)

    result = await service.send(EmailMessage(
        to=["your-email@example.com"],
        subject="Test from AI Agent Platform",
        html_body="<h1>Hello!</h1><p>This is a test email.</p>",
    ))

    print(f"Success: {result.success}")
    print(f"Message ID: {result.message_id}")
    print(f"Error: {result.error}")

    await service.close()

asyncio.run(test_send())
```

---

## 7. Potential Issues & Solutions

### Issue 1: Breaking change in scheduler constructor

**Problem:** The `PriceCheckScheduler.__init__` signature changes from `notifier: PriceNotifier` to `email_service: IEmailService`.

**Solution:** Update all places that instantiate `PriceCheckScheduler` (only in `app.py`).

### Issue 2: Test imports need updating

**Problem:** Existing tests may import `PriceNotifier` and use old constructor.

**Solution:** Tests updated to use mock `IEmailService` instead of mocking `httpx` directly.

### Issue 3: Config field rename

**Problem:** `price_tracker_from_email` renamed to `email_from_address`.

**Solution:** Keep both fields with backward compatibility validator. Existing deployments won't break.

---

## 8. Success Validation

**How to verify this is done correctly:**

1. **Functionality:**
   - [ ] Price tracker alerts still work
   - [ ] Email service can be used from other modules
   - [ ] Retry logic works for transient failures

2. **Quality:**
   - [ ] `python scripts/code_check.py` passes
   - [ ] All existing tests pass
   - [ ] New tests added and passing

3. **Architecture:**
   - [ ] Email service follows protocol pattern
   - [ ] No architecture violations
   - [ ] Clean dependency graph

---

## 9. Implementation Notes (for Sonnet)

**Order of Operations:**
1. Phase 1: Create protocol and provider (core/)
2. Phase 2: Create email module (modules/email/)
3. Phase 3: Update price tracker (modules/price_tracker/)
4. Phase 4: Update app.py wiring (interfaces/)
5. Phase 5: Update config (core/core/config.py)
6. Run quality checks
7. Run tests
8. Update this plan with completion status

**When You Get Stuck:**
- Re-read the relevant phase section
- Look at existing protocol implementations (IFetcher, IEmbedder)
- Check existing test patterns in the codebase

**Do Not:**
- Skip phases (follow order)
- Change the protocol interface without updating all consumers
- Add features not in the plan
- Skip quality checks

---

## 10. Status Tracking

- [ ] Phase 1: Core Protocol and Provider
- [ ] Phase 2: Email Module Implementation
- [ ] Phase 3: Update Price Tracker
- [ ] Phase 4: Startup Wiring
- [ ] Phase 5: Configuration Update
- [ ] Quality Checks Passed
- [ ] All Tests Pass
- [ ] Success Validation Complete

**Notes:**
[Sonnet adds notes here during implementation]
