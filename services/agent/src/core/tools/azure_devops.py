import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from uuid import UUID

import yaml
from azure.devops.connection import Connection
from msrest.authentication import BasicAuthentication
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.credential_service import CredentialService
from core.core.config import get_settings
from core.tools.base import Tool

LOGGER = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load_ado_mappings() -> dict[str, Any]:
    """Load and cache ADO mappings from config file.

    Tries multiple locations for Docker and local development compatibility.

    Returns:
        Parsed YAML content as dict, empty dict if not found.
    """
    # Candidate paths in priority order
    candidates = [
        Path("/app/config/ado_mappings.yaml"),  # Docker mount
        Path(__file__).resolve().parent.parent.parent.parent / "config" / "ado_mappings.yaml",
    ]

    for config_path in candidates:
        try:
            if config_path.exists():
                LOGGER.debug("Loading ADO mappings from %s", config_path)
                with open(config_path, encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
        except Exception as e:
            LOGGER.warning("Failed to load ADO mappings from %s: %s", config_path, e)
            continue

    LOGGER.warning("ADO mappings not found in any of: %s", [str(p) for p in candidates])
    return {}


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


def _find_similar(target: str, candidates: list[str], max_suggestions: int = 3) -> list[str]:
    """Find similar strings using Levenshtein distance.

    Args:
        target: String to find matches for.
        candidates: List of strings to compare against.
        max_suggestions: Maximum number of suggestions to return.

    Returns:
        List of similar strings, sorted by similarity.
    """

    def levenshtein_distance(s1: str, s2: str) -> int:
        """Calculate Levenshtein distance between two strings."""
        if len(s1) < len(s2):
            return levenshtein_distance(s2, s1)
        if len(s2) == 0:
            return len(s1)

        previous_row = list(range(len(s2) + 1))
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row

        return previous_row[-1]

    # Calculate distances and sort
    distances = [
        (candidate, levenshtein_distance(target.lower(), candidate.lower()))
        for candidate in candidates
    ]
    distances.sort(key=lambda x: x[1])

    # Return top suggestions with distance <= 3
    return [candidate for candidate, dist in distances[:max_suggestions] if dist <= 3]


class AzureDevOpsTool(Tool):
    name = "azure_devops"
    description = (
        "Interacts with Azure DevOps to manage work items. "
        "Actions: create, get, list (query), search (WIQL), children (child items). "
        "For 'create', set confirm_write=True after user approval."
    )
    # Per-action confirmation: only 'create' requires confirmation (checked in run())
    activity_hint = {"action": "DevOps: {action}"}
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "create",
                    "get",
                    "list",
                    "search",
                    "children",
                    "get_teams",
                    "team_summary",
                ],
                "description": (
                    "Action: create (new item), get (by ID), list (query), "
                    "search (WIQL), children, get_teams, team_summary"
                ),
            },
            "title": {"type": "string", "description": "Title (required for create)"},
            "description": {
                "type": "string",
                "description": "Description (required for create)",
            },
            "work_item_id": {
                "type": "integer",
                "description": "Work item ID (required for get/children)",
            },
            "team_alias": {
                "type": "string",
                "description": "Team identifier (e.g., 'infra', 'platform'). "
                "Use get_teams to discover.",
            },
            "type": {
                "type": "string",
                "description": "Work Item Type: 'Feature', 'User Story', 'Bug'",
            },
            "state": {
                "type": "string",
                "enum": ["New", "Active", "Closed"],
                "description": "Filter by state",
            },
            "area_path": {"type": "string", "description": "Area Path filter"},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tags to add/filter",
            },
            "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
            "target_date": {"type": "string", "description": "Target date (YYYY-MM-DD)"},
            "query": {"type": "string", "description": "WIQL search text"},
            "top": {"type": "integer", "description": "Max results (default 20)"},
        },
        "required": ["action"],
    }

    def __init__(self) -> None:
        """Initialize Azure DevOps tool.

        Configuration is loaded per-user from credentials in platformadmin.
        No environment variable fallbacks - all config must be in credentials.
        """
        # Use cached mappings loaded at module level
        self.mappings = _load_ado_mappings()

        # Validate and warn
        warnings = self._validate_mappings()
        for warning in warnings:
            LOGGER.warning(f"ADO Mapping: {warning}")

    def _get_available_teams(self) -> list[str]:
        """Return list of configured team aliases."""
        return list(self.mappings.get("teams", {}).keys())

    def find_team_by_owner(self, owner_name: str) -> str | None:
        """Find team alias by owner name (case-insensitive partial match).

        Args:
            owner_name: Name or partial name of the team owner.

        Returns:
            Team alias if found, None otherwise.
        """
        owner_lower = owner_name.lower()
        for alias, config in self.mappings.get("teams", {}).items():
            owner = config.get("owner", "")
            if owner and owner_lower in owner.lower():
                return alias
        return None

    def _resolve_team_config(self, team_alias: str | None) -> dict[str, Any]:
        """Resolve team configuration with validation.

        Supports both team alias (e.g., 'infra') and owner name (e.g., 'Martin').

        Returns:
            dict with: area_path, default_type, default_tags, _resolved_team

        Raises:
            ValueError: If team_alias is invalid (with suggestions)
        """
        if not team_alias:
            return self.mappings.get("defaults", {})

        teams = self.mappings.get("teams", {})

        # Direct match by team alias
        if team_alias in teams:
            config = teams[team_alias].copy()
            config["_resolved_team"] = team_alias
            return config

        # Try to find by owner name
        resolved_alias = self.find_team_by_owner(team_alias)
        if resolved_alias:
            LOGGER.info(f"Resolved '{team_alias}' to team '{resolved_alias}' via owner lookup")
            config = teams[resolved_alias].copy()
            config["_resolved_team"] = resolved_alias
            return config

        # No match found - provide helpful error
        available = list(teams.keys())
        suggestions = _find_similar(team_alias, available)
        error_msg = f"Unknown team '{team_alias}'. Available teams: {', '.join(available)}."
        if suggestions:
            error_msg += f" Did you mean: {', '.join(suggestions)}?"
        raise ValueError(error_msg)

    def _validate_mappings(self) -> list[str]:
        """Validate mapping structure, return warnings."""
        warnings = []
        teams = self.mappings.get("teams", {})

        for team, config in teams.items():
            if not config.get("area_path"):
                warnings.append(f"Team '{team}' missing area_path")
            if not config.get("default_type"):
                warnings.append(f"Team '{team}' missing default_type")

        return warnings

    @staticmethod
    def _parse_org_url(url: str) -> tuple[str, str | None]:
        """Parse Azure DevOps URL to extract org URL and project.

        Handles URLs like:
        - https://dev.azure.com/Coromant/Web%20Teams/
        - https://dev.azure.com/MyOrg/MyProject
        - https://dev.azure.com/MyOrg

        Returns:
            Tuple of (org_url, project) where project may be None
        """
        # URL-decode first (handles %20 etc.)
        url = unquote(url.strip().rstrip("/"))

        # Match Azure DevOps URL pattern
        match = re.match(r"^(https://dev\.azure\.com/[^/]+)(?:/(.+))?$", url)
        if match:
            org_url = match.group(1)
            project = match.group(2) if match.group(2) else None
            return org_url, project

        # If no match, assume it's already just the org URL
        return url, None

    async def _get_credentials_for_user(
        self,
        user_id: UUID | None,
        session: AsyncSession | None,
    ) -> tuple[str, str, str | None] | None:
        """Get Azure DevOps credentials for user from database.

        Args:
            user_id: User ID to get credential for.
            session: Database session for credential lookup.

        Returns:
            Tuple of (pat, org_url, project) if found, None otherwise.
            No environment variable fallback - credentials must be configured
            via platformadmin.
        """
        if not user_id or not session:
            LOGGER.warning("Azure DevOps: No user_id or session provided")
            return None

        settings = get_settings()
        if not settings.credential_encryption_key:
            LOGGER.error("Azure DevOps: Credential encryption key not configured")
            return None

        cred_service = CredentialService(settings.credential_encryption_key)
        try:
            result = await cred_service.get_credential_with_metadata(
                user_id=user_id,
                credential_type="azure_devops_pat",
                session=session,
            )
            if not result:
                LOGGER.debug(f"No Azure DevOps credentials found for user {user_id}")
                return None

            pat, metadata = result
            org_url_raw = metadata.get("organization_url", "")

            if not org_url_raw:
                LOGGER.warning(f"Azure DevOps credential missing organization_url: {user_id}")
                return None

            org_url, project = self._parse_org_url(org_url_raw)
            LOGGER.debug(f"Using Azure DevOps creds for {user_id}: {org_url}")
            return pat, org_url, project

        except Exception as e:
            LOGGER.warning(f"Failed to get Azure DevOps credentials for {user_id}: {e}")
            return None

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
        user_id: UUID | None = None,
        session: AsyncSession | None = None,
        **kwargs: Any,
    ) -> str:
        """
        Manage Azure DevOps Work Items.

        Args:
            action: 'create', 'get', 'list', 'search', 'children', 'get_teams', or
                'team_summary'.
            title: Title (required for create).
            description: Description (required for create).
            acceptance_criteria: Acceptance Criteria (optional).
            work_item_id: ID (required for get/children).
            team_alias: Team identifier (use get_teams to discover). Examples: 'infra',
                'platform', 'security'. Used for create, list, and search actions.
            area_path: Filter by Area Path (for list).
            type: Work Item Type filter (for list) or type to create.
            project: Project Name (required for team_summary).
            tags: List of tags to add (create) or filter by (list).
            start_date: 'YYYY-MM-DD' (create).
            target_date: 'YYYY-MM-DD' (create).
            state: Filter by state: 'New', 'Active', 'Closed' (for list).
            query: Search text for WIQL query (for search action).
            top: Max results to return (default 20, for list/search).
            user_id: Optional user ID for per-user PAT lookup.
            session: Optional database session for credential lookup.
        """
        # Get credentials from user's stored credentials (no .env fallback)
        creds = await self._get_credentials_for_user(user_id, session)
        if not creds:
            return (
                "❌ Error: Azure DevOps credentials not configured.\n\n"
                "Please configure your Azure DevOps credentials in the Admin Portal:\n"
                "1. Go to Admin Portal → Credentials\n"
                "2. Add 'Azure DevOps PAT' credential\n"
                "3. Enter your PAT and DevOps URL (e.g., https://dev.azure.com/YourOrg/YourProject)"
            )

        pat, org_url, cred_project = creds

        try:
            credentials = BasicAuthentication("", pat)
            connection = Connection(base_url=org_url, creds=credentials)
            wit_client = connection.clients.get_work_item_tracking_client()

            # Project priority: explicit param > credential metadata
            target_project = project or kwargs.get("project") or cred_project

            if action == "create":
                # Per-action confirmation: require explicit confirm_write=True (boolean)
                # String "True" or other truthy values must NOT bypass confirmation
                if kwargs.get("confirm_write") is not True:
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
                try:
                    team_config = self._resolve_team_config(team_alias)
                except ValueError as e:
                    return f"❌ Error: {str(e)}"

                default_area = self.mappings.get("defaults", {}).get("area_path")

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

                web_url = f"{org_url}/{target_project}/_workitems/edit/{wi.id}"
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

                # Resolve team_alias to area_path
                if team_alias:
                    try:
                        team_config = self._resolve_team_config(team_alias)
                        # Override area_path if team provided
                        if not area_path:
                            area_path = team_config.get("area_path")
                        # Auto-add team default tags to filter if not specified
                        if not tags and team_config.get("default_tags"):
                            tags = team_config["default_tags"]
                    except ValueError as e:
                        return f"❌ Error: {str(e)}"

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
                    assigned = f.get("System.AssignedTo", {})
                    assigned_name = (
                        assigned.get("displayName", "Unassigned")
                        if isinstance(assigned, dict)
                        else "Unassigned"
                    )
                    results.append(
                        f"- **#{wi.id}** [{f.get('System.WorkItemType')}] "
                        f"{f.get('System.Title')} ({f.get('System.State')}) "
                        f"- {assigned_name}"
                    )

                return "\n".join(results)

            elif action == "search":
                # Search by text query
                if not query:
                    return "❌ Error: 'query' is required for action='search'."
                if not target_project:
                    return "❌ Error: Project not specified for search action."

                # Team-aware search
                team_area_clause = ""
                if team_alias:
                    try:
                        team_config = self._resolve_team_config(team_alias)
                        if team_config.get("area_path"):
                            safe_area = _sanitize_wiql_value(team_config["area_path"])
                            team_area_clause = f" AND [System.AreaPath] UNDER '{safe_area}'"
                    except ValueError as e:
                        return f"❌ Error: {str(e)}"

                # WIQL text search with sanitized inputs
                safe_project = _sanitize_wiql_value(target_project)
                safe_query = _sanitize_wiql_value(query)
                wiql = f"""
                SELECT [System.Id], [System.Title], [System.State], [System.WorkItemType]
                FROM WorkItems
                WHERE [System.TeamProject] = '{safe_project}'
                  {team_area_clause}
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

            elif action == "get_teams":
                """List configured teams with their settings."""
                teams = self.mappings.get("teams", {})

                if not teams:
                    return "⚠️ No teams configured in ado_mappings.yaml"

                results = ["### Configured Teams\n"]
                for team_alias, config in teams.items():
                    display_name = config.get("display_name", team_alias)
                    owner = config.get("owner", "")
                    area = config.get("area_path", "Not set")
                    type_ = config.get("default_type", "Not set")
                    tags = config.get("default_tags", [])
                    tags_str = ", ".join(tags) if tags else "None"

                    results.append(f"**{team_alias}** ({display_name})")
                    if owner:
                        results.append(f"  - Owner: {owner}")
                    results.append(f"  - Area Path: {area}")
                    results.append(f"  - Default Type: {type_}")
                    results.append(f"  - Default Tags: {tags_str}")
                    results.append("")

                return "\n".join(results)

            elif action == "team_summary":
                """Show workload distribution across all teams."""
                if not target_project:
                    return "❌ Error: Project not specified for team_summary action."

                teams = self.mappings.get("teams", {})
                if not teams:
                    return "⚠️ No teams configured in ado_mappings.yaml"

                results = ["### Team Workload Summary\n"]
                results.append("| Team | Active | New | Closed |")
                results.append("|------|--------|-----|--------|")

                for team_alias, config in teams.items():
                    area_path_value = config.get("area_path")
                    if not area_path_value:
                        continue

                    # Query active count
                    safe_project = _sanitize_wiql_value(target_project)
                    safe_area = _sanitize_wiql_value(area_path_value)

                    active_query = f"""
                    SELECT [System.Id] FROM WorkItems
                    WHERE [System.TeamProject] = '{safe_project}'
                      AND [System.AreaPath] UNDER '{safe_area}'
                      AND [System.State] = 'Active'
                    """  # noqa: S608
                    active_result = wit_client.query_by_wiql({"query": active_query})
                    active_count = len(active_result.work_items)

                    # Query new count
                    new_query = f"""
                    SELECT [System.Id] FROM WorkItems
                    WHERE [System.TeamProject] = '{safe_project}'
                      AND [System.AreaPath] UNDER '{safe_area}'
                      AND [System.State] = 'New'
                    """  # noqa: S608
                    new_result = wit_client.query_by_wiql({"query": new_query})
                    new_count = len(new_result.work_items)

                    # Query closed count
                    closed_query = f"""
                    SELECT [System.Id] FROM WorkItems
                    WHERE [System.TeamProject] = '{safe_project}'
                      AND [System.AreaPath] UNDER '{safe_area}'
                      AND [System.State] = 'Closed'
                    """  # noqa: S608
                    closed_result = wit_client.query_by_wiql({"query": closed_query})
                    closed_count = len(closed_result.work_items)

                    results.append(
                        f"| {team_alias} | {active_count} | {new_count} | {closed_count} |"
                    )

                return "\n".join(results)

            else:
                return (
                    f"❌ Error: Unknown action '{action}'. "
                    "Use 'create', 'get', 'list', 'search', 'children', 'get_teams', "
                    "or 'team_summary'."
                )

        except Exception as e:
            LOGGER.exception("Azure DevOps Tool Failed")
            return f"❌ Error: {str(e)}"
