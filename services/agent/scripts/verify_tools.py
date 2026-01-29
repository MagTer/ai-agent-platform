import sys
from pathlib import Path

# Add src to path
sys.path.append("/app/src")

from core.tools import load_tool_registry


def verify_tools():
    try:
        config_path = Path("/app/config/tools.yaml")
        if not config_path.exists():
            print(f"ERROR: {config_path} does not exist. Trying relative path.")
            config_path = Path("config/tools.yaml")

        if not config_path.exists():
            print(f"ERROR: Cannot find tools.yaml at {config_path}")
            return

        print(f"Loading tools from {config_path}...")

        registry = load_tool_registry(config_path)

        print(f"Loaded {len(registry.tools())} tools:")
        for tool in registry.tools():
            print(f" - Name: {tool.name}")
            print(f"   Description: {tool.description[:60]}...")
            if hasattr(tool, "parameters"):
                print(f"   Parameters: {list(tool.parameters.get('properties', {}).keys())}")

    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    verify_tools()
