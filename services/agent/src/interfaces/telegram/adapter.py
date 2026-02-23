import logging
from typing import Any

from aiogram import Bot
from aiogram import Dispatcher as TelegramDispatcher
from aiogram.types import Message

from core.context import ContextService
from core.db.engine import AsyncSessionLocal
from core.runtime.service_factory import ServiceFactory
from interfaces.base import PlatformAdapter
from orchestrator.dispatcher import Dispatcher as AgentDispatcher
from shared.chunk_filter import ChunkFilter
from shared.streaming import VerbosityLevel

LOGGER = logging.getLogger(__name__)


class TelegramAdapter(PlatformAdapter):
    platform_name = "telegram"

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

    async def _handle_message(self, message: Message) -> None:
        if not message.text:
            return

        chat_id = str(message.chat.id)
        text = message.text

        LOGGER.info(f"Telegram Message: {text} from {chat_id}")

        try:
            async with AsyncSessionLocal() as session:
                # Get or create context for this Telegram chat
                context_id = await ContextService.resolve_for_platform("telegram", chat_id, session)

                # Create context-scoped agent service
                agent_service = await self.service_factory.create_service(context_id, session)

                chunk_filter = ChunkFilter(VerbosityLevel.DEFAULT)

                # Build metadata with context_id
                metadata = {"context_id": str(context_id)}

                # Delegate to Dispatcher with explicit Platform context
                full_response = ""
                async for chunk in self.dispatcher.stream_message(
                    session_id=chat_id,  # Temporary ID, logic uses platform_id
                    message=text,
                    platform="telegram",
                    platform_id=chat_id,
                    db_session=session,
                    agent_service=agent_service,
                    metadata=metadata,
                ):
                    chunk_type = chunk["type"]
                    content = chunk.get("content")

                    if not chunk_filter.should_show(chunk_type, chunk.get("metadata"), content):
                        continue

                    if chunk_type == "content" and content:
                        if chunk_filter.is_safe_content(content):
                            full_response += content

                    elif chunk_type == "awaiting_input":
                        meta = chunk.get("metadata") or {}
                        prompt = meta.get("prompt", "Input needed:")
                        options = meta.get("options")
                        hitl_text = f"[Input needed] {prompt}"
                        if options:
                            for i, opt in enumerate(options, 1):
                                hitl_text += f"\n{i}. {opt}"
                        if full_response:
                            await self.send_message(chat_id, full_response)
                            full_response = ""
                        await self.send_message(chat_id, hitl_text)

                    elif chunk_type == "error" and content:
                        full_response += f"\nError: {content}"

                if full_response:
                    await self.send_message(chat_id, full_response)

        except Exception as e:
            LOGGER.error(f"Error handling Telegram message: {e}")
            await self.send_message(chat_id, "Sorry, an internal error occurred.")
