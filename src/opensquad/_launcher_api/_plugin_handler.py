"""
Plugin Handler Mixin — plugin management HTTP handler methods.

Extracted from _launcher_api/__init__.py to reduce its size.
This mixin provides all plugin-related handler methods for the
ManagementHandler class.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import shutil
import sys
import time
import zipfile

logger = logging.getLogger(__name__)


class PluginHandlerMixin:
    """Mixin providing plugin management handler methods.

    Used by ManagementHandler in _launcher_api.__init__.
    All methods rely on self.state (HandlerState) and self._send_json().
    """

    # Directories to skip when listing plugins (no plugin.py found)
    _skipped_dirs: set = set()

    def _find_plugin_dir(self, plugin_name: str):
        """Find a plugin directory by name or plugin.json metadata."""
        # Strategy 1: directory name matches directly
        direct = os.path.join(self.state.plugins_dir, plugin_name)
        if os.path.isdir(direct) and os.path.isfile(os.path.join(direct, "plugin.py")):
            return direct, plugin_name
        # Strategy 2: scan for matching plugin.json["name"]
        if os.path.isdir(self.state.plugins_dir):
            for entry in os.listdir(self.state.plugins_dir):
                plugin_dir = os.path.join(self.state.plugins_dir, entry)
                if not os.path.isdir(plugin_dir):
                    continue
                if not os.path.isfile(os.path.join(plugin_dir, "plugin.py")):
                    continue
                manifest = os.path.join(plugin_dir, "plugin.json")
                if os.path.isfile(manifest):
                    try:
                        with open(manifest, encoding="utf-8") as f:
                            meta = json.load(f)
                        if meta.get("name") == plugin_name:
                            return plugin_dir, entry
                    except (OSError, ValueError):
                        pass
        return None, None

    def _handle_list_plugins(self):
        """GET /api/plugins — list all plugins."""
        plugins = []
        if not os.path.isdir(self.state.plugins_dir):
            return self._send_json({"plugins": []})
        for name in sorted(os.listdir(self.state.plugins_dir)):
            plugin_dir = os.path.join(self.state.plugins_dir, name)
            if not os.path.isdir(plugin_dir):
                continue
            if not os.path.isfile(os.path.join(plugin_dir, "plugin.py")):
                self._skipped_dirs.add(name)
                continue
            plugin_json_path = os.path.join(plugin_dir, "plugin.json")
            if os.path.isfile(plugin_json_path):
                try:
                    with open(plugin_json_path, encoding="utf-8") as f:
                        meta = json.load(f)
                except (OSError, ValueError):
                    meta = {}
            else:
                meta = {}
            is_builtin = name in self.state.builtin_plugins
            if is_builtin and not meta:
                bp_cfg = self.state.builtin_plugins[name]
                meta["enabled"] = bp_cfg.get("default_enabled", True)
            plugins.append(
                {
                    "name": meta.get("name", name),
                    "dir_name": name,
                    "display_name": meta.get("display_name", name),
                    "version": meta.get("version", "0.0.0"),
                    "type": meta.get("type", "tool"),
                    "enabled": meta.get("enabled", True),
                    "description": meta.get("description", ""),
                    "author": meta.get("author", ""),
                    "tags": meta.get("tags", []),
                    "category": meta.get("category", ""),
                    "tools": meta.get("tools", []),
                    "hooks": meta.get("hooks", []),
                    "config": meta.get("config", {}),
                    "config_schema": meta.get("config_schema", {}),
                    "contributes": meta.get("contributes", {}),
                    "dependencies": meta.get("dependencies", {}),
                    "service": meta.get("service"),
                    "service_only": meta.get("service_only", False),
                    "service_toggle": meta.get("service_toggle", False),
                    "builtin": is_builtin,
                }
            )
        return self._send_json({"plugins": plugins})

    def _handle_plugin_set_enabled(self, name: str, enabled: bool):
        """PUT /api/plugins/{name}/enable or /disable — toggle plugin enabled state."""
        plugin_dir, _dir_name = self._find_plugin_dir(name)
        if not plugin_dir:
            return self._send_json({"error": f"Plugin '{name}' not found"}, 404)
        plugin_json_path = os.path.join(plugin_dir, "plugin.json")
        if os.path.isfile(plugin_json_path):
            try:
                with open(plugin_json_path, encoding="utf-8") as f:
                    meta = json.load(f)
            except (OSError, ValueError):
                meta = {}
        else:
            meta = {"name": name}
        if enabled and meta.get("service_only"):
            return self._send_json({"error": f"Plugin '{name}' is service_only and cannot be enabled"}, 400)
        meta["enabled"] = enabled
        try:
            with open(plugin_json_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2, ensure_ascii=False)
        except (OSError, ValueError) as e:
            return self._send_json({"error": f"Failed to write plugin.json: {e}"}, 500)
        # Sync service_toggle plugins to system_config.json
        if meta.get("service_toggle"):
            try:
                sys_cfg_path = self.state.syscfg.workspace_config_path()
                with open(sys_cfg_path, encoding="utf-8") as f:
                    full_cfg = json.load(f)
                if "services" not in full_cfg:
                    full_cfg["services"] = {}
                if name not in full_cfg["services"]:
                    full_cfg["services"][name] = {}
                full_cfg["services"][name]["enabled"] = enabled
                with open(sys_cfg_path, "w", encoding="utf-8") as f:
                    json.dump(full_cfg, f, indent=2, ensure_ascii=False)
                from opensquad import system_config as _syscfg_mod

                _syscfg_mod._cache = None
            except Exception as e:
                self.state.logger.warning(f"[Launcher] Failed to sync services config: {e}", exc_info=True)
        # Reload signal
        try:
            reload_ts_path = os.path.join(self.state.plugins_dir, ".reload_ts")
            with open(reload_ts_path, "w") as f:
                f.write(str(time.time()))
        except (OSError, ValueError):
            pass
        # Auto-start/stop service
        if meta.get("service_toggle") and name in self.state.plug_svcs:
            psp = self.state.plug_svcs[name]
            if enabled:
                if not psp.is_alive():
                    service_cfg = meta.get("service", {})
                    if service_cfg.get("auto_start"):
                        psp.start()
            else:
                if psp.is_alive():
                    psp.stop()

    def _handle_get_plugin_config(self, name: str):
        """GET /api/plugins/{name}/config — get plugin config."""
        plugin_dir, _dir_name = self._find_plugin_dir(name)
        if not plugin_dir:
            return self._send_json({"error": f"Plugin '{name}' not found"}, 404)
        plugin_json_path = os.path.join(plugin_dir, "plugin.json")
        schema = {}
        section = None
        plugin_type = "tool"
        if os.path.isfile(plugin_json_path):
            try:
                with open(plugin_json_path, encoding="utf-8") as f:
                    meta = json.load(f)
                schema = meta.get("config_schema", {})
                section = meta.get("config", {}).get("section")
                plugin_type = meta.get("type", "tool")
            except (OSError, ValueError):
                pass
        if section and plugin_type == "platform":
            try:
                sys_cfg_path = self.state.syscfg.workspace_config_path()
                with open(sys_cfg_path, encoding="utf-8") as f:
                    full_cfg = json.load(f)
                sec_data = full_cfg.get(section, {})
                values = {
                    "service_enabled": full_cfg.get("services", {}).get(section, {}).get("enabled", False),
                    "bots": sec_data.get("bots", []),
                }
            except (OSError, ValueError):
                values = {}
        else:
            config_path = self.state.syscfg.workspace_data_dir("plugins", name, "config.json")
            values = {}
            if os.path.isfile(config_path):
                try:
                    with open(config_path, encoding="utf-8") as f:
                        values = json.load(f)
                except (OSError, ValueError):
                    pass
        merged = {}
        for key, field_schema in schema.items():
            if isinstance(field_schema, dict):
                default_val = field_schema.get("default")
                if key == "bots":
                    merged[key] = values.get(key, default_val if default_val is not None else [])
                else:
                    merged[key] = values.get(key, default_val)
            else:
                merged[key] = values.get(key)
        return self._send_json({"name": name, "config_schema": schema, "config": merged})

    def _handle_put_plugin_config(self, name: str, body: dict):
        """PUT /api/plugins/{name}/config — update plugin config."""
        plugin_dir, _dir_name = self._find_plugin_dir(name)
        if not plugin_dir:
            return self._send_json({"error": f"Plugin '{name}' not found"}, 404)
        config_values = body.get("config", body)
        plugin_json_path = os.path.join(plugin_dir, "plugin.json")
        section = None
        plugin_type = "tool"
        if os.path.isfile(plugin_json_path):
            try:
                with open(plugin_json_path, encoding="utf-8") as f:
                    meta = json.load(f)
                section = meta.get("config", {}).get("section")
                plugin_type = meta.get("type", "tool")
            except (OSError, ValueError):
                pass
        if section and plugin_type == "platform":
            try:
                sys_cfg_path = self.state.syscfg.workspace_config_path()
                with open(sys_cfg_path, encoding="utf-8") as f:
                    full_cfg = json.load(f)
                if section not in full_cfg:
                    full_cfg[section] = {}
                if "bots" in config_values:
                    full_cfg[section]["bots"] = config_values["bots"]
                if "service_enabled" in config_values:
                    if "services" not in full_cfg:
                        full_cfg["services"] = {}
                    if section not in full_cfg["services"]:
                        full_cfg["services"][section] = {}
                    full_cfg["services"][section]["enabled"] = config_values["service_enabled"]
                    if meta.get("service_toggle"):
                        meta["enabled"] = config_values["service_enabled"]
                        try:
                            with open(plugin_json_path, "w", encoding="utf-8") as f:
                                json.dump(meta, f, indent=2, ensure_ascii=False)
                        except (OSError, ValueError):
                            pass
                with open(sys_cfg_path, "w", encoding="utf-8") as f:
                    json.dump(full_cfg, f, indent=2, ensure_ascii=False)
                from opensquad import system_config as _syscfg_mod

                _syscfg_mod._cache = None
            except (OSError, ValueError) as e:
                return self._send_json({"error": f"Failed to write system config: {e}"}, 500)
        else:
            config_dir = self.state.syscfg.workspace_data_dir("plugins", name)
            config_path = os.path.join(config_dir, "config.json")
            try:
                os.makedirs(config_dir, exist_ok=True)
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(config_values, f, indent=2, ensure_ascii=False)
            except (OSError, ValueError) as e:
                return self._send_json({"error": f"Failed to write config: {e}"}, 500)
        try:
            reload_ts_path = os.path.join(self.state.plugins_dir, ".reload_ts")
            with open(reload_ts_path, "w") as f:
                f.write(str(time.time()))
        except (OSError, ValueError):
            pass
        return self._send_json({"ok": True, "message": f"Config saved for '{name}'"})

    def _handle_get_plugin_data(self, name: str, qs: dict):
        """GET /api/plugins/{name}/data — query plugin data."""
        plugin_dir, _dir_name = self._find_plugin_dir(name)
        if not plugin_dir:
            return self._send_json({"error": f"Plugin '{name}' not found"}, 404)
        query_module_path = os.path.join(plugin_dir, "query.py")
        if not os.path.isfile(query_module_path):
            return self._send_json({"error": f"Plugin '{name}' has no data query module (query.py)"}, 404)
        import importlib

        module_name = f"plugins.{name}.query"
        try:
            if module_name in sys.modules:
                mod = importlib.reload(sys.modules[module_name])
            else:
                mod = importlib.import_module(module_name)
        except Exception as e:
            return self._send_json({"error": f"Failed to import {module_name}: {e}"}, 500)
        if not hasattr(mod, "query_data"):
            return self._send_json({"error": f"Plugin '{name}' query.py missing query_data() function"}, 400)
        params = {k: v[0] if isinstance(v, list) and v else v for k, v in qs.items()}
        try:
            result = mod.query_data(self.state.syscfg.project_root(), params)
            return self._send_json(result)
        except Exception as e:
            return self._send_json({"error": f"Query failed: {e}"}, 500)

    def _handle_plugin_view_error(self, body: dict):
        """POST /api/plugin-view-error — log a plugin view error."""
        import datetime as _dt

        plugin_name = body.get("plugin_name", "unknown")
        view_key = body.get("view_key", "")
        error_msg = body.get("error", "")
        stack = body.get("stack", "")
        log_path = os.path.join(self.state.plugins_dir, plugin_name, "view_errors.log")
        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry = f"[{ts}] view={view_key}\n  error: {error_msg}\n  stack: {stack[:800]}\n{'─' * 60}\n"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(entry)
            return self._send_json({"ok": True, "log": log_path})
        except Exception as e:
            return self._send_json({"ok": False, "error": str(e)}, 500)

    def _handle_resource_upload(self, body: dict):
        """POST /api/resources/upload — upload resource files (skills/plugins)."""
        resource_type = body.get("resource_type")
        files = body.get("files", [])
        if resource_type == "skills":
            base_dir = self.state.skills_dir
        elif resource_type == "plugins":
            base_dir = self.state.plugins_dir
        else:
            return self._send_json({"error": "Invalid resource type"}, 400)
        if not files:
            return self._send_json({"error": "No files provided"}, 400)
        try:
            os.makedirs(base_dir, exist_ok=True)
            resource_names = set()
            for f in files:
                file_path = f.get("filename", "")
                content_b64 = f.get("content", "")
                if not file_path or not content_b64:
                    continue
                parts = file_path.replace("\\", "/").split("/")
                if len(parts) > 1:
                    resource_names.add(parts[0])
                target_path = os.path.join(base_dir, file_path)
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                with open(target_path, "wb") as out_f:
                    out_f.write(base64.b64decode(content_b64))
            if resource_type == "plugins" and resource_names:
                reload_ts_path = os.path.join(base_dir, ".reload_ts")
                with open(reload_ts_path, "w") as rf:
                    rf.write(str(time.time()))
            return self._send_json(
                {
                    "success": True,
                    "message": f"Successfully uploaded {len(files)} files to {resource_type}",
                    "resources": list(resource_names),
                }
            )
        except Exception as e:
            return self._send_json({"error": str(e)}, 500)

    def _handle_delete_resource(self, resource_type: str, name: str):
        """DELETE /api/resources/{type}/{name} — delete a resource."""
        if resource_type == "skills":
            base_dir = self.state.skills_dir
        elif resource_type == "plugins":
            base_dir = self.state.plugins_dir
        else:
            return self._send_json({"error": "Invalid resource type"}, 400)
        if not re.match(r"^[a-zA-Z0-9_\-\.]+$", name):
            return self._send_json({"error": "Invalid resource name"}, 400)
        target_dir = os.path.join(base_dir, name)
        if not os.path.abspath(target_dir).startswith(os.path.abspath(base_dir)):
            return self._send_json({"error": "Path traversal detected"}, 400)
        if not os.path.isdir(target_dir):
            return self._send_json({"error": f"{resource_type[:-1].capitalize()} '{name}' not found"}, 404)
        try:
            shutil.rmtree(target_dir)
            if resource_type == "plugins":
                reload_ts_path = os.path.join(base_dir, ".reload_ts")
                try:
                    with open(reload_ts_path, "w") as rf:
                        rf.write(str(time.time()))
                except (OSError, ValueError):
                    pass
            return self._send_json({"ok": True, "message": f"{resource_type[:-1].capitalize()} '{name}' deleted"})
        except (OSError, ValueError) as e:
            return self._send_json({"error": f"Failed to delete {resource_type}: {e}"}, 500)

    def _handle_plugin_action(self, name: str, body: dict):
        """POST /api/plugins/{name}/action — execute a plugin action."""
        plugin_dir, _dir_name = self._find_plugin_dir(name)
        if not plugin_dir:
            return self._send_json({"error": f"Plugin '{name}' not found"}, 404)
        query_module_path = os.path.join(plugin_dir, "query.py")
        if not os.path.isfile(query_module_path):
            return self._send_json({"error": f"Plugin '{name}' does not support actions"}, 400)
        try:
            import importlib.util as _ilu

            spec = _ilu.spec_from_file_location(f"plugins.{name}.query", query_module_path)
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if not hasattr(mod, "handle_action"):
                return self._send_json({"error": f"Plugin '{name}' does not support actions"}, 400)
            action = body.get("action", "")
            data = body.get("data", {})
            result = mod.handle_action(self.state.syscfg.project_root(), action, data)
            return self._send_json(result)
        except Exception as e:
            return self._send_json({"error": f"Action failed: {e}"}, 500)

    def _handle_install_zip_plugin(self, body: dict):
        """POST /api/plugins/install-zip — install/update a plugin from zip."""
        plugin_id = (body.get("plugin_id") or "").strip()
        zip_b64 = body.get("zip_b64", "")
        if not plugin_id or not zip_b64:
            return self._send_json({"error": "plugin_id and zip_b64 are required"}, 400)
        try:
            zip_bytes = base64.b64decode(zip_b64)
        except Exception as e:
            return self._send_json({"error": f"Invalid base64: {e}"}, 400)
        plugin_dest = os.path.join(self.state.plugins_dir, plugin_id)
        existing_manifest = os.path.join(plugin_dest, "plugin.json")
        existing_plugin_py_path = os.path.join(plugin_dest, "plugin.py")
        existing_enabled = True
        existing_version = None
        existing_category = None
        existing_plugin_py: bytes | None = None
        if os.path.isfile(existing_manifest):
            try:
                with open(existing_manifest, encoding="utf-8") as f:
                    existing_data = json.load(f)
                existing_enabled = existing_data.get("enabled", True)
                existing_version = existing_data.get("version")
                existing_category = existing_data.get("category")
            except (OSError, ValueError):
                pass
        if os.path.isfile(existing_plugin_py_path):
            try:
                with open(existing_plugin_py_path, "rb") as f:
                    existing_plugin_py = f.read()
            except (OSError, ValueError):
                pass
        os.makedirs(self.state.plugins_dir, exist_ok=True)
        try:
            buf = io.BytesIO(zip_bytes)
            with zipfile.ZipFile(buf) as zf:
                for member in zf.infolist():
                    parts = member.filename.split("/")
                    relative = "/".join(parts[1:]) if len(parts) > 1 else parts[0]
                    if not relative:
                        continue
                    dest_path = os.path.join(plugin_dest, relative)
                    if not os.path.abspath(dest_path).startswith(os.path.abspath(plugin_dest)):
                        continue
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    if not member.is_dir():
                        with zf.open(member) as src, open(dest_path, "wb") as dst:
                            shutil.copyfileobj(src, dst)
        except zipfile.BadZipFile as e:
            return self._send_json({"error": f"Invalid zip archive: {e}"}, 422)
        except Exception as e:
            return self._send_json({"error": f"Failed to extract plugin: {e}"}, 500)
        if existing_plugin_py is not None:
            try:
                with open(existing_plugin_py_path, "wb") as f:
                    f.write(existing_plugin_py)
            except (OSError, ValueError):
                pass
        if os.path.isfile(existing_manifest):
            try:
                with open(existing_manifest, encoding="utf-8") as f:
                    new_manifest = json.load(f)
                new_manifest["enabled"] = existing_enabled
                if existing_category and not new_manifest.get("category"):
                    new_manifest["category"] = existing_category
                with open(existing_manifest, "w", encoding="utf-8") as f:
                    json.dump(new_manifest, f, indent=2, ensure_ascii=False)
            except (OSError, ValueError):
                pass
        try:
            reload_ts_path = os.path.join(self.state.plugins_dir, ".reload_ts")
            with open(reload_ts_path, "w") as f:
                f.write(str(time.time()))
        except (OSError, ValueError):
            pass
        action = "updated" if existing_version else "installed"
        return self._send_json({"ok": True, "action": action, "plugin_id": plugin_id})
