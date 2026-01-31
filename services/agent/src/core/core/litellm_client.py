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
from core.core.model_registry import ModelCapabilityRegistry, ReasoningMode
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
        self._registry = ModelCapabilityRegistry.get_instance()

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

                            # Get model from payload
                            current_model = payload.get("model", "")

                            # Get model capability
                            cap = self._registry.get_capability(current_model)

                            # Extract reasoning based on model capability
                            reasoning = None
                            if (
                                cap.reasoning_mode == ReasoningMode.SEPARATE_FIELD
                                and cap.reasoning_field
                            ):
                                reasoning = delta.get(cap.reasoning_field)
                                # Fallback to common field names if configured field not found
                                if reasoning is None:
                                    reasoning = delta.get("reasoning_content") or delta.get(
                                        "reasoning"
                                    )
                            elif cap.reasoning_mode == ReasoningMode.INLINE_TAGS:
                                content_text = delta.get("content", "")
                                if content_text:
                                    (
                                        reasoning_part,
                                        clean_content,
                                    ) = self._registry.extract_inline_reasoning(
                                        content_text, current_model
                                    )
                                    if reasoning_part:
                                        yield {
                                            "type": "thinking",
                                            "content": reasoning_part,
                                            "tool_call": None,
                                            "metadata": {"source": "reasoning_model"},
                                        }
                                    if clean_content:
                                        if not first_token_received:
                                            ttft_ms = (time.perf_counter() - start_time) * 1000
                                            set_span_attributes(
                                                {"gen_ai.performance.ttft_ms": ttft_ms}
                                            )
                                            add_span_event(
                                                "gen_ai.first_token", {"ttft_ms": ttft_ms}
                                            )
                                            first_token_received = True

                                        yield {
                                            "type": "content",
                                            "content": clean_content,
                                            "tool_call": None,
                                            "metadata": None,
                                        }
                                    continue

                            # Emit reasoning as thinking if found
                            if reasoning:
                                yield {
                                    "type": "thinking",
                                    "content": reasoning,
                                    "tool_call": None,
                                    "metadata": {"source": "reasoning_model"},
                                }

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
        Handles reasoning models by collecting thinking content as fallback.
        """
        full_content: list[str] = []
        full_thinking: list[str] = []
        current_model = model or self._settings.model_agentchat

        async for chunk in self.stream_chat(messages, model=model):
            if chunk["type"] == "content" and chunk["content"]:
                full_content.append(chunk["content"])
            elif chunk["type"] == "thinking" and chunk["content"]:
                full_thinking.append(chunk["content"])
            elif chunk["type"] == "error":
                raise LiteLLMError(chunk["content"])

        # If no content but we have thinking, use thinking as content (for reasoning models)
        if not full_content and full_thinking:
            if self._registry.should_fallback_to_reasoning(current_model):
                LOGGER.debug("Using reasoning_content as fallback (no content received)")
                return "".join(full_thinking)

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
        """Run completion with tool definitions and return full response object.

        Handles reasoning models by falling back to reasoning_content if content is empty.
        """
        current_model = model or self._settings.model_agentchat
        payload: dict[str, Any] = {
            "model": current_model,
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
            message = data["choices"][0]["message"]

            # Handle reasoning models: if content is empty, use reasoning field
            if not message.get("content"):
                cap = self._registry.get_capability(current_model)
                if cap.reasoning_field and message.get(cap.reasoning_field):
                    if self._registry.should_fallback_to_reasoning(current_model):
                        LOGGER.debug("Using %s as content fallback", cap.reasoning_field)
                        message["content"] = message[cap.reasoning_field]

            return message
        except httpx.HTTPError as exc:
            raise LiteLLMError(f"Tool completion failed: {exc}") from exc

    async def embed(
        self,
        texts: list[str],
        model: str = "embedder",
    ) -> list[list[float]]:
        """Get embeddings via LiteLLM proxy.

        Args:
            texts: List of strings to embed
            model: Model name configured in LiteLLM (default: "embedder")

        Returns:
            List of embedding vectors (list of floats)

        Raises:
            LiteLLMError: If the API returns an error
        """
        payload = {"model": model, "input": texts}
        response = await self._client.post(
            "/v1/embeddings",
            json=payload,
            headers=self._build_headers(),
        )
        if response.status_code >= 400:
            error_text = response.text
            LOGGER.error("LiteLLM embedding error %s: %s", response.status_code, error_text)
            raise LiteLLMError(f"Embedding failed: {response.status_code} - {error_text[:200]}")
        data = response.json()
        return [item["embedding"] for item in data["data"]]

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
