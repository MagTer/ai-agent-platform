"""Send email tool for the agent."""

from __future__ import annotations

import logging
import re

from core.protocols.email import EmailMessage
from core.providers import get_email_service_optional
from core.tools.base import Tool
from core.utils.email import wrap_html_email

LOGGER = logging.getLogger(__name__)


class SendEmailTool(Tool):
    """Send an email to the authenticated user.

    This tool allows the agent to email reports, summaries, and reminders
    to the user who initiated the conversation.

    Security:
    - Only the authenticated user can receive emails (no arbitrary recipients)
    - Rate limiting enforced at API level
    - Email service must be configured (Resend API key required)
    """

    name = "send_email"
    description = (
        "Send an email to yourself with a report, summary, or reminder. "
        "You MUST provide the email subject and body content. "
        "The body should be well-formatted text (plain text or markdown). "
        "Example: send_email(subject='Meeting Summary', body='Here are the key points...')"
    )
    category = "communication"

    parameters = {
        "type": "object",
        "properties": {
            "subject": {
                "type": "string",
                "description": "Email subject line (required, max 200 chars)",
            },
            "body": {
                "type": "string",
                "description": (
                    "Email body content. Can be plain text or markdown. "
                    "Will be formatted as HTML for email delivery."
                ),
            },
        },
        "required": ["subject", "body"],
    }

    activity_hint = {"subject": 'Sending email: "{subject}"'}

    def __init__(
        self,
        *,
        allow_external_recipients: bool = False,
        max_body_length: int = 50000,
    ) -> None:
        """Initialize the email tool.

        Args:
            allow_external_recipients: If True, allow sending to any email.
                Default False (self-only mode for security).
            max_body_length: Maximum characters allowed in email body.
        """
        self._allow_external = allow_external_recipients
        self._max_body_length = max_body_length

    async def run(
        self,
        subject: str,
        body: str,
        user_email: str | None = None,
        **kwargs: object,
    ) -> str:
        """Send an email to the authenticated user.

        Args:
            subject: Email subject line.
            body: Email body content (text or markdown).
            user_email: Authenticated user's email (injected by agent service).
            **kwargs: Additional arguments (ignored).

        Returns:
            Success message with email details, or error message.

        Raises:
            ToolError: If email service is not configured or validation fails.
        """
        # Validate email service is configured
        email_service = get_email_service_optional()
        if email_service is None:
            LOGGER.warning("Email tool called but email service not configured")
            return (
                "Email service is not configured. "
                "Please contact your administrator to enable email features."
            )

        if not email_service.is_configured():
            LOGGER.warning("Email service exists but is not properly configured")
            return "Email service is not properly configured. " "Please contact your administrator."

        # Validate user email
        if not user_email:
            LOGGER.warning("Email tool called without user_email")
            return (
                "Unable to determine your email address. "
                "Please ensure you are logged in via Open WebUI with email headers enabled."
            )

        # Validate email format
        if not self._is_valid_email(user_email):
            LOGGER.warning(f"Invalid user email format: {user_email}")
            return f"Invalid email format: {user_email}"

        # Validate subject
        subject = subject.strip()
        if not subject:
            return "Email subject is required."
        if len(subject) > 200:
            subject = subject[:197] + "..."

        # Validate body
        body = body.strip()
        if not body:
            return "Email body is required."
        if len(body) > self._max_body_length:
            body = body[: self._max_body_length - 50] + "\n\n[Content truncated]"

        # Convert markdown to simple HTML
        html_body = self._markdown_to_html(body)

        # Wrap in email template
        html_content = wrap_html_email(
            title=subject,
            body_content=html_body,
            footer_text="This email was sent by AI Agent Platform on your request.",
        )

        # Create email message
        message = EmailMessage(
            to=[user_email],
            subject=subject,
            html_body=html_content,
            text_body=body,  # Plain text fallback
        )

        # Send email
        LOGGER.info(f"Sending email to {user_email}: {subject[:50]}")
        result = await email_service.send(message)

        if result.success:
            LOGGER.info(f"Email sent successfully to {user_email}, id={result.message_id}")
            return f"Email sent successfully to {user_email}.\nSubject: {subject}"
        else:
            LOGGER.error(f"Email send failed: {result.error}")
            return f"Failed to send email: {result.error}"

    @staticmethod
    def _is_valid_email(email: str) -> bool:
        """Basic email format validation."""
        pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        return bool(re.match(pattern, email))

    @staticmethod
    def _markdown_to_html(text: str) -> str:
        """Convert simple markdown to HTML.

        Handles:
        - Headers (# ## ###)
        - Bold (**text**)
        - Italic (*text*)
        - Bullet lists (- item)
        - Numbered lists (1. item)
        - Line breaks

        For complex markdown, consider using a library like markdown2.
        """
        import html

        # Escape HTML entities first
        text = html.escape(text)

        # Headers
        text = re.sub(r"^### (.+)$", r"<h4>\1</h4>", text, flags=re.MULTILINE)
        text = re.sub(r"^## (.+)$", r"<h3>\1</h3>", text, flags=re.MULTILINE)
        text = re.sub(r"^# (.+)$", r"<h2>\1</h2>", text, flags=re.MULTILINE)

        # Bold and italic
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)

        # Bullet lists (simple approach)
        lines = text.split("\n")
        result_lines = []
        in_list = False

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("- "):
                if not in_list:
                    result_lines.append("<ul>")
                    in_list = True
                result_lines.append(f"<li>{stripped[2:]}</li>")
            elif re.match(r"^\d+\. ", stripped):
                if not in_list:
                    result_lines.append("<ol>")
                    in_list = True
                # Extract content after numbered list prefix
                list_content = re.sub(r"^\d+\. ", "", stripped)
                result_lines.append(f"<li>{list_content}</li>")
            else:
                if in_list:
                    prev_line = lines[lines.index(line) - 1] if lines.index(line) > 0 else ""
                    close_tag = "</ul>" if "- " in prev_line else "</ol>"
                    result_lines.append(close_tag)
                    in_list = False
                result_lines.append(line)

        if in_list:
            result_lines.append("</ul>")

        text = "\n".join(result_lines)

        # Line breaks (paragraphs)
        text = re.sub(r"\n\n+", "</p><p>", text)
        text = text.replace("\n", "<br>")
        text = f"<p>{text}</p>"

        return text


__all__ = ["SendEmailTool"]
