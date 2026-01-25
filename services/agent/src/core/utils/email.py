"""Email utility functions for core.

These utilities provide HTML template generation for emails.
Located in core to avoid module-to-module dependencies.
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


__all__ = ["wrap_html_email"]
