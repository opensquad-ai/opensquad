# -*- coding: utf-8 -*-
"""
OpenSquad Workspace Utilities

Workspace management tools: detect, initialize, and record recently used workspaces.
"""
import os
import json
import platform
from typing import Optional
from pathlib import Path

# Global workspace config directory (across installation directories)
if platform.system() == "Windows":
    GLOBAL_CONFIG_DIR = Path(os.environ.get("USERPROFILE", "C:\\Users\\Default")) / ".opensquad"
else:
    GLOBAL_CONFIG_DIR = Path.home() / ".opensquad"

LAST_WORKSPACE_FILE = GLOBAL_CONFIG_DIR / "last_workspace.json"

# In-process cache: avoid repeated file reads for workspace metadata
_last_workspace_cache: dict = {}
_last_workspace_cache_loaded: bool = False


def _load_last_workspace_raw() -> dict:
    """Load raw workspace metadata with in-process caching."""
    global _last_workspace_cache, _last_workspace_cache_loaded
    if _last_workspace_cache_loaded:
        return _last_workspace_cache
    if LAST_WORKSPACE_FILE.exists():
        try:
            with open(LAST_WORKSPACE_FILE, "r", encoding="utf-8") as f:
                _last_workspace_cache = json.load(f)
        except Exception:
            _last_workspace_cache = {}
    _last_workspace_cache_loaded = True
    return _last_workspace_cache


def get_default_workspace_path() -> str:
    """Return the default workspace path (OS-dependent)."""
    if platform.system() == "Windows":
        base_dir = Path(os.environ.get("USERPROFILE", "C:\\Users\\Default")) / "Documents"
    else:
        base_dir = Path.home() / "Documents"
    
    return str(base_dir / "OpenSquad-Workspace")


def load_last_workspace() -> Optional[str]:
    """Load the most recently used workspace path (cached in-process)."""
    data = _load_last_workspace_raw()
    workspace_path = data.get("last_workspace")

    if workspace_path and os.path.exists(os.path.join(workspace_path, ".opensquad")):
        return workspace_path
    return None


def save_last_workspace(workspace_path: str, workspace_name: str = None, set_as_current: bool = True):
    """Save workspace record (write-guarded: only writes when data actually changes).

    set_as_current=True  -> also update last_workspace (used when switching)
    set_as_current=False -> only add to recent_workspaces list without changing the active workspace (used when adding/registering)
    """
    GLOBAL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Load existing records (cached)
    data = dict(_load_last_workspace_raw())  # shallow copy so we can mutate
    data.setdefault("recent_workspaces", [])

    from datetime import datetime, timezone

    # Compute new values
    new_recent = data.get("recent_workspaces", [])
    if set_as_current:
        new_last = workspace_path
    else:
        new_last = data.get("last_workspace")

    new_recent = [w for w in new_recent if w["path"] != workspace_path]
    new_recent.insert(0, {
        "path": workspace_path,
        "name": workspace_name or os.path.basename(workspace_path),
        "last_opened": datetime.now(timezone.utc).isoformat() + "Z"
    })
    new_recent = new_recent[:10]

    # Write-guarded: skip disk write if nothing changed
    if data.get("last_workspace") == new_last and data.get("recent_workspaces") == new_recent:
        return

    data["last_workspace"] = new_last
    data["recent_workspaces"] = new_recent

    # Update cache
    global _last_workspace_cache
    _last_workspace_cache = data

    with open(LAST_WORKSPACE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def detect_legacy_data(install_dir: str) -> bool:
    """
    Detect whether the installation directory contains legacy user data (pre-workspace era).
    
    Detection markers:
    - chat.db or sessions/ directory containing files
    - data/ directory containing user files
    - agents/ directory containing user agents
    """
    legacy_indicators = [
        os.path.join(install_dir, "gateway", "backend", "chat.db"),
        os.path.join(install_dir, "data", "uploads"),
        os.path.join(install_dir, "sessions"),
        os.path.join(install_dir, "agents"),
    ]
    
    for path in legacy_indicators:
        if os.path.exists(path):
            # If it's a directory, check if it's non-empty
            if os.path.isdir(path):
                try:
                    if any(os.scandir(path)):  # directory is non-empty
                        return True
                except PermissionError:
                    # No access permission, skip
                    continue
            else:
                # File exists = treat as having data
                return True
    
    return False


def _copy_default_resources(workspace_path: str, install_dir: str):
    """Copy default model cards, MCP config and agent to a new workspace."""
    import shutil

    # Copy model cards
    src_model_cards = os.path.join(install_dir, "model_cards")
    ws_model_cards = os.path.join(workspace_path, "model_cards")
    if os.path.isdir(src_model_cards):
        os.makedirs(ws_model_cards, exist_ok=True)
        for card_name in ("deepseek-v4-flash.json", "deepseek-v4-pro.json"):
            src = os.path.join(src_model_cards, card_name)
            dst = os.path.join(ws_model_cards, card_name)
            if os.path.isfile(src) and not os.path.isfile(dst):
                shutil.copy2(src, dst)
                print(f"[Workspace] Copied model card: {card_name}")

    # Copy default MCP config to workspace data/
    src_mcp = os.path.join(install_dir, "pymcp", "config_basic.json")
    ws_data = os.path.join(workspace_path, "data")
    ws_mcp = os.path.join(ws_data, "mcp_config.json")
    if os.path.isfile(src_mcp) and not os.path.isfile(ws_mcp):
        os.makedirs(ws_data, exist_ok=True)
        shutil.copy2(src_mcp, ws_mcp)
        print(f"[Workspace] Created default MCP config: data/mcp_config.json")

    # Copy seed agents into the workspace.
    # pm/coder/qa: the multi-agent collaboration team that ships out of the
    # box. Workspace DB init (init_data.init_default_data) pre-registers
    # their group_chat accounts and a default group so a fresh deploy can
    # experience multi-agent collaboration after the user fills in a model
    # card api_key and starts them.
    src_agents = os.path.join(install_dir, "agents")
    ws_agents = os.path.join(workspace_path, "agents")
    for agent_name in ("pm", "coder", "qa"):
        agent_src = os.path.join(src_agents, agent_name)
        agent_dst = os.path.join(ws_agents, agent_name)
        if os.path.isdir(agent_src) and not os.path.isdir(agent_dst):
            shutil.copytree(agent_src, agent_dst)
            print(f"[Workspace] Created seed agent: agents/{agent_name}/")


def bootstrap_workspace() -> str:
    """
    Workspace initialization flow at startup.
    Returns the finalized workspace path.
    """
    from opensquad import system_config as syscfg

    # 1. Try to load the last-used workspace
    last_workspace = load_last_workspace()
    if last_workspace:
        syscfg.set_workspace(last_workspace)
        print(f"[Workspace] Loaded: {last_workspace}")
        save_last_workspace(last_workspace)  # update last-opened time
        return last_workspace

    # 2. Detect legacy data
    install_dir = syscfg.get_builtin_root()
    has_legacy_data = detect_legacy_data(install_dir)

    if has_legacy_data:
        # Trigger migration flow (use installation dir as workspace temporarily, pending user migration)
        print("[Workspace] Detected legacy data in installation directory")
        print("[Workspace] Using installation directory as workspace (legacy mode)")
        print("[Workspace] Please run migration wizard to move data to a dedicated workspace")
        workspace_path = install_dir
        syscfg.set_workspace(workspace_path)
    else:
        # Create default workspace with full initialization
        workspace_path = get_default_workspace_path()
        print(f"[Workspace] Creating default workspace at: {workspace_path}")
        syscfg.init_workspace(workspace_path, copy_config=True)
        _copy_default_resources(workspace_path, install_dir)

    # 3. Save workspace path
    save_last_workspace(workspace_path)
    return workspace_path
