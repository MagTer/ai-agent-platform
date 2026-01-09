import logging
import os
from pathlib import Path
from typing import Any

import yaml
from azure.devops.connection import Connection
from msrest.authentication import BasicAuthentication

from .base import Tool

LOGGER = logging.getLogger(__name__)


def _sanitize_wiql_value(value: str) -> str:
    """Escape single quotes in WIQL string values.

    WIQL doesn't support parameterized queries, so we escape single quotes
    by doubling them (standard SQL escaping convention).

    Args:
        value: Raw string value to be used in WIQL query.

    Returns:
        Escaped string safe for WIQL interpolation.
    """
    if not value:
        return value
    # Escape single quotes by doubling them
    return value.replace("'", "''")


class AzureDevOpsTool(Tool):
    name = "azure_devops"
    description = (
        "Interacts with Azure DevOps to manage work items. "
        "Actions: create, get, list (query), search (WIQL), children (child items). "
        "For 'create', set confirm_write=True after user approval."
    )
    # Per-action confirmation: only 'create' requires confirmation (checked in run())
    activity_hint = {"action": "DevOps: {action}"}

    def __init__(self, org_url: str | None = None, pat: str | None = None) -> None:
        self.org_url = org_url or os.environ.get("AZURE_DEVOPS_ORG_URL")
        if not self.org_url:
            org = os.environ.get("AZURE_DEVOPS_ORG")
            if org:
                self.org_url = f"https://dev.azure.com/{org}"

        self.pat = pat or os.environ.get("AZURE_DEVOPS_PAT")
        self.mappings = self._load_mappings()

    def _load_mappings(self) -> dict[str, Any]:
        """Load ADO mappings from default config path."""
        try:
            base_path = Path(__file__).resolve().parent.parent.parent.parent
            config_path = base_path / "config" / "ado_mappings.yaml"

            if config_path.exists():
                with open(config_path, encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            else:
                LOGGER.warning(f"ADO mappings not found at {config_path}")
                return {}
        except Exception as e:
            LOGGER.error(f"Failed to load ADO mappings: {e}")
            return {}

    async def run(
        self,
        action: str = "create",
        title: str | None = None,
        description: str | None = None,
        acceptance_criteria: str | None = None,
        work_item_id: int | None = None,
        team_alias: str | None = None,
        area_path: str | None = None,
        type: str | None = None,
        project: str | None = None,
        tags: list[str] | None = None,
        start_date: str | None = None,
        target_date: str | None = None,
        state: str | None = None,
        query: str | None = None,
        top: int = 20,
        **kwargs: Any,
    ) -> str:
        """
        Manage Azure DevOps Work Items.

        Args:
            action: 'create', 'get', 'list', 'search', or 'children'.
            title: Title (required for create).
            description: Description (required for create).
            acceptance_criteria: Acceptance Criteria (optional).
            work_item_id: ID (required for get/children).
            team_alias: 'backend', 'frontend', 'security' etc.
            area_path: Filter by Area Path (for list).
            type: Work Item Type filter (for list) or type to create.
            project: Project Name.
            tags: List of tags to add (create) or filter by (list).
            start_date: 'YYYY-MM-DD' (create).
            target_date: 'YYYY-MM-DD' (create).
            state: Filter by state: 'New', 'Active', 'Closed' (for list).
            query: Search text for WIQL query (for search action).
            top: Max results to return (default 20, for list/search).
        """
        if not self.org_url or not self.pat:
            return "❌ Error: Azure DevOps configuration (ORG_URL or PAT) is missing."

        try:
            credentials = BasicAuthentication("", self.pat)
            connection = Connection(base_url=self.org_url, creds=credentials)
            wit_client = connection.clients.get_work_item_tracking_client()

            target_project = (
                project or kwargs.get("project") or os.environ.get("AZURE_DEVOPS_PROJECT")
            )

            if action == "create":
                # Per-action confirmation: require explicit confirm_write=True
                if not kwargs.get("confirm_write"):
                    from core.tools.base import ToolConfirmationError

                    raise ToolConfirmationError(
                        tool_name=self.name,
                        tool_args={
                            "action": action,
                            "title": title,
                            "type": type,
                            "team_alias": team_alias,
                        },
                    )

                if not title or not description:
                    return "❌ Error: 'title' and 'description' are required for action='create'."
                if not target_project:
                    return "❌ Error: Azure DevOps Project not specified."

                # 1. Resolve Configuration based on Team Alias
                default_area = self.mappings.get("defaults", {}).get("area_path")
                team_config = (
                    self.mappings.get("teams", {}).get(team_alias, {}) if team_alias else {}
                )

                # 2. Determine Final Values (Arg > Team Config > Default)
                final_area_path = area_path or team_config.get("area_path") or default_area
                final_type = type or team_config.get("default_type") or "Bug"

                # Tags: Merge defaults with explicit
                final_tags = []
                if team_config.get("default_tags"):
                    final_tags.extend(team_config["default_tags"])
                if tags:
                    final_tags.extend(tags)
                # Deduplicate
                final_tags = list(set(final_tags))

                # Feature Constraint: Features do NOT support Acceptance Criteria
                if final_type == "Feature" and acceptance_criteria:
                    LOGGER.warning("Acceptance Criteria ignored for Feature work item.")
                    acceptance_criteria = None

                # 3. Construct Document
                full_description = description

                document = [
                    {"op": "add", "path": "/fields/System.Title", "value": title},
                ]

                if final_area_path:
                    document.append(
                        {
                            "op": "add",
                            "path": "/fields/System.AreaPath",
                            "value": final_area_path,
                        }
                    )

                if final_tags:
                    # Tags are semicolon separated string
                    tags_str = "; ".join(final_tags)
                    document.append({"op": "add", "path": "/fields/System.Tags", "value": tags_str})

                if start_date:
                    document.append(
                        {
                            "op": "add",
                            "path": "/fields/Microsoft.VSTS.Scheduling.StartDate",
                            "value": start_date,
                        }
                    )
                if target_date:
                    document.append(
                        {
                            "op": "add",
                            "path": "/fields/Microsoft.VSTS.Scheduling.TargetDate",
                            "value": target_date,
                        }
                    )

                # Handle Acceptance Criteria
                if acceptance_criteria:
                    document.append(
                        {
                            "op": "add",
                            "path": "/fields/Microsoft.VSTS.Common.AcceptanceCriteria",
                            "value": acceptance_criteria,
                        }
                    )

                document.append(
                    {
                        "op": "add",
                        "path": "/fields/System.Description",
                        "value": full_description,
                    }
                )

                try:
                    wi = wit_client.create_work_item(
                        document=document, project=target_project, type=final_type
                    )
                except Exception as create_err:
                    # Fallback for AC field failure
                    if acceptance_criteria and "Microsoft.VSTS.Common.AcceptanceCriteria" in str(
                        create_err
                    ):
                        LOGGER.warning("AC field failed, appending to description.")
                        full_description += f"\n<h3>Acceptance Criteria</h3>\n{acceptance_criteria}"

                        # Rebuild doc without AC field - re-apply ALL fields
                        document = [
                            {
                                "op": "add",
                                "path": "/fields/System.Title",
                                "value": title,
                            },
                            {
                                "op": "add",
                                "path": "/fields/System.Description",
                                "value": full_description,
                            },
                        ]
                        if final_area_path:
                            document.append(
                                {
                                    "op": "add",
                                    "path": "/fields/System.AreaPath",
                                    "value": final_area_path,
                                }
                            )
                        if final_tags:
                            document.append(
                                {
                                    "op": "add",
                                    "path": "/fields/System.Tags",
                                    "value": "; ".join(final_tags),
                                }
                            )
                        if start_date:
                            document.append(
                                {
                                    "op": "add",
                                    "path": "/fields/Microsoft.VSTS.Scheduling.StartDate",
                                    "value": start_date,
                                }
                            )
                        if target_date:
                            document.append(
                                {
                                    "op": "add",
                                    "path": "/fields/Microsoft.VSTS.Scheduling.TargetDate",
                                    "value": target_date,
                                }
                            )

                        wi = wit_client.create_work_item(
                            document=document, project=target_project, type=final_type
                        )
                    else:
                        raise create_err

                web_url = f"{self.org_url}/{target_project}/_workitems/edit/{wi.id}"
                return f"✅ Created {final_type} #{wi.id}: [{title}]({web_url})"

            elif action == "get":
                if not work_item_id:
                    return "❌ Error: 'work_item_id' is required for action='get'."

                wi = wit_client.get_work_item(work_item_id, expand="All")
                fields = wi.fields

                out_title = fields.get("System.Title", "No Title")
                out_desc = fields.get("System.Description", "")
                out_ac = fields.get("Microsoft.VSTS.Common.AcceptanceCriteria", "")

                res = f"### Work Item #{wi.id}: {out_title}\n"
                res += f"**Type**: {fields.get('System.WorkItemType')}\n"
                res += f"**State**: {fields.get('System.State')}\n"
                res += f"**Area**: {fields.get('System.AreaPath')}\n"
                res += f"**Tags**: {fields.get('System.Tags')}\n\n"

                if out_desc:
                    res += f"**Description**:\n{out_desc}\n\n"
                if out_ac:
                    res += f"**Acceptance Criteria**:\n{out_ac}\n"

                return res

            elif action == "list":
                # Query work items by filters
                if not target_project:
                    return "❌ Error: Project not specified for list action."

                # Build WIQL query with sanitized values
                safe_project = _sanitize_wiql_value(target_project)
                conditions = [f"[System.TeamProject] = '{safe_project}'"]
                if area_path:
                    safe_area = _sanitize_wiql_value(area_path)
                    conditions.append(f"[System.AreaPath] UNDER '{safe_area}'")
                if type:
                    safe_type = _sanitize_wiql_value(type)
                    conditions.append(f"[System.WorkItemType] = '{safe_type}'")
                if state:
                    safe_state = _sanitize_wiql_value(state)
                    conditions.append(f"[System.State] = '{safe_state}'")
                if tags:
                    for tag in tags:
                        safe_tag = _sanitize_wiql_value(tag)
                        conditions.append(f"[System.Tags] CONTAINS '{safe_tag}'")

                where_clause = " AND ".join(conditions)
                # WIQL doesn't support parameterized queries
                wiql = f"""  
                SELECT [System.Id], [System.Title], [System.State], [System.WorkItemType]
                FROM WorkItems
                WHERE {where_clause}
                ORDER BY [System.ChangedDate] DESC
                """  # noqa: S608

                wiql_result = wit_client.query_by_wiql({"query": wiql}, top=top)

                if not wiql_result.work_items:
                    return "No work items found matching the criteria."

                # Fetch details
                ids = [wi.id for wi in wiql_result.work_items[:top]]
                work_items = wit_client.get_work_items(ids=ids)

                results = [f"### Found {len(work_items)} Work Items\n"]
                for wi in work_items:
                    f = wi.fields
                    results.append(
                        f"- **#{wi.id}** [{f.get('System.WorkItemType')}] "
                        f"{f.get('System.Title')} ({f.get('System.State')})"
                    )

                return "\n".join(results)

            elif action == "search":
                # Search by text query
                if not query:
                    return "❌ Error: 'query' is required for action='search'."
                if not target_project:
                    return "❌ Error: Project not specified for search action."

                # WIQL text search with sanitized inputs
                safe_project = _sanitize_wiql_value(target_project)
                safe_query = _sanitize_wiql_value(query)
                wiql = f"""
                SELECT [System.Id], [System.Title], [System.State], [System.WorkItemType]
                FROM WorkItems
                WHERE [System.TeamProject] = '{safe_project}'
                  AND ([System.Title] CONTAINS '{safe_query}'
                       OR [System.Description] CONTAINS '{safe_query}')
                ORDER BY [System.ChangedDate] DESC
                """  # noqa: S608

                wiql_result = wit_client.query_by_wiql({"query": wiql}, top=top)

                if not wiql_result.work_items:
                    return f"No work items found matching '{query}'."

                ids = [wi.id for wi in wiql_result.work_items[:top]]
                work_items = wit_client.get_work_items(ids=ids)

                results = [f"### Search Results for '{query}' ({len(work_items)} items)\n"]
                for wi in work_items:
                    f = wi.fields
                    results.append(
                        f"- **#{wi.id}** [{f.get('System.WorkItemType')}] "
                        f"{f.get('System.Title')} ({f.get('System.State')})"
                    )

                return "\n".join(results)

            elif action == "children":
                # Get child work items
                if not work_item_id:
                    return "❌ Error: 'work_item_id' is required for action='children'."

                # Get parent item first
                parent = wit_client.get_work_item(work_item_id, expand="Relations")
                fields = parent.fields
                parent_title = fields.get("System.Title", "Unknown")

                child_ids = []
                if parent.relations:
                    for rel in parent.relations:
                        if rel.rel == "System.LinkTypes.Hierarchy-Forward":
                            # Extract ID from URL
                            url = rel.url
                            child_id = int(url.split("/")[-1])
                            child_ids.append(child_id)

                if not child_ids:
                    return f"Work item #{work_item_id} ({parent_title}) has no child items."

                children = wit_client.get_work_items(ids=child_ids)

                results = [f"### Children of #{work_item_id}: {parent_title}\n"]
                state_counts: dict[str, int] = {}
                for wi in children:
                    f = wi.fields
                    state = f.get("System.State", "Unknown")
                    state_counts[state] = state_counts.get(state, 0) + 1
                    results.append(
                        f"- **#{wi.id}** [{f.get('System.WorkItemType')}] "
                        f"{f.get('System.Title')} ({state})"
                    )

                # Add summary
                summary = ", ".join([f"{k}: {v}" for k, v in state_counts.items()])
                results.insert(1, f"**Progress**: {summary}\n")

                return "\n".join(results)

            else:
                return (
                    f"❌ Error: Unknown action '{action}'. "
                    "Use 'create', 'get', 'list', 'search', or 'children'."
                )

        except Exception as e:
            LOGGER.exception("Azure DevOps Tool Failed")
            return f"❌ Error: {str(e)}"
