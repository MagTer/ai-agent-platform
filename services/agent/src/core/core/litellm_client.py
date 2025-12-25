"""Async client for the LiteLLM gateway."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator, Iterable
from typing import Any

import httpx
from shared.streaming import AgentChunk

from .config import Settings
from .models import AgentMessage

LOGGER = logging.getLogger(__name__)


class LiteLLMError(RuntimeError):
    """Raised when the LiteLLM gateway returns an unexpected error."""


class LiteLLMClient:
    """Wrapper around the LiteLLM HTTP API."""

    def __init__(self, settings: Settings, *, timeout: float | None = None) -> None:
        self._settings = settings
        self._timeout = timeout if timeout is not None else settings.litellm_timeout
        self._client = httpx.AsyncClient(
            base_url=str(settings.litellm_api_base),
            timeout=self._timeout,
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._settings.litellm_api_key:
            headers["Authorization"] = f"Bearer {self._settings.litellm_api_key}"
        return headers

    async def stream_chat(
        self, messages: Iterable[AgentMessage], *, model: str | None = None
    ) -> AsyncGenerator[AgentChunk, None]:
        """Stream chat completions from LiteLLM."""
        payload: dict[str, Any] = {
            "model": model or self._settings.litellm_model,
            "messages": [message.model_dump() for message in messages],
            "stream": True,
        }

        try:
            async with self._client.stream(
                "POST",
                "/v1/chat/completions",
                json=payload,
                headers=self._build_headers(),
            ) as response:
                if response.status_code >= 400:
                    error_text = await response.aread()
                    error_str = error_text.decode("utf-8", errors="replace")
                    LOGGER.error("LiteLLM error %s: %s", response.status_code, error_str)
                    yield {
                        "type": "error",
                        "content": f"LiteLLM {response.status_code}: {error_str[:200]}",
                        "tool_call": None,
                        "metadata": {"status_code": response.status_code},
                    }
                    return

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue

                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        yield {
                            "type": "done",
                            "content": None,
                            "tool_call": None,
                            "metadata": None,
                        }
                        break

                    try:
                        data = json.loads(data_str)
                        delta = data["choices"][0]["delta"]

                        # Handle content
                        if "content" in delta and delta["content"] is not None:
                            yield {
                                "type": "content",
                                "content": delta["content"],
                                "tool_call": None,
                                "metadata": None,
                            }

                        # Handle tool calls (if supported by LiteLLM/Model)
                        # Basic support for OpenAI format tool calls
                        if "tool_calls" in delta and delta["tool_calls"]:
                            for tool_call in delta["tool_calls"]:
                                yield {
                                    # or tool_output if it's chunked, simplified for now
                                    "type": "tool_start",
                                    "content": None,
                                    "tool_call": tool_call,
                                    "metadata": None,
                                }

                    except json.JSONDecodeError:
                        LOGGER.warning("Failed to decode JSON chunk: %s", data_str)
                        continue

        except httpx.HTTPError as exc:
            yield {
                "type": "error",
                "content": f"Network error: {str(exc)}",
                "tool_call": None,
                "metadata": None,
            }

    async def generate(self, messages: Iterable[AgentMessage], *, model: str | None = None) -> str:
        """Call the LiteLLM chat completions endpoint and return the full assistant message.

        Refactored to consume the stream for backward compatibility.
        """
        full_content = []
        async for chunk in self.stream_chat(messages, model=model):
            if chunk["type"] == "content" and chunk["content"]:
                full_content.append(chunk["content"])
            elif chunk["type"] == "error":
                raise LiteLLMError(chunk["content"])

        return "".join(full_content)

    async def plan(self, messages: Iterable[AgentMessage], *, model: str | None = None) -> str:
        """Ask the model to emit a plan."""
        return await self.generate(messages, model=model)

    async def list_models(self) -> Any:
        """Return the raw body from LiteLLM's `/v1/models` endpoint."""

        try:
            response = await self._client.get(
                "/v1/models",
                headers=self._build_headers(),
            )
        except httpx.HTTPError as exc:
            raise LiteLLMError("Failed to reach LiteLLM gateway") from exc

        if response.status_code >= 400:
            LOGGER.error("LiteLLM error %s: %s", response.status_code, response.text)
            raise LiteLLMError(
                f"LiteLLM responded with {response.status_code}: {response.text[:256]}"
            )

        return response.json()


__all__ = ["LiteLLMClient", "LiteLLMError"]
