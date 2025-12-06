import logging
import os
from typing import Any

from azure.devops.connection import Connection
from msrest.authentication import BasicAuthentication

from .base import Tool

LOGGER = logging.getLogger(__name__)


class AzureDevOpsTool(Tool):
    name = "azure_devops"
    description = "Interacts with Azure DevOps to manage work items."

    def __init__(self, org_url: str | None = None, pat: str | None = None) -> None:
        self.org_url = org_url or os.environ.get("AZURE_DEVOPS_ORG_URL")
        # Fallback to constructing from ORG name if URL not full
        if not self.org_url:
            org = os.environ.get("AZURE_DEVOPS_ORG")
            if org:
                self.org_url = f"https://dev.azure.com/{org}"

        self.pat = pat or os.environ.get("AZURE_DEVOPS_PAT")

    async def run(
        self,
        title: str,
        description: str,
        area_path: str | None = None,
        type: str = "Bug",
        **kwargs: Any,
    ) -> str:
        """
        Create a work item in Azure DevOps.

        Args:
            title: Title of the work item.
            description: Description/Body of the work item.
            area_path: Optional Area Path.
            type: Work Item Type (default: Bug).
        """
        if not self.org_url or not self.pat:
            return "❌ Error: Azure DevOps configuration (ORG_URL or PAT) is missing."

        try:
            # Connect to Azure DevOps
            credentials = BasicAuthentication("", self.pat)
            connection = Connection(base_url=self.org_url, creds=credentials)
            wit_client = connection.clients.get_work_item_tracking_client()

            document = [
                {"op": "add", "path": "/fields/System.Title", "value": title},
                {
                    "op": "add",
                    "path": "/fields/System.Description",
                    "value": description,
                },
            ]

            if area_path:
                document.append(
                    {"op": "add", "path": "/fields/System.AreaPath", "value": area_path}
                )

            # We need a Project name. Usually passed or env.
            # For now, let's assume a default project or try to infer.
            # Since the prompt didn't specify project handling, I'll check env or fail.
            project = os.environ.get("AZURE_DEVOPS_PROJECT", "Agile")
            # 'Agile' is often just a process template, not project name.
            # But we need a project to create a work item usually, OR we can pass it.
            # `create_work_item` requires `project` argument.

            # I will check if 'project' is in kwargs, else env.
            project = kwargs.get("project") or os.environ.get("AZURE_DEVOPS_PROJECT")
            if not project:
                return "❌ Error: Azure DevOps Project not specified in environment or arguments."

            work_item = wit_client.create_work_item(
                document=document, project=project, type=type
            )

            # The URL returned by API is API URL. UI URL is different.
            # Example: https://dev.azure.com/{org}/{project}/_workitems/edit/{id}
            # But work_item.url is .../_apis/wit/workItems/{id}
            # Let's try to construct a user-friendly link if possible, or just use ID.

            # Constructing web URL:
            web_url = f"{self.org_url}/{project}/_workitems/edit/{work_item.id}"

            return f"✅ Created {type} #{work_item.id}: [{title}]({web_url})"

        except Exception as e:
            LOGGER.exception("Failed to create work item")
            return f"❌ Error creating work item: {str(e)}"
