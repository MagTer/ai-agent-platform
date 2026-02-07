from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.tools.qa import RunLinterTool, RunPytestTool


@pytest.mark.asyncio
async def test_qa_tools(tmp_path: Path) -> None:
    # Setup
    base_path = str(tmp_path)
    # Create a dummy test file
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_dummy.py").write_text("def test_foo(): assert True")

    # Test RunPytestTool
    pytest_tool = RunPytestTool(base_path=base_path)

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"collected 1 item\n, 1 passed", b""))

    with patch("core.tools.qa.asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        result = await pytest_tool.run()
        assert "Pytest PASSED" in result
        mock_exec.assert_called_once()
        args = mock_exec.call_args[0]
        assert args[0] == "pytest"
        assert args[1].startswith("tests")  # Allow 'tests' or 'tests/'

    # Test RunLinterTool
    linter_tool = RunLinterTool(base_path=base_path)

    mock_proc2 = MagicMock()
    mock_proc2.returncode = 0
    mock_proc2.communicate = AsyncMock(return_value=(b"", b""))

    patch_path = "core.tools.qa.asyncio.create_subprocess_exec"
    with patch(patch_path, return_value=mock_proc2) as mock_exec:
        result = await linter_tool.run(files=["tests/test_dummy.py"])
        assert "Linting Passed" in result
        args = mock_exec.call_args[0]
        assert args[0] == "ruff"
        assert args[1] == "check"
