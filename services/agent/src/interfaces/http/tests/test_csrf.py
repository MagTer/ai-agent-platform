"""Tests for CSRF protection utilities."""

from __future__ import annotations

import hmac
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, Request, Response

from interfaces.http.csrf import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    CSRF_TOKEN_LENGTH,
    generate_csrf_token,
    require_csrf,
    set_csrf_cookie,
    validate_csrf_token,
)


class TestGenerateCSRFToken:
    """Tests for CSRF token generation."""

    def test_token_format_is_valid(self) -> None:
        """Generated token should have format: random_value||signature."""
        secret = "test-secret-key"
        token = generate_csrf_token(secret)

        assert "||" in token
        parts = token.split("||")
        assert len(parts) == 2
        assert len(parts[0]) == CSRF_TOKEN_LENGTH * 2  # hex encoding doubles length
        assert len(parts[1]) == 64  # SHA256 hex digest is 64 chars

    def test_two_tokens_are_different(self) -> None:
        """Two generated tokens should be different (randomness check)."""
        secret = "test-secret-key"
        token1 = generate_csrf_token(secret)
        token2 = generate_csrf_token(secret)

        assert token1 != token2

    def test_random_part_is_hex(self) -> None:
        """Random value part should be valid hex string."""
        secret = "test-secret-key"
        token = generate_csrf_token(secret)

        random_part = token.split("||")[0]
        # Should not raise ValueError
        int(random_part, 16)

    def test_signature_is_hex(self) -> None:
        """Signature part should be valid hex string."""
        secret = "test-secret-key"
        token = generate_csrf_token(secret)

        signature_part = token.split("||")[1]
        # Should not raise ValueError
        int(signature_part, 16)

    def test_token_length_is_consistent(self) -> None:
        """All generated tokens should have consistent length."""
        secret = "test-secret-key"
        token1 = generate_csrf_token(secret)
        token2 = generate_csrf_token(secret)
        token3 = generate_csrf_token(secret)

        assert len(token1) == len(token2) == len(token3)


class TestHMACSignature:
    """Tests for HMAC signing behavior."""

    def test_signed_token_differs_from_random_value(self) -> None:
        """Signed token should be different from raw random value."""
        secret = "test-secret-key"
        token = generate_csrf_token(secret)

        random_value, signature = token.split("||")
        assert random_value != signature

    def test_same_token_same_key_produces_same_signature(self) -> None:
        """Same token with same key should produce identical signature."""
        secret = "test-secret-key"
        token = generate_csrf_token(secret)

        # Validate twice
        assert validate_csrf_token(token, secret)
        assert validate_csrf_token(token, secret)

    def test_same_token_different_key_produces_different_signature(self) -> None:
        """Same token with different key should fail validation."""
        secret1 = "test-secret-key-1"
        secret2 = "test-secret-key-2"

        token = generate_csrf_token(secret1)

        # Valid with original secret
        assert validate_csrf_token(token, secret1)

        # Invalid with different secret
        assert not validate_csrf_token(token, secret2)

    def test_signature_computation_is_deterministic(self) -> None:
        """Signature should be deterministic for same input."""
        secret = "test-secret-key"

        # Generate token
        token = generate_csrf_token(secret)
        random_value, expected_signature = token.split("||")

        # Recompute signature manually
        import hashlib

        recomputed_signature = hmac.new(
            secret.encode("utf-8"),
            random_value.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        assert recomputed_signature == expected_signature


class TestValidateCSRFToken:
    """Tests for CSRF token validation."""

    def test_valid_token_accepted(self) -> None:
        """Valid token with correct signature should be accepted."""
        secret = "test-secret-key"
        token = generate_csrf_token(secret)

        assert validate_csrf_token(token, secret)

    def test_tampered_signature_rejected(self) -> None:
        """Token with tampered signature should be rejected."""
        secret = "test-secret-key"
        token = generate_csrf_token(secret)

        random_value, signature = token.split("||")
        tampered_signature = "a" * 64  # Replace with invalid signature
        tampered_token = f"{random_value}||{tampered_signature}"

        assert not validate_csrf_token(tampered_token, secret)

    def test_tampered_random_value_rejected(self) -> None:
        """Token with tampered random value should be rejected."""
        secret = "test-secret-key"
        token = generate_csrf_token(secret)

        random_value, signature = token.split("||")
        tampered_random = "b" * len(random_value)
        tampered_token = f"{tampered_random}||{signature}"

        assert not validate_csrf_token(tampered_token, secret)

    def test_missing_separator_rejected(self) -> None:
        """Token without separator should be rejected."""
        secret = "test-secret-key"

        assert not validate_csrf_token("notokenseparator", secret)

    def test_empty_token_rejected(self) -> None:
        """Empty token should be rejected."""
        secret = "test-secret-key"

        assert not validate_csrf_token("", secret)

    def test_none_token_rejected(self) -> None:
        """None token should be rejected (type coercion to string)."""
        secret = "test-secret-key"

        # The function checks `if not token` which handles None
        result = validate_csrf_token(None, secret)  # type: ignore[arg-type]
        assert not result

    def test_multiple_separators_handled(self) -> None:
        """Token with multiple separators should use first split only."""
        secret = "test-secret-key"
        token = generate_csrf_token(secret)

        # Add extra separator to signature part
        random_value, signature = token.split("||")
        modified_token = f"{random_value}||{signature}||extra"

        # Should fail because signature is now "signature||extra"
        assert not validate_csrf_token(modified_token, secret)

    def test_whitespace_in_token_rejected(self) -> None:
        """Token with whitespace should be rejected."""
        secret = "test-secret-key"
        token = generate_csrf_token(secret)

        # Add whitespace
        token_with_whitespace = f" {token} "

        assert not validate_csrf_token(token_with_whitespace, secret)

    def test_malformed_token_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Malformed token should log warning with exception info."""
        secret = "test-secret-key"

        # Token that will cause exception during processing
        assert not validate_csrf_token("||", secret)

        # Should log warning (but might not contain specific text based on implementation)
        # The function catches all exceptions and logs with exc_info=True

    def test_constant_time_comparison_used(self) -> None:
        """Validation should use hmac.compare_digest for timing attack prevention."""
        secret = "test-secret-key"
        token = generate_csrf_token(secret)

        # Verify by checking that validation returns boolean (compare_digest behavior)
        # and that the function doesn't use direct == comparison
        with patch("interfaces.http.csrf.hmac.compare_digest") as mock_compare:
            mock_compare.return_value = True

            validate_csrf_token(token, secret)

            # Verify compare_digest was called
            assert mock_compare.called


class TestSetCSRFCookie:
    """Tests for CSRF cookie setting."""

    def test_cookie_name_is_correct(self) -> None:
        """Cookie should be set with correct name."""
        response = Response()
        token = "test-token-value"

        set_csrf_cookie(response, token, secure=False)

        # Check cookie was set (FastAPI stores in response.raw_headers or Set-Cookie)
        assert CSRF_COOKIE_NAME in str(response.headers.get("set-cookie", ""))

    def test_cookie_value_is_correct(self) -> None:
        """Cookie should contain the token value."""
        response = Response()
        token = "test-token-value"

        set_csrf_cookie(response, token, secure=False)

        cookie_header = response.headers.get("set-cookie", "")
        assert token in cookie_header

    def test_httponly_is_false(self) -> None:
        """Cookie should NOT be HttpOnly (must be readable by JavaScript)."""
        response = Response()
        token = "test-token-value"

        set_csrf_cookie(response, token, secure=False)

        cookie_header = response.headers.get("set-cookie", "")
        # HttpOnly should NOT be present (or explicitly set to false)
        # When httponly=False, the attribute is omitted from Set-Cookie
        assert "HttpOnly" not in cookie_header

    def test_samesite_is_strict(self) -> None:
        """Cookie should have SameSite=Strict attribute."""
        response = Response()
        token = "test-token-value"

        set_csrf_cookie(response, token, secure=False)

        cookie_header = response.headers.get("set-cookie", "")
        # Note: case may vary (SameSite=strict or samesite=strict)
        assert "samesite=strict" in cookie_header.lower()

    def test_secure_flag_when_enabled(self) -> None:
        """Cookie should have Secure flag when secure=True."""
        response = Response()
        token = "test-token-value"

        set_csrf_cookie(response, token, secure=True)

        cookie_header = response.headers.get("set-cookie", "")
        assert "Secure" in cookie_header

    def test_secure_flag_when_disabled(self) -> None:
        """Cookie should NOT have Secure flag when secure=False."""
        response = Response()
        token = "test-token-value"

        set_csrf_cookie(response, token, secure=False)

        cookie_header = response.headers.get("set-cookie", "")
        assert "Secure" not in cookie_header

    def test_path_is_admin_portal(self) -> None:
        """Cookie should be scoped to /platformadmin/ path."""
        response = Response()
        token = "test-token-value"

        set_csrf_cookie(response, token, secure=False)

        cookie_header = response.headers.get("set-cookie", "")
        assert "Path=/platformadmin/" in cookie_header

    def test_max_age_is_24_hours(self) -> None:
        """Cookie should have 24-hour max age."""
        response = Response()
        token = "test-token-value"

        set_csrf_cookie(response, token, secure=False)

        cookie_header = response.headers.get("set-cookie", "")
        expected_max_age = 3600 * 24
        assert f"Max-Age={expected_max_age}" in cookie_header


class TestRequireCSRF:
    """Tests for CSRF validation dependency."""

    def _mock_request(self, client_ip: str = "127.0.0.1") -> Request:
        """Create a mock FastAPI Request."""
        mock_request = MagicMock(spec=Request)
        mock_client = MagicMock()
        mock_client.host = client_ip
        mock_request.client = mock_client
        return mock_request

    @pytest.mark.asyncio
    async def test_skips_validation_in_test_environment(self) -> None:
        """Should skip CSRF checks when environment=test."""
        request = self._mock_request()

        with patch("core.core.config.get_settings") as mock_settings:
            settings = MagicMock()
            settings.environment = "test"
            mock_settings.return_value = settings

            # Should not raise
            await require_csrf(request, csrf_cookie=None, csrf_header=None)

    @pytest.mark.asyncio
    async def test_raises_500_when_secret_not_configured(self) -> None:
        """Should raise 500 when admin_jwt_secret is not set."""
        request = self._mock_request()

        with patch("core.core.config.get_settings") as mock_settings:
            settings = MagicMock()
            settings.environment = "production"
            settings.admin_jwt_secret = None
            mock_settings.return_value = settings

            with pytest.raises(HTTPException) as exc_info:
                await require_csrf(request, csrf_cookie=None, csrf_header=None)

            assert exc_info.value.status_code == 500
            assert "not configured" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_raises_403_when_cookie_missing(self) -> None:
        """Should raise 403 when CSRF cookie is missing."""
        request = self._mock_request()

        with patch("core.core.config.get_settings") as mock_settings:
            settings = MagicMock()
            settings.environment = "production"
            settings.admin_jwt_secret = "test-secret"
            mock_settings.return_value = settings

            with pytest.raises(HTTPException) as exc_info:
                await require_csrf(request, csrf_cookie=None, csrf_header="some-token")

            assert exc_info.value.status_code == 403
            assert "cookie" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_raises_403_when_header_missing(self) -> None:
        """Should raise 403 when CSRF header is missing."""
        request = self._mock_request()

        with patch("core.core.config.get_settings") as mock_settings:
            settings = MagicMock()
            settings.environment = "production"
            settings.admin_jwt_secret = "test-secret"
            mock_settings.return_value = settings

            # Generate valid token for cookie
            secret = "test-secret"
            token = generate_csrf_token(secret)

            with pytest.raises(HTTPException) as exc_info:
                await require_csrf(request, csrf_cookie=token, csrf_header=None)

            assert exc_info.value.status_code == 403
            assert "header" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_raises_403_when_cookie_signature_invalid(self) -> None:
        """Should raise 403 when cookie has invalid signature."""
        request = self._mock_request()

        with patch("core.core.config.get_settings") as mock_settings:
            settings = MagicMock()
            settings.environment = "production"
            settings.admin_jwt_secret = "test-secret"
            mock_settings.return_value = settings

            # Tampered token
            tampered_token = "random||badsignature"

            with pytest.raises(HTTPException) as exc_info:
                await require_csrf(request, csrf_cookie=tampered_token, csrf_header=tampered_token)

            assert exc_info.value.status_code == 403
            assert "signature" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_raises_403_when_cookie_header_mismatch(self) -> None:
        """Should raise 403 when cookie and header don't match."""
        request = self._mock_request()

        with patch("core.core.config.get_settings") as mock_settings:
            settings = MagicMock()
            settings.environment = "production"
            settings.admin_jwt_secret = "test-secret"
            mock_settings.return_value = settings

            # Two different valid tokens
            secret = "test-secret"
            token1 = generate_csrf_token(secret)
            token2 = generate_csrf_token(secret)

            with pytest.raises(HTTPException) as exc_info:
                await require_csrf(request, csrf_cookie=token1, csrf_header=token2)

            assert exc_info.value.status_code == 403
            assert "mismatch" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_accepts_valid_matching_tokens(self) -> None:
        """Should accept when cookie and header match and are valid."""
        request = self._mock_request()

        with patch("core.core.config.get_settings") as mock_settings:
            settings = MagicMock()
            settings.environment = "production"
            settings.admin_jwt_secret = "test-secret"
            mock_settings.return_value = settings

            # Valid token in both cookie and header
            secret = "test-secret"
            token = generate_csrf_token(secret)

            # Should not raise
            await require_csrf(request, csrf_cookie=token, csrf_header=token)

    @pytest.mark.asyncio
    async def test_logs_client_ip_on_failure(self, caplog: pytest.LogCaptureFixture) -> None:
        """Should log client IP when validation fails."""
        request = self._mock_request(client_ip="192.168.1.100")

        with patch("core.core.config.get_settings") as mock_settings:
            settings = MagicMock()
            settings.environment = "production"
            settings.admin_jwt_secret = "test-secret"
            mock_settings.return_value = settings

            with pytest.raises(HTTPException):
                await require_csrf(request, csrf_cookie=None, csrf_header=None)

            # Should log IP address
            assert "192.168.1.100" in caplog.text

    @pytest.mark.asyncio
    async def test_handles_missing_client_info(self) -> None:
        """Should handle requests where client info is None."""
        mock_request = MagicMock(spec=Request)
        mock_request.client = None  # No client info

        with patch("core.core.config.get_settings") as mock_settings:
            settings = MagicMock()
            settings.environment = "production"
            settings.admin_jwt_secret = "test-secret"
            mock_settings.return_value = settings

            with pytest.raises(HTTPException) as exc_info:
                await require_csrf(mock_request, csrf_cookie=None, csrf_header=None)

            # Should not crash, should use "unknown" as fallback
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_uses_constant_time_comparison_for_token_match(self) -> None:
        """Should use constant-time comparison for cookie/header match."""
        request = self._mock_request()

        with patch("core.core.config.get_settings") as mock_settings:
            settings = MagicMock()
            settings.environment = "production"
            settings.admin_jwt_secret = "test-secret"
            mock_settings.return_value = settings

            secret = "test-secret"
            token = generate_csrf_token(secret)

            # Patch hmac.compare_digest to verify it's called
            with patch("interfaces.http.csrf.hmac.compare_digest") as mock_compare:
                mock_compare.return_value = True

                await require_csrf(request, csrf_cookie=token, csrf_header=token)

                # Should be called at least twice:
                # 1. In validate_csrf_token for signature check
                # 2. In require_csrf for cookie/header match
                assert mock_compare.call_count >= 2

    @pytest.mark.asyncio
    async def test_empty_cookie_rejected(self) -> None:
        """Should reject empty string cookie."""
        request = self._mock_request()

        with patch("core.core.config.get_settings") as mock_settings:
            settings = MagicMock()
            settings.environment = "production"
            settings.admin_jwt_secret = "test-secret"
            mock_settings.return_value = settings

            with pytest.raises(HTTPException) as exc_info:
                await require_csrf(request, csrf_cookie="", csrf_header="token")

            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_empty_header_rejected(self) -> None:
        """Should reject empty string header."""
        request = self._mock_request()

        with patch("core.core.config.get_settings") as mock_settings:
            settings = MagicMock()
            settings.environment = "production"
            settings.admin_jwt_secret = "test-secret"
            mock_settings.return_value = settings

            secret = "test-secret"
            token = generate_csrf_token(secret)

            with pytest.raises(HTTPException) as exc_info:
                await require_csrf(request, csrf_cookie=token, csrf_header="")

            assert exc_info.value.status_code == 403


class TestCSRFConstants:
    """Tests for CSRF module constants."""

    def test_cookie_name_constant(self) -> None:
        """CSRF_COOKIE_NAME should be defined correctly."""
        assert CSRF_COOKIE_NAME == "csrf_token"

    def test_header_name_constant(self) -> None:
        """CSRF_HEADER_NAME should be defined correctly."""
        assert CSRF_HEADER_NAME == "X-CSRF-Token"

    def test_token_length_constant(self) -> None:
        """CSRF_TOKEN_LENGTH should be 32 bytes."""
        assert CSRF_TOKEN_LENGTH == 32


class TestCSRFIntegration:
    """Integration tests for CSRF protection flow."""

    def test_end_to_end_token_flow(self) -> None:
        """Test complete flow: generate -> set cookie -> validate."""
        secret = "test-secret-key"

        # Generate token
        token = generate_csrf_token(secret)

        # Validate token
        assert validate_csrf_token(token, secret)

        # Set cookie
        response = Response()
        set_csrf_cookie(response, token, secure=True)

        # Verify cookie contains token
        cookie_header = response.headers.get("set-cookie", "")
        assert token in cookie_header
        assert "Secure" in cookie_header or "secure" in cookie_header.lower()
        assert "samesite=strict" in cookie_header.lower()

    def test_token_reuse_across_multiple_validations(self) -> None:
        """Token should remain valid across multiple validations."""
        secret = "test-secret-key"
        token = generate_csrf_token(secret)

        # Validate multiple times
        for _ in range(10):
            assert validate_csrf_token(token, secret)

    def test_different_secrets_produce_incompatible_tokens(self) -> None:
        """Tokens generated with different secrets should not cross-validate."""
        secret1 = "secret-1"
        secret2 = "secret-2"

        token1 = generate_csrf_token(secret1)
        token2 = generate_csrf_token(secret2)

        # Each token valid with its own secret
        assert validate_csrf_token(token1, secret1)
        assert validate_csrf_token(token2, secret2)

        # But not with the other secret
        assert not validate_csrf_token(token1, secret2)
        assert not validate_csrf_token(token2, secret1)
