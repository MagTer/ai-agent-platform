"""Integration tests for multi-user authentication flow.

Tests the complete flow from headers to user creation to context creation,
verifying that:
- Users are auto-provisioned on first login
- Personal contexts are created and linked
- User isolation is maintained
- Headers are correctly extracted and processed

Requires PostgreSQL running (docker-compose up postgres).
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.auth.header_auth import UserIdentity, extract_user_from_headers
from core.auth.user_service import get_or_create_user, get_user_default_context
from core.db.models import Base, Context, User, UserContext


def unique_email(base: str = "test") -> str:
    """Generate a unique email for testing."""
    return f"{base}_{uuid.uuid4().hex[:8]}@company.com"


def unique_name(base: str = "context") -> str:
    """Generate a unique name for testing."""
    return f"{base}_{uuid.uuid4().hex[:8]}"


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


# Use Postgres for testing (models use JSONB which requires Postgres)
TEST_DB_URL = os.getenv(
    "POSTGRES_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_db"
)


def _postgres_available() -> bool:
    """Check if PostgreSQL is available for testing."""
    import asyncio

    async def check():
        try:
            engine = create_async_engine(TEST_DB_URL, echo=False)
            async with engine.connect():
                pass
            await engine.dispose()
            return True
        except Exception:
            return False

    return asyncio.get_event_loop().run_until_complete(check())


# Skip all tests in this module if Postgres isn't available
pytestmark = pytest.mark.skipif(
    not _postgres_available(),
    reason="PostgreSQL not available (run docker-compose up postgres)",
)


@pytest.fixture
async def db_engine() -> AsyncEngine:
    """Create async database engine for testing."""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncSession:
    """Create async database session for testing."""
    session_maker = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_maker() as session:
        yield session


def make_mock_request(headers: dict[str, str]) -> MagicMock:
    """Create a mock FastAPI Request with headers."""
    request = MagicMock()
    # Simulate case-insensitive header access like Starlette does
    headers_lower = {k.lower(): v for k, v in headers.items()}
    request.headers = MagicMock()
    request.headers.get = lambda key, default=None: headers_lower.get(key.lower(), default)
    return request


@pytest.mark.asyncio
class TestMultiUserAutoProvisioning:
    """Test auto-provisioning of new users."""

    async def test_new_user_is_created_with_personal_context(self, db_session: AsyncSession):
        """Test that a new user gets auto-provisioned with a personal context."""
        email = unique_email("newuser")
        identity = UserIdentity(
            email=email,
            name="New User",
            openwebui_id="owui-12345",
            role="user",
        )

        # Auto-provision user
        user = await get_or_create_user(identity, db_session)
        await db_session.commit()

        # Verify user was created
        assert user is not None
        assert user.email == email.lower()
        assert user.display_name == "New User"
        assert user.role == "user"
        assert user.openwebui_id == "owui-12345"
        assert user.is_active is True

        # Verify personal context was created
        context = await get_user_default_context(user, db_session)
        assert context is not None
        assert context.type == "personal"
        # Context name is personal_{user_id}, email is stored in config
        assert context.name.startswith("personal_")
        assert context.config.get("owner_email") == email.lower()

        # Verify user_context link exists
        stmt = select(UserContext).where(
            UserContext.user_id == user.id,
            UserContext.context_id == context.id,
        )
        result = await db_session.execute(stmt)
        user_context = result.scalar_one_or_none()
        assert user_context is not None
        assert user_context.is_default is True
        assert user_context.role == "owner"

    async def test_existing_user_is_returned_without_duplicate(self, db_session: AsyncSession):
        """Test that existing users are returned without creating duplicates."""
        email = unique_email("existing")
        identity = UserIdentity(
            email=email,
            name="Existing User",
            role="user",
        )

        # First login - creates user
        user1 = await get_or_create_user(identity, db_session)
        await db_session.commit()
        user1_id = user1.id

        # Detach user from session to simulate a new request
        db_session.expunge(user1)

        # Second login - should return same user
        user2 = await get_or_create_user(identity, db_session)
        await db_session.commit()

        assert user2.id == user1_id

        # Verify only one user exists with this email
        stmt = select(User).where(User.email == email.lower())
        result = await db_session.execute(stmt)
        users = result.scalars().all()
        assert len(users) == 1

    async def test_user_info_is_updated_on_subsequent_login(self, db_session: AsyncSession):
        """Test that user info is updated when they log in again with different data."""
        email = unique_email("updating")
        # First login
        identity1 = UserIdentity(
            email=email,
            name="Original Name",
            role="user",
        )
        user = await get_or_create_user(identity1, db_session)
        await db_session.commit()
        original_login = user.last_login_at

        # Simulate time passing
        db_session.expunge(user)

        # Second login with updated name
        identity2 = UserIdentity(
            email=email,
            name="Updated Name",
            role="admin",  # Role changed
            openwebui_id="new-owui-id",
        )
        user = await get_or_create_user(identity2, db_session)
        await db_session.commit()

        # Verify updates
        assert user.display_name == "Updated Name"
        assert user.role == "admin"
        assert user.openwebui_id == "new-owui-id"
        # last_login_at might be same if executed too fast, but should be >= original
        assert user.last_login_at >= original_login


@pytest.mark.asyncio
class TestUserContextIsolation:
    """Test that users are properly isolated via their contexts."""

    async def test_users_have_separate_contexts(self, db_session: AsyncSession):
        """Test that different users have different personal contexts."""
        # Create two users
        identity_a = UserIdentity(email=unique_email("user_a"), name="User A", role="user")
        identity_b = UserIdentity(email=unique_email("user_b"), name="User B", role="user")

        user_a = await get_or_create_user(identity_a, db_session)
        user_b = await get_or_create_user(identity_b, db_session)
        await db_session.commit()

        # Get their contexts
        context_a = await get_user_default_context(user_a, db_session)
        context_b = await get_user_default_context(user_b, db_session)

        # Verify contexts are different
        assert context_a is not None
        assert context_b is not None
        assert context_a.id != context_b.id

    async def test_user_cannot_access_other_users_context(self, db_session: AsyncSession):
        """Test that user_contexts correctly links users to their own contexts."""
        # Create two users
        identity_a = UserIdentity(email=unique_email("isolated_a"), name="User A", role="user")
        identity_b = UserIdentity(email=unique_email("isolated_b"), name="User B", role="user")

        user_a = await get_or_create_user(identity_a, db_session)
        user_b = await get_or_create_user(identity_b, db_session)
        await db_session.commit()

        # Query user_contexts for user A
        stmt_a = select(UserContext).where(UserContext.user_id == user_a.id)
        result_a = await db_session.execute(stmt_a)
        contexts_a = result_a.scalars().all()

        # Query user_contexts for user B
        stmt_b = select(UserContext).where(UserContext.user_id == user_b.id)
        result_b = await db_session.execute(stmt_b)
        contexts_b = result_b.scalars().all()

        # Each user should only have their own context
        assert len(contexts_a) == 1
        assert len(contexts_b) == 1

        # Context IDs should be different
        assert contexts_a[0].context_id != contexts_b[0].context_id

    async def test_context_cascade_deletes_user_context_link(self, db_session: AsyncSession):
        """Test that deleting a context cascades to user_context links."""
        identity = UserIdentity(email=unique_email("cascade"), name="Cascade User", role="user")
        user = await get_or_create_user(identity, db_session)
        await db_session.commit()

        context = await get_user_default_context(user, db_session)
        context_id = context.id

        # Delete context
        await db_session.delete(context)
        await db_session.commit()

        # Verify user_context link is also deleted
        stmt = select(UserContext).where(UserContext.context_id == context_id)
        result = await db_session.execute(stmt)
        assert result.scalar_one_or_none() is None

        # User should still exist
        stmt = select(User).where(User.id == user.id)
        result = await db_session.execute(stmt)
        assert result.scalar_one_or_none() is not None

    async def test_user_cascade_deletes_user_context_link(self, db_session: AsyncSession):
        """Test that deleting a user cascades to user_context links."""
        email = unique_email("user_cascade")
        identity = UserIdentity(email=email, name="User Cascade", role="user")
        user = await get_or_create_user(identity, db_session)
        await db_session.commit()

        user_id = user.id

        # Delete user
        await db_session.delete(user)
        await db_session.commit()

        # Verify user_context link is also deleted
        stmt = select(UserContext).where(UserContext.user_id == user_id)
        result = await db_session.execute(stmt)
        assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
class TestHeaderExtractionIntegration:
    """Test header extraction integrated with user creation."""

    async def test_full_flow_from_headers_to_user(self, db_session: AsyncSession):
        """Test the complete flow from HTTP headers to user creation."""
        email = unique_email("flowtest")
        # Simulate Open WebUI headers
        headers = {
            "X-OpenWebUI-User-Email": email,
            "X-OpenWebUI-User-Name": "Flow Test User",
            "X-OpenWebUI-User-Id": "owui-flow-123",
            "X-OpenWebUI-User-Role": "user",
        }
        request = make_mock_request(headers)

        # Extract identity from headers
        identity = extract_user_from_headers(request)
        assert identity is not None
        assert identity.email == email.lower()

        # Auto-provision user
        user = await get_or_create_user(identity, db_session)
        await db_session.commit()

        # Verify user
        assert user.email == email.lower()
        assert user.display_name == "Flow Test User"

        # Verify context
        context = await get_user_default_context(user, db_session)
        assert context is not None
        assert context.type == "personal"

    async def test_anonymous_request_without_headers(self, db_session: AsyncSession):
        """Test that requests without headers return None identity."""
        request = make_mock_request({})
        identity = extract_user_from_headers(request)
        assert identity is None

    async def test_request_with_only_authorization_header(self, db_session: AsyncSession):
        """Test that Authorization header alone doesn't create identity."""
        # Open WebUI sends Authorization: Bearer sk-dummy without user headers
        headers = {"Authorization": "Bearer sk-dummy"}
        request = make_mock_request(headers)
        identity = extract_user_from_headers(request)
        assert identity is None


@pytest.mark.asyncio
class TestEmailNormalization:
    """Test that email normalization works correctly across the flow."""

    async def test_email_is_normalized_to_lowercase(self, db_session: AsyncSession):
        """Test that emails are stored lowercase regardless of input case."""
        base = unique_email("uppercase").replace("@company.com", "")
        email_upper = f"{base.upper()}@COMPANY.COM"
        email_lower = f"{base}@company.com"
        identity = UserIdentity(
            email=email_upper,
            name="Upper Case User",
            role="user",
        )

        user = await get_or_create_user(identity, db_session)
        await db_session.commit()

        assert user.email == email_lower

    async def test_same_user_regardless_of_email_case(self, db_session: AsyncSession):
        """Test that email case doesn't create duplicate users."""
        base = unique_email("casetest").replace("@company.com", "")
        email_lower = f"{base}@company.com"
        email_upper = f"{base.upper()}@COMPANY.COM"
        # First login with lowercase
        identity1 = UserIdentity(email=email_lower, name="User", role="user")
        user1 = await get_or_create_user(identity1, db_session)
        await db_session.commit()
        user1_id = user1.id

        db_session.expunge(user1)

        # Second login with uppercase
        identity2 = UserIdentity(email=email_upper, name="User", role="user")
        user2 = await get_or_create_user(identity2, db_session)
        await db_session.commit()

        assert user2.id == user1_id


@pytest.mark.asyncio
class TestAdminRoleHandling:
    """Test admin role handling in the multi-user flow."""

    async def test_admin_role_is_preserved(self, db_session: AsyncSession):
        """Test that admin role from headers is stored correctly."""
        identity = UserIdentity(
            email=unique_email("admin"),
            name="Admin User",
            role="admin",
        )

        user = await get_or_create_user(identity, db_session)
        await db_session.commit()

        assert user.role == "admin"

    async def test_role_upgrade_on_subsequent_login(self, db_session: AsyncSession):
        """Test that role can be upgraded on subsequent login."""
        email = unique_email("promoted")
        # First login as regular user
        identity1 = UserIdentity(email=email, name="User", role="user")
        user = await get_or_create_user(identity1, db_session)
        await db_session.commit()
        assert user.role == "user"

        db_session.expunge(user)

        # Second login as admin (promoted)
        identity2 = UserIdentity(email=email, name="User", role="admin")
        user = await get_or_create_user(identity2, db_session)
        await db_session.commit()

        assert user.role == "admin"


@pytest.mark.asyncio
class TestConcurrentUserCreation:
    """Test concurrent user creation scenarios."""

    async def test_multiple_users_created_concurrently(self, db_session: AsyncSession):
        """Test that multiple users can be created in the same session."""
        run_id = uuid.uuid4().hex[:8]
        identities = [
            UserIdentity(email=f"concurrent{i}_{run_id}@company.com", name=f"User {i}", role="user")
            for i in range(5)
        ]

        users = []
        for identity in identities:
            user = await get_or_create_user(identity, db_session)
            users.append(user)

        await db_session.commit()

        # Verify all users were created
        assert len(users) == 5

        # Verify all users have unique IDs
        user_ids = [u.id for u in users]
        assert len(set(user_ids)) == 5

        # Verify all users have personal contexts
        for user in users:
            context = await get_user_default_context(user, db_session)
            assert context is not None


@pytest.mark.asyncio
class TestUserContextRelationship:
    """Test the relationship between users and contexts."""

    async def test_user_can_have_multiple_contexts(self, db_session: AsyncSession):
        """Test that a user can be linked to multiple contexts."""
        email = unique_email("multicontext")
        identity = UserIdentity(email=email, name="Multi Context", role="user")
        user = await get_or_create_user(identity, db_session)
        await db_session.commit()

        # Create additional context with unique name
        shared_context = Context(
            name=unique_name("shared_project"),
            type="shared",
            config={},
            default_cwd="/tmp",  # noqa: S108
        )
        db_session.add(shared_context)
        await db_session.flush()

        # Link user to shared context
        user_context = UserContext(
            user_id=user.id,
            context_id=shared_context.id,
            role="member",
            is_default=False,
        )
        db_session.add(user_context)
        await db_session.commit()

        # Verify user has two contexts
        stmt = select(UserContext).where(UserContext.user_id == user.id)
        result = await db_session.execute(stmt)
        user_contexts = result.scalars().all()
        assert len(user_contexts) == 2

        # Verify default is still personal
        default_ctx = await get_user_default_context(user, db_session)
        assert default_ctx.type == "personal"

    async def test_context_can_have_multiple_users(self, db_session: AsyncSession):
        """Test that a context can be shared by multiple users."""
        # Create two users
        user1 = await get_or_create_user(
            UserIdentity(email=unique_email("shared1"), name="User 1", role="user"),
            db_session,
        )
        user2 = await get_or_create_user(
            UserIdentity(email=unique_email("shared2"), name="User 2", role="user"),
            db_session,
        )
        await db_session.commit()

        # Create shared context with unique name
        shared_context = Context(
            name=unique_name("team_project"),
            type="shared",
            config={},
            default_cwd="/tmp",  # noqa: S108
        )
        db_session.add(shared_context)
        await db_session.flush()

        # Link both users to shared context
        for user, role in [(user1, "owner"), (user2, "member")]:
            user_context = UserContext(
                user_id=user.id,
                context_id=shared_context.id,
                role=role,
                is_default=False,
            )
            db_session.add(user_context)

        await db_session.commit()

        # Verify context has two users
        stmt = select(UserContext).where(UserContext.context_id == shared_context.id)
        result = await db_session.execute(stmt)
        links = result.scalars().all()
        assert len(links) == 2

        # Verify roles
        roles = {link.user_id: link.role for link in links}
        assert roles[user1.id] == "owner"
        assert roles[user2.id] == "member"
