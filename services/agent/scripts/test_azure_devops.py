#!/usr/bin/env python3
# ruff: noqa: S608
"""Quick test script for Azure DevOps connection."""
import asyncio
import os
import sys


def test_team_operations() -> int:
    """Test team discovery and validation operations."""
    print("\n=== Testing Team Operations ===")

    try:
        # Import after adding to path
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
        from core.tools.azure_devops import AzureDevOpsTool

        tool = AzureDevOpsTool()

        # Test 1: Get teams
        print("\n1. Testing get_teams action...")
        result = asyncio.run(tool.run(action="get_teams"))
        print(result)
        assert "platform" in result.lower() or "Configured Teams" in result
        print("✅ get_teams works")

        # Test 2: List by team (if project configured)
        project = os.environ.get("AZURE_DEVOPS_PROJECT")
        if project:
            print("\n2. Testing list with team_alias...")
            result = asyncio.run(
                tool.run(action="list", team_alias="platform", state="Active", top=5)
            )
            print(result[:200] + "..." if len(result) > 200 else result)
            assert "Error" not in result or "Project not specified" in result
            print("✅ list with team_alias works")

            # Test 3: Invalid team
            print("\n3. Testing invalid team validation...")
            result = asyncio.run(tool.run(action="list", team_alias="invalid_team_name"))
            print(result)
            assert "Unknown team" in result
            assert "Available teams:" in result
            print("✅ Invalid team shows helpful error")

            # Test 4: Team-aware search
            print("\n4. Testing search with team_alias...")
            result = asyncio.run(
                tool.run(action="search", query="test", team_alias="platform", top=5)
            )
            print(result[:200] + "..." if len(result) > 200 else result)
            assert "Error" not in result or "Project not specified" in result
            print("✅ search with team_alias works")
        else:
            print("\n⚠️  Skipping team queries (AZURE_DEVOPS_PROJECT not set)")

        print("\n✅ All team operations passed")
        return 0

    except Exception as e:
        print(f"\n❌ ERROR in team operations: {e}")
        import traceback

        traceback.print_exc()
        return 1


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

        # Run team operations tests
        team_result = test_team_operations()
        if team_result != 0:
            return team_result

        return 0

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
