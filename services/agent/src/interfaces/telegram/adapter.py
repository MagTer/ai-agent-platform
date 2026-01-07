import logging
from typing import Any
from uuid import UUID

from aiogram import Bot
from aiogram import Dispatcher as TelegramDispatcher
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.core.service_factory import ServiceFactory
from core.db.engine import AsyncSessionLocal
from core.db.models import Context, Conversation
from interfaces.base import PlatformAdapter
from orchestrator.dispatcher import Dispatcher as AgentDispatcher

LOGGER = logging.getLogger(__name__)


class TelegramAdapter(PlatformAdapter):
    def __init__(
        self,
        dispatcher: AgentDispatcher,
        token: str,
        service_factory: ServiceFactory,
    ):
        """Initialize Telegram adapter with context-aware service factory.

        Args:
            dispatcher: Agent dispatcher for message handling
            token: Telegram bot token
            service_factory: Factory for creating context-scoped agent services
        """
        super().__init__(dispatcher)
        self.token = token
        self.service_factory = service_factory
        # Initialize Bot and Dispatcher
        self.bot: Bot | None = None
        self.dp: TelegramDispatcher | None = None

        if self.token:
            self.bot = Bot(token=self.token)
            self.dp = TelegramDispatcher()
            self.dp.message.register(self._handle_message)
        else:
            LOGGER.warning("Telegram token not provided. Adapter disabled.")

    async def start(self) -> None:
        if self.dp and self.bot:
            LOGGER.info("Starting Telegram Adapter...")
            # Note: start_polling is blocking. In a real app with multiple adapters,
            # this should run in a task.
            await self.dp.start_polling(self.bot)

    async def stop(self) -> None:
        if self.dp:
            LOGGER.info("Stopping Telegram Adapter...")
            await self.dp.stop_polling()
        if self.bot:
            await self.bot.session.close()

    async def send_message(
        self, conversation_id: str, content: str, metadata: dict[str, Any] | None = None
    ) -> None:
        if not self.bot:
            return

        try:
            await self.bot.send_message(chat_id=conversation_id, text=content)
        except Exception as e:
            LOGGER.error(f"Failed to send Telegram message: {e}")

    async def _get_or_create_context(self, chat_id: str, session: AsyncSession) -> UUID:
        """Get or create context for a Telegram chat.

        Each Telegram chat gets its own context for isolation.

        Args:
            chat_id: Telegram chat ID
            session: Database session

        Returns:
            Context UUID for this Telegram chat
        """
        # Look for existing conversation with this platform_id
        stmt = select(Conversation).where(
            Conversation.platform == "telegram",
            Conversation.platform_id == chat_id,
        )
        result = await session.execute(stmt)
        conversation = result.scalar_one_or_none()

        if conversation:
            # Found existing conversation - return its context
            LOGGER.debug(
                f"Found existing Telegram conversation, context_id={conversation.context_id}"
            )
            return conversation.context_id

        # No conversation yet - create a new context for this Telegram chat
        context = Context(
            name=f"telegram_{chat_id}",
            type="virtual",
            config={"platform": "telegram", "chat_id": chat_id},
            default_cwd="/tmp",  # noqa: S108
        )
        session.add(context)
        await session.flush()
        LOGGER.info(f"Created new context for Telegram chat {chat_id}: {context.id}")
        return context.id

    async def _handle_message(self, message: Message) -> None:
        if not message.text:
            return

        chat_id = str(message.chat.id)
        text = message.text

        LOGGER.info(f"Telegram Message: {text} from {chat_id}")

        try:
            async with AsyncSessionLocal() as session:
                # Get or create context for this Telegram chat
                context_id = await self._get_or_create_context(chat_id, session)

                # Create context-scoped agent service
                agent_service = await self.service_factory.create_service(context_id, session)

                # Delegate to Dispatcher with explicit Platform context
                full_response = ""
                async for chunk in self.dispatcher.stream_message(
                    session_id=chat_id,  # Temporary ID, logic uses platform_id
                    message=text,
                    platform="telegram",
                    platform_id=chat_id,
                    db_session=session,
                    agent_service=agent_service,
                ):
                    if chunk["type"] == "content" and chunk["content"]:
                        full_response += chunk["content"]
                    elif chunk["type"] == "error" and chunk["content"]:
                        full_response += f"\nError: {chunk['content']}"

                if full_response:
                    await self.send_message(chat_id, full_response)

        except Exception as e:
            LOGGER.error(f"Error handling Telegram message: {e}")
            await self.send_message(chat_id, "Sorry, an internal error occurred.")
