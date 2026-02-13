"""Tests for internal API key authentication."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from core.runtime.config import Settings
from interfaces.http.app import verify_internal_api_key


@pytest.fixture
def auth_settings() -> Settings:
    """Settings with internal API key configured."""
    return Settings(internal_api_key="test-key-12345")


@pytest.fixture
def no_auth_settings() -> Settings:
    """Settings without internal API key (auth disabled)."""
    return Settings(internal_api_key=None)


def test_verify_api_key_with_bearer_token(auth_settings: Settings) -> None:
    """Test authentication with Bearer token in Authorization header."""
    # Should not raise exception
    verify_internal_api_key(
        authorization="Bearer test-key-12345",
        x_api_key=None,
        settings=auth_settings,
    )


def test_verify_api_key_with_x_api_key_header(auth_settings: Settings) -> None:
    """Test authentication with X-API-Key header."""
    # Should not raise exception
    verify_internal_api_key(
        authorization=None,
        x_api_key="test-key-12345",
        settings=auth_settings,
    )


def test_verify_api_key_x_api_key_takes_precedence(auth_settings: Settings) -> None:
    """Test that X-API-Key takes precedence over Authorization header."""
    # Should not raise exception (X-API-Key is correct)
    verify_internal_api_key(
        authorization="Bearer wrong-key",
        x_api_key="test-key-12345",
        settings=auth_settings,
    )


def test_verify_api_key_missing_key(auth_settings: Settings) -> None:
    """Test that missing API key raises 401."""
    with pytest.raises(HTTPException) as exc_info:
        verify_internal_api_key(
            authorization=None,
            x_api_key=None,
            settings=auth_settings,
        )
    assert exc_info.value.status_code == 401
    assert "Invalid or missing API key" in str(exc_info.value.detail)


def test_verify_api_key_invalid_key(auth_settings: Settings) -> None:
    """Test that invalid API key raises 401."""
    with pytest.raises(HTTPException) as exc_info:
        verify_internal_api_key(
            authorization="Bearer wrong-key",
            x_api_key=None,
            settings=auth_settings,
        )
    assert exc_info.value.status_code == 401


def test_verify_api_key_disabled_when_not_configured(no_auth_settings: Settings) -> None:
    """Test that auth is skipped when AGENT_INTERNAL_API_KEY is not set."""
    # Should not raise exception even with no headers
    verify_internal_api_key(
        authorization=None,
        x_api_key=None,
        settings=no_auth_settings,
    )


def test_verify_api_key_malformed_bearer_token(auth_settings: Settings) -> None:
    """Test that malformed Bearer token raises 401."""
    with pytest.raises(HTTPException) as exc_info:
        verify_internal_api_key(
            authorization="Bearer",  # Missing key part
            x_api_key=None,
            settings=auth_settings,
        )
    assert exc_info.value.status_code == 401


def test_verify_api_key_empty_string(auth_settings: Settings) -> None:
    """Test that empty string key raises 401."""
    with pytest.raises(HTTPException) as exc_info:
        verify_internal_api_key(
            authorization="Bearer ",
            x_api_key=None,
            settings=auth_settings,
        )
    assert exc_info.value.status_code == 401
