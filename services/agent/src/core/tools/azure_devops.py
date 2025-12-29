import logging
import os
from pathlib import Path
from typing import Any

import yaml
from azure.devops.connection import Connection
from msrest.authentication import BasicAuthentication

from .base import Tool

LOGGER = logging.getLogger(__name__)


class AzureDevOpsTool(Tool):
    name = "azure_devops"
    description = (
        "Interacts with Azure DevOps to manage work items (create, get). Supports team aliases."
    )

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
            # Assuming standard path structure
            # src/core/tools/azure_devops.py -> ... -> services/agent
            # Config at services/agent/config/ado_mappings.yaml
            # current file is services/agent/src/core/tools/azure_devops.py
            # parent: tools
            # parent: core
            # parent: src
            # parent: agent
            # target: agent/config/ado_mappings.yaml

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
        type: str | None = None,  # Make optional to allow default override
        project: str | None = None,
        tags: list[str] | None = None,
        start_date: str | None = None,
        target_date: str | None = None,
        **kwargs: Any,
    ) -> str:
        """
        Manage Azure DevOps Work Items.

        Args:
            action: 'create' or 'get'.
            title: Title (required for create).
            description: Description (required for create).
            acceptance_criteria: Acceptance Criteria (optional).
            work_item_id: ID (required for get).
            team_alias: 'backend', 'frontend', 'security' etc. (Applies configured defaults).
            area_path: Override Area Path.
            type: Work Item Type (default: Bug or Team Default).
            project: Project Name (overrides env).
            tags: List of tags to add.
            start_date: 'YYYY-MM-DD'.
            target_date: 'YYYY-MM-DD'.
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
                        {"op": "add", "path": "/fields/System.AreaPath", "value": final_area_path}
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
                    {"op": "add", "path": "/fields/System.Description", "value": full_description}
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
                        # Rebuild doc without AC field logic
                        # (simplified for brevity, should re-apply Tags/Area)

                        # Re-construct base doc
                        document = [
                            {"op": "add", "path": "/fields/System.Title", "value": title},
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

            else:
                return f"❌ Error: Unknown action '{action}'. Use 'create' or 'get'."

        except Exception as e:
            LOGGER.exception("Azure DevOps Tool Failed")
            return f"❌ Error: {str(e)}"
