"""Tests for Azure DevOps create, children, team_summary actions and helpers."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from core.tools.azure_devops import AzureDevOpsTool, _sanitize_wiql_value

MOCK_MAPPINGS: dict = {
    "defaults": {
        "area_path": "Web Teams\\Common",
        "default_type": "Feature",
    },
    "teams": {
        "platform": {
            "area_path": "Web Teams\\Platform",
            "default_type": "User Story",
            "default_tags": ["platform-team"],
        },
        "security": {
            "area_path": "Web Teams\\Security",
            "default_type": "Bug",
            "default_tags": ["Security"],
        },
        "infra": {
            "area_path": "Web Teams\\Infra",
            "default_type": "User Story",
        },
    },
}

FAKE_CONTEXT_ID = UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def tool() -> AzureDevOpsTool:
    """Create AzureDevOpsTool with mocked DB mappings and credentials."""
    t = AzureDevOpsTool()
    t._get_credentials_for_context = AsyncMock(  # type: ignore[method-assign]
        return_value=("fake_pat", "https://dev.azure.com/TestOrg", "TestProject")
    )
    t._load_mappings_from_db = AsyncMock(return_value=MOCK_MAPPINGS)  # type: ignore[method-assign]
    return t


# ---------------------------------------------------------------------------
# _sanitize_wiql_value
# ---------------------------------------------------------------------------


class TestSanitizeWiqlValue:
    """Direct tests for the _sanitize_wiql_value helper."""

    def test_single_quotes_doubled(self) -> None:
        """Single quotes in values must be escaped by doubling them."""
        assert _sanitize_wiql_value("O'Brien") == "O''Brien"

    def test_multiple_single_quotes(self) -> None:
        """Multiple single quotes are all escaped."""
        assert _sanitize_wiql_value("it's a test's value") == "it''s a test''s value"

    def test_empty_string_returns_empty(self) -> None:
        """Empty string returns empty string unchanged."""
        assert _sanitize_wiql_value("") == ""

    def test_normal_string_unchanged(self) -> None:
        """Strings without single quotes pass through unchanged."""
        assert _sanitize_wiql_value("Normal Project Name") == "Normal Project Name"

    def test_only_quotes(self) -> None:
        """A string of only quotes is escaped correctly."""
        assert _sanitize_wiql_value("'''") == "''''''"


# ---------------------------------------------------------------------------
# create action
# ---------------------------------------------------------------------------


class TestCreateAction:
    """Tests for the 'create' action of AzureDevOpsTool."""

    @pytest.mark.asyncio
    async def test_create_requires_confirm_write_true(self, tool: AzureDevOpsTool) -> None:
        """Create action without confirm_write=True returns a confirmation-required message.

        Note: ToolConfirmationError is raised inside the try block but caught by the outer
        except handler and returned as an error string. The caller (executor) handles the
        confirmation workflow based on the error message content.
        """
        with patch("core.tools.azure_devops.Connection"):
            result = await tool.run(
                action="create",
                title="My Feature",
                description="Feature description",
                # confirm_write NOT passed -> defaults to missing
            )
        # ToolConfirmationError is caught and returned as error string
        assert "confirmation" in result.lower() or "requires" in result.lower()

    @pytest.mark.asyncio
    async def test_create_confirm_write_false_returns_confirmation_message(
        self, tool: AzureDevOpsTool
    ) -> None:
        """confirm_write=False (boolean False) still triggers the confirmation check."""
        with patch("core.tools.azure_devops.Connection"):
            result = await tool.run(
                action="create",
                title="My Feature",
                description="Feature description",
                confirm_write=False,
            )
        assert "confirmation" in result.lower() or "requires" in result.lower()

    @pytest.mark.asyncio
    async def test_create_missing_title_returns_error(self, tool: AzureDevOpsTool) -> None:
        """Missing title should return an error message."""
        mock_wit_client = MagicMock()
        with patch("core.tools.azure_devops.Connection") as mock_conn:
            mock_conn.return_value.clients.get_work_item_tracking_client.return_value = (
                mock_wit_client
            )
            result = await tool.run(
                action="create",
                description="Some description",
                confirm_write=True,
            )
        assert "Error" in result
        assert "title" in result.lower() or "description" in result.lower()

    @pytest.mark.asyncio
    async def test_create_missing_description_returns_error(self, tool: AzureDevOpsTool) -> None:
        """Missing description should return an error message."""
        mock_wit_client = MagicMock()
        with patch("core.tools.azure_devops.Connection") as mock_conn:
            mock_conn.return_value.clients.get_work_item_tracking_client.return_value = (
                mock_wit_client
            )
            result = await tool.run(
                action="create",
                title="My Feature",
                confirm_write=True,
            )
        assert "Error" in result
        assert "title" in result.lower() or "description" in result.lower()

    @pytest.mark.asyncio
    async def test_create_document_construction(self, tool: AzureDevOpsTool) -> None:
        """Create should build document with title, description, area_path, and tags."""
        mock_wi = MagicMock()
        mock_wi.id = 999
        mock_wit_client = MagicMock()
        mock_wit_client.create_work_item.return_value = mock_wi

        with patch("core.tools.azure_devops.Connection") as mock_conn:
            mock_conn.return_value.clients.get_work_item_tracking_client.return_value = (
                mock_wit_client
            )
            result = await tool.run(
                action="create",
                title="My Feature",
                description="Feature description",
                team_alias="platform",
                confirm_write=True,
            )

        assert "999" in result
        call_kwargs = mock_wit_client.create_work_item.call_args[1]
        document = call_kwargs["document"]
        paths = {entry["path"]: entry["value"] for entry in document}

        assert paths["/fields/System.Title"] == "My Feature"
        assert "Feature description" in paths["/fields/System.Description"]
        assert paths["/fields/System.AreaPath"] == "Web Teams\\Platform"

    @pytest.mark.asyncio
    async def test_create_tag_deduplication(self, tool: AzureDevOpsTool) -> None:
        """Passing tags that duplicate team defaults should not create duplicate tags."""
        mock_wi = MagicMock()
        mock_wi.id = 42
        mock_wit_client = MagicMock()
        mock_wit_client.create_work_item.return_value = mock_wi

        with patch("core.tools.azure_devops.Connection") as mock_conn:
            mock_conn.return_value.clients.get_work_item_tracking_client.return_value = (
                mock_wit_client
            )
            await tool.run(
                action="create",
                title="Tagged Item",
                description="Description",
                team_alias="platform",
                # "platform-team" is already a default tag for the "platform" team
                tags=["platform-team", "extra-tag"],
                confirm_write=True,
            )

        call_kwargs = mock_wit_client.create_work_item.call_args[1]
        document = call_kwargs["document"]
        paths = {entry["path"]: entry["value"] for entry in document}
        tags_value = paths.get("/fields/System.Tags", "")
        # Split tags and count occurrences
        tags_list = [t.strip() for t in tags_value.split(";") if t.strip()]
        assert tags_list.count("platform-team") == 1
        assert "extra-tag" in tags_list

    @pytest.mark.asyncio
    async def test_create_ac_fallback_on_field_rejection(self, tool: AzureDevOpsTool) -> None:
        """When AC field is rejected, AC text is appended to description and retried.

        This test uses team_alias='platform' which has default_type='User Story' so that
        Acceptance Criteria is NOT pre-emptively stripped (it's only stripped for 'Feature').
        """
        mock_wi = MagicMock()
        mock_wi.id = 77
        mock_wit_client = MagicMock()

        # First call raises AC field error; second call succeeds
        ac_error = Exception(
            "TF51535: The field 'Microsoft.VSTS.Common.AcceptanceCriteria' is not in use"
        )
        mock_wit_client.create_work_item.side_effect = [ac_error, mock_wi]

        with patch("core.tools.azure_devops.Connection") as mock_conn:
            mock_conn.return_value.clients.get_work_item_tracking_client.return_value = (
                mock_wit_client
            )
            result = await tool.run(
                action="create",
                title="Story with AC",
                description="Original description",
                acceptance_criteria="Given X, When Y, Then Z",
                # Use "platform" team whose default type is "User Story" (not Feature)
                # so the AC is NOT pre-emptively dropped before the first create attempt.
                team_alias="platform",
                confirm_write=True,
            )

        assert "77" in result
        assert mock_wit_client.create_work_item.call_count == 2

        # Second call's document should NOT have AC field but should include AC in description
        second_call_doc = mock_wit_client.create_work_item.call_args_list[1][1]["document"]
        paths = {entry["path"]: entry["value"] for entry in second_call_doc}
        assert "/fields/Microsoft.VSTS.Common.AcceptanceCriteria" not in paths
        assert "Acceptance Criteria" in paths["/fields/System.Description"]
        assert "Given X, When Y, Then Z" in paths["/fields/System.Description"]

    @pytest.mark.asyncio
    async def test_create_with_acceptance_criteria_included_in_document(
        self, tool: AzureDevOpsTool
    ) -> None:
        """Acceptance criteria should appear as a separate field when AC field is available.

        Uses team_alias='platform' (default_type='User Story') so that AC is NOT stripped
        (AC is only stripped for 'Feature' type work items).
        """
        mock_wi = MagicMock()
        mock_wi.id = 55
        mock_wit_client = MagicMock()
        mock_wit_client.create_work_item.return_value = mock_wi

        with patch("core.tools.azure_devops.Connection") as mock_conn:
            mock_conn.return_value.clients.get_work_item_tracking_client.return_value = (
                mock_wit_client
            )
            await tool.run(
                action="create",
                title="Story",
                description="Desc",
                acceptance_criteria="AC text here",
                team_alias="platform",  # User Story type -> AC not pre-stripped
                confirm_write=True,
            )

        call_kwargs = mock_wit_client.create_work_item.call_args[1]
        document = call_kwargs["document"]
        paths = {entry["path"]: entry["value"] for entry in document}
        assert paths.get("/fields/Microsoft.VSTS.Common.AcceptanceCriteria") == "AC text here"


# ---------------------------------------------------------------------------
# children action
# ---------------------------------------------------------------------------


class TestChildrenAction:
    """Tests for the 'children' action of AzureDevOpsTool."""

    @pytest.mark.asyncio
    async def test_children_missing_work_item_id_returns_error(self, tool: AzureDevOpsTool) -> None:
        """Children without work_item_id returns an error message."""
        with patch("core.tools.azure_devops.Connection"):
            result = await tool.run(action="children")
        assert "Error" in result
        assert "work_item_id" in result.lower()

    @pytest.mark.asyncio
    async def test_children_no_relations_returns_message(self, tool: AzureDevOpsTool) -> None:
        """Work item with no children should return an appropriate message."""
        mock_parent = MagicMock()
        mock_parent.fields = {"System.Title": "Parent Item"}
        mock_parent.relations = None

        mock_wit_client = MagicMock()
        mock_wit_client.get_work_item.return_value = mock_parent

        with patch("core.tools.azure_devops.Connection") as mock_conn:
            mock_conn.return_value.clients.get_work_item_tracking_client.return_value = (
                mock_wit_client
            )
            result = await tool.run(action="children", work_item_id=100)

        assert "no child items" in result.lower() or "no children" in result.lower()
        assert "100" in result

    @pytest.mark.asyncio
    async def test_children_returns_items_with_state_counts(self, tool: AzureDevOpsTool) -> None:
        """Children action should list child items and show state counts."""
        # Parent with two child relations
        mock_parent = MagicMock()
        mock_parent.fields = {"System.Title": "Epic #1"}
        rel1 = MagicMock()
        rel1.rel = "System.LinkTypes.Hierarchy-Forward"
        rel1.url = "https://dev.azure.com/org/proj/_apis/wit/workItems/201"
        rel2 = MagicMock()
        rel2.rel = "System.LinkTypes.Hierarchy-Forward"
        rel2.url = "https://dev.azure.com/org/proj/_apis/wit/workItems/202"
        mock_parent.relations = [rel1, rel2]

        child1 = MagicMock()
        child1.id = 201
        child1.fields = {
            "System.Title": "Child Story A",
            "System.State": "Active",
            "System.WorkItemType": "User Story",
        }
        child2 = MagicMock()
        child2.id = 202
        child2.fields = {
            "System.Title": "Child Story B",
            "System.State": "New",
            "System.WorkItemType": "User Story",
        }

        mock_wit_client = MagicMock()
        mock_wit_client.get_work_item.return_value = mock_parent
        mock_wit_client.get_work_items.return_value = [child1, child2]

        with patch("core.tools.azure_devops.Connection") as mock_conn:
            mock_conn.return_value.clients.get_work_item_tracking_client.return_value = (
                mock_wit_client
            )
            result = await tool.run(action="children", work_item_id=200)

        assert "201" in result
        assert "202" in result
        assert "Child Story A" in result
        assert "Child Story B" in result
        # State counts should be present somewhere in the result
        assert "Active" in result
        assert "New" in result


# ---------------------------------------------------------------------------
# team_summary action
# ---------------------------------------------------------------------------


class TestTeamSummaryAction:
    """Tests for the 'team_summary' action of AzureDevOpsTool."""

    @pytest.mark.asyncio
    async def test_team_summary_missing_project_returns_error(self, tool: AzureDevOpsTool) -> None:
        """team_summary without project and no project in credentials returns error.

        When credentials do not supply a project and no explicit project is given,
        team_summary must return an error message.
        """
        # Override credentials so that cred_project is None
        tool._get_credentials_for_context = AsyncMock(  # type: ignore[method-assign]
            return_value=("fake_pat", "https://dev.azure.com/TestOrg", None)
        )
        with patch("core.tools.azure_devops.Connection"):
            result = await tool.run(action="team_summary")
        assert "Error" in result
        assert "project" in result.lower()

    @pytest.mark.asyncio
    async def test_team_summary_queries_all_states(self, tool: AzureDevOpsTool) -> None:
        """team_summary should issue WIQL queries for New, Active, and Closed states."""
        mock_result = MagicMock()
        mock_result.work_items = []
        mock_wit_client = MagicMock()
        mock_wit_client.query_by_wiql.return_value = mock_result

        with patch("core.tools.azure_devops.Connection") as mock_conn:
            mock_conn.return_value.clients.get_work_item_tracking_client.return_value = (
                mock_wit_client
            )
            await tool.run(action="team_summary", project="TestProject")

        # Should have queried Active, New, and Closed for each team that has an area_path
        all_wiql_queries = [
            str(c[0][0]["query"]) for c in mock_wit_client.query_by_wiql.call_args_list
        ]
        states_seen = set()
        for q in all_wiql_queries:
            if "'Active'" in q:
                states_seen.add("Active")
            if "'New'" in q:
                states_seen.add("New")
            if "'Closed'" in q:
                states_seen.add("Closed")
        assert states_seen == {"Active", "New", "Closed"}

    @pytest.mark.asyncio
    async def test_team_summary_returns_formatted_table(self, tool: AzureDevOpsTool) -> None:
        """team_summary result should include a markdown table header."""

        def make_result(count: int) -> MagicMock:
            r = MagicMock()
            r.work_items = [MagicMock() for _ in range(count)]
            return r

        # Return different counts per query to make them distinguishable
        mock_wit_client = MagicMock()
        mock_wit_client.query_by_wiql.side_effect = [
            make_result(3),  # platform Active
            make_result(1),  # platform New
            make_result(5),  # platform Closed
            make_result(0),  # security Active
            make_result(2),  # security New
            make_result(0),  # security Closed
            make_result(1),  # infra Active
            make_result(0),  # infra New
            make_result(4),  # infra Closed
        ]

        with patch("core.tools.azure_devops.Connection") as mock_conn:
            mock_conn.return_value.clients.get_work_item_tracking_client.return_value = (
                mock_wit_client
            )
            result = await tool.run(action="team_summary", project="TestProject")

        assert "| Team |" in result
        assert "| Active |" in result or "Active" in result
        assert "platform" in result


# ---------------------------------------------------------------------------
# Credential failure paths
# ---------------------------------------------------------------------------


class TestCredentialFailurePaths:
    """Tests for credential lookup failures."""

    @pytest.mark.asyncio
    async def test_no_context_id_returns_config_error(self) -> None:
        """When context_id is None, run() returns a credential error message."""
        t = AzureDevOpsTool()
        t._get_credentials_for_context = AsyncMock(return_value=None)  # type: ignore[method-assign]
        t._load_mappings_from_db = AsyncMock(return_value=MOCK_MAPPINGS)  # type: ignore[method-assign]
        result = await t.run(action="get", work_item_id=1, context_id=None)

        assert "Error" in result or "credential" in result.lower() or "not configured" in result

    @pytest.mark.asyncio
    async def test_credential_not_found_returns_helpful_message(self) -> None:
        """When credentials are not stored for a context, return a helpful message."""
        t = AzureDevOpsTool()
        t._get_credentials_for_context = AsyncMock(return_value=None)  # type: ignore[method-assign]
        t._load_mappings_from_db = AsyncMock(return_value=MOCK_MAPPINGS)  # type: ignore[method-assign]
        result = await t.run(
            action="list",
            context_id=FAKE_CONTEXT_ID,
            session=AsyncMock(),
        )

        # Should contain a message directing user to configure credentials
        assert "credential" in result.lower() or "not configured" in result.lower()
        assert "Error" in result or "Admin Portal" in result
