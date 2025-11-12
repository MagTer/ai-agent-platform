"""Async client for the LiteLLM gateway."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

import httpx

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

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._settings.litellm_api_key:
            headers["Authorization"] = f"Bearer {self._settings.litellm_api_key}"
        return headers

    async def _chat(self, messages: Iterable[AgentMessage], *, model: str | None = None) -> str:
        """Send the chat payload to LiteLLM and return the assistant response."""

        payload: dict[str, Any] = {
            "model": model or self._settings.litellm_model,
            "messages": [message.model_dump() for message in messages],
        }

        async with httpx.AsyncClient(base_url=str(self._settings.litellm_api_base)) as client:
            try:
                response = await client.post(
                    "/v1/chat/completions",
                    json=payload,
                    headers=self._build_headers(),
                    timeout=self._timeout,
                )
            except httpx.HTTPError as exc:
                # pragma: no cover - network errors are unlikely in tests
                raise LiteLLMError("Failed to reach LiteLLM gateway") from exc

        if response.status_code >= 400:
            LOGGER.error("LiteLLM error %s: %s", response.status_code, response.text)
            raise LiteLLMError(
                f"LiteLLM responded with {response.status_code}: {response.text[:256]}"
            )

        data = response.json()
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:  # pragma: no cover - defensive
            raise LiteLLMError("Unexpected response format from LiteLLM") from exc

    async def generate(self, messages: Iterable[AgentMessage], *, model: str | None = None) -> str:
        """Call the LiteLLM chat completions endpoint and return the assistant message."""

        return await self._chat(messages, model=model)

    async def plan(self, messages: Iterable[AgentMessage], *, model: str | None = None) -> str:
        """Ask Gemma3 to emit a structured execution plan before running the final completion."""

        return await self._chat(messages, model=model)

    async def list_models(self) -> Any:
        """Return the raw body from LiteLLM's `/v1/models` endpoint."""

        async with httpx.AsyncClient(base_url=str(self._settings.litellm_api_base)) as client:
            try:
                response = await client.get(
                    "/v1/models",
                    headers=self._build_headers(),
                    timeout=self._timeout,
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
