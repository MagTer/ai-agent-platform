from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from core.context_manager import ContextManager
from core.core.config import Settings
from core.db.models import Context
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
def mock_settings(tmp_path):
    settings = Settings()
    # Mock settings.contexts_dir to use tmp_path
    settings.contexts_dir = tmp_path / "contexts"
    return settings


@pytest.mark.asyncio
async def test_create_virtual_context(mock_settings, tmp_path):
    # Mock DB Session
    session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute.return_value = mock_result

    manager = ContextManager(mock_settings)

    # Action
    context = await manager.create_context(session, "test-virtual", "virtual", {})

    # Verify DB interaction
    assert session.add.called
    assert context.name == "test-virtual"
    assert context.type == "virtual"

    # Verify FS
    expected_path = mock_settings.contexts_dir / "test-virtual"
    assert expected_path.exists()
    assert context.default_cwd == str(expected_path)


@pytest.mark.asyncio
async def test_create_git_context_success(mock_settings):
    session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute.return_value = mock_result

    manager = ContextManager(mock_settings)

    with patch("subprocess.run") as mock_run:
        args = {"url": "https://github.com/test/repo.git"}
        context = await manager.create_context(session, "test-git", "git", args)

        # Verify subprocess called
        expected_path = mock_settings.contexts_dir / "test-git"
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "git"
        assert call_args[1] == "clone"
        assert call_args[2] == args["url"]
        assert call_args[3] == str(expected_path)

        assert context.type == "git"
        assert context.config == args


@pytest.mark.asyncio
async def test_create_context_collision(mock_settings):
    session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    # Simulate existing context
    mock_result.scalar_one_or_none.return_value = Context(name="collision")
    session.execute.return_value = mock_result

    manager = ContextManager(mock_settings)

    with pytest.raises(ValueError, match="already exists"):
        await manager.create_context(session, "collision", "virtual", {})
