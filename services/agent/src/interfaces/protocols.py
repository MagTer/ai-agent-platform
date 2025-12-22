from collections.abc import AsyncGenerator
from typing import Any, Protocol


class IPlatformAdapter(Protocol):
    """
    Standard interface for any external platform (Slack, WebUI, CLI).
    """

    async def send_message(self, conversation_id: str, content: str) -> None: ...

    async def get_streaming_mode(self) -> bool: ...

    async def listen(self) -> AsyncGenerator[Any, None]: ...


class IAssistantClient(Protocol):
    """
    Wrapper around LLM inference (usually LiteLLM).
    """

    async def chat_stream(
        self, messages: list[dict[str, Any]], model: str
    ) -> AsyncGenerator[str, None]: ...

    async def chat_complete(self, messages: list[dict[str, Any]], model: str) -> str: ...
