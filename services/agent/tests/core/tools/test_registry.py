"""Unit tests for ToolRegistry clone and permissions."""

from __future__ import annotations

from unittest.mock import MagicMock

from core.tools.registry import ToolRegistry


class TestToolRegistryClone:
    """Test ToolRegistry clone functionality."""

    def test_clone_creates_shallow_copy(self):
        """Test that clone creates a new registry instance with copied tools dict."""
        # Create original registry
        registry = ToolRegistry()

        # Register some tools
        tool1 = MagicMock()
        tool1.name = "tool1"
        tool2 = MagicMock()
        tool2.name = "tool2"

        registry.register(tool1)
        registry.register(tool2)

        # Clone registry
        cloned = registry.clone()

        # Verify it's a different instance
        assert cloned is not registry

        # Verify tools are copied
        assert len(cloned.list_tools()) == 2
        assert "tool1" in cloned.list_tools()
        assert "tool2" in cloned.list_tools()

        # Verify tools dict is a shallow copy (different dict, same tool objects)
        assert cloned._tools is not registry._tools
        assert cloned._tools["tool1"] is registry._tools["tool1"]
        assert cloned._tools["tool2"] is registry._tools["tool2"]

    def test_clone_mutations_dont_affect_original(self):
        """Test that modifying cloned registry doesn't affect original."""
        # Create and populate original
        registry = ToolRegistry()
        tool1 = MagicMock()
        tool1.name = "tool1"
        tool2 = MagicMock()
        tool2.name = "tool2"

        registry.register(tool1)
        registry.register(tool2)

        # Clone and modify
        cloned = registry.clone()
        tool3 = MagicMock()
        tool3.name = "tool3"
        cloned.register(tool3)

        # Original should be unchanged
        assert "tool3" not in registry.list_tools()
        assert len(registry.list_tools()) == 2

        # Clone should have new tool
        assert "tool3" in cloned.list_tools()
        assert len(cloned.list_tools()) == 3

    def test_clone_empty_registry(self):
        """Test cloning an empty registry."""
        registry = ToolRegistry()
        cloned = registry.clone()

        assert cloned is not registry
        assert len(cloned.list_tools()) == 0


class TestToolRegistryPermissions:
    """Test ToolRegistry permissions filtering."""

    def test_filter_by_permissions_removes_denied_tools(self):
        """Test that denied tools are removed from registry."""
        # Create registry with tools
        registry = ToolRegistry()
        bash_tool = MagicMock()
        bash_tool.name = "bash"
        python_tool = MagicMock()
        python_tool.name = "python"
        grep_tool = MagicMock()
        grep_tool.name = "grep"

        registry.register(bash_tool)
        registry.register(python_tool)
        registry.register(grep_tool)

        # Apply permissions: deny bash, allow others
        permissions = {
            "bash": False,
            "python": True,
            "grep": True,
        }

        registry.filter_by_permissions(permissions)

        # Verify bash removed
        tools = registry.list_tools()
        assert "bash" not in tools
        assert "python" in tools
        assert "grep" in tools
        assert len(tools) == 2

    def test_filter_by_permissions_default_allow(self):
        """Test that tools not in permissions dict are allowed by default."""
        registry = ToolRegistry()
        bash_tool = MagicMock()
        bash_tool.name = "bash"
        python_tool = MagicMock()
        python_tool.name = "python"
        grep_tool = MagicMock()
        grep_tool.name = "grep"

        registry.register(bash_tool)
        registry.register(python_tool)
        registry.register(grep_tool)

        # Only deny bash, don't mention python/grep
        permissions = {
            "bash": False,
        }

        registry.filter_by_permissions(permissions)

        # Verify only bash removed
        tools = registry.list_tools()
        assert "bash" not in tools
        assert "python" in tools  # Allowed by default
        assert "grep" in tools  # Allowed by default
        assert len(tools) == 2

    def test_filter_by_permissions_empty_dict(self):
        """Test that empty permissions dict allows all tools."""
        registry = ToolRegistry()
        bash_tool = MagicMock()
        bash_tool.name = "bash"
        python_tool = MagicMock()
        python_tool.name = "python"

        registry.register(bash_tool)
        registry.register(python_tool)

        # Empty permissions
        permissions = {}

        registry.filter_by_permissions(permissions)

        # All tools should remain
        tools = registry.list_tools()
        assert len(tools) == 2
        assert "bash" in tools
        assert "python" in tools

    def test_filter_by_permissions_none(self):
        """Test that None permissions allows all tools."""
        registry = ToolRegistry()
        bash_tool = MagicMock()
        bash_tool.name = "bash"

        registry.register(bash_tool)

        # None permissions (early return in implementation)
        registry.filter_by_permissions(None)

        # Tool should remain
        assert "bash" in registry.list_tools()

    def test_filter_by_permissions_all_denied(self):
        """Test denying all tools."""
        registry = ToolRegistry()
        bash_tool = MagicMock()
        bash_tool.name = "bash"
        python_tool = MagicMock()
        python_tool.name = "python"

        registry.register(bash_tool)
        registry.register(python_tool)

        # Deny all
        permissions = {
            "bash": False,
            "python": False,
        }

        registry.filter_by_permissions(permissions)

        # All tools removed
        assert len(registry.list_tools()) == 0

    def test_filter_by_permissions_mixed(self):
        """Test mixed allow/deny permissions."""
        registry = ToolRegistry()
        tools = []
        tool_names = ["bash", "python", "grep", "git", "docker"]

        for name in tool_names:
            tool = MagicMock()
            tool.name = name
            registry.register(tool)
            tools.append(tool)

        # Mixed permissions
        permissions = {
            "bash": False,
            "python": True,
            "grep": False,
            # git and docker not mentioned - should be allowed by default
        }

        registry.filter_by_permissions(permissions)

        # Verify correct tools remain
        result_tools = registry.list_tools()
        assert "bash" not in result_tools
        assert "python" in result_tools
        assert "grep" not in result_tools
        assert "git" in result_tools  # Allowed by default
        assert "docker" in result_tools  # Allowed by default
        assert len(result_tools) == 3

    def test_filter_by_permissions_preserves_tool_objects(self):
        """Test that permission filtering preserves original tool objects."""
        registry = ToolRegistry()
        python_tool = MagicMock()
        python_tool.name = "python"
        python_tool.special_attribute = "preserved"

        registry.register(python_tool)

        permissions = {"python": True}
        registry.filter_by_permissions(permissions)

        # Verify tool object is preserved
        tools_dict = registry._tools
        assert "python" in tools_dict
        assert tools_dict["python"] is python_tool
        assert tools_dict["python"].special_attribute == "preserved"

    def test_filter_by_permissions_is_mutation(self):
        """Test that filter_by_permissions mutates the registry in place."""
        registry = ToolRegistry()
        bash_tool = MagicMock()
        bash_tool.name = "bash"

        registry.register(bash_tool)

        original_tools_dict = registry._tools
        assert "bash" in original_tools_dict

        permissions = {"bash": False}
        registry.filter_by_permissions(permissions)

        # Same dict object (mutated)
        assert registry._tools is original_tools_dict
        # But content changed
        assert "bash" not in registry._tools


class TestToolRegistryIntegration:
    """Integration tests for clone + permissions workflow."""

    def test_clone_then_filter_workflow(self):
        """Test the full workflow: clone registry, apply permissions."""
        # Base registry (shared template)
        base_registry = ToolRegistry()
        tools = []
        for name in ["bash", "python", "grep", "git"]:
            tool = MagicMock()
            tool.name = name
            base_registry.register(tool)
            tools.append(tool)

        # Context A: Clone and deny bash
        context_a_registry = base_registry.clone()
        context_a_registry.filter_by_permissions({"bash": False})

        assert "bash" not in context_a_registry.list_tools()
        assert len(context_a_registry.list_tools()) == 3

        # Context B: Clone and deny python, git
        context_b_registry = base_registry.clone()
        context_b_registry.filter_by_permissions({"python": False, "git": False})

        assert "python" not in context_b_registry.list_tools()
        assert "git" not in context_b_registry.list_tools()
        assert len(context_b_registry.list_tools()) == 2

        # Verify base registry unchanged
        assert len(base_registry.list_tools()) == 4
        assert "bash" in base_registry.list_tools()
        assert "python" in base_registry.list_tools()

    def test_multiple_clones_independent(self):
        """Test that multiple clones are independent."""
        base = ToolRegistry()
        for name in ["tool1", "tool2", "tool3"]:
            tool = MagicMock()
            tool.name = name
            base.register(tool)

        clone1 = base.clone()
        clone2 = base.clone()
        clone3 = base.clone()

        # Filter each differently
        clone1.filter_by_permissions({"tool1": False})
        clone2.filter_by_permissions({"tool2": False})
        clone3.filter_by_permissions({"tool3": False})

        # Verify independence
        assert "tool1" not in clone1.list_tools()
        assert "tool2" in clone1.list_tools()
        assert "tool3" in clone1.list_tools()

        assert "tool1" in clone2.list_tools()
        assert "tool2" not in clone2.list_tools()
        assert "tool3" in clone2.list_tools()

        assert "tool1" in clone3.list_tools()
        assert "tool2" in clone3.list_tools()
        assert "tool3" not in clone3.list_tools()

        # Base unchanged
        assert len(base.list_tools()) == 3
