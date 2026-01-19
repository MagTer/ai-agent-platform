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

    body_style = (
        "font-family: Arial, sans-serif; max-width: 600px; "
        "margin: 0 auto; padding: 20px; color: #333;"
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
</head>
<body style="{body_style}">
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
            f'<td style="padding: 8px; border-bottom: 1px solid #eee;">{cell}</td>' for cell in row
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
