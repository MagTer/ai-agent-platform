"""Async client for the LiteLLM gateway."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator, Iterable
from typing import Any

import httpx
from shared.streaming import AgentChunk

from core.core.config import Settings
from core.core.models import AgentMessage
from core.observability.tracing import add_span_event, set_span_attributes

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
        self,
        messages: Iterable[AgentMessage],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[AgentChunk, None]:
        """Stream chat completions from LiteLLM."""
        payload: dict[str, Any] = {
            "model": model or self._settings.model_agentchat,
            "messages": [message.model_dump() for message in messages],
            "stream": True,
        }

        # Add tools if provided
        if tools:
            payload["tools"] = tools
        start_time = time.perf_counter()
        first_token_received = False

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

                    # Force yield to allow event loop to process other tasks (e.g. flushing)
                    await asyncio.sleep(0)

                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        total_latency_ms = (time.perf_counter() - start_time) * 1000
                        set_span_attributes({"gen_ai.performance.latency_ms": total_latency_ms})
                        yield {
                            "type": "done",
                            "content": None,
                            "tool_call": None,
                            "metadata": None,
                        }
                        break

                    try:
                        data = json.loads(data_str)
                        if "model" in data and data["model"]:
                            set_span_attributes({"gen_ai.response.model": data["model"]})

                        if "usage" in data and data["usage"]:
                            usage = data["usage"]
                            attrs = {}
                            if "prompt_tokens" in usage:
                                attrs["gen_ai.usage.prompt_tokens"] = usage["prompt_tokens"]
                            if "completion_tokens" in usage:
                                attrs["gen_ai.usage.completion_tokens"] = usage["completion_tokens"]
                            if "total_tokens" in usage:
                                attrs["gen_ai.usage.total_tokens"] = usage["total_tokens"]
                            # OpenRouter sometimes sends cost
                            if "cost" in usage:
                                attrs["gen_ai.usage.cost"] = usage["cost"]

                            if attrs:
                                set_span_attributes(attrs)

                        # Handle content
                        if "choices" in data and len(data["choices"]) > 0:
                            choice = data["choices"][0]
                            delta = choice.get("delta", {})

                            if "content" in delta and delta["content"] is not None:
                                if not first_token_received:
                                    ttft_ms = (time.perf_counter() - start_time) * 1000
                                    set_span_attributes({"gen_ai.performance.ttft_ms": ttft_ms})
                                    add_span_event("gen_ai.first_token", {"ttft_ms": ttft_ms})
                                    first_token_received = True

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

    async def run_with_tools(
        self,
        messages: Iterable[AgentMessage],
        tools: list[dict[str, Any]],
        *,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Run completion with tool definitions and return full response object."""
        payload: dict[str, Any] = {
            "model": model or self._settings.model_agentchat,
            "messages": [message.model_dump() for message in messages],
            "tools": tools,
            "tool_choice": "auto",
        }

        try:
            response = await self._client.post(
                "/v1/chat/completions",
                json=payload,
                headers=self._build_headers(),
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]
        except httpx.HTTPError as exc:
            raise LiteLLMError(f"Tool completion failed: {exc}") from exc

    async def list_models(self) -> dict[str, Any]:
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
