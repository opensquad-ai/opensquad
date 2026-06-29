# -*- coding: utf-8 -*-
"""
Plugin Admin Plugin

Gives the Agent the ability to list, enable/disable, and configure other plugins
without leaving the chat.

All operations are purely file-system based:
  - plugins/{name}/plugin.json  — enabled flag
  - data/plugins/{name}/config.json — persisted config values
  - plugins/.reload_ts           — touched to trigger hot-reload

Constraints (enforced throughout):
  - No subprocess / eval / exec / os.system
  - All paths via context.project_root (never hardcoded)
  - All exceptions caught; tools return {"error": "..."} instead of raising
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from opensquad.plugin_api import register, tool, Context, Plugin

logger = logging.getLogger("plugins.plugin_admin")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_json(path: str) -> Optional[Dict]:
    """Read a JSON file; return None on any error."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_json(path: str, data: Dict) -> bool:
    """Atomically write data as JSON; return True on success."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception as e:
        logger.error(f"[PluginAdmin] _write_json({path}): {e}")
        return False


def _touch_reload_ts(plugins_dir: str) -> None:
    """Touch plugins/.reload_ts to trigger hot-reload in AgentRunner."""
    try:
        ts_path = os.path.join(plugins_dir, ".reload_ts")
        with open(ts_path, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except Exception as e:
        logger.warning(f"[PluginAdmin] Failed to touch .reload_ts: {e}")


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------

@register(
    name="plugin_admin",
    author="lihua179",
    description=(
        "Plugin administration tools. Allows the Agent to list plugins, "
        "enable/disable them, read and write their configuration, and "
        "trigger a hot-reload — all without leaving the chat."
    ),
    version="1.0.0",
    plugin_type="tool",
    display_name="Plugin Admin",
    node_scope="all",
    config_schema={},
    tags=["admin", "plugins", "management"],
)
class PluginAdminPlugin(Plugin):

    def __init__(self, context: Context):
        super().__init__(context)

    # ---- private helpers ----

    @property
    def _plugins_dir(self) -> str:
        return os.path.join(self.context.project_root, "plugins")

    @property
    def _plugin_data_root(self) -> str:
        return os.path.join(self.context.project_root, "data", "plugins")

    def _manifest_path(self, name: str) -> str:
        return os.path.join(self._plugins_dir, name, "plugin.json")

    def _config_path(self, name: str) -> str:
        return os.path.join(self._plugin_data_root, name, "config.json")

    def _iter_plugin_dirs(self) -> List[str]:
        """Return directory names that have a plugin.py inside."""
        result = []
        try:
            for entry in sorted(os.listdir(self._plugins_dir)):
                if entry.startswith("_") or entry.startswith("."):
                    continue
                plugin_dir = os.path.join(self._plugins_dir, entry)
                if os.path.isdir(plugin_dir) and os.path.isfile(
                    os.path.join(plugin_dir, "plugin.py")
                ):
                    result.append(entry)
        except Exception as e:
            logger.error(f"[PluginAdmin] _iter_plugin_dirs: {e}")
        return result

    # ---- agent tools ----

    @tool(
        name="plugin_admin",
        level="extended",
        description=(
            "List all plugins. Returns name, display_name, version, type, "
            "enabled (bool), description, and tags for every plugin found on disk."
        ),
    )
    def list_plugins(self) -> Dict:
        """List all plugins discovered on disk."""
        try:
            plugins = []
            for dir_name in self._iter_plugin_dirs():
                manifest = _read_json(self._manifest_path(dir_name)) or {}
                plugins.append({
                    "name": manifest.get("name", dir_name),
                    "dir_name": dir_name,
                    "display_name": manifest.get("display_name", dir_name),
                    "version": manifest.get("version", "?"),
                    "type": manifest.get("type", "?"),
                    "enabled": manifest.get("enabled", True),
                    "description": manifest.get("description", ""),
                    "tags": manifest.get("tags", []),
                    "node_scope": manifest.get("node_scope", "all"),
                })
            return {"plugins": plugins, "count": len(plugins)}
        except Exception as e:
            logger.error(f"[PluginAdmin] list_plugins error: {e}")
            return {"error": str(e)}

    @tool(
        name="plugin_admin",
        level="extended",
        description=(
            "Enable a plugin by name. Writes enabled=true to the plugin's "
            "plugin.json and triggers a hot-reload. "
            "Parameter: name (str) — the plugin's directory/name, e.g. 'websearch'."
        ),
    )
    def enable_plugin(self, name: str) -> Dict:
        """Enable a plugin."""
        try:
            manifest_path = self._manifest_path(name)
            if not os.path.isfile(manifest_path):
                return {"error": f"Plugin '{name}' not found (no plugin.json)"}

            manifest = _read_json(manifest_path) or {}
            if manifest.get("enabled") is True:
                return {"ok": True, "message": f"Plugin '{name}' is already enabled", "changed": False}

            manifest["enabled"] = True
            if not _write_json(manifest_path, manifest):
                return {"error": f"Failed to write plugin.json for '{name}'"}

            _touch_reload_ts(self._plugins_dir)
            logger.info(f"[PluginAdmin] Enabled plugin '{name}'")
            return {"ok": True, "message": f"Plugin '{name}' enabled. Hot-reload triggered.", "changed": True}
        except Exception as e:
            logger.error(f"[PluginAdmin] enable_plugin({name!r}) error: {e}")
            return {"error": str(e)}

    @tool(
        name="plugin_admin",
        level="extended",
        description=(
            "Disable a plugin by name. Writes enabled=false to the plugin's "
            "plugin.json and triggers a hot-reload so the plugin is unloaded "
            "from memory immediately. "
            "Parameter: name (str) — the plugin's directory/name, e.g. 'websearch'."
        ),
    )
    def disable_plugin(self, name: str) -> Dict:
        """Disable a plugin."""
        try:
            if name == "plugin_admin":
                return {"error": "Cannot disable plugin_admin itself"}

            manifest_path = self._manifest_path(name)
            if not os.path.isfile(manifest_path):
                return {"error": f"Plugin '{name}' not found (no plugin.json)"}

            manifest = _read_json(manifest_path) or {}
            if manifest.get("enabled") is False:
                return {"ok": True, "message": f"Plugin '{name}' is already disabled", "changed": False}

            manifest["enabled"] = False
            if not _write_json(manifest_path, manifest):
                return {"error": f"Failed to write plugin.json for '{name}'"}

            _touch_reload_ts(self._plugins_dir)
            logger.info(f"[PluginAdmin] Disabled plugin '{name}'")
            return {"ok": True, "message": f"Plugin '{name}' disabled. Hot-reload triggered.", "changed": True}
        except Exception as e:
            logger.error(f"[PluginAdmin] disable_plugin({name!r}) error: {e}")
            return {"error": str(e)}

    @tool(
        name="plugin_admin",
        level="extended",
        description=(
            "Get a plugin's current configuration. Returns the config schema "
            "(with defaults) merged with any user-saved values, plus the full "
            "schema definition. "
            "Parameter: name (str) — the plugin's directory/name."
        ),
    )
    def get_plugin_config(self, name: str) -> Dict:
        """Get a plugin's current configuration."""
        try:
            manifest_path = self._manifest_path(name)
            if not os.path.isfile(manifest_path):
                return {"error": f"Plugin '{name}' not found (no plugin.json)"}

            manifest = _read_json(manifest_path) or {}
            schema: Dict[str, Any] = manifest.get("config_schema") or {}

            # Start with schema defaults
            config_values: Dict[str, Any] = {}
            for key, field in schema.items():
                if isinstance(field, dict) and "default" in field:
                    config_values[key] = field["default"]

            # Overlay with persisted user config
            persisted = _read_json(self._config_path(name)) or {}
            config_values.update(persisted)

            # Mask secret fields
            display_values = {}
            for key, val in config_values.items():
                field_def = schema.get(key, {})
                if isinstance(field_def, dict) and field_def.get("secret"):
                    display_values[key] = "***" if val else ""
                else:
                    display_values[key] = val

            return {
                "name": name,
                "config": display_values,
                "schema": schema,
                "has_persisted": bool(persisted),
            }
        except Exception as e:
            logger.error(f"[PluginAdmin] get_plugin_config({name!r}) error: {e}")
            return {"error": str(e)}

    @tool(
        name="plugin_admin",
        level="extended",
        description=(
            "Set one or more configuration values for a plugin. The values are "
            "written to data/plugins/{name}/config.json and a hot-reload is "
            "triggered so the plugin picks up the new config immediately. "
            "Parameters: name (str), config (dict) — key-value pairs to set. "
            "Example: set_plugin_config('websearch', {'max_results': 20})"
        ),
    )
    def set_plugin_config(self, name: str, config: Dict[str, Any]) -> Dict:
        """Set one or more config values for a plugin."""
        try:
            manifest_path = self._manifest_path(name)
            if not os.path.isfile(manifest_path):
                return {"error": f"Plugin '{name}' not found (no plugin.json)"}

            if not isinstance(config, dict):
                return {"error": "config must be a dict of key-value pairs"}

            # Load existing persisted config, then merge
            config_path = self._config_path(name)
            existing = _read_json(config_path) or {}
            existing.update(config)

            if not _write_json(config_path, existing):
                return {"error": f"Failed to write config for '{name}'"}

            _touch_reload_ts(self._plugins_dir)
            logger.info(f"[PluginAdmin] Updated config for '{name}': keys={list(config.keys())}")
            return {
                "ok": True,
                "message": f"Config for '{name}' updated. Hot-reload triggered.",
                "updated_keys": list(config.keys()),
            }
        except Exception as e:
            logger.error(f"[PluginAdmin] set_plugin_config({name!r}) error: {e}")
            return {"error": str(e)}

    @tool(
        name="plugin_admin",
        level="extended",
        description=(
            "Trigger a hot-reload of all plugins. This touches plugins/.reload_ts "
            "so that the AgentRunner re-evaluates every plugin's enabled state: "
            "newly disabled plugins are unloaded, newly enabled ones are loaded. "
            "No parameters required."
        ),
    )
    def reload_plugins(self) -> Dict:
        """Trigger a hot-reload of all plugins."""
        try:
            _touch_reload_ts(self._plugins_dir)
            logger.info("[PluginAdmin] Hot-reload triggered by agent")
            return {"ok": True, "message": "Hot-reload triggered. Changes will take effect within the next runner loop tick."}
        except Exception as e:
            logger.error(f"[PluginAdmin] reload_plugins error: {e}")
            return {"error": str(e)}

    @tool(
        name="plugin_admin",
        level="extended",
        description=(
            "Show detailed info for a single plugin: metadata, config schema, "
            "registered tools, hooks, and current enabled status. "
            "Parameter: name (str) — the plugin's directory/name."
        ),
    )
    def get_plugin_info(self, name: str) -> Dict:
        """Get detailed metadata for a single plugin."""
        try:
            manifest_path = self._manifest_path(name)
            if not os.path.isfile(manifest_path):
                return {"error": f"Plugin '{name}' not found (no plugin.json)"}

            manifest = _read_json(manifest_path) or {}

            # Check if a persisted config exists
            has_config = os.path.isfile(self._config_path(name))

            return {
                "name": manifest.get("name", name),
                "dir_name": name,
                "display_name": manifest.get("display_name", name),
                "version": manifest.get("version", "?"),
                "type": manifest.get("type", "?"),
                "enabled": manifest.get("enabled", True),
                "node_scope": manifest.get("node_scope", "all"),
                "description": manifest.get("description", ""),
                "author": manifest.get("author", ""),
                "tags": manifest.get("tags", []),
                "tools": [t.get("name") for t in manifest.get("tools", [])],
                "hooks": manifest.get("hooks", []),
                "dependencies": manifest.get("dependencies", {}),
                "has_config": has_config,
                "config_schema": manifest.get("config_schema", {}),
            }
        except Exception as e:
            logger.error(f"[PluginAdmin] get_plugin_info({name!r}) error: {e}")
            return {"error": str(e)}
