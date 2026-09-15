"""Workspace list, create, switch and legacy migration for the Launcher management API.

Extracted verbatim from ``launcher_main._start_management_server`` -- method
bodies are byte-identical to the original, only re-indented, so this is a pure
move.  See ``management_api/__init__.py`` for the composed handler.

Shared launcher registries are imported **by value** from ``launcher_main``.
That is safe because launcher_main only ever mutates these containers in place
(``dict[...] = ...`` / ``.append`` / ``.add``); the single rebind of
``_BUILTIN_PLUGINS`` happens at launcher_main module load, i.e. before this
module can be imported -- ``launcher_main`` imports this package lazily, from
inside the function that starts the server.
"""

from __future__ import annotations

import json
import os

from opensquad.launcher_main import (
    _workspace_migration_tasks,
)
from opensquad.system_config import syscfg


class WorkspaceMixin:
    """Workspace list, create, switch and legacy migration."""

    def _handle_workspace_list(self):
        """List all known workspaces on the Launcher server"""
        current = syscfg.get_workspace()
        from opensquad.workspace_utils import get_default_workspace_path

        default_path = get_default_workspace_path()

        record_file = os.path.expanduser("~/.opensquad/last_workspace.json")
        recent_paths: list = []
        if os.path.exists(record_file):
            try:
                with open(record_file, encoding="utf-8") as f:
                    data = json.load(f)
                rw = data.get("recent_workspaces", [])
                r = data.get("recent", [])
                if rw and isinstance(rw[0], dict):
                    recent_paths = [ws["path"] for ws in rw]
                elif r:
                    recent_paths = r
            except Exception:
                pass

        if default_path not in recent_paths:
            recent_paths.insert(0, default_path)

        workspaces = []
        for p in recent_paths:
            meta: dict = {}
            meta_file = os.path.join(p, ".opensquad", "workspace.json")
            if os.path.exists(meta_file):
                try:
                    with open(meta_file, encoding="utf-8") as f:
                        meta = json.load(f)
                except Exception:
                    pass
            workspaces.append(
                {
                    "path": p,
                    "name": os.path.basename(p),
                    "is_current": os.path.normpath(p) == os.path.normpath(current),
                    "exists": os.path.exists(p),
                    "created_at": meta.get("created_at"),
                    "last_used": meta.get("last_used"),
                }
            )
        return self._send_json(
            {
                "workspaces": workspaces,
                "current": current,
                "default_path": default_path,
            }
        )

    def _handle_workspace_create(self, body: dict):
        """Create or register a workspace on the Launcher server (does not auto-switch)"""
        from datetime import datetime as _dt

        from opensquad.workspace_utils import (
            _copy_default_resources,
            get_default_workspace_path,
            save_last_workspace,
        )

        raw_path = (body.get("path") or "").strip()
        name = (body.get("name") or "").strip()

        if raw_path:
            workspace_path = os.path.abspath(raw_path)
        else:
            base = os.path.dirname(get_default_workspace_path())
            ws_name = name or f"OpenSquad-Workspace-{_dt.now().strftime('%Y%m%d')}"
            workspace_path = os.path.join(base, ws_name)

        if os.path.exists(workspace_path):
            meta_dir = os.path.join(workspace_path, ".opensquad")
            if os.path.exists(meta_dir):
                save_last_workspace(workspace_path, set_as_current=False)
                return self._send_json(
                    {
                        "success": True,
                        "message": "Existing workspace added",
                        "path": workspace_path,
                        "action": "added",
                    }
                )
            try:
                syscfg.init_workspace(workspace_path, copy_config=True)
                _copy_default_resources(workspace_path, syscfg.get_builtin_root())
                save_last_workspace(workspace_path, set_as_current=False)
                return self._send_json(
                    {
                        "success": True,
                        "message": "Existing directory initialized as workspace",
                        "path": workspace_path,
                        "action": "initialized",
                    }
                )
            except Exception as e:
                return self._send_json({"error": f"Failed to initialize workspace: {e}"}, 500)

        try:
            syscfg.init_workspace(workspace_path, copy_config=True)
            _copy_default_resources(workspace_path, syscfg.get_builtin_root())
            save_last_workspace(workspace_path, set_as_current=False)
            return self._send_json(
                {
                    "success": True,
                    "message": "Workspace created successfully",
                    "path": workspace_path,
                    "action": "created",
                }
            )
        except Exception as e:
            return self._send_json({"error": f"Failed to create workspace: {e}"}, 500)

    def _handle_workspace_switch(self, body: dict):
        """Switch the current workspace (recorded to config; requires Launcher restart to fully take effect)"""
        from datetime import datetime as _dt

        from opensquad.workspace_utils import persist_desktop_workspace_switch, save_last_workspace

        raw_path = (body.get("path") or "").strip()
        if not raw_path:
            return self._send_json({"error": "Missing 'path'"}, 400)

        workspace_path = os.path.abspath(raw_path)
        if not os.path.exists(workspace_path):
            return self._send_json({"error": f"Workspace does not exist: {workspace_path}"}, 404)

        meta_dir = os.path.join(workspace_path, ".opensquad")
        if not os.path.exists(meta_dir):
            return self._send_json(
                {"error": f"Invalid workspace (missing .opensquad directory): {workspace_path}"}, 400
            )

        try:
            ws_json = os.path.join(meta_dir, "workspace.json")
            if not os.path.exists(ws_json):
                with open(ws_json, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "name": os.path.basename(workspace_path),
                            "created_at": _dt.utcnow().isoformat() + "Z",
                            "last_used": _dt.utcnow().isoformat() + "Z",
                        },
                        f,
                        indent=2,
                        ensure_ascii=False,
                    )
            save_last_workspace(workspace_path)
            persist_desktop_workspace_switch(workspace_path)
            return self._send_json(
                {
                    "success": True,
                    "message": "Workspace switched; please restart the app for the change to take effect",
                    "path": workspace_path,
                    "requires_restart": True,
                    "desktop_restart": bool(os.environ.get("OPENSQUAD_APP_DATA")),
                }
            )
        except Exception as e:
            return self._send_json({"error": f"Failed to switch workspace: {e}"}, 500)

    def _handle_workspace_detect_legacy(self):
        """Detect legacy data in the installation directory"""
        install_dir = syscfg.get_builtin_root()
        current_workspace = syscfg.get_workspace()

        if current_workspace and os.path.normpath(current_workspace) == os.path.normpath(install_dir):
            return self._send_json(
                {
                    "has_legacy_data": False,
                    "legacy_location": install_dir,
                    "detected_items": {
                        "database": False,
                        "agents": False,
                        "uploads": False,
                        "sessions": False,
                        "logs": False,
                    },
                }
            )

        def _has(p):
            return os.path.exists(p) and bool(os.listdir(p))

        detected = {
            "database": os.path.exists(os.path.join(install_dir, "gateway", "backend", "chat.db")),
            "agents": _has(os.path.join(install_dir, "agents")),
            "uploads": _has(os.path.join(install_dir, "data", "uploads")),
            "sessions": _has(os.path.join(install_dir, "data", "sessions")),
            "logs": _has(os.path.join(install_dir, "data", "logs")),
        }
        return self._send_json(
            {
                "has_legacy_data": any(detected.values()),
                "legacy_location": install_dir,
                "detected_items": detected,
            }
        )

    def _handle_workspace_migrate(self, body: dict):
        """Start a background workspace migration task (copy=keep source / move=delete source after migration)"""
        import threading as _threading
        import uuid

        source = (body.get("source") or "").strip()
        target = (body.get("target") or "").strip()
        mode = body.get("mode", "copy")  # "copy" | "move"
        conflict = body.get("conflict", "skip")  # "skip" | "overwrite"

        if not source or not target:
            return self._send_json({"error": "Missing 'source' or 'target'"}, 400)

        task_id = str(uuid.uuid4())
        _workspace_migration_tasks[task_id] = {
            "status": "pending",
            "progress": 0.0,
            "message": "Waiting to start...",
            "report": None,
        }

        def _run():
            import re as _re

            try:
                _workspace_migration_tasks[task_id]["status"] = "running"
                _workspace_migration_tasks[task_id]["message"] = "Migrating data..."

                def _progress(msg: str):
                    m = _re.search(r"\[(\d+)/(\d+)\]", msg)
                    if m:
                        cur, tot = int(m.group(1)), int(m.group(2))
                        _workspace_migration_tasks[task_id]["progress"] = round(cur / tot, 2) if tot else 0.0
                    _workspace_migration_tasks[task_id]["message"] = msg

                from opensquad.migration_tool import LegacyDataMigrator

                migrator = LegacyDataMigrator(
                    install_dir=source,
                    target_workspace=target,
                    mode=mode,
                    overwrite=(conflict == "overwrite"),
                )
                report = migrator.migrate(progress_callback=_progress)
                _workspace_migration_tasks[task_id]["status"] = "completed"
                _workspace_migration_tasks[task_id]["progress"] = 1.0
                _workspace_migration_tasks[task_id]["message"] = (
                    f"Migration complete: {len(report.success)} item(s) succeeded"
                )
                _workspace_migration_tasks[task_id]["report"] = report.to_dict()
            except Exception as e:
                _workspace_migration_tasks[task_id]["status"] = "failed"
                _workspace_migration_tasks[task_id]["message"] = f"Migration failed: {e}"

        _threading.Thread(target=_run, daemon=True, name=f"ws-migrate-{task_id[:8]}").start()

        return self._send_json(
            {
                "success": True,
                "task_id": task_id,
                "message": "Migration task started",
            }
        )

    def _handle_workspace_migrate_status(self, task_id: str):
        """Query migration task progress"""
        task = _workspace_migration_tasks.get(task_id)
        if task is None:
            return self._send_json({"error": f"Task not found: {task_id}"}, 404)
        return self._send_json(
            {
                "task_id": task_id,
                "status": task["status"],
                "progress": task["progress"],
                "message": task["message"],
                "report": task.get("report"),
            }
        )
