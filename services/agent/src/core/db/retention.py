"""Database retention service for automatic cleanup of old data.

Provides two cleanup strategies:
1. Per-conversation message limit (prevents runaway loops)
2. Age-based cleanup (prevents unbounded database growth)
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import Conversation, Message, Session

LOGGER = logging.getLogger(__name__)

# Default retention settings
DEFAULT_MAX_MESSAGES_PER_CONVERSATION = 500  # Generous limit for loop prevention
DEFAULT_MESSAGE_RETENTION_DAYS = 30  # Keep messages for 30 days
DEFAULT_INACTIVE_CONVERSATION_DAYS = 90  # Remove conversations inactive for 90 days


async def cleanup_old_messages(
    db_session: AsyncSession,
    retention_days: int = DEFAULT_MESSAGE_RETENTION_DAYS,
) -> int:
    """Delete messages older than retention_days.

    Returns the number of deleted messages.
    """
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=retention_days)

    # Count before delete
    count_stmt = select(func.count(Message.id)).where(Message.created_at < cutoff)
    result = await db_session.execute(count_stmt)
    count = result.scalar() or 0

    if count > 0:
        stmt = delete(Message).where(Message.created_at < cutoff)
        await db_session.execute(stmt)
        await db_session.commit()
        LOGGER.info(f"Retention: Deleted {count} messages older than {retention_days} days")

    return count


async def cleanup_inactive_conversations(
    db_session: AsyncSession,
    inactive_days: int = DEFAULT_INACTIVE_CONVERSATION_DAYS,
) -> int:
    """Delete conversations with no activity for inactive_days.

    This cascades to delete associated sessions and messages.
    Returns the number of deleted conversations.
    """
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=inactive_days)

    # Find conversations with no recent messages
    # Subquery to get max message date per conversation
    subq = (
        select(
            Session.conversation_id,
            func.max(Message.created_at).label("last_activity"),
        )
        .join(Message, Message.session_id == Session.id)
        .group_by(Session.conversation_id)
        .subquery()
    )

    # Conversations where last activity < cutoff OR no messages at all
    inactive_convs = (
        select(Conversation.id)
        .outerjoin(subq, Conversation.id == subq.c.conversation_id)
        .where((subq.c.last_activity < cutoff) | (subq.c.last_activity.is_(None)))
        .where(Conversation.updated_at < cutoff)
    )

    result = await db_session.execute(inactive_convs)
    conv_ids = [row[0] for row in result.fetchall()]

    if conv_ids:
        stmt = delete(Conversation).where(Conversation.id.in_(conv_ids))
        await db_session.execute(stmt)
        await db_session.commit()
        LOGGER.info(
            f"Retention: Deleted {len(conv_ids)} conversations "
            f"inactive for {inactive_days}+ days"
        )

    return len(conv_ids)


async def trim_conversation_messages(
    db_session: AsyncSession,
    max_messages: int = DEFAULT_MAX_MESSAGES_PER_CONVERSATION,
) -> int:
    """Trim messages per conversation to max_messages (keep newest).

    This prevents runaway loops from filling the database.
    Returns total number of trimmed messages.
    """
    total_trimmed = 0

    # Find conversations with too many messages
    msg_counts = (
        select(
            Session.conversation_id,
            func.count(Message.id).label("msg_count"),
        )
        .join(Message, Message.session_id == Session.id)
        .group_by(Session.conversation_id)
        .having(func.count(Message.id) > max_messages)
    )

    result = await db_session.execute(msg_counts)
    over_limit = result.fetchall()

    for conv_id, msg_count in over_limit:
        excess = msg_count - max_messages

        # Get session IDs for this conversation
        session_ids_stmt = select(Session.id).where(Session.conversation_id == conv_id)
        session_result = await db_session.execute(session_ids_stmt)
        session_ids = [row[0] for row in session_result.fetchall()]

        if not session_ids:
            continue

        # Find oldest messages to delete
        oldest_msgs = (
            select(Message.id)
            .where(Message.session_id.in_(session_ids))
            .order_by(Message.created_at.asc())
            .limit(excess)
        )

        oldest_result = await db_session.execute(oldest_msgs)
        msg_ids_to_delete = [row[0] for row in oldest_result.fetchall()]

        if msg_ids_to_delete:
            del_stmt = delete(Message).where(Message.id.in_(msg_ids_to_delete))
            await db_session.execute(del_stmt)
            total_trimmed += len(msg_ids_to_delete)

    if total_trimmed > 0:
        await db_session.commit()
        LOGGER.info(
            f"Retention: Trimmed {total_trimmed} messages " f"(max {max_messages} per conversation)"
        )

    return total_trimmed


async def run_retention_cleanup(
    db_session: AsyncSession,
    message_retention_days: int = DEFAULT_MESSAGE_RETENTION_DAYS,
    inactive_conversation_days: int = DEFAULT_INACTIVE_CONVERSATION_DAYS,
    max_messages_per_conversation: int = DEFAULT_MAX_MESSAGES_PER_CONVERSATION,
) -> dict[str, int]:
    """Run all retention cleanup tasks.

    Returns a summary of cleanup actions.
    """
    LOGGER.info("Starting retention cleanup...")

    results = {
        "old_messages_deleted": await cleanup_old_messages(db_session, message_retention_days),
        "inactive_conversations_deleted": await cleanup_inactive_conversations(
            db_session, inactive_conversation_days
        ),
        "messages_trimmed": await trim_conversation_messages(
            db_session, max_messages_per_conversation
        ),
    }

    LOGGER.info(f"Retention cleanup complete: {results}")
    return results
