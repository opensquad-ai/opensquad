"""opensquad init -- Initialize a new workspace."""

import os
import sys


def run_init(args):
    if args.workspace:
        workspace = os.path.abspath(args.workspace)
    else:
        workspace = os.path.join(os.path.expanduser("~"), ".opensquad", "workspace")
    os.makedirs(workspace, exist_ok=True)
    print(f"[init] Initializing workspace at: {workspace}")

    # Ensure opensquad package is importable
    _root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
    if _root not in sys.path:
        sys.path.insert(0, _root)

    from opensquad.system_config import syscfg

    try:
        syscfg.init_workspace(workspace, copy_config=not args.no_config)
    except Exception as e:
        print(f"[init] Error: {e}", file=sys.stderr)
        sys.exit(1)

    print("[init] Workspace initialized successfully.")

    # Save workspace path so start/launcher can find it
    from opensquad.workspace_utils import save_last_workspace

    save_last_workspace(workspace)

    # Copy default model cards and agent to workspace
    from opensquad.workspace_utils import _copy_default_resources

    _copy_default_resources(workspace, os.path.join(_root, "src"))

    # Print directory structure
    print("\n[init] Directory structure:")
    for d in [
        "agents/pm/",
        "model_cards/",
        "data/uploads/",
        "data/logs/gateway/",
        "data/sessions/",
        "data/plugins/",
        "data/audit/",
    ]:
        full = os.path.join(workspace, d)
        exists = os.path.isdir(full)
        print(f"  {'[OK]' if exists else '[--]'} {d}")

    config_path = os.path.join(workspace, "system_config.json")
    if os.path.isfile(config_path):
        print(f"\n[init] Config: {config_path}")

    print("\n[init] Next steps:")
    print("  1. Edit model_cards/deepseek-v4-flash.json, fill in your api_key")
    print("     Get your key at: https://platform.deepseek.com")
    print("  2. Run: opensquad start")
