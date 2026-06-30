"""
Workspace Tool
Provides workspace management capabilities for agents: query current workspace, list workspaces,
create workspaces, switch workspaces, and migrate data.
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


# ===================================================================
# Tool functions
# ===================================================================


def get_current() -> dict[str, Any]:
    """
    Get detailed information about the currently active workspace.

    Returned fields:
    - workspace_root  Absolute path of the current workspace root directory
    - work_dir        Public working directory (workspace/ subdirectory)
    - agents_dir      Agents directory
    - exists          Whether the workspace directory exists
    - metadata        Metadata from .opensquad/workspace.json (if present)

    Example::

        result = workspace.get_current()
        # result["workspace_root"] -> "C:/Users/me/Documents/OpenSquad-Workspace"
        # result["work_dir"]       -> "C:/Users/me/Documents/OpenSquad-Workspace/workspace"
    """
    try:
        from opensquad.system_config import syscfg

        ws_root = syscfg.get_workspace()
    except Exception as e:
        return {"status": "error", "message": f"Cannot read workspace config: {e}"}

    work_dir = os.path.join(ws_root, "workspace")
    agents_dir = os.path.join(ws_root, "agents")
    meta_path = os.path.join(ws_root, ".opensquad", "workspace.json")

    metadata: dict[str, Any] = {}
    if os.path.isfile(meta_path):
        try:
            import json

            with open(meta_path, encoding="utf-8") as f:
                metadata = json.load(f)
        except Exception:
            pass

    return {
        "status": "ok",
        "workspace_root": ws_root,
        "work_dir": work_dir,
        "agents_dir": agents_dir,
        "exists": os.path.isdir(ws_root),
        "work_dir_exists": os.path.isdir(work_dir),
        "metadata": metadata,
    }


def list_workspaces() -> dict[str, Any]:
    """
    List all known workspaces (currently active + recently used list).

    Returned fields:
    - current         Path of the currently active workspace
    - workspaces      List of workspaces, each with path / name / is_current / exists / last_opened

    Example::

        result = workspace.list_workspaces()
        for ws in result["workspaces"]:
            print(ws["path"], ws["is_current"])
    """
    try:
        import json

        from opensquad.system_config import syscfg

        current = syscfg.get_workspace()

        # Read recent_workspaces
        record_file = os.path.join(os.path.expanduser("~"), ".opensquad", "last_workspace.json")
        recent: list[dict] = []
        if os.path.isfile(record_file):
            try:
                with open(record_file, encoding="utf-8") as f:
                    data = json.load(f)
                raw = data.get("recent_workspaces", [])
                if raw and isinstance(raw[0], dict):
                    recent = raw  # New format
                elif raw:
                    recent = [{"path": p, "name": os.path.basename(p), "last_opened": None} for p in raw]
            except Exception:
                pass

        # Ensure current workspace is in the list
        paths_in_list = {item["path"] for item in recent}
        if current not in paths_in_list:
            recent.insert(0, {"path": current, "name": os.path.basename(current), "last_opened": None})

        workspaces = []
        for item in recent:
            path = item.get("path", "")
            workspaces.append(
                {
                    "path": path,
                    "name": item.get("name") or os.path.basename(path),
                    "is_current": (path == current),
                    "exists": os.path.isdir(path),
                    "last_opened": item.get("last_opened"),
                }
            )

        return {
            "status": "ok",
            "current": current,
            "workspaces": workspaces,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def create(path: str, name: str | None = None) -> dict[str, Any]:
    """
    Create a new workspace (or initialize an existing directory as a workspace).

    Does not automatically switch to the new workspace after creation; call switch() to switch.

    Args:
    - path   Target directory path (absolute path). Created automatically if it does not exist.
    - name   Workspace name (optional, defaults to directory name)

    Returned fields:
    - status   "ok" / "error"
    - path     Final workspace path
    - action   "created" / "initialized" / "added" (when already a workspace)
    - message  Description

    Example::

        result = workspace.create("C:/Projects/MyWorkspace")
        # result["action"] -> "created"
    """
    if not path or not path.strip():
        return {"status": "error", "message": "path cannot be empty"}

    try:
        from opensquad.system_config import syscfg
        from opensquad.workspace_utils import save_last_workspace

        abs_path = os.path.abspath(path.strip())
        meta_dir = os.path.join(abs_path, ".opensquad")

        if os.path.isdir(abs_path):
            if os.path.isdir(meta_dir):
                # Already a workspace; just add to list
                save_last_workspace(abs_path, workspace_name=name, set_as_current=False)
                return {
                    "status": "ok",
                    "path": abs_path,
                    "action": "added",
                    "message": "Directory is already a workspace; added to workspace list (not switched)",
                }
            else:
                # Regular directory; initialize as workspace
                syscfg.init_workspace(abs_path, copy_config=True)
                save_last_workspace(abs_path, workspace_name=name, set_as_current=False)
                return {
                    "status": "ok",
                    "path": abs_path,
                    "action": "initialized",
                    "message": "Existing directory initialized as workspace (not switched)",
                }
        else:
            # Create new workspace
            syscfg.init_workspace(abs_path, copy_config=True)
            save_last_workspace(abs_path, workspace_name=name, set_as_current=False)
            return {
                "status": "ok",
                "path": abs_path,
                "action": "created",
                "message": "Workspace created (not switched; call switch() and restart to take effect)",
            }
    except Exception as e:
        logger.error(f"[workspace] create failed: {e}")
        return {"status": "error", "message": str(e)}


def switch(path: str) -> dict[str, Any]:
    """
    Switch to the specified workspace.

    Note: Switching only saves the preference to ~/.opensquad/last_workspace.json.
    Restart the Agent/Launcher for it to fully take effect (syscfg and filesystem
    whitelist are initialized at startup).

    Args:
    - path   Target workspace path (must be a validly initialized workspace with a .opensquad/ directory)

    Returned fields:
    - status            "ok" / "error"
    - path              Target workspace path
    - requires_restart  True (restart is always required)

    Example::

        result = workspace.switch("C:/Projects/MyWorkspace")
        # result["requires_restart"] -> True
    """
    if not path or not path.strip():
        return {"status": "error", "message": "path cannot be empty"}

    try:
        from opensquad.workspace_utils import save_last_workspace

        abs_path = os.path.abspath(path.strip())
        meta_dir = os.path.join(abs_path, ".opensquad")

        if not os.path.isdir(abs_path):
            return {"status": "error", "message": f"Directory does not exist: {abs_path}"}
        if not os.path.isdir(meta_dir):
            return {
                "status": "error",
                "message": f"Not a valid workspace (missing .opensquad/ directory): {abs_path}. Call create() to initialize first.",
            }

        save_last_workspace(abs_path, set_as_current=True)
        return {
            "status": "ok",
            "path": abs_path,
            "requires_restart": True,
            "message": "Workspace preference saved. Restart the Agent and Launcher for it to fully take effect.",
        }
    except Exception as e:
        logger.error(f"[workspace] switch failed: {e}")
        return {"status": "error", "message": str(e)}


def migrate(source: str, target: str, mode: str = "copy", conflict: str = "skip") -> dict[str, Any]:
    """
    Migrate data from an old workspace (or installation directory) to the target workspace.

    Args:
    - source    Source directory path (usually the old workspace or installation directory)
    - target    Target workspace path (must already exist or will be created automatically)
    - mode      Migration mode: "copy" (keep source files, default) or "move" (move and delete source files)
    - conflict  Conflict handling: "skip" (skip existing items, default) or "overwrite" (backup then overwrite)

    Returned fields:
    - status          "ok" / "error"
    - success_count   Number of successfully migrated items
    - failed_count    Number of failed items
    - skipped_count   Number of skipped items
    - report          Full migration report dict

    Example::

        result = workspace.migrate(
            source="/path/to/old_workspace",
            target="/path/to/new_workspace",
            mode="copy",
        )
        print(result["success_count"], "items migrated")
    """
    if not source or not target:
        return {"status": "error", "message": "source and target cannot be empty"}
    if mode not in ("copy", "move"):
        return {"status": "error", "message": "mode must be 'copy' or 'move'"}
    if conflict not in ("skip", "overwrite"):
        return {"status": "error", "message": "conflict must be 'skip' or 'overwrite'"}

    try:
        from opensquad.migration_tool import LegacyDataMigrator
        from opensquad.system_config import syscfg

        abs_source = os.path.abspath(source.strip())
        abs_target = os.path.abspath(target.strip())

        if not os.path.isdir(abs_source):
            return {"status": "error", "message": f"Source directory does not exist: {abs_source}"}

        # Create target workspace if it does not exist
        if not os.path.isdir(abs_target):
            syscfg.init_workspace(abs_target, copy_config=True)

        migrator = LegacyDataMigrator(
            install_dir=abs_source,
            target_workspace=abs_target,
            mode=mode,
            overwrite=(conflict == "overwrite"),
        )
        report = migrator.migrate()
        report_dict = report.to_dict()

        return {
            "status": "ok",
            "success_count": report_dict["success_count"],
            "failed_count": report_dict["failed_count"],
            "skipped_count": report_dict["skipped_count"],
            "report": report_dict,
        }
    except Exception as e:
        logger.error(f"[workspace] migrate failed: {e}")
        return {"status": "error", "message": str(e)}


# ===================================================================
# Tool descriptions (for ToolRegistry auto-generated schema)
# ===================================================================

TOOL_DESCRIPTION = {
    "get_current": {
        "description": "Get the path and metadata of the currently active workspace",
        "parameters": {},
    },
    "list_workspaces": {
        "description": "List all known workspaces (currently active + recently used)",
        "parameters": {},
    },
    "create": {
        "description": "Create a new workspace or initialize an existing directory as a workspace",
        "parameters": {
            "path": {"type": "string", "description": "Target directory path (absolute path)"},
            "name": {"type": "string", "description": "Workspace name (optional)"},
        },
        "required": ["path"],
    },
    "switch": {
        "description": "Switch to the specified workspace (saves preference; restart required to take effect)",
        "parameters": {
            "path": {"type": "string", "description": "Target workspace path"},
        },
        "required": ["path"],
    },
    "migrate": {
        "description": "Migrate data from an old workspace or installation directory to the target workspace",
        "parameters": {
            "source": {"type": "string", "description": "Source directory path"},
            "target": {"type": "string", "description": "Target workspace path"},
            "mode": {"type": "string", "description": "'copy' (default) or 'move'"},
            "conflict": {"type": "string", "description": "'skip' (default) or 'overwrite'"},
        },
        "required": ["source", "target"],
    },
}
