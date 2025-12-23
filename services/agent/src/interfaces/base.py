from abc import ABC, abstractmethod
from typing import Any

from orchestrator.dispatcher import Dispatcher


class PlatformAdapter(ABC):
    """
    Abstract base class for platform adapters (e.g., HTTP, Telegram, Slack).
    Adapters are responsible for:
    1. Receiving messages from a specific platform.
    2. Routing them to the agent Dispatcher.
    3. Sending responses back to the platform.
    """

    def __init__(self, dispatcher: Dispatcher):
        self.dispatcher = dispatcher

    @abstractmethod
    async def start(self) -> None:
        """
        Start listening for messages.
        For persistent connections (e.g., WebSocket, Long Polling), this runs the loop.
        For request-response (e.g., HTTP), this might be a no-op or startup hook.
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """
        Stop listening and cleanup resources.
        """
        pass

    @abstractmethod
    async def send_message(
        self, conversation_id: str, content: str, metadata: dict[str, Any] | None = None
    ) -> None:
        """
        Send a message back to the platform.
        """
        pass
