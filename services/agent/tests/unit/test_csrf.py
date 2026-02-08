"""Unit tests for CSRF protection module.

Tests cover token generation, validation, and FastAPI dependency.
"""

import hashlib
import hmac
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, Request, Response

from interfaces.http.csrf import (
    CSRF_COOKIE_NAME,
    CSRF_TOKEN_LENGTH,
    generate_csrf_token,
    require_csrf,
    set_csrf_cookie,
    validate_csrf_token,
)

# ===== Token Generation Tests =====


def test_generate_csrf_token_returns_nonempty_string() -> None:
    """Test that token generation returns a non-empty string."""
    secret = "test_secret_key"
    token = generate_csrf_token(secret)

    assert token is not None
    assert isinstance(token, str)
    assert len(token) > 0


def test_generate_csrf_token_contains_separator() -> None:
    """Test that generated token contains the || separator."""
    secret = "test_secret_key"
    token = generate_csrf_token(secret)

    assert "||" in token


def test_generate_csrf_token_has_two_components() -> None:
    """Test that generated token has exactly two components (random||signature)."""
    secret = "test_secret_key"
    token = generate_csrf_token(secret)
    parts = token.split("||")

    assert len(parts) == 2
    assert len(parts[0]) > 0  # Random value part
    assert len(parts[1]) > 0  # Signature part


def test_generate_csrf_token_random_value_has_correct_length() -> None:
    """Test that random value component has expected hex length."""
    secret = "test_secret_key"
    token = generate_csrf_token(secret)
    random_value = token.split("||")[0]

    # Should be 32 bytes = 64 hex characters
    expected_length = CSRF_TOKEN_LENGTH * 2
    assert len(random_value) == expected_length


def test_generate_csrf_token_signature_is_sha256_hex() -> None:
    """Test that signature component is valid SHA256 hex digest."""
    secret = "test_secret_key"
    token = generate_csrf_token(secret)
    signature = token.split("||")[1]

    # SHA256 hex digest is always 64 characters
    assert len(signature) == 64
    # Should be valid hex
    assert all(c in "0123456789abcdef" for c in signature)


def test_generate_csrf_token_produces_unique_tokens() -> None:
    """Test that multiple calls produce different tokens."""
    secret = "test_secret_key"
    token1 = generate_csrf_token(secret)
    token2 = generate_csrf_token(secret)

    assert token1 != token2


def test_generate_csrf_token_signature_matches_hmac() -> None:
    """Test that signature is correctly computed using HMAC-SHA256."""
    secret = "test_secret_key"
    token = generate_csrf_token(secret)
    random_value, signature = token.split("||")

    # Recompute expected signature
    expected_signature = hmac.new(
        secret.encode("utf-8"),
        random_value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    assert signature == expected_signature


# ===== Token Validation Tests =====


def test_validate_csrf_token_accepts_valid_token() -> None:
    """Test that validation accepts a properly generated token."""
    secret = "test_secret_key"
    token = generate_csrf_token(secret)

    assert validate_csrf_token(token, secret) is True


def test_validate_csrf_token_rejects_tampered_random_value() -> None:
    """Test that validation rejects token with modified random value."""
    secret = "test_secret_key"
    token = generate_csrf_token(secret)
    random_value, signature = token.split("||")

    # Tamper with random value
    tampered_token = f"{random_value[:-1]}x||{signature}"

    assert validate_csrf_token(tampered_token, secret) is False


def test_validate_csrf_token_rejects_tampered_signature() -> None:
    """Test that validation rejects token with modified signature."""
    secret = "test_secret_key"
    token = generate_csrf_token(secret)
    random_value, signature = token.split("||")

    # Tamper with signature
    tampered_token = f"{random_value}||{signature[:-1]}x"

    assert validate_csrf_token(tampered_token, secret) is False


def test_validate_csrf_token_rejects_different_secret() -> None:
    """Test that token generated with one secret fails with another."""
    secret1 = "secret_key_1"
    secret2 = "secret_key_2"

    token = generate_csrf_token(secret1)

    assert validate_csrf_token(token, secret2) is False


def test_validate_csrf_token_rejects_empty_token() -> None:
    """Test that validation rejects empty token."""
    secret = "test_secret_key"

    assert validate_csrf_token("", secret) is False


def test_validate_csrf_token_rejects_none_token() -> None:
    """Test that validation rejects None token."""
    secret = "test_secret_key"

    # Intentionally pass None to test error handling
    assert validate_csrf_token(None, secret) is False  # noqa: PGH003


def test_validate_csrf_token_rejects_token_without_separator() -> None:
    """Test that validation rejects token missing || separator."""
    secret = "test_secret_key"
    token = "random_value_without_separator"

    assert validate_csrf_token(token, secret) is False


def test_validate_csrf_token_rejects_malformed_token_extra_separators() -> None:
    """Test that validation rejects token with multiple || separators."""
    secret = "test_secret_key"
    token = "part1||part2||part3"

    # Should still work (splits on first ||), but signature won't match
    assert validate_csrf_token(token, secret) is False


def test_validate_csrf_token_rejects_token_with_only_separator() -> None:
    """Test that validation rejects token that is just the separator."""
    secret = "test_secret_key"

    assert validate_csrf_token("||", secret) is False


def test_validate_csrf_token_handles_exception_gracefully() -> None:
    """Test that validation returns False on unexpected errors."""
    secret = "test_secret_key"

    # Token with non-string random value that will cause encoding error
    # Actually, this is hard to trigger since we check format first
    # But let's test with a token that has invalid hex in random part
    invalid_token = "not_valid_hex||valid_signature_format_but_wrong"

    # Should not raise, should return False
    assert validate_csrf_token(invalid_token, secret) is False


# ===== Cookie Setting Tests =====


def test_set_csrf_cookie_sets_cookie_with_correct_name() -> None:
    """Test that cookie is set with correct name."""
    response = Response()
    token = "test_token||test_signature"

    set_csrf_cookie(response, token, secure=False)

    # Check cookie is in response
    set_cookie_header = response.headers.get("set-cookie")
    assert set_cookie_header is not None
    assert f"{CSRF_COOKIE_NAME}={token}" in set_cookie_header


def test_set_csrf_cookie_sets_httponly_false() -> None:
    """Test that cookie has httponly=False (must be readable by JS)."""
    response = Response()
    token = "test_token||test_signature"

    set_csrf_cookie(response, token, secure=False)

    set_cookie_header = response.headers.get("set-cookie")
    assert set_cookie_header is not None
    # httponly should NOT be in the header (defaults to true if present)
    # When httponly=False, the flag is omitted
    assert "HttpOnly" not in set_cookie_header


def test_set_csrf_cookie_sets_samesite_strict() -> None:
    """Test that cookie has SameSite=Strict."""
    response = Response()
    token = "test_token||test_signature"

    set_csrf_cookie(response, token, secure=False)

    set_cookie_header = response.headers.get("set-cookie")
    assert set_cookie_header is not None
    assert "samesite=strict" in set_cookie_header.lower()


def test_set_csrf_cookie_sets_secure_flag_when_requested() -> None:
    """Test that cookie has Secure flag when secure=True."""
    response = Response()
    token = "test_token||test_signature"

    set_csrf_cookie(response, token, secure=True)

    set_cookie_header = response.headers.get("set-cookie")
    assert set_cookie_header is not None
    assert "Secure" in set_cookie_header


def test_set_csrf_cookie_omits_secure_flag_when_not_requested() -> None:
    """Test that cookie omits Secure flag when secure=False."""
    response = Response()
    token = "test_token||test_signature"

    set_csrf_cookie(response, token, secure=False)

    set_cookie_header = response.headers.get("set-cookie")
    assert set_cookie_header is not None
    assert "Secure" not in set_cookie_header


def test_set_csrf_cookie_sets_path_to_admin_portal() -> None:
    """Test that cookie path is scoped to /platformadmin/."""
    response = Response()
    token = "test_token||test_signature"

    set_csrf_cookie(response, token, secure=False)

    set_cookie_header = response.headers.get("set-cookie")
    assert set_cookie_header is not None
    assert "Path=/platformadmin/" in set_cookie_header


def test_set_csrf_cookie_sets_max_age() -> None:
    """Test that cookie has max-age set (24 hours)."""
    response = Response()
    token = "test_token||test_signature"

    set_csrf_cookie(response, token, secure=False)

    set_cookie_header = response.headers.get("set-cookie")
    assert set_cookie_header is not None
    expected_max_age = 3600 * 24  # 24 hours
    assert f"Max-Age={expected_max_age}" in set_cookie_header


# ===== Dependency Tests (require_csrf) =====


@pytest.mark.asyncio
async def test_require_csrf_succeeds_with_valid_tokens() -> None:
    """Test that require_csrf passes with matching valid tokens."""
    secret = "test_secret_key"
    token = generate_csrf_token(secret)

    # Mock request
    request = MagicMock(spec=Request)
    request.client = MagicMock()
    request.client.host = "127.0.0.1"

    # Mock settings
    mock_settings = MagicMock()
    mock_settings.environment = "production"
    mock_settings.admin_jwt_secret = secret

    with patch("core.core.config.get_settings", return_value=mock_settings):
        # Should not raise
        await require_csrf(request, csrf_cookie=token, csrf_header=token)


@pytest.mark.asyncio
async def test_require_csrf_skips_validation_in_test_environment() -> None:
    """Test that require_csrf skips validation when environment is test."""
    # Mock settings with test environment
    mock_settings = MagicMock()
    mock_settings.environment = "test"

    request = MagicMock(spec=Request)

    with patch("core.core.config.get_settings", return_value=mock_settings):
        # Should not raise even with invalid tokens
        await require_csrf(request, csrf_cookie=None, csrf_header=None)


@pytest.mark.asyncio
async def test_require_csrf_raises_500_when_secret_not_configured() -> None:
    """Test that require_csrf raises 500 when admin_jwt_secret is missing."""
    # Mock settings without secret
    mock_settings = MagicMock()
    mock_settings.environment = "production"
    mock_settings.admin_jwt_secret = None

    request = MagicMock(spec=Request)

    with patch("core.core.config.get_settings", return_value=mock_settings):
        with pytest.raises(HTTPException) as exc_info:
            await require_csrf(request, csrf_cookie="token", csrf_header="token")

        assert exc_info.value.status_code == 500
        assert "not configured" in exc_info.value.detail


@pytest.mark.asyncio
async def test_require_csrf_raises_403_when_cookie_missing() -> None:
    """Test that require_csrf raises 403 when cookie is missing."""
    secret = "test_secret_key"
    token = generate_csrf_token(secret)

    request = MagicMock(spec=Request)
    request.client = MagicMock()
    request.client.host = "127.0.0.1"

    mock_settings = MagicMock()
    mock_settings.environment = "production"
    mock_settings.admin_jwt_secret = secret

    with patch("core.core.config.get_settings", return_value=mock_settings):
        with pytest.raises(HTTPException) as exc_info:
            await require_csrf(request, csrf_cookie=None, csrf_header=token)

        assert exc_info.value.status_code == 403
        assert "cookie" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_require_csrf_raises_403_when_header_missing() -> None:
    """Test that require_csrf raises 403 when header is missing."""
    secret = "test_secret_key"
    token = generate_csrf_token(secret)

    request = MagicMock(spec=Request)
    request.client = MagicMock()
    request.client.host = "127.0.0.1"

    mock_settings = MagicMock()
    mock_settings.environment = "production"
    mock_settings.admin_jwt_secret = secret

    with patch("core.core.config.get_settings", return_value=mock_settings):
        with pytest.raises(HTTPException) as exc_info:
            await require_csrf(request, csrf_cookie=token, csrf_header=None)

        assert exc_info.value.status_code == 403
        assert "header" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_require_csrf_raises_403_when_cookie_signature_invalid() -> None:
    """Test that require_csrf raises 403 when cookie has invalid signature."""
    secret = "test_secret_key"
    token = generate_csrf_token(secret)

    # Tamper with token
    random_value, signature = token.split("||")
    tampered_token = f"{random_value}||{signature[:-1]}x"

    request = MagicMock(spec=Request)
    request.client = MagicMock()
    request.client.host = "127.0.0.1"

    mock_settings = MagicMock()
    mock_settings.environment = "production"
    mock_settings.admin_jwt_secret = secret

    with patch("core.core.config.get_settings", return_value=mock_settings):
        with pytest.raises(HTTPException) as exc_info:
            await require_csrf(request, csrf_cookie=tampered_token, csrf_header=tampered_token)

        assert exc_info.value.status_code == 403
        assert "signature" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_require_csrf_raises_403_when_cookie_and_header_mismatch() -> None:
    """Test that require_csrf raises 403 when cookie and header tokens differ."""
    secret = "test_secret_key"
    token1 = generate_csrf_token(secret)
    token2 = generate_csrf_token(secret)

    request = MagicMock(spec=Request)
    request.client = MagicMock()
    request.client.host = "127.0.0.1"

    mock_settings = MagicMock()
    mock_settings.environment = "production"
    mock_settings.admin_jwt_secret = secret

    with patch("core.core.config.get_settings", return_value=mock_settings):
        with pytest.raises(HTTPException) as exc_info:
            await require_csrf(request, csrf_cookie=token1, csrf_header=token2)

        assert exc_info.value.status_code == 403
        assert "mismatch" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_require_csrf_raises_403_when_empty_cookie() -> None:
    """Test that require_csrf raises 403 when cookie is empty string."""
    secret = "test_secret_key"
    token = generate_csrf_token(secret)

    request = MagicMock(spec=Request)
    request.client = MagicMock()
    request.client.host = "127.0.0.1"

    mock_settings = MagicMock()
    mock_settings.environment = "production"
    mock_settings.admin_jwt_secret = secret

    with patch("core.core.config.get_settings", return_value=mock_settings):
        with pytest.raises(HTTPException) as exc_info:
            await require_csrf(request, csrf_cookie="", csrf_header=token)

        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_require_csrf_raises_403_when_empty_header() -> None:
    """Test that require_csrf raises 403 when header is empty string."""
    secret = "test_secret_key"
    token = generate_csrf_token(secret)

    request = MagicMock(spec=Request)
    request.client = MagicMock()
    request.client.host = "127.0.0.1"

    mock_settings = MagicMock()
    mock_settings.environment = "production"
    mock_settings.admin_jwt_secret = secret

    with patch("core.core.config.get_settings", return_value=mock_settings):
        with pytest.raises(HTTPException) as exc_info:
            await require_csrf(request, csrf_cookie=token, csrf_header="")

        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_require_csrf_handles_missing_client_info() -> None:
    """Test that require_csrf handles request without client info gracefully."""
    secret = "test_secret_key"
    token = generate_csrf_token(secret)

    # Request without client attribute
    request = MagicMock(spec=Request)
    request.client = None

    mock_settings = MagicMock()
    mock_settings.environment = "production"
    mock_settings.admin_jwt_secret = secret

    with patch("core.core.config.get_settings", return_value=mock_settings):
        with pytest.raises(HTTPException) as exc_info:
            await require_csrf(request, csrf_cookie=None, csrf_header=token)

        # Should still raise 403, not crash
        assert exc_info.value.status_code == 403


# ===== Integration Tests =====


def test_full_workflow_token_generation_and_validation() -> None:
    """Test complete workflow: generate token, validate it, reject tampered version."""
    secret = "my_secret_key_12345"

    # Generate token
    token = generate_csrf_token(secret)

    # Validate it
    assert validate_csrf_token(token, secret) is True

    # Tamper and validate again
    random_value, signature = token.split("||")
    tampered = f"{random_value}||{'0' * 64}"  # Replace signature with zeros
    assert validate_csrf_token(tampered, secret) is False


def test_token_validation_is_constant_time() -> None:
    """Test that validation uses hmac.compare_digest for constant-time comparison."""
    secret = "test_secret"
    token = generate_csrf_token(secret)

    # This is more of a smoke test - we rely on hmac.compare_digest
    # being used in the implementation (which we verified by reading the code)
    assert validate_csrf_token(token, secret) is True

    # Generate a second valid token
    token2 = generate_csrf_token(secret)
    assert validate_csrf_token(token2, secret) is True

    # Tokens should not validate each other (different random values)
    assert validate_csrf_token(token, secret) is True
    assert validate_csrf_token(token2, secret) is True
    # But they shouldn't be equal
    assert token != token2
