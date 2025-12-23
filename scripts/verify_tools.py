import sys
import os
import yaml
from pathlib import Path

# Add src to path
sys.path.append(os.path.join(os.getcwd(), "services", "agent", "src"))

from core.tools import ToolRegistry, load_tools_from_config
from core.core.config import Settings

def verify_tools():
    try:
        config_path = Path("services/agent/config/tools.yaml")
        if not config_path.exists():
            print(f"ERROR: {config_path} does not exist.")
            return

        print(f"Loading tools from {config_path}...")
        
        # We might need to mock Settings if load_tools_from_config requires it, 
        # but looking at code it usually takes a path or list.
        # Let's check the signature in a moment, but for now assuming it takes path or dict.
        # Actually I should check the code for `load_tools_from_config` to be safe.
        # I'll just read the yaml manually and check the classes if I can't easily import.
        
        with open(config_path, "r") as f:
            data = yaml.safe_load(f)
            
        print("Tools in YAML:")
        for tool_def in data:
            print(f" - Name: {tool_def.get('name')}")
            print(f"   Type: {tool_def.get('type')}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    verify_tools()
