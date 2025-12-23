from pathlib import Path
from unittest.mock import MagicMock, patch

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

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="collected 1 item\n, 1 passed", stderr=""
        )

        result = await pytest_tool.run()
        assert "Pytest PASSED" in result
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "pytest"
        assert args[1].startswith("tests")  # Allow 'tests' or 'tests/'

    # Test RunLinterTool
    linter_tool = RunLinterTool(base_path=base_path)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        result = await linter_tool.run(files=["tests/test_dummy.py"])
        assert "Linting Passed" in result
        args = mock_run.call_args[0][0]
        assert args[0] == "ruff"
        assert args[1] == "check"
