"""Unit tests for admin authentication."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from interfaces.http.admin_auth import verify_admin_api_key


class TestAdminAuthentication:
    """Test admin API key authentication."""

    def test_verify_admin_api_key_success(self):
        """Test that valid API key passes verification."""
        settings = MagicMock()
        settings.admin_api_key = "test_secret_key_12345"

        # Should not raise
        verify_admin_api_key(x_api_key="test_secret_key_12345", settings=settings)

    def test_verify_admin_api_key_missing_header(self):
        """Test that missing X-API-Key header raises 401."""
        settings = MagicMock()
        settings.admin_api_key = "test_secret_key_12345"

        with pytest.raises(HTTPException) as exc_info:
            verify_admin_api_key(x_api_key=None, settings=settings)

        assert exc_info.value.status_code == 401
        assert "Missing X-API-Key header" in exc_info.value.detail

    def test_verify_admin_api_key_invalid_key(self):
        """Test that invalid API key raises 401."""
        settings = MagicMock()
        settings.admin_api_key = "correct_key"

        with pytest.raises(HTTPException) as exc_info:
            verify_admin_api_key(x_api_key="wrong_key", settings=settings)

        assert exc_info.value.status_code == 401
        assert "Invalid API key" in exc_info.value.detail

    def test_verify_admin_api_key_not_configured(self):
        """Test that unconfigured admin key raises 503."""
        settings = MagicMock()
        settings.admin_api_key = None

        with pytest.raises(HTTPException) as exc_info:
            verify_admin_api_key(x_api_key="any_key", settings=settings)

        assert exc_info.value.status_code == 503
        assert "Admin API key not configured" in exc_info.value.detail

    def test_verify_admin_api_key_timing_attack_resistance(self):
        """Test that verification uses constant-time comparison."""
        from unittest.mock import patch

        settings = MagicMock()
        settings.admin_api_key = "correct_key"

        # Mock secrets.compare_digest to verify it's called
        with patch("interfaces.http.admin_auth.secrets.compare_digest") as mock_compare:
            mock_compare.return_value = True

            verify_admin_api_key(x_api_key="test_key", settings=settings)

            # Verify constant-time comparison was used
            mock_compare.assert_called_once_with("test_key", "correct_key")

    def test_verify_admin_api_key_case_sensitive(self):
        """Test that API key comparison is case-sensitive."""
        settings = MagicMock()
        settings.admin_api_key = "TestKey123"

        # Lowercase should fail
        with pytest.raises(HTTPException) as exc_info:
            verify_admin_api_key(x_api_key="testkey123", settings=settings)

        assert exc_info.value.status_code == 401

        # Uppercase should fail
        with pytest.raises(HTTPException) as exc_info:
            verify_admin_api_key(x_api_key="TESTKEY123", settings=settings)

        assert exc_info.value.status_code == 401

        # Exact match should succeed
        verify_admin_api_key(x_api_key="TestKey123", settings=settings)

    def test_verify_admin_api_key_empty_string(self):
        """Test that empty string API key raises 401."""
        settings = MagicMock()
        settings.admin_api_key = "correct_key"

        with pytest.raises(HTTPException) as exc_info:
            verify_admin_api_key(x_api_key="", settings=settings)

        assert exc_info.value.status_code == 401

    def test_verify_admin_api_key_whitespace(self):
        """Test that whitespace in API key matters."""
        settings = MagicMock()
        settings.admin_api_key = "correct_key"

        # Key with leading/trailing spaces should fail
        with pytest.raises(HTTPException) as exc_info:
            verify_admin_api_key(x_api_key=" correct_key ", settings=settings)

        assert exc_info.value.status_code == 401
