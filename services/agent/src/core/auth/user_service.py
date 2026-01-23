"""User management service for auto-provisioning and lookup."""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.header_auth import UserIdentity
from core.db.models import Context, User, UserContext

LOGGER = logging.getLogger(__name__)


async def get_or_create_user(
    identity: UserIdentity,
    session: AsyncSession,
) -> User:
    """Get existing user or create new one with personal context.

    Auto-provisions user on first login:
    1. Creates User record
    2. Creates personal Context
    3. Links User to Context via UserContext

    Args:
        identity: User identity from headers
        session: Database session

    Returns:
        User object (existing or newly created)
    """
    # Normalize email (case-insensitive, trimmed)
    normalized_email = identity.email.lower().strip()

    # Try to find existing user
    stmt = select(User).where(User.email == normalized_email)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if user:
        # Update last login and any changed fields
        user.last_login_at = _utc_now()
        if identity.name and user.display_name != identity.name:
            user.display_name = identity.name
        if identity.openwebui_id and user.openwebui_id != identity.openwebui_id:
            user.openwebui_id = identity.openwebui_id
        # SECURITY: Do NOT sync role from headers - database role is authoritative.
        # Header role claims could be spoofed. Role changes must be done by admins
        # through the admin portal, not automatically from SSO headers.
        # if identity.role and user.role != identity.role:
        #     user.role = identity.role
        await session.flush()
        LOGGER.info(f"User logged in: {user.email}")
        return user

    # Create new user with normalized email
    try:
        user = User(
            email=normalized_email,
            display_name=identity.name,
            openwebui_id=identity.openwebui_id,
            role=identity.role,
        )
        session.add(user)
        await session.flush()  # Get user.id

        # Create personal context
        context = Context(
            name=f"personal_{user.id}",
            type="personal",
            config={"owner_email": identity.email},
            default_cwd="/tmp",  # noqa: S108
        )
        session.add(context)
        await session.flush()  # Get context.id

        # Link user to context
        user_context = UserContext(
            user_id=user.id,
            context_id=context.id,
            role="owner",
            is_default=True,
        )
        session.add(user_context)
        await session.flush()

        LOGGER.info(f"Auto-provisioned new user: {user.email} with context {context.id}")
        return user

    except IntegrityError:
        # Race condition: another request created the user/context concurrently
        # Rollback and retry lookup
        await session.rollback()
        LOGGER.warning(
            f"Race condition during user creation for {normalized_email}, retrying lookup"
        )

        stmt = select(User).where(User.email == normalized_email)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        if user:
            user.last_login_at = _utc_now()
            await session.flush()
            return user

        # Still not found - re-raise the error
        raise


async def get_user_default_context(
    user: User,
    session: AsyncSession,
) -> Context | None:
    """Get user's default (personal) context."""
    stmt = (
        select(Context)
        .join(UserContext, UserContext.context_id == Context.id)
        .where(UserContext.user_id == user.id)
        .where(UserContext.is_default == True)  # noqa: E712
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def _utc_now() -> datetime:
    """Return naive UTC datetime."""
    return datetime.now(UTC).replace(tzinfo=None)
