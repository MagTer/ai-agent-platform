"""Tests for ContextService shared context resolution."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.auth.header_auth import UserIdentity
from core.context.service import ContextService


@pytest.fixture
def mock_session() -> AsyncMock:
    """Mock AsyncSession that simulates id assignment on flush."""
    session = AsyncMock()
    added_objects: list[object] = []

    def track_add(obj: object) -> None:
        added_objects.append(obj)

    async def simulate_flush() -> None:
        """Simulate SQLAlchemy flush: assign id to any added object missing one."""
        for obj in added_objects:
            if hasattr(obj, "id") and obj.id is None:
                obj.id = uuid.uuid4()

    session.add = MagicMock(side_effect=track_add)
    session.flush = AsyncMock(side_effect=simulate_flush)
    session._added = added_objects
    return session


@pytest.fixture
def identity() -> UserIdentity:
    return UserIdentity(email="test@example.com", name="Test User")


# --- resolve_for_authenticated_user ---


@pytest.mark.asyncio
async def test_resolve_for_authenticated_user_existing_context(
    mock_session: AsyncMock, identity: UserIdentity
) -> None:
    """Authenticated user with existing default context returns that context."""
    fake_user = MagicMock()
    fake_user.email = "test@example.com"
    fake_user.active_context_id = None
    fake_context = MagicMock()
    fake_context.id = uuid.uuid4()

    with (
        patch("core.context.service.get_or_create_user", new_callable=AsyncMock) as mock_get_user,
        patch(
            "core.context.service.get_user_default_context", new_callable=AsyncMock
        ) as mock_get_ctx,
    ):
        mock_get_user.return_value = fake_user
        mock_get_ctx.return_value = fake_context

        result = await ContextService.resolve_for_authenticated_user(identity, mock_session)

    assert result == fake_context.id
    mock_get_user.assert_awaited_once_with(identity, mock_session)
    mock_get_ctx.assert_awaited_once_with(fake_user, mock_session)


@pytest.mark.asyncio
async def test_resolve_for_authenticated_user_missing_context_creates_one(
    mock_session: AsyncMock, identity: UserIdentity
) -> None:
    """Authenticated user without default context gets a new personal context."""
    fake_user = MagicMock()
    fake_user.id = uuid.uuid4()
    fake_user.email = "test@example.com"
    fake_user.active_context_id = None

    with (
        patch("core.context.service.get_or_create_user", new_callable=AsyncMock) as mock_get_user,
        patch(
            "core.context.service.get_user_default_context", new_callable=AsyncMock
        ) as mock_get_ctx,
    ):
        mock_get_user.return_value = fake_user
        mock_get_ctx.return_value = None

        result = await ContextService.resolve_for_authenticated_user(identity, mock_session)

    mock_session.add.assert_called_once()
    added_context = mock_session.add.call_args[0][0]
    assert added_context.name == f"personal_{fake_user.id}"
    assert isinstance(result, uuid.UUID)


# --- resolve_for_platform ---


@pytest.mark.asyncio
async def test_resolve_for_platform_existing_conversation(mock_session: AsyncMock) -> None:
    """Platform with existing conversation returns its context_id."""
    fake_conversation = MagicMock()
    fake_conversation.context_id = uuid.uuid4()

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = fake_conversation
    mock_session.execute.return_value = mock_result

    result = await ContextService.resolve_for_platform("telegram", "12345", mock_session)

    assert result == fake_conversation.context_id


@pytest.mark.asyncio
async def test_resolve_for_platform_new_conversation(mock_session: AsyncMock) -> None:
    """Platform with no existing conversation creates a new context."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    result = await ContextService.resolve_for_platform("telegram", "99999", mock_session)

    mock_session.add.assert_called_once()
    added_context = mock_session.add.call_args[0][0]
    assert added_context.name == "telegram_99999"
    assert added_context.type == "shared"
    assert isinstance(result, uuid.UUID)


# --- resolve_for_conversation_id ---


@pytest.mark.asyncio
async def test_resolve_for_conversation_id_valid_uuid_found(mock_session: AsyncMock) -> None:
    """Valid UUID that matches existing conversation returns its context."""
    conv_uuid = uuid.uuid4()
    fake_conversation = MagicMock()
    fake_conversation.context_id = uuid.uuid4()

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = fake_conversation
    mock_session.execute.return_value = mock_result

    result = await ContextService.resolve_for_conversation_id(
        str(conv_uuid), "openwebui", mock_session
    )

    assert result == fake_conversation.context_id


@pytest.mark.asyncio
async def test_resolve_for_conversation_id_valid_uuid_not_found_creates_context(
    mock_session: AsyncMock,
) -> None:
    """Valid UUID with no conversation creates context with correct name."""
    conv_uuid = uuid.uuid4()

    # Both conversation and context lookups return None
    mock_result_none = MagicMock()
    mock_result_none.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result_none

    result = await ContextService.resolve_for_conversation_id(
        str(conv_uuid), "openwebui", mock_session
    )

    mock_session.add.assert_called_once()
    added_context = mock_session.add.call_args[0][0]
    assert added_context.name == f"openwebui_{conv_uuid}"
    assert isinstance(result, uuid.UUID)


@pytest.mark.asyncio
async def test_resolve_for_conversation_id_invalid_uuid(mock_session: AsyncMock) -> None:
    """Invalid UUID falls back to resolve_anonymous."""
    result = await ContextService.resolve_for_conversation_id(
        "not-a-uuid", "openwebui", mock_session
    )

    mock_session.add.assert_called_once()
    added_context = mock_session.add.call_args[0][0]
    assert added_context.name.startswith("openwebui_")
    assert isinstance(result, uuid.UUID)


@pytest.mark.asyncio
async def test_resolve_for_conversation_id_reuses_existing_context(
    mock_session: AsyncMock,
) -> None:
    """Valid UUID with no conversation but existing context reuses it."""
    conv_uuid = uuid.uuid4()
    existing_context = MagicMock()
    existing_context.id = uuid.uuid4()

    # First call: conversation lookup -> None
    # Second call: context lookup -> existing
    call_count = 0

    def side_effect(stmt: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        mock = MagicMock()
        if call_count == 1:
            mock.scalar_one_or_none.return_value = None  # No conversation
        else:
            mock.scalar_one_or_none.return_value = existing_context  # Existing context
        return mock

    mock_session.execute = AsyncMock(side_effect=side_effect)

    result = await ContextService.resolve_for_conversation_id(
        str(conv_uuid), "openwebui", mock_session
    )

    assert result == existing_context.id
    mock_session.add.assert_not_called()


# --- resolve_anonymous ---


@pytest.mark.asyncio
async def test_resolve_anonymous(mock_session: AsyncMock) -> None:
    """Anonymous resolution creates a new ephemeral context."""
    result = await ContextService.resolve_anonymous("openwebui", mock_session)

    mock_session.add.assert_called_once()
    added_context = mock_session.add.call_args[0][0]
    assert added_context.name.startswith("openwebui_")
    assert added_context.type == "shared"
    assert isinstance(result, uuid.UUID)
