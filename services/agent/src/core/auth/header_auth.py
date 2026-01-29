"""Trusted header authentication for Open WebUI integration."""

from dataclasses import dataclass
from urllib.parse import unquote

from fastapi import Request


@dataclass
class UserIdentity:
    """User identity extracted from Open WebUI headers."""

    email: str
    name: str | None = None
    openwebui_id: str | None = None
    role: str = "user"


def _decode_header_value(value: str | None) -> str | None:
    """Decode header value, handling URL encoding and UTF-8 as Latin-1.

    Handles two common encoding issues:
    1. URL encoding: %C3%B6 -> ö
    2. UTF-8 as Latin-1: Ã¶ -> ö
    """
    if not value:
        return None

    # First, try URL decoding (handles %C3%B6 -> ö)
    decoded = unquote(value)

    # Then, try fixing Latin-1/UTF-8 encoding
    try:
        # If the string contains Latin-1 artifacts of UTF-8, fix it
        return decoded.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        # Already valid UTF-8 or other encoding, return URL-decoded value
        return decoded


def extract_user_from_headers(request: Request) -> UserIdentity | None:
    """Extract user identity from X-OpenWebUI-* headers.

    Returns None if no user email header is present (anonymous request).

    Headers expected:
        X-OpenWebUI-User-Email: user@example.com (required)
        X-OpenWebUI-User-Name: John Doe (optional)
        X-OpenWebUI-User-Id: uuid (optional)
        X-OpenWebUI-User-Role: user|admin (optional, defaults to "user")
    """
    email = request.headers.get("x-openwebui-user-email")
    if not email:
        return None

    return UserIdentity(
        email=email.lower().strip(),  # Normalize email
        name=_decode_header_value(request.headers.get("x-openwebui-user-name")),
        openwebui_id=request.headers.get("x-openwebui-user-id"),
        role=request.headers.get("x-openwebui-user-role", "user"),
    )
