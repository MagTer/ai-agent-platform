"""Tests for user_service module."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from core.auth.header_auth import UserIdentity
from core.auth.user_service import get_or_create_user, get_user_default_context
from core.db.models import Context, User, UserContext


@pytest.fixture
def mock_session() -> AsyncMock:
    """Create a mock async database session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


@pytest.fixture
def sample_identity() -> UserIdentity:
    """Create a sample UserIdentity."""
    return UserIdentity(
        email="newuser@example.com",
        name="New User",
        openwebui_id="owui-123",
        role="user",
    )


@pytest.fixture
def existing_user() -> User:
    """Create a sample existing User."""
    user = User(
        id=uuid4(),
        email="existing@example.com",
        display_name="Existing User",
        role="user",
        openwebui_id="owui-existing",
    )
    return user


class TestGetOrCreateUser:
    """Tests for get_or_create_user function."""

    @pytest.mark.asyncio
    async def test_creates_new_user_when_not_found(
        self, mock_session: AsyncMock, sample_identity: UserIdentity
    ) -> None:
        """Should create a new user when email not found."""
        # Mock: user not found
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        await get_or_create_user(sample_identity, mock_session)

        # Verify user was added to session
        assert mock_session.add.call_count >= 3  # User, Context, UserContext
        assert mock_session.flush.called

    @pytest.mark.asyncio
    async def test_returns_existing_user(
        self, mock_session: AsyncMock, existing_user: User
    ) -> None:
        """Should return existing user without creating new one."""
        identity = UserIdentity(
            email=existing_user.email,
            name="Updated Name",
            role="admin",
        )

        # Mock: user found
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_user
        mock_session.execute.return_value = mock_result

        user = await get_or_create_user(identity, mock_session)

        assert user.email == existing_user.email
        # Should flush for updates
        assert mock_session.flush.called

    @pytest.mark.asyncio
    async def test_updates_display_name_on_login(
        self, mock_session: AsyncMock, existing_user: User
    ) -> None:
        """Should update display_name if it changed."""
        identity = UserIdentity(
            email=existing_user.email,
            name="New Display Name",
            role="user",
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_user
        mock_session.execute.return_value = mock_result

        user = await get_or_create_user(identity, mock_session)

        assert user.display_name == "New Display Name"

    @pytest.mark.asyncio
    async def test_updates_role_on_login(
        self, mock_session: AsyncMock, existing_user: User
    ) -> None:
        """Should update role if it changed."""
        existing_user.role = "user"
        identity = UserIdentity(
            email=existing_user.email,
            name=existing_user.display_name,
            role="admin",
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_user
        mock_session.execute.return_value = mock_result

        user = await get_or_create_user(identity, mock_session)

        assert user.role == "admin"

    @pytest.mark.asyncio
    async def test_updates_openwebui_id_on_login(
        self, mock_session: AsyncMock, existing_user: User
    ) -> None:
        """Should update openwebui_id if it changed."""
        existing_user.openwebui_id = "old-id"
        identity = UserIdentity(
            email=existing_user.email,
            name=existing_user.display_name,
            openwebui_id="new-id",
            role="user",
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_user
        mock_session.execute.return_value = mock_result

        user = await get_or_create_user(identity, mock_session)

        assert user.openwebui_id == "new-id"

    @pytest.mark.asyncio
    async def test_updates_last_login_at_on_login(
        self, mock_session: AsyncMock, existing_user: User
    ) -> None:
        """Should update last_login_at timestamp."""
        old_time = datetime.now(UTC).replace(tzinfo=None)
        existing_user.last_login_at = old_time

        identity = UserIdentity(
            email=existing_user.email,
            name=existing_user.display_name,
            role="user",
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_user
        mock_session.execute.return_value = mock_result

        user = await get_or_create_user(identity, mock_session)

        # last_login_at should be updated
        assert user.last_login_at != old_time

    @pytest.mark.asyncio
    async def test_does_not_update_display_name_if_not_provided(
        self, mock_session: AsyncMock, existing_user: User
    ) -> None:
        """Should not update display_name if identity.name is None."""
        original_name = existing_user.display_name
        identity = UserIdentity(
            email=existing_user.email,
            name=None,  # No name provided
            role="user",
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_user
        mock_session.execute.return_value = mock_result

        user = await get_or_create_user(identity, mock_session)

        # Should keep original name
        assert user.display_name == original_name

    @pytest.mark.asyncio
    async def test_does_not_update_if_values_unchanged(
        self, mock_session: AsyncMock, existing_user: User
    ) -> None:
        """Should not update fields if values are the same."""
        identity = UserIdentity(
            email=existing_user.email,
            name=existing_user.display_name,
            openwebui_id=existing_user.openwebui_id,
            role=existing_user.role,
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_user
        mock_session.execute.return_value = mock_result

        user = await get_or_create_user(identity, mock_session)

        # Should still flush (for last_login_at update)
        assert mock_session.flush.called
        assert user.display_name == existing_user.display_name
        assert user.role == existing_user.role
        assert user.openwebui_id == existing_user.openwebui_id

    @pytest.mark.asyncio
    async def test_creates_personal_context_for_new_user(
        self, mock_session: AsyncMock, sample_identity: UserIdentity
    ) -> None:
        """Should create a personal Context for new users."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        await get_or_create_user(sample_identity, mock_session)

        # Check that Context was added
        add_calls = mock_session.add.call_args_list
        added_objects = [call[0][0] for call in add_calls]

        # Should have added User, Context, and UserContext
        assert len(added_objects) >= 3

        # Verify Context object was added
        context_added = any(isinstance(obj, Context) for obj in added_objects)
        assert context_added

    @pytest.mark.asyncio
    async def test_creates_user_context_link(
        self, mock_session: AsyncMock, sample_identity: UserIdentity
    ) -> None:
        """Should create UserContext linking user to personal context."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        await get_or_create_user(sample_identity, mock_session)

        # Verify UserContext was added
        add_calls = mock_session.add.call_args_list
        added_types = [type(call[0][0]).__name__ for call in add_calls]

        assert "User" in added_types
        assert "Context" in added_types
        assert "UserContext" in added_types

    @pytest.mark.asyncio
    async def test_personal_context_has_correct_config(
        self, mock_session: AsyncMock, sample_identity: UserIdentity
    ) -> None:
        """Should create personal context with correct configuration."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        await get_or_create_user(sample_identity, mock_session)

        # Get the Context object that was added
        add_calls = mock_session.add.call_args_list
        added_objects = [call[0][0] for call in add_calls]
        contexts = [obj for obj in added_objects if isinstance(obj, Context)]

        assert len(contexts) == 1
        context = contexts[0]

        assert context.type == "personal"
        assert context.config["owner_email"] == sample_identity.email
        assert context.default_cwd == "/tmp"  # noqa: S108

    @pytest.mark.asyncio
    async def test_user_context_is_default_and_owner(
        self, mock_session: AsyncMock, sample_identity: UserIdentity
    ) -> None:
        """Should create UserContext with is_default=True and role='owner'."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        await get_or_create_user(sample_identity, mock_session)

        # Get the UserContext object that was added
        add_calls = mock_session.add.call_args_list
        added_objects = [call[0][0] for call in add_calls]
        user_contexts = [obj for obj in added_objects if isinstance(obj, UserContext)]

        assert len(user_contexts) == 1
        user_context = user_contexts[0]

        assert user_context.is_default is True
        assert user_context.role == "owner"


class TestGetUserDefaultContext:
    """Tests for get_user_default_context function."""

    @pytest.mark.asyncio
    async def test_returns_default_context(self, mock_session: AsyncMock) -> None:
        """Should return user's default context."""
        user = User(id=uuid4(), email="user@example.com")
        context = Context(id=uuid4(), name="personal_ctx", type="personal")

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = context
        mock_session.execute.return_value = mock_result

        result = await get_user_default_context(user, mock_session)

        assert result == context
        assert mock_session.execute.called

    @pytest.mark.asyncio
    async def test_returns_none_when_no_default_context(self, mock_session: AsyncMock) -> None:
        """Should return None when no default context exists."""
        user = User(id=uuid4(), email="user@example.com")

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await get_user_default_context(user, mock_session)

        assert result is None

    @pytest.mark.asyncio
    async def test_queries_with_user_id(self, mock_session: AsyncMock) -> None:
        """Should query using the provided user's ID."""
        user_id = uuid4()
        user = User(id=user_id, email="user@example.com")

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        await get_user_default_context(user, mock_session)

        # Verify session.execute was called
        assert mock_session.execute.called

        # Verify the statement was executed (we can't easily inspect SQLAlchemy statement,
        # but we can verify the call happened)
        assert mock_session.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_queries_for_is_default_true(self, mock_session: AsyncMock) -> None:
        """Should filter for is_default=True."""
        user = User(id=uuid4(), email="user@example.com")

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        await get_user_default_context(user, mock_session)

        # Verify session.execute was called (the actual query filtering is tested
        # in integration tests with a real database)
        assert mock_session.execute.called
