"""
Pytest fixtures for semantic integration tests.

These tests treat the agent as a black box via HTTP and validate
response quality, structure, and observability.
"""

import asyncio
import os

import httpx
import pytest
import pytest_asyncio

# Default to localhost, can be overridden via env var
AGENT_BASE_URL = os.getenv("AGENT_BASE_URL", "http://localhost:8000")

# Test timeouts (semantic tests can be slow due to real LLM + web searches)
DEFAULT_TIMEOUT = 120.0  # 2 minutes
HEALTH_CHECK_TIMEOUT = 60.0  # 1 minute to become healthy


@pytest.fixture(scope="session")
def agent_base_url() -> str:
    """Get the agent base URL."""
    return AGENT_BASE_URL


@pytest_asyncio.fixture
async def async_client() -> httpx.AsyncClient:
    """Async HTTP client with generous timeout for semantic tests."""
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        yield client


@pytest_asyncio.fixture(scope="session")
async def ensure_agent_healthy() -> None:
    """
    Wait for agent to be healthy before running tests.

    Polls /diagnostics/summary until overall_status == "HEALTHY".
    Raises if agent doesn't become healthy within timeout.
    """
    url = f"{AGENT_BASE_URL}/diagnostics/summary"
    start_time = asyncio.get_event_loop().time()
    last_error: str | None = None

    async with httpx.AsyncClient(timeout=10.0) as client:
        while (asyncio.get_event_loop().time() - start_time) < HEALTH_CHECK_TIMEOUT:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    status = data.get("overall_status", "UNKNOWN")

                    if status == "HEALTHY":
                        return  # Agent is ready

                    last_error = f"Status: {status}"
                else:
                    last_error = f"HTTP {resp.status_code}"

            except httpx.ConnectError:
                last_error = "Connection refused"
            except Exception as e:
                last_error = str(e)

            await asyncio.sleep(2.0)

    pytest.skip(
        f"Agent not healthy after {HEALTH_CHECK_TIMEOUT}s. "
        f"Last error: {last_error}. "
        f"Start the agent with docker-compose."
    )


def make_chat_request_payload(
    message: str,
    model: str = "local/llama3-en",
) -> dict:
    """Create a chat completions request payload."""
    return {
        "model": model,
        "messages": [{"role": "user", "content": message}],
        "stream": True,
        "metadata": {
            "platform": "semantic_test",
            "platform_id": "pytest_runner",
        },
    }


def get_request_headers() -> dict[str, str]:
    """Get standard request headers."""
    return {
        "Content-Type": "application/json",
        "Authorization": "Bearer sk-semantic-test",
    }
