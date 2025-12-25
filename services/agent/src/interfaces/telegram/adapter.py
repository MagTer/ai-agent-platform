import logging
from typing import Any

from aiogram import Bot
from aiogram import Dispatcher as TelegramDispatcher
from aiogram.types import Message

from core.core.service import AgentService
from core.db.engine import AsyncSessionLocal
from interfaces.base import PlatformAdapter
from orchestrator.dispatcher import Dispatcher as AgentDispatcher

LOGGER = logging.getLogger(__name__)


class TelegramAdapter(PlatformAdapter):
    def __init__(self, dispatcher: AgentDispatcher, token: str, agent_service: AgentService):
        super().__init__(dispatcher)
        self.token = token
        self.agent_service = agent_service
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
                # Delegate to Dispatcher with explicit Platform context
                # Delegate to Dispatcher with explicit Platform context
                full_response = ""
                async for chunk in self.dispatcher.stream_message(
                    session_id=chat_id,  # Temporary ID, logic uses platform_id
                    message=text,
                    platform="telegram",
                    platform_id=chat_id,
                    db_session=session,
                    agent_service=self.agent_service,
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
