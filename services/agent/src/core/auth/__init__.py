"""Authentication module."""

from core.auth.credential_service import CredentialService
from core.auth.header_auth import UserIdentity, extract_user_from_headers
from core.auth.user_service import get_or_create_user, get_user_default_context

__all__ = [
    "CredentialService",
    "UserIdentity",
    "extract_user_from_headers",
    "get_or_create_user",
    "get_user_default_context",
]
