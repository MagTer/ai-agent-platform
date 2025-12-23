import shutil
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from core.tools.filesystem import ListDirectoryTool, ReadFileTool, validate_path


class TestFilesystemTools:
    @pytest.fixture(autouse=True)
    def setup_teardown(self) -> Generator[None, None, None]:
        # Create a temporary directory for testing
        self.temp_dir = tempfile.mkdtemp()
        self.base_path = Path(self.temp_dir).resolve()
        yield
        # Cleanup
        shutil.rmtree(self.temp_dir)

    def create_file(self, filename: str, content: str) -> Path:
        path = self.base_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    # --- validate_path Tests ---

    def test_validate_path_valid(self) -> None:
        """Test allowing valid relative paths within base."""
        # Create a dummy file to point to
        self.create_file("safe.txt", "content")

        # Test direct child
        res = validate_path(self.base_path, "safe.txt")
        assert res == self.base_path / "safe.txt"

        # Test subdir
        res = validate_path(self.base_path, "subdir/safe.txt")
        assert res == self.base_path / "subdir" / "safe.txt"

    def test_validate_path_traversal(self) -> None:
        """Test blocking attempts to escape base path (Security)."""
        with pytest.raises(ValueError, match="outside the sandbox"):
            validate_path(self.base_path, "../outside.txt")

        with pytest.raises(ValueError, match="outside the sandbox"):
            validate_path(self.base_path, "/etc/passwd")

        # Test sneaky traversal
        with pytest.raises(ValueError, match="outside the sandbox"):
            validate_path(self.base_path, "subdir/../../outside.txt")

    # --- ListDirectoryTool Tests ---

    @pytest.mark.asyncio
    async def test_ls_happy_path(self) -> None:
        """Verify listing a directory."""
        self.create_file("file1.txt", "")
        self.create_file("subdir/file2.txt", "")

        tool = ListDirectoryTool(base_path=str(self.base_path))

        # List root
        output = await tool.run(".")
        assert "file1.txt" in output
        assert "subdir/" in output

        # List subdir
        output = await tool.run("subdir")
        assert "- file2.txt" in output

    @pytest.mark.asyncio
    async def test_ls_security_block(self) -> None:
        """Verify ls blocks traversal."""
        tool = ListDirectoryTool(base_path=str(self.base_path))
        output = await tool.run("..")
        assert "Error:" in output
        assert "outside the sandbox" in output

    # --- ReadFileTool Tests ---

    @pytest.mark.asyncio
    async def test_read_happy_path(self) -> None:
        """Verify reading a file."""
        content = "Hello safe world"
        self.create_file("test.txt", content)

        tool = ReadFileTool(base_path=str(self.base_path))
        output = await tool.run("test.txt")
        assert output == content

    @pytest.mark.asyncio
    async def test_read_truncation(self) -> None:
        """Verify huge files are truncated."""
        tool = ReadFileTool(base_path=str(self.base_path), max_length=10)
        self.create_file("big.txt", "123456789012345")  # 15 chars

        output = await tool.run("big.txt")
        assert output.startswith("1234567890")
        assert "Content Truncated" in output
        assert len(output) < 100  # Ensure it didn't return full string + msg

    @pytest.mark.asyncio
    async def test_read_security_block(self) -> None:
        """Verify read blocks traversal."""
        tool = ReadFileTool(base_path=str(self.base_path))
        output = await tool.run("../outside.txt")
        assert "Error:" in output
        assert "outside the sandbox" in output

    # --- EditFileTool Tests ---

    @pytest.mark.asyncio
    async def test_edit_file_success(self) -> None:
        """Verify successful search and replace."""
        from core.tools.filesystem import EditFileTool

        content = "line1\nline2\nline3"
        self.create_file("edit.txt", content)

        tool = EditFileTool(base_path=str(self.base_path))
        target = "line2"
        replacement = "line2_modified"

        output = await tool.run("edit.txt", target=target, replacement=replacement)
        assert "Success" in output

        # Verify content
        new_content = (self.base_path / "edit.txt").read_text(encoding="utf-8")
        assert new_content == "line1\nline2_modified\nline3"

    @pytest.mark.asyncio
    async def test_edit_file_not_found(self) -> None:
        """Verify error when target is missing."""
        from core.tools.filesystem import EditFileTool

        self.create_file("edit.txt", "content")
        tool = EditFileTool(base_path=str(self.base_path))

        output = await tool.run("edit.txt", target="missing", replacement="foo")
        assert "Error: Target block not found" in output

    @pytest.mark.asyncio
    async def test_edit_file_ambiguous(self) -> None:
        """Verify error when multiple matches found."""
        from core.tools.filesystem import EditFileTool

        self.create_file("edit.txt", "repeat\nrepeat")
        tool = EditFileTool(base_path=str(self.base_path))

        output = await tool.run("edit.txt", target="repeat", replacement="foo")
        assert "Error: Target block found 2 times" in output
