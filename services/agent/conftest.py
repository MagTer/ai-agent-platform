# ruff: noqa: E402
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from core.runtime.config import Settings
from core.runtime.memory import MemoryStore
from core.runtime.service import AgentService
from core.tests.mocks import MockLLMClient


@pytest.fixture
def mock_litellm():
    return MockLLMClient()


@pytest.fixture
def mock_settings():
    return Settings(
        litellm_model="mock-model",
        litellm_api_key="mock-key",
        litellm_api_base="http://mock-url",
        contexts_config_path="contexts/contexts.yaml",
        tools_config_path="config/tools.yaml",
    )


@pytest.fixture
def mock_memory_store(mock_settings):
    # For now, using real MemoryStore but could mock further if needed.
    # Ideally we'd use an in-memory Qdrant or mock basic methods.
    return MemoryStore(mock_settings)


from core.tools import ToolRegistry
from core.tools.filesystem import ReadFileTool


@pytest.fixture
def mock_agent_service(mock_litellm, mock_settings, mock_memory_store):
    # Register core tools for testing
    from pathlib import Path

    registry = ToolRegistry([ReadFileTool(base_path=Path("/"))])

    return AgentService(
        settings=mock_settings,
        litellm=mock_litellm,
        memory=mock_memory_store,
        tool_registry=registry,
    )
