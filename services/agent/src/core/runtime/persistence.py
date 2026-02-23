"""Conversation persistence module - handles DB CRUD for conversations, sessions, and messages."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.context_manager import ContextManager
from core.db import Context, Conversation, Message, Session
from core.models.pydantic_schemas import SupervisorDecision, TraceContext
from core.observability.logging import log_event
from core.observability.tracing import current_trace_ids
from core.runtime.memory import MemoryRecord, MemoryStore
from shared.models import AgentMessage

LOGGER = logging.getLogger(__name__)


async def _persist_memory_background(
    memory: MemoryStore,
    conversation_id: str,
    text: str,
    logger: logging.Logger,
) -> None:
    """Background task to persist conversation memory (non-blocking)."""
    try:
        await memory.add_records([MemoryRecord(conversation_id=conversation_id, text=text)])
    except Exception:  # pragma: no cover
        logger.exception("Failed to persist memory for conversation %s", conversation_id)


class ConversationPersistence:
    """Handles database operations for conversations, sessions, and messages."""

    def __init__(
        self,
        context_manager: ContextManager,
        memory: MemoryStore | None = None,
    ):
        """Initialize the persistence layer.

        Args:
            context_manager: Context manager for creating/accessing contexts
            memory: Optional memory store for conversation history
        """
        self._context_manager = context_manager
        self._memory = memory

    async def get_history(self, conversation_id: str, session: AsyncSession) -> list[AgentMessage]:
        """Retrieve conversation history from database.

        Args:
            conversation_id: UUID of the conversation.
            session: Database session.

        Returns:
            List of messages in chronological order.
        """
        stmt = (
            select(Message)
            .join(Session)
            .where(Session.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
        )
        result = await session.execute(stmt)
        db_messages = result.scalars().all()

        return [AgentMessage(role=msg.role, content=msg.content) for msg in db_messages]

    async def _ensure_conversation_exists(
        self,
        session: AsyncSession,
        conversation_id: str,
        request_metadata: dict[str, Any],
    ) -> Conversation:
        """Ensure a Conversation exists, creating one if needed.

        IMPORTANT: request_metadata MUST include 'context_id' (UUID).
        This is set upstream by the adapter/dispatcher using ContextService.

        Args:
            session: Database session
            conversation_id: UUID for the conversation
            request_metadata: Request metadata containing platform info and context_id

        Returns:
            The existing or newly created Conversation

        Raises:
            ValueError: If context_id is missing or invalid
        """
        db_conversation = await session.get(Conversation, conversation_id)
        if db_conversation:
            return db_conversation

        # Context ID MUST be provided by upstream (adapter/dispatcher via ContextService)
        context_id = request_metadata.get("context_id")
        if not context_id:
            raise ValueError(
                f"Cannot create conversation {conversation_id}: "
                "context_id missing from request_metadata. "
                "Ensure adapter calls ContextService.resolve_*() first."
            )

        # Verify context exists
        db_context = await session.get(Context, context_id)
        if not db_context:
            raise ValueError(
                f"Cannot create conversation {conversation_id}: "
                f"context_id {context_id} not found in database."
            )

        db_conversation = Conversation(
            id=conversation_id,
            platform=request_metadata.get("platform", "api"),
            platform_id=request_metadata.get("platform_id", "generic"),
            context_id=db_context.id,
            current_cwd=db_context.default_cwd,
        )
        session.add(db_conversation)
        await session.flush()
        return db_conversation

    async def _get_or_create_session(
        self,
        session: AsyncSession,
        conversation_id: str,
    ) -> Session:
        """Get active session or create a new one.

        Args:
            session: Database session
            conversation_id: UUID for the conversation

        Returns:
            The active Session for this conversation
        """
        session_stmt = select(Session).where(
            Session.conversation_id == conversation_id, Session.active.is_(True)
        )
        session_result = await session.execute(session_stmt)
        db_session = session_result.scalar_one_or_none()

        if not db_session:
            db_session = Session(conversation_id=conversation_id, active=True)
            session.add(db_session)
            await session.flush()

        return db_session

    async def _load_conversation_history(
        self,
        session: AsyncSession,
        db_session: Session,
    ) -> list[AgentMessage]:
        """Load message history for a session.

        Args:
            session: Database session
            db_session: The active Session

        Returns:
            List of AgentMessage objects representing conversation history
        """
        history_stmt = (
            select(Message)
            .where(Message.session_id == db_session.id)
            .order_by(Message.created_at.asc())
        )
        history_result = await session.execute(history_stmt)
        db_messages = history_result.scalars().all()

        history = [AgentMessage(role=msg.role, content=msg.content) for msg in db_messages]

        # Inject current date as system context
        current_date_str = datetime.now().strftime("%Y-%m-%d")
        history.insert(
            0,
            AgentMessage(role="system", content=f"Current Date: {current_date_str}"),
        )

        return history

    async def _finalize_and_persist(
        self,
        session: AsyncSession,
        db_session: Session,
        conversation_id: str,
        completion_text: str,
        prompt_history: list[AgentMessage],
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Persist results and emit final events.

        Args:
            session: Database session
            db_session: The active Session
            conversation_id: The conversation ID
            completion_text: Final completion text
            prompt_history: Full conversation history

        Yields:
            Final events including history snapshot
        """
        # Record assistant message
        if completion_text:
            session.add(
                Message(
                    session_id=db_session.id,
                    role="assistant",
                    content=completion_text,
                    trace_id=current_trace_ids().get("trace_id"),
                )
            )

        # Background memory persistence (fire-and-forget)
        if self._memory and completion_text:
            asyncio.create_task(
                _persist_memory_background(self._memory, conversation_id, completion_text, LOGGER)
            )

        # Commit transaction
        await session.commit()

        # Log event
        LOGGER.info("Completed conversation %s", conversation_id)
        log_event(
            SupervisorDecision(
                item_id=conversation_id,
                decision="ok",
                comments="Conversation complete",
                trace=TraceContext(**current_trace_ids()),
            )
        )

        # Yield history snapshot
        final_history = list(prompt_history)
        if completion_text:
            final_history.append(AgentMessage(role="assistant", content=completion_text))

        yield {"type": "history_snapshot", "messages": final_history}

    async def _store_pending_hitl(
        self,
        session: AsyncSession,
        db_conversation: Conversation,
        hitl_metadata: dict[str, Any],
    ) -> None:
        """Store HITL state in conversation metadata for resume.

        Args:
            session: Database session
            db_conversation: The conversation to update
            hitl_metadata: HITL metadata including skill_messages, step, etc.
        """
        # Update conversation_metadata with pending_hitl
        current_meta = dict(db_conversation.conversation_metadata or {})
        current_meta["pending_hitl"] = hitl_metadata
        db_conversation.conversation_metadata = current_meta
        await session.flush()
        LOGGER.info(
            "Stored pending HITL for conversation %s: %s",
            db_conversation.id,
            hitl_metadata.get("skill_name"),
        )

    async def _clear_pending_hitl(
        self,
        session: AsyncSession,
        db_conversation: Conversation,
    ) -> None:
        """Clear pending HITL state after resume.

        Args:
            session: Database session
            db_conversation: The conversation to update
        """
        current_meta = dict(db_conversation.conversation_metadata or {})
        if "pending_hitl" in current_meta:
            del current_meta["pending_hitl"]
            db_conversation.conversation_metadata = current_meta
            await session.flush()
            LOGGER.info("Cleared pending HITL for conversation %s", db_conversation.id)
