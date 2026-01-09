#!/usr/bin/env python3
# ruff: noqa: S608
"""Quick test script for Azure DevOps connection."""
import os
import sys


def main():
    org = os.environ.get("AZURE_DEVOPS_ORG")
    org_url = os.environ.get("AZURE_DEVOPS_ORG_URL")
    project = os.environ.get("AZURE_DEVOPS_PROJECT")
    pat = os.environ.get("AZURE_DEVOPS_PAT")

    print("=== Azure DevOps Environment Variables ===")
    print(f"AZURE_DEVOPS_ORG: {org or '(not set)'}")
    print(f"AZURE_DEVOPS_ORG_URL: {org_url or '(not set)'}")
    print(f"AZURE_DEVOPS_PROJECT: {project or '(not set)'}")
    print(f"AZURE_DEVOPS_PAT: {pat[:15] + '...' if pat else '(not set)'}")

    if not pat:
        print("\n❌ ERROR: PAT not set!")
        return 1

    if not org and not org_url:
        print("\n❌ ERROR: Neither ORG nor ORG_URL set!")
        return 1

    # Build URL
    if org_url:
        url = org_url
    else:
        url = f"https://dev.azure.com/{org}"

    print(f"\nConnecting to: {url}")
    print(f"Project: {project}")

    try:
        from azure.devops.connection import Connection
        from msrest.authentication import BasicAuthentication

        credentials = BasicAuthentication("", pat)
        connection = Connection(base_url=url, creds=credentials)
        wit_client = connection.clients.get_work_item_tracking_client()

        # Try a simple query (project comes from trusted env var, not user input)
        safe_project = project.replace("'", "''")  # Basic escaping
        query = (
            f"SELECT [System.Id] FROM WorkItems "
            f"WHERE [System.TeamProject] = '{safe_project}' "
            f"ORDER BY [System.ChangedDate] DESC"
        )
        wiql = {"query": query}
        result = wit_client.query_by_wiql(wiql, top=5)

        print(f"\n✅ SUCCESS! Found {len(result.work_items)} work items")
        return 0

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
