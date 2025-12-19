from pathlib import Path

from core.tools.loader import load_tool_registry


def main() -> None:
    config_path = Path("config/tools.yaml")
    print(f"Loading tools from {config_path.absolute()}...")

    registry = load_tool_registry(config_path)
    tools = registry.tools()

    print(f"Found {len(tools)} tools:")
    for tool in tools:
        print(f"- Name: {tool.name}")
        print(f"  Type: {type(tool)}")
        print(f"  Description: {getattr(tool, 'description', 'N/A')}")


if __name__ == "__main__":
    main()
