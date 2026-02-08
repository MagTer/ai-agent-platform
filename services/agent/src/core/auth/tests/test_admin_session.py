"""Tests for JWT session management for admin portal."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import Request

from core.auth.admin_session import (
    COOKIE_NAME,
    JWT_ALGORITHM,
    JWT_EXPIRY_HOURS,
    create_admin_jwt,
    get_jwt_from_request,
    verify_admin_jwt,
)


class TestCreateAdminJWT:
    """Tests for JWT token creation."""

    def test_creates_valid_jwt_structure(self) -> None:
        """Created JWT should have valid structure (header.payload.signature)."""
        user_id = uuid4()
        secret = "test-secret-key"

        token = create_admin_jwt(
            user_id=user_id,
            email="test@example.com",
            name="Test User",
            role="admin",
            secret_key=secret,
        )

        # JWT structure: header.payload.signature
        parts = token.split(".")
        assert len(parts) == 3

    def test_token_contains_user_id_in_subject(self) -> None:
        """Token should contain user_id in 'sub' claim."""
        user_id = uuid4()
        secret = "test-secret-key"

        token = create_admin_jwt(
            user_id=user_id,
            email="test@example.com",
            name="Test User",
            role="admin",
            secret_key=secret,
        )

        # Decode without verification to check payload
        payload = jwt.decode(token, options={"verify_signature": False})
        assert payload["sub"] == str(user_id)

    def test_token_contains_email(self) -> None:
        """Token should contain email in payload."""
        user_id = uuid4()
        secret = "test-secret-key"
        email = "admin@example.com"

        token = create_admin_jwt(
            user_id=user_id,
            email=email,
            name="Test User",
            role="admin",
            secret_key=secret,
        )

        payload = jwt.decode(token, options={"verify_signature": False})
        assert payload["email"] == email

    def test_token_contains_name(self) -> None:
        """Token should contain user display name in payload."""
        user_id = uuid4()
        secret = "test-secret-key"
        name = "John Doe"

        token = create_admin_jwt(
            user_id=user_id,
            email="test@example.com",
            name=name,
            role="admin",
            secret_key=secret,
        )

        payload = jwt.decode(token, options={"verify_signature": False})
        assert payload["name"] == name

    def test_token_contains_role(self) -> None:
        """Token should contain user role in payload."""
        user_id = uuid4()
        secret = "test-secret-key"
        role = "admin"

        token = create_admin_jwt(
            user_id=user_id,
            email="test@example.com",
            name="Test User",
            role=role,
            secret_key=secret,
        )

        payload = jwt.decode(token, options={"verify_signature": False})
        assert payload["role"] == role

    def test_token_has_issued_at_timestamp(self) -> None:
        """Token should contain 'iat' (issued at) timestamp."""
        user_id = uuid4()
        secret = "test-secret-key"

        before_creation = int(datetime.utcnow().timestamp())
        token = create_admin_jwt(
            user_id=user_id,
            email="test@example.com",
            name="Test User",
            role="admin",
            secret_key=secret,
        )
        after_creation = int(datetime.utcnow().timestamp())

        payload = jwt.decode(token, options={"verify_signature": False})
        assert "iat" in payload
        assert before_creation <= payload["iat"] <= after_creation

    def test_token_has_expiry_timestamp(self) -> None:
        """Token should contain 'exp' (expiry) timestamp."""
        user_id = uuid4()
        secret = "test-secret-key"

        token = create_admin_jwt(
            user_id=user_id,
            email="test@example.com",
            name="Test User",
            role="admin",
            secret_key=secret,
        )

        payload = jwt.decode(token, options={"verify_signature": False})
        assert "exp" in payload

        # Expiry should be approximately JWT_EXPIRY_HOURS from now
        expected_expiry = int((datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS)).timestamp())
        actual_expiry = payload["exp"]

        # Allow 5 second tolerance for test execution time
        assert abs(expected_expiry - actual_expiry) <= 5

    def test_token_expiry_is_24_hours(self) -> None:
        """Token should expire in 24 hours."""
        user_id = uuid4()
        secret = "test-secret-key"

        token = create_admin_jwt(
            user_id=user_id,
            email="test@example.com",
            name="Test User",
            role="admin",
            secret_key=secret,
        )

        payload = jwt.decode(token, options={"verify_signature": False})

        # Calculate difference between exp and iat
        expiry_duration = payload["exp"] - payload["iat"]

        # Should be 24 hours (with small tolerance)
        expected_duration = JWT_EXPIRY_HOURS * 3600
        assert abs(expiry_duration - expected_duration) <= 5

    def test_token_uses_hs256_algorithm(self) -> None:
        """Token should use HS256 algorithm."""
        user_id = uuid4()
        secret = "test-secret-key"

        token = create_admin_jwt(
            user_id=user_id,
            email="test@example.com",
            name="Test User",
            role="admin",
            secret_key=secret,
        )

        # Decode header without verification
        header = jwt.get_unverified_header(token)
        assert header["alg"] == JWT_ALGORITHM
        assert header["alg"] == "HS256"

    def test_different_users_produce_different_tokens(self) -> None:
        """Different user IDs should produce different tokens."""
        secret = "test-secret-key"

        token1 = create_admin_jwt(
            user_id=uuid4(),
            email="user1@example.com",
            name="User One",
            role="admin",
            secret_key=secret,
        )

        token2 = create_admin_jwt(
            user_id=uuid4(),
            email="user2@example.com",
            name="User Two",
            role="admin",
            secret_key=secret,
        )

        assert token1 != token2

    def test_same_user_different_times_produces_different_tokens(self) -> None:
        """Same user at different times should produce different tokens (different iat)."""
        user_id = uuid4()
        secret = "test-secret-key"

        token1 = create_admin_jwt(
            user_id=user_id,
            email="test@example.com",
            name="Test User",
            role="admin",
            secret_key=secret,
        )

        # Wait enough time to ensure different timestamp (at least 1 second)
        time.sleep(1.1)

        token2 = create_admin_jwt(
            user_id=user_id,
            email="test@example.com",
            name="Test User",
            role="admin",
            secret_key=secret,
        )

        assert token1 != token2

    def test_token_is_signed_with_secret(self) -> None:
        """Token should be properly signed and verifiable with secret."""
        user_id = uuid4()
        secret = "test-secret-key"

        token = create_admin_jwt(
            user_id=user_id,
            email="test@example.com",
            name="Test User",
            role="admin",
            secret_key=secret,
        )

        # Should verify successfully
        payload = jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
        assert payload["sub"] == str(user_id)

    def test_token_verification_fails_with_wrong_secret(self) -> None:
        """Token should fail verification with different secret."""
        user_id = uuid4()
        secret = "correct-secret"
        wrong_secret = "wrong-secret"

        token = create_admin_jwt(
            user_id=user_id,
            email="test@example.com",
            name="Test User",
            role="admin",
            secret_key=secret,
        )

        # Should raise InvalidSignatureError
        with pytest.raises(jwt.InvalidSignatureError):
            jwt.decode(token, wrong_secret, algorithms=[JWT_ALGORITHM])

    def test_uuid_converted_to_string_in_subject(self) -> None:
        """UUID should be converted to string in 'sub' claim."""
        user_id = uuid4()
        secret = "test-secret-key"

        token = create_admin_jwt(
            user_id=user_id,
            email="test@example.com",
            name="Test User",
            role="admin",
            secret_key=secret,
        )

        payload = jwt.decode(token, options={"verify_signature": False})

        # Should be string representation
        assert isinstance(payload["sub"], str)
        assert payload["sub"] == str(user_id)

        # Should be parseable back to UUID
        parsed_uuid = UUID(payload["sub"])
        assert parsed_uuid == user_id


class TestVerifyAdminJWT:
    """Tests for JWT token verification."""

    def test_verifies_valid_token(self) -> None:
        """Valid token should be verified successfully."""
        user_id = uuid4()
        secret = "test-secret-key"
        email = "test@example.com"
        name = "Test User"
        role = "admin"

        token = create_admin_jwt(
            user_id=user_id,
            email=email,
            name=name,
            role=role,
            secret_key=secret,
        )

        payload = verify_admin_jwt(token, secret)

        assert payload is not None
        assert payload["sub"] == str(user_id)
        assert payload["email"] == email
        assert payload["name"] == name
        assert payload["role"] == role

    def test_returns_none_for_expired_token(self) -> None:
        """Expired token should return None."""
        user_id = uuid4()
        secret = "test-secret-key"

        # Create token that's already expired
        now = datetime.utcnow()
        expired_time = now - timedelta(hours=1)

        payload = {
            "sub": str(user_id),
            "email": "test@example.com",
            "name": "Test User",
            "role": "admin",
            "iat": int(expired_time.timestamp()),
            "exp": int((expired_time + timedelta(seconds=1)).timestamp()),  # Expired 59 min ago
        }

        expired_token = jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)

        result = verify_admin_jwt(expired_token, secret)
        assert result is None

    def test_returns_none_for_invalid_signature(self) -> None:
        """Token with invalid signature should return None."""
        user_id = uuid4()
        secret = "correct-secret"
        wrong_secret = "wrong-secret"

        token = create_admin_jwt(
            user_id=user_id,
            email="test@example.com",
            name="Test User",
            role="admin",
            secret_key=secret,
        )

        result = verify_admin_jwt(token, wrong_secret)
        assert result is None

    def test_returns_none_for_malformed_token(self) -> None:
        """Malformed token should return None."""
        secret = "test-secret-key"

        # Not a valid JWT
        malformed_token = "not.a.valid.jwt.token"

        result = verify_admin_jwt(malformed_token, secret)
        assert result is None

    def test_returns_none_for_empty_token(self) -> None:
        """Empty token should return None."""
        secret = "test-secret-key"

        result = verify_admin_jwt("", secret)
        assert result is None

    def test_returns_none_for_tampered_payload(self) -> None:
        """Token with tampered payload should return None."""
        user_id = uuid4()
        secret = "test-secret-key"

        token = create_admin_jwt(
            user_id=user_id,
            email="test@example.com",
            name="Test User",
            role="admin",
            secret_key=secret,
        )

        # Tamper with payload by modifying the middle part
        parts = token.split(".")
        # Replace payload with different data (will break signature)
        import base64

        tampered_payload = base64.urlsafe_b64encode(b'{"sub":"hacker"}').decode().rstrip("=")
        tampered_token = f"{parts[0]}.{tampered_payload}.{parts[2]}"

        result = verify_admin_jwt(tampered_token, secret)
        assert result is None

    def test_logs_expired_token_at_debug_level(self, caplog: pytest.LogCaptureFixture) -> None:
        """Expired token should log at debug level."""
        import logging

        caplog.set_level(logging.DEBUG)

        user_id = uuid4()
        secret = "test-secret-key"

        # Create expired token
        now = datetime.utcnow()
        expired_time = now - timedelta(hours=1)

        payload = {
            "sub": str(user_id),
            "email": "test@example.com",
            "name": "Test User",
            "role": "admin",
            "iat": int(expired_time.timestamp()),
            "exp": int((expired_time + timedelta(seconds=1)).timestamp()),
        }

        expired_token = jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)

        verify_admin_jwt(expired_token, secret)

        # Should log expiry message
        assert "expired" in caplog.text.lower()

    def test_logs_invalid_token_at_debug_level(self, caplog: pytest.LogCaptureFixture) -> None:
        """Invalid token should log at debug level."""
        import logging

        caplog.set_level(logging.DEBUG)

        secret = "test-secret-key"
        invalid_token = "completely.invalid.token"

        verify_admin_jwt(invalid_token, secret)

        # Should log invalid token message
        assert "invalid" in caplog.text.lower()

    def test_preserves_all_payload_fields(self) -> None:
        """All payload fields should be preserved in verification."""
        user_id = uuid4()
        secret = "test-secret-key"
        email = "admin@example.com"
        name = "Admin User"
        role = "superadmin"

        token = create_admin_jwt(
            user_id=user_id,
            email=email,
            name=name,
            role=role,
            secret_key=secret,
        )

        payload = verify_admin_jwt(token, secret)

        assert payload is not None
        assert payload["sub"] == str(user_id)
        assert payload["email"] == email
        assert payload["name"] == name
        assert payload["role"] == role
        assert "iat" in payload
        assert "exp" in payload

    def test_handles_unicode_in_name(self) -> None:
        """Should handle Unicode characters in user name."""
        user_id = uuid4()
        secret = "test-secret-key"
        name = "Jöhn Döe 日本語"

        token = create_admin_jwt(
            user_id=user_id,
            email="test@example.com",
            name=name,
            role="admin",
            secret_key=secret,
        )

        payload = verify_admin_jwt(token, secret)

        assert payload is not None
        assert payload["name"] == name

    def test_returns_dict_with_string_values(self) -> None:
        """Verified payload should return dict with string values."""
        user_id = uuid4()
        secret = "test-secret-key"

        token = create_admin_jwt(
            user_id=user_id,
            email="test@example.com",
            name="Test User",
            role="admin",
            secret_key=secret,
        )

        payload = verify_admin_jwt(token, secret)

        assert payload is not None
        assert isinstance(payload, dict)
        # Check type hints match (dict[str, str] in signature, though iat/exp are int)
        assert isinstance(payload["sub"], str)
        assert isinstance(payload["email"], str)
        assert isinstance(payload["name"], str)
        assert isinstance(payload["role"], str)


class TestGetJWTFromRequest:
    """Tests for extracting JWT from request."""

    def test_extracts_token_from_cookie(self) -> None:
        """Should extract JWT from admin_session cookie."""
        token = "test.jwt.token"

        # Mock FastAPI Request
        mock_request = MagicMock(spec=Request)
        mock_request.cookies = {COOKIE_NAME: token}

        result = get_jwt_from_request(mock_request)
        assert result == token

    def test_returns_none_when_cookie_missing(self) -> None:
        """Should return None when cookie is not present."""
        mock_request = MagicMock(spec=Request)
        mock_request.cookies = {}

        result = get_jwt_from_request(mock_request)
        assert result is None

    def test_uses_correct_cookie_name(self) -> None:
        """Should use COOKIE_NAME constant for lookup."""
        token = "test.jwt.token"

        mock_request = MagicMock(spec=Request)
        mock_request.cookies = {COOKIE_NAME: token, "other_cookie": "other_value"}

        result = get_jwt_from_request(mock_request)
        assert result == token

    def test_handles_empty_string_cookie(self) -> None:
        """Should return empty string if cookie value is empty."""
        mock_request = MagicMock(spec=Request)
        mock_request.cookies = {COOKIE_NAME: ""}

        result = get_jwt_from_request(mock_request)
        assert result == ""

    def test_handles_multiple_cookies(self) -> None:
        """Should extract correct cookie when multiple cookies present."""
        token = "correct.jwt.token"

        mock_request = MagicMock(spec=Request)
        mock_request.cookies = {
            "session": "other_session",
            COOKIE_NAME: token,
            "csrf_token": "csrf_value",
        }

        result = get_jwt_from_request(mock_request)
        assert result == token

    def test_returns_none_for_none_cookies(self) -> None:
        """Should handle request with None cookies gracefully."""
        mock_request = MagicMock(spec=Request)

        # Configure the mock to return None when .get() is called
        mock_cookies = MagicMock()
        mock_cookies.get.return_value = None
        mock_request.cookies = mock_cookies

        result = get_jwt_from_request(mock_request)
        assert result is None


class TestJWTConstants:
    """Tests for JWT module constants."""

    def test_cookie_name_constant(self) -> None:
        """COOKIE_NAME should be 'admin_session'."""
        assert COOKIE_NAME == "admin_session"

    def test_jwt_algorithm_constant(self) -> None:
        """JWT_ALGORITHM should be 'HS256'."""
        assert JWT_ALGORITHM == "HS256"

    def test_jwt_expiry_hours_constant(self) -> None:
        """JWT_EXPIRY_HOURS should be 24."""
        assert JWT_EXPIRY_HOURS == 24


class TestJWTIntegration:
    """Integration tests for JWT flow."""

    def test_end_to_end_jwt_flow(self) -> None:
        """Test complete flow: create -> extract -> verify."""
        user_id = uuid4()
        email = "admin@example.com"
        name = "Admin User"
        role = "admin"
        secret = "test-secret-key"

        # Create token
        token = create_admin_jwt(
            user_id=user_id,
            email=email,
            name=name,
            role=role,
            secret_key=secret,
        )

        # Simulate request with cookie
        mock_request = MagicMock(spec=Request)
        mock_request.cookies = {COOKIE_NAME: token}

        # Extract token
        extracted_token = get_jwt_from_request(mock_request)
        assert extracted_token == token

        # Verify token
        payload = verify_admin_jwt(extracted_token, secret)
        assert payload is not None
        assert payload["sub"] == str(user_id)
        assert payload["email"] == email
        assert payload["name"] == name
        assert payload["role"] == role

    def test_token_remains_valid_until_expiry(self) -> None:
        """Token should remain valid for the full expiry period."""
        user_id = uuid4()
        secret = "test-secret-key"

        token = create_admin_jwt(
            user_id=user_id,
            email="test@example.com",
            name="Test User",
            role="admin",
            secret_key=secret,
        )

        # Verify immediately
        payload = verify_admin_jwt(token, secret)
        assert payload is not None

        # Verify again after short delay
        time.sleep(0.1)
        payload = verify_admin_jwt(token, secret)
        assert payload is not None

    def test_different_secrets_produce_incompatible_tokens(self) -> None:
        """Tokens created with different secrets should not cross-verify."""
        user_id = uuid4()
        secret1 = "secret-one"
        secret2 = "secret-two"

        token1 = create_admin_jwt(
            user_id=user_id,
            email="test@example.com",
            name="Test User",
            role="admin",
            secret_key=secret1,
        )

        token2 = create_admin_jwt(
            user_id=user_id,
            email="test@example.com",
            name="Test User",
            role="admin",
            secret_key=secret2,
        )

        # Each token valid with its own secret
        assert verify_admin_jwt(token1, secret1) is not None
        assert verify_admin_jwt(token2, secret2) is not None

        # But not with the other secret
        assert verify_admin_jwt(token1, secret2) is None
        assert verify_admin_jwt(token2, secret1) is None

    def test_token_reuse_across_multiple_verifications(self) -> None:
        """Token should remain valid across multiple verifications."""
        user_id = uuid4()
        secret = "test-secret-key"

        token = create_admin_jwt(
            user_id=user_id,
            email="test@example.com",
            name="Test User",
            role="admin",
            secret_key=secret,
        )

        # Verify multiple times
        for _ in range(10):
            payload = verify_admin_jwt(token, secret)
            assert payload is not None
            assert payload["sub"] == str(user_id)

    def test_token_with_missing_secret_fails_verification(self) -> None:
        """Token verification should fail when secret is missing."""
        user_id = uuid4()
        secret = "test-secret-key"

        token = create_admin_jwt(
            user_id=user_id,
            email="test@example.com",
            name="Test User",
            role="admin",
            secret_key=secret,
        )

        # Verify with empty secret
        result = verify_admin_jwt(token, "")
        assert result is None
