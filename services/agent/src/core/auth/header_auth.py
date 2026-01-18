"""Trusted header authentication for Open WebUI integration."""

from dataclasses import dataclass

from fastapi import Request


@dataclass
class UserIdentity:
    """User identity extracted from Open WebUI headers."""

    email: str
    name: str | None = None
    openwebui_id: str | None = None
    role: str = "user"


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
        name=request.headers.get("x-openwebui-user-name"),
        openwebui_id=request.headers.get("x-openwebui-user-id"),
        role=request.headers.get("x-openwebui-user-role", "user"),
    )
