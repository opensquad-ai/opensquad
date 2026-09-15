"""MCP server configuration (central / per-agent / global) for the Launcher management API.

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
    AGENTS_DIR,
    _processes,
)
from opensquad.system_config import syscfg


class McpMixin:
    """MCP server configuration (central / per-agent / global)."""

    def _handle_get_mcp_central(self):
        """GET /api/mcp/config — Read the central (unified) MCP config from data/mcp_config.json"""
        central_path = syscfg.workspace_data_dir("mcp_config.json")
        if not os.path.isfile(central_path):
            # Migration: if central config doesn't exist yet, try to build it from the first agent
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agents")
            if hasattr(self, "_find_agents_dir"):
                pass
            merged = {}
            if os.path.isdir(AGENTS_DIR):
                for dname in sorted(os.listdir(AGENTS_DIR)):
                    agent_mcp = os.path.join(AGENTS_DIR, dname, "mcp_config.json")
                    if os.path.isfile(agent_mcp):
                        try:
                            with open(agent_mcp, encoding="utf-8-sig") as f:
                                data = json.load(f)
                            for k, v in (data.get("mcpServers") or {}).items():
                                if k not in merged:
                                    merged[k] = v
                        except Exception:
                            pass
                        break  # Use the first agent as seed
            if merged:
                # Write the central config as seed
                os.makedirs(os.path.dirname(central_path), exist_ok=True)
                with open(central_path, "w", encoding="utf-8") as f:
                    json.dump({"mcpServers": merged}, f, ensure_ascii=False, indent=2)
            return self._send_json({"mcpServers": merged})
        try:
            with open(central_path, encoding="utf-8-sig") as f:
                data = json.load(f)
            return self._send_json({"mcpServers": data.get("mcpServers", {})})
        except Exception as e:
            return self._send_json({"error": f"Failed to read central mcp_config.json: {e}"}, 500)

    def _handle_put_mcp_central(self, body: dict):
        """PUT /api/mcp/config — Write the central MCP config and sync to all agents"""
        mcp_servers = body.get("mcpServers")
        if mcp_servers is None:
            return self._send_json({"error": "Missing 'mcpServers' in body"}, 400)
        central_path = syscfg.workspace_data_dir("mcp_config.json")
        os.makedirs(os.path.dirname(central_path), exist_ok=True)
        try:
            payload = {"mcpServers": mcp_servers}
            # Write central config
            with open(central_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            # Sync to all agents for backward compatibility
            synced = []
            if os.path.isdir(AGENTS_DIR):
                for dname in os.listdir(AGENTS_DIR):
                    agent_dir = os.path.join(AGENTS_DIR, dname)
                    if os.path.isdir(agent_dir):
                        agent_mcp = os.path.join(agent_dir, "mcp_config.json")
                        try:
                            with open(agent_mcp, "w", encoding="utf-8") as f:
                                json.dump(payload, f, ensure_ascii=False, indent=2)
                            synced.append(dname)
                        except Exception:
                            pass
            # Restart running agents to pick up new MCP config immediately
            restarted = []
            for name, ap in list(_processes.items()):
                if ap.is_alive():
                    try:
                        ap.restart()
                        restarted.append(name)
                    except Exception:
                        pass
            return self._send_json(
                {
                    "ok": True,
                    "message": f"Central MCP config saved, synced to {len(synced)} agents",
                    "synced_agents": synced,
                    "restarted_agents": restarted,
                }
            )
        except Exception as e:
            return self._send_json({"error": f"Failed to write central mcp_config.json: {e}"}, 500)

    def _handle_get_mcp(self, name: str):
        """GET /api/agents/{name}/mcp — Read mcp_config.json"""
        agent_dir = os.path.join(AGENTS_DIR, name)
        if not os.path.isdir(agent_dir):
            return self._send_json({"error": f"Agent '{name}' not found"}, 404)
        mcp_path = os.path.join(agent_dir, "mcp_config.json")
        if not os.path.isfile(mcp_path):
            return self._send_json({"agent": name, "mcpServers": {}})
        try:
            with open(mcp_path, encoding="utf-8-sig") as f:
                data = json.load(f)
            return self._send_json({"agent": name, "mcpServers": data.get("mcpServers", {})})
        except Exception as e:
            return self._send_json({"error": f"Failed to read mcp_config.json: {e}"}, 500)

    def _handle_put_mcp(self, name: str, body: dict):
        """PUT /api/agents/{name}/mcp — Write mcp_config.json (pass mcpServers object in body)"""
        agent_dir = os.path.join(AGENTS_DIR, name)
        if not os.path.isdir(agent_dir):
            return self._send_json({"error": f"Agent '{name}' not found"}, 404)
        mcp_servers = body.get("mcpServers")
        if mcp_servers is None:
            return self._send_json({"error": "Missing 'mcpServers' in body"}, 400)
        mcp_path = os.path.join(agent_dir, "mcp_config.json")
        try:
            with open(mcp_path, "w", encoding="utf-8") as f:
                json.dump({"mcpServers": mcp_servers}, f, ensure_ascii=False, indent=2)
            # Restart running agent to pick up new MCP config immediately
            restarted = False
            if name in _processes and _processes[name].is_alive():
                try:
                    _processes[name].restart()
                    restarted = True
                except Exception:
                    pass
            return self._send_json({"ok": True, "message": f"MCP config saved for '{name}'", "restarted": restarted})
        except Exception as e:
            return self._send_json({"error": f"Failed to write mcp_config.json: {e}"}, 500)

    def _handle_get_mcp_global(self):
        """GET /api/mcp/global — Read the global enable/disable state for each MCP server"""
        global_path = syscfg.workspace_data_dir("mcp_global.json")
        if not os.path.isfile(global_path):
            return self._send_json({"servers": {}})
        try:
            with open(global_path, encoding="utf-8-sig") as f:
                data = json.load(f)
            return self._send_json({"servers": data.get("servers", {})})
        except Exception as e:
            return self._send_json({"error": f"Failed to read mcp_global.json: {e}"}, 500)

    def _handle_put_mcp_server_global(self, server_name: str, enabled: bool):
        """PUT /api/mcp/global/servers/{name}/enable|disable — Set the global toggle for a single server"""
        data_dir = syscfg.workspace_data_dir()
        os.makedirs(data_dir, exist_ok=True)
        global_path = os.path.join(data_dir, "mcp_global.json")
        try:
            if os.path.isfile(global_path):
                with open(global_path, encoding="utf-8-sig") as f:
                    data = json.load(f)
            else:
                data = {}
            servers = data.get("servers", {})
            servers[server_name] = {"enabled": enabled}
            data["servers"] = servers
            with open(global_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            action = "enabled" if enabled else "disabled"
            return self._send_json(
                {
                    "ok": True,
                    "server": server_name,
                    "enabled": enabled,
                    "message": f"MCP server '{server_name}' globally {action}",
                }
            )
        except Exception as e:
            return self._send_json({"error": f"Failed to write mcp_global.json: {e}"}, 500)
