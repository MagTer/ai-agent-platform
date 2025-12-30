import sys
import traceback
from pathlib import Path

# Add src to path
# Assuming script is in scripts/
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root / "services" / "agent" / "src"))

from core.tools.loader import load_tool_registry  # noqa: E402


def verify_tools():
    config_path = project_root / "services/agent/config/tools.yaml"
    if not config_path.exists():
        print(f"❌ ERROR: {config_path} does not exist.")
        return

    print(f"🔍 Verifying tools from {config_path}...")

    try:
        # load_tool_registry attempts to instantiate all tools
        # We want to capture which ones succeeded and which failed.
        # But load_tool_registry currently catches exceptions and logs them,
        # it doesn't return failures explicitly.
        # However, we can use the ToolRegistry to see what was loaded.

        # But to really "verify", we might want to fail the script if tools are missing.
        # Let's see if we can iterate the YAML ourselves and try strict loading
        # OR we just rely on load_tool_registry and check the output registry size vs yaml entries.

        registry = load_tool_registry(config_path)

        import yaml

        with open(config_path) as f:
            raw_tools = yaml.safe_load(f) or []

        defined_count = len(raw_tools)
        loaded_count = len(registry._tools)

        print(f"\n📊 Summary: {loaded_count}/{defined_count} tools loaded successfully.")

        failed = False
        loaded_names = registry._tools.keys()

        for tool_def in raw_tools:
            name = tool_def.get("name")
            if name in loaded_names:
                print(f"✅ {name}: Loaded")
            else:
                print(f"❌ {name}: Failed to load")
                failed = True

        if failed:
            print("\n⚠️  Some tools failed to load. Check logs for details.")
            sys.exit(1)
        else:
            print("\n✨ All tools verified successfully.")

    except Exception:
        print("❌ Critical Verification Failure:")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    verify_tools()
