"""System command handler for the Agent Service."""

import shlex
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from core.db.models import Conversation, Session, Context

if TYPE_CHECKING:
    from core.core.service import AgentService

LOGGER = logging.getLogger(__name__)


async def handle_system_command(
    prompt: str,
    service: "AgentService",
    session: AsyncSession,
    conversation_id: str,
) -> str | None:
    """
    Check if the prompt is a system command and execute it.
    Returns:
        String output if handled (to be returned as assistant message),
        None if not a system command (continue normal flow).
    """
    if not prompt.startswith("/"):
        return None

    try:
        parts = shlex.split(prompt)
    except ValueError:
        # Malformed quoting etc.
        return None
    
    if not parts:
        return None

    cmd = parts[0]
    args = parts[1:]

    if cmd == "/init":
        return await _handle_init(args, service, session, conversation_id)
    elif cmd == "/switch":
        return await _handle_switch(args, service, session, conversation_id)
    elif cmd == "/status":
        return await _handle_status(service, session, conversation_id)

    return None


async def _handle_init(
    args: list[str],
    service: "AgentService",
    session: AsyncSession,
    conversation_id: str
) -> str:
    """
    Usage: /init <name> <type> [<key>=<value> ...]
    Example: /init myproject git url=...
    """
    if len(args) < 2:
        return "Usage: /init <name> <type> [key=value ...]"

    name = args[0]
    ctype = args[1]
    
    # Parse kwargs
    config = {}
    for kv in args[2:]:
        if "=" in kv:
            k, v = kv.split("=", 1)
            config[k] = v
        else:
            # Handle flag or error? MVP: ignore or value=True
            config[kv] = True

    try:
        context = await service.context_manager.create_context(session, name, ctype, config)
        
        # Auto-switch
        # Need to load conversation and update it
        # Assuming conversation exists if we are here?
        # Service handle_request ensures conversation exists before calling?
        # Yes, we will call this inside handle_request AFTER conversation loaded.
        
        # Ideally we reuse the logic of /switch
        await _switch_context(session, conversation_id, context.id, context.default_cwd)
        
        return f"Initialized and switched to new context: **{name}** ({ctype})"
    except Exception as e:
        LOGGER.exception("Failed to init context")
        return f"Error initializing context: {e}"


async def _handle_switch(
    args: list[str],
    service: "AgentService",
    session: AsyncSession,
    conversation_id: str
) -> str:
    """
    Usage: /switch <name>
    """
    if not args:
        return "Usage: /switch <name>"

    name = args[0]
    context = await service.context_manager.get_context(session, name)
    if not context:
        return f"Context '{name}' not found."

    await _switch_context(session, conversation_id, context.id, context.default_cwd)
    return f"Switched to context: **{name}**"


async def _switch_context(
    session: AsyncSession,
    conversation_id: str,
    context_id: Any,
    new_cwd: str
) -> None:
    """Helper to update conversation context."""
    conversation = await session.get(Conversation, conversation_id)
    if conversation:
        conversation.context_id = context_id
        conversation.current_cwd = new_cwd
        session.add(conversation)
        await session.flush()


async def _handle_status(
    service: "AgentService",
    session: AsyncSession,
    conversation_id: str
) -> str:
    """Return status of current conversation."""
    conversation = await session.get(Conversation, conversation_id)
    if not conversation:
        return "No active conversation."

    context = await service.context_manager.get_context_by_id(session, conversation.context_id)
    context_name = context.name if context else "Unknown"
    
    # Get active session
    sess_stmt = select(Session).where(Session.conversation_id == conversation_id, Session.active == True)
    sess_res = await session.execute(sess_stmt)
    active_session = sess_res.scalar_one_or_none()
    
    lines = [
        "## Agent Status",
        f"- **Conversation ID:** `{conversation_id}`",
        f"- **Active Session:** `{active_session.id if active_session else 'None'}`",
        f"- **Context:** `{context_name}`",
        f"- **CWD:** `{conversation.current_cwd}`",
    ]
    return "\n".join(lines)
