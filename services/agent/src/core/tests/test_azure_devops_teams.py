"""Tests for Azure DevOps team resolution and validation functionality."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.tools.azure_devops import AzureDevOpsTool, _find_similar, _load_ado_mappings


@pytest.fixture(autouse=True)
def clear_ado_cache() -> None:
    """Clear the ADO mappings cache before each test."""
    _load_ado_mappings.cache_clear()


class TestLevenshteinDistance:
    """Test the Levenshtein distance helper function."""

    def test_find_similar_exact_match(self) -> None:
        """Exact match should be returned first."""
        candidates = ["platform", "infra", "security"]
        result = _find_similar("platform", candidates)
        assert "platform" in result

    def test_find_similar_close_match(self) -> None:
        """Close matches should be suggested."""
        candidates = ["platform", "infra", "security"]
        result = _find_similar("platfrom", candidates)  # typo
        assert "platform" in result

    def test_find_similar_no_match(self) -> None:
        """Distant matches should not be suggested."""
        candidates = ["platform", "infra", "security"]
        result = _find_similar("completely_different", candidates)
        assert len(result) == 0

    def test_find_similar_max_suggestions(self) -> None:
        """Should respect max_suggestions parameter."""
        candidates = ["platform", "platfrom", "pltform", "plat", "plf"]
        result = _find_similar("platform", candidates, max_suggestions=2)
        assert len(result) <= 2

    def test_find_similar_case_insensitive(self) -> None:
        """Comparison should be case-insensitive."""
        candidates = ["Platform", "INFRA", "Security"]
        result = _find_similar("platform", candidates)
        assert "Platform" in result


class TestTeamResolution:
    """Test team resolution and configuration."""

    @pytest.fixture
    def mock_mappings(self) -> dict:
        """Provide mock team mappings."""
        return {
            "defaults": {
                "area_path": "Web Teams\\Common",
                "default_type": "Feature",
            },
            "teams": {
                "platform": {
                    "area_path": "Web Teams\\Platform",
                    "default_type": "User story",
                },
                "security": {
                    "area_path": "Web Teams\\Platform\\Security",
                    "default_type": "User Story",
                    "default_tags": ["Security", "SecurityIncidentHigh"],
                },
                "infra": {
                    "area_path": "Web Teams\\Common\\Infra",
                    "default_type": "User story",
                },
            },
        }

    @pytest.fixture
    def tool(self, mock_mappings: dict) -> AzureDevOpsTool:
        """Create tool instance with mocked mappings."""
        with patch("core.tools.azure_devops._load_ado_mappings", return_value=mock_mappings):
            return AzureDevOpsTool()

    def test_get_available_teams(self, tool: AzureDevOpsTool) -> None:
        """Should return list of configured teams."""
        teams = tool._get_available_teams()
        assert teams == ["platform", "security", "infra"]

    def test_resolve_valid_team(self, tool: AzureDevOpsTool) -> None:
        """Valid team alias should return correct config."""
        config = tool._resolve_team_config("platform")
        assert config["area_path"] == "Web Teams\\Platform"
        assert config["default_type"] == "User story"
        assert config["_resolved_team"] == "platform"

    def test_resolve_team_with_tags(self, tool: AzureDevOpsTool) -> None:
        """Team with tags should include them in config."""
        config = tool._resolve_team_config("security")
        assert config["area_path"] == "Web Teams\\Platform\\Security"
        assert config["default_tags"] == ["Security", "SecurityIncidentHigh"]
        assert config["_resolved_team"] == "security"

    def test_resolve_invalid_team_shows_suggestions(self, tool: AzureDevOpsTool) -> None:
        """Invalid team should raise ValueError with suggestions."""
        with pytest.raises(ValueError) as exc_info:
            tool._resolve_team_config("platfrom")  # typo
        error_msg = str(exc_info.value)
        assert "Unknown team 'platfrom'" in error_msg
        assert "Available teams:" in error_msg
        assert "Did you mean: platform" in error_msg

    def test_resolve_invalid_team_no_suggestions(self, tool: AzureDevOpsTool) -> None:
        """Invalid team with no close matches should show available teams."""
        with pytest.raises(ValueError) as exc_info:
            tool._resolve_team_config("completely_invalid")
        error_msg = str(exc_info.value)
        assert "Unknown team 'completely_invalid'" in error_msg
        assert "Available teams: platform, security, infra" in error_msg
        assert "Did you mean" not in error_msg

    def test_resolve_no_team_uses_defaults(self, tool: AzureDevOpsTool) -> None:
        """None team_alias should return default config."""
        config = tool._resolve_team_config(None)
        assert config["area_path"] == "Web Teams\\Common"
        assert config["default_type"] == "Feature"
        assert "_resolved_team" not in config


class TestTeamValidation:
    """Test mapping validation functionality."""

    def test_validate_complete_mappings(self) -> None:
        """Valid mappings should produce no warnings."""
        mappings = {
            "teams": {
                "platform": {
                    "area_path": "Web Teams\\Platform",
                    "default_type": "User story",
                }
            }
        }
        with patch("core.tools.azure_devops._load_ado_mappings", return_value=mappings):
            tool = AzureDevOpsTool()
            warnings = tool._validate_mappings()
            assert len(warnings) == 0

    def test_validate_missing_area_path(self) -> None:
        """Missing area_path should produce warning."""
        mappings = {
            "teams": {
                "platform": {
                    "default_type": "User story",
                    # Missing area_path
                }
            }
        }
        with patch("core.tools.azure_devops._load_ado_mappings", return_value=mappings):
            tool = AzureDevOpsTool()
            warnings = tool._validate_mappings()
            assert len(warnings) == 1
            assert "Team 'platform' missing area_path" in warnings[0]

    def test_validate_missing_default_type(self) -> None:
        """Missing default_type should produce warning."""
        mappings = {
            "teams": {
                "platform": {
                    "area_path": "Web Teams\\Platform",
                    # Missing default_type
                }
            }
        }
        with patch("core.tools.azure_devops._load_ado_mappings", return_value=mappings):
            tool = AzureDevOpsTool()
            warnings = tool._validate_mappings()
            assert len(warnings) == 1
            assert "Team 'platform' missing default_type" in warnings[0]

    def test_validate_multiple_issues(self) -> None:
        """Multiple validation issues should all be reported."""
        mappings: dict[str, dict[str, dict[str, str]]] = {
            "teams": {
                "platform": {
                    # Missing both area_path and default_type
                },
                "security": {
                    "area_path": "Web Teams\\Security",
                    # Missing default_type
                },
            }
        }
        with patch("core.tools.azure_devops._load_ado_mappings", return_value=mappings):
            tool = AzureDevOpsTool()
            warnings = tool._validate_mappings()
            assert len(warnings) == 3

    def test_validate_empty_teams(self) -> None:
        """Empty teams dict should produce no warnings."""
        mappings: dict[str, dict[str, dict[str, str]]] = {"teams": {}}
        with patch("core.tools.azure_devops._load_ado_mappings", return_value=mappings):
            tool = AzureDevOpsTool()
            warnings = tool._validate_mappings()
            assert len(warnings) == 0


class TestTeamAwareQuerying:
    """Test team-aware list and search operations."""

    @pytest.fixture
    def mock_mappings(self) -> dict:
        """Provide mock team mappings."""
        return {
            "teams": {
                "platform": {
                    "area_path": "Web Teams\\Platform",
                    "default_type": "User story",
                },
                "security": {
                    "area_path": "Web Teams\\Platform\\Security",
                    "default_type": "User Story",
                    "default_tags": ["Security"],
                },
            }
        }

    @pytest.fixture
    def tool(self, mock_mappings: dict) -> AzureDevOpsTool:
        """Create tool instance with mocked mappings."""
        with patch("core.tools.azure_devops._load_ado_mappings", return_value=mock_mappings):
            tool = AzureDevOpsTool()
            # Mock credentials to return test values
            tool._get_credentials_for_user = AsyncMock(  # type: ignore[method-assign]
                return_value=("fake_pat", "https://dev.azure.com/test", "TestProject")
            )
            return tool

    @pytest.mark.asyncio
    async def test_list_by_team_alias(self, tool: AzureDevOpsTool) -> None:
        """List action should resolve team_alias to area_path."""
        # Mock the Azure DevOps client
        mock_wit_client = MagicMock()
        mock_result = MagicMock()
        mock_result.work_items = []
        mock_wit_client.query_by_wiql.return_value = mock_result

        with patch("core.tools.azure_devops.Connection") as mock_conn:
            mock_conn.return_value.clients.get_work_item_tracking_client.return_value = (
                mock_wit_client
            )

            await tool.run(action="list", team_alias="platform", state="Active")

            # Verify WIQL query contains team's area path
            call_args = mock_wit_client.query_by_wiql.call_args
            wiql_query = call_args[0][0]["query"]
            assert "Web Teams\\Platform" in wiql_query
            assert "[System.State] = 'Active'" in wiql_query

    @pytest.mark.asyncio
    async def test_list_invalid_team_returns_error(self, tool: AzureDevOpsTool) -> None:
        """List with invalid team should return error without querying ADO."""
        # Mock Connection to prevent actual API calls
        with patch("core.tools.azure_devops.Connection"):
            result = await tool.run(action="list", team_alias="invalid_team")
            assert "❌ Error: Unknown team 'invalid_team'" in result
            assert "Available teams:" in result

    @pytest.mark.asyncio
    async def test_search_by_team_alias(self, tool: AzureDevOpsTool) -> None:
        """Search action should scope to team's area path."""
        mock_wit_client = MagicMock()
        mock_result = MagicMock()
        mock_result.work_items = []
        mock_wit_client.query_by_wiql.return_value = mock_result

        with patch("core.tools.azure_devops.Connection") as mock_conn:
            mock_conn.return_value.clients.get_work_item_tracking_client.return_value = (
                mock_wit_client
            )

            await tool.run(action="search", query="auth", team_alias="security")

            # Verify WIQL query contains team area clause
            call_args = mock_wit_client.query_by_wiql.call_args
            wiql_query = call_args[0][0]["query"]
            assert "Web Teams\\Platform\\Security" in wiql_query
            assert "[System.AreaPath] UNDER" in wiql_query

    @pytest.mark.asyncio
    async def test_search_invalid_team_returns_error(self, tool: AzureDevOpsTool) -> None:
        """Search with invalid team should return error without querying ADO."""
        # Mock Connection to prevent actual API calls
        with patch("core.tools.azure_devops.Connection"):
            result = await tool.run(action="search", query="test", team_alias="invalid_team")
            assert "❌ Error: Unknown team 'invalid_team'" in result

    @pytest.mark.asyncio
    async def test_create_with_team_validation(self, tool: AzureDevOpsTool) -> None:
        """Create with invalid team should fail before API call."""
        # Mock Connection to prevent actual API calls
        with patch("core.tools.azure_devops.Connection"):
            result = await tool.run(
                action="create",
                title="Test Item",
                description="Test",
                team_alias="invalid_team",
                confirm_write=True,
            )
            assert "❌ Error: Unknown team 'invalid_team'" in result
            assert "Available teams:" in result


class TestGetTeamsAction:
    """Test the get_teams action."""

    @pytest.fixture
    def mock_mappings(self) -> dict:
        """Provide mock team mappings."""
        return {
            "teams": {
                "platform": {
                    "area_path": "Web Teams\\Platform",
                    "default_type": "User story",
                },
                "security": {
                    "area_path": "Web Teams\\Platform\\Security",
                    "default_type": "User Story",
                    "default_tags": ["Security", "SecurityIncidentHigh"],
                },
                "infra": {
                    "area_path": "Web Teams\\Common\\Infra",
                    "default_type": "User story",
                },
            }
        }

    @pytest.fixture
    def tool(self, mock_mappings: dict) -> AzureDevOpsTool:
        """Create tool instance with mocked mappings."""
        with patch("core.tools.azure_devops._load_ado_mappings", return_value=mock_mappings):
            tool = AzureDevOpsTool()
            # Mock credentials to return test values
            tool._get_credentials_for_user = AsyncMock(  # type: ignore[method-assign]
                return_value=("fake_pat", "https://dev.azure.com/test", "TestProject")
            )
            return tool

    @pytest.mark.asyncio
    async def test_get_teams_returns_formatted_list(self, tool: AzureDevOpsTool) -> None:
        """get_teams should return formatted list of teams."""
        # Mock Connection to prevent actual API calls (get_teams doesn't use it)
        with patch("core.tools.azure_devops.Connection"):
            result = await tool.run(action="get_teams")

            assert "### Configured Teams" in result
            assert "**platform**" in result
            assert "Area Path: Web Teams\\Platform" in result
            assert "Default Type: User story" in result

            assert "**security**" in result
            assert "Area Path: Web Teams\\Platform\\Security" in result
            assert "Default Tags: Security, SecurityIncidentHigh" in result

            assert "**infra**" in result

    @pytest.mark.asyncio
    async def test_get_teams_with_no_tags(self, tool: AzureDevOpsTool) -> None:
        """Teams without tags should show 'None'."""
        # Mock Connection to prevent actual API calls
        with patch("core.tools.azure_devops.Connection"):
            result = await tool.run(action="get_teams")
            # Platform team has no default_tags
            assert "**platform**" in result
            # Should show None for tags
            lines = result.split("\n")
            platform_section = []
            capture = False
            for line in lines:
                if "**platform**" in line:
                    capture = True
                elif capture and "**" in line:
                    break
                if capture:
                    platform_section.append(line)

            tags_line = [line for line in platform_section if "Default Tags:" in line]
            assert len(tags_line) == 1
            assert "None" in tags_line[0]

    @pytest.mark.asyncio
    async def test_get_teams_empty_config(self) -> None:
        """Empty teams config should return helpful message."""
        empty_mappings: dict[str, dict[str, dict[str, str]]] = {"teams": {}}
        with patch("core.tools.azure_devops._load_ado_mappings", return_value=empty_mappings):
            tool = AzureDevOpsTool()
            # Mock credentials to return test values
            tool._get_credentials_for_user = AsyncMock(  # type: ignore[method-assign]
                return_value=("fake_pat", "https://dev.azure.com/test", "TestProject")
            )
            # Mock Connection to prevent actual API calls
            with patch("core.tools.azure_devops.Connection"):
                result = await tool.run(action="get_teams")
                assert "No teams configured" in result


class TestBackwardsCompatibility:
    """Test that existing functionality still works."""

    @pytest.fixture
    def mock_mappings(self) -> dict:
        """Provide mock team mappings."""
        return {
            "defaults": {
                "area_path": "Web Teams\\Common",
                "default_type": "Feature",
            },
            "teams": {
                "platform": {
                    "area_path": "Web Teams\\Platform",
                    "default_type": "User story",
                }
            },
        }

    @pytest.fixture
    def tool(self, mock_mappings: dict) -> AzureDevOpsTool:
        """Create tool instance with mocked mappings."""
        with patch("core.tools.azure_devops._load_ado_mappings", return_value=mock_mappings):
            tool = AzureDevOpsTool()
            # Mock credentials to return test values
            tool._get_credentials_for_user = AsyncMock(  # type: ignore[method-assign]
                return_value=("fake_pat", "https://dev.azure.com/test", "TestProject")
            )
            return tool

    @pytest.mark.asyncio
    async def test_list_with_area_path_still_works(self, tool: AzureDevOpsTool) -> None:
        """List with area_path (no team_alias) should still work."""
        mock_wit_client = MagicMock()
        mock_result = MagicMock()
        mock_result.work_items = []
        mock_wit_client.query_by_wiql.return_value = mock_result

        with patch("core.tools.azure_devops.Connection") as mock_conn:
            mock_conn.return_value.clients.get_work_item_tracking_client.return_value = (
                mock_wit_client
            )

            await tool.run(action="list", area_path="Custom\\Path", state="Active")

            # Should use provided area_path
            call_args = mock_wit_client.query_by_wiql.call_args
            wiql_query = call_args[0][0]["query"]
            assert "Custom\\Path" in wiql_query

    @pytest.mark.asyncio
    async def test_create_without_team_uses_defaults(self, tool: AzureDevOpsTool) -> None:
        """Create without team_alias should use defaults."""
        mock_wit_client = MagicMock()
        mock_wi = MagicMock()
        mock_wi.id = 12345
        mock_wit_client.create_work_item.return_value = mock_wi

        with patch("core.tools.azure_devops.Connection") as mock_conn:
            mock_conn.return_value.clients.get_work_item_tracking_client.return_value = (
                mock_wit_client
            )

            await tool.run(
                action="create",
                title="Test",
                description="Test desc",
                confirm_write=True,
            )

            # Should use default area_path and type
            call_args = mock_wit_client.create_work_item.call_args
            document = call_args[1]["document"]
            area_field = [d for d in document if "AreaPath" in d.get("path", "")]
            assert len(area_field) == 1
            assert area_field[0]["value"] == "Web Teams\\Common"
