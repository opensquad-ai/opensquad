"""
System Handler Mixin — system/service/session/MCP/skill/card/workspace HTTP handler methods.

Extracted from _launcher_api/__init__.py to reduce its size.
This mixin provides all system-level handler methods for the ManagementHandler class.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time

logger = logging.getLogger(__name__)


class SystemHandlerMixin:
    """Mixin providing system-level handler methods.

    Used by ManagementHandler in _launcher_api.__init__.
    All methods rely on self.state (HandlerState) and self._send_json().
    """

    # ── Plugin service handlers ────────────────────────────────────────

    def _handle_services_manage(self):
        """GET /api/services/manage — enriched service list for the Service Manager UI.
        Returns ALL discovered services (from plugin.json) merged with runtime status."""
        # 1. Discover all plugin services from plugin.json
        discovered = self.state.disc_plug_svcs(self.state.plugins_dir)

        # 2. Build result merging discovery info with runtime status
        services = []
        for info in discovered:
            pid = info["plugin_id"]
            if pid in self.state.plug_svcs:
                psp = self.state.plug_svcs[pid]
                status = psp.get_status()
            else:
                status = {
                    "plugin_id": pid,
                    "display_name": info.get("display_name", pid),
                    "plugin_type": info.get("plugin_type", "tool"),
                    "alive": False,
                    "pid": None,
                    "port": self.state.res_disc_port(info),
                    "host": info.get("service_cfg", {}).get("host", "0.0.0.0"),
                    "auto_start": self.state.syscfg.is_service_enabled(pid),
                    "should_run": False,
                    "restart_count": 0,
                    "max_restarts": 5,
                    "started_at": None,
                    "uptime_seconds": None,
                    "health_endpoint": info["service_cfg"].get("health_endpoint", "/health"),
                    "health_ok": None,
                    "service_cfg": info["service_cfg"],
                }
            services.append(status)
        return self._send_json({"services": services})

    def _handle_list_plugin_services(self):
        """GET /api/plugin-services — list all plugin services."""
        discovered = self.state.disc_plug_svcs(self.state.plugins_dir)
        services = []
        for info in discovered:
            pid = info["plugin_id"]
            if pid in self.state.plug_svcs:
                psp = self.state.plug_svcs[pid]
                status = psp.get_status()
            else:
                status = {
                    "plugin_id": pid,
                    "display_name": info.get("display_name", pid),
                    "plugin_type": info.get("plugin_type", "tool"),
                    "alive": False,
                    "pid": None,
                    "port": self.state.res_disc_port(info),
                    "host": info.get("service_cfg", {}).get("host", "0.0.0.0"),
                    "auto_start": self.state.syscfg.is_service_enabled(pid),
                    "should_run": False,
                    "restart_count": 0,
                    "max_restarts": 5,
                    "started_at": None,
                    "uptime_seconds": None,
                    "health_endpoint": info["service_cfg"].get("health_endpoint", "/health"),
                    "health_ok": None,
                    "service_cfg": info["service_cfg"],
                }
            services.append(status)
        return self._send_json({"services": services})

    def _handle_runtime_list(self):
        """GET /api/runtime/list — list all managed processes."""
        cleanup_result = self.state.cln_reg(force_kill=False)
        managed_agents = []
        for ap in list(self.state.procesos.values()):
            managed_agents.append(
                {
                    "agent_id": ap.agent_id,
                    "agent_name": ap.agent_name,
                    "pid": ap.process.pid if ap.process and ap.process.poll() is None else None,
                    "port": ap.actual_port,
                    "alive": ap.is_alive(),
                    "should_run": ap.should_run,
                }
            )
        managed_plugins = []
        for psp in list(self.state.plug_svcs.values()):
            managed_plugins.append(
                {
                    "plugin_id": psp.plugin_id,
                    "pid": psp.process.pid if psp.process and psp.process.poll() is None else None,
                    "port": psp.port,
                    "alive": psp.is_alive(),
                    "should_run": psp.should_run,
                }
            )
        return self._send_json(
            {
                "runtime_registry": cleanup_result.get("remaining", []),
                "cleanup": {
                    "cleaned": cleanup_result.get("cleaned", 0),
                    "killed": cleanup_result.get("killed", 0),
                },
                "managed": {
                    "agents": managed_agents,
                    "plugins": managed_plugins,
                },
            }
        )

    def _handle_plugin_service_start(self, plugin_id: str):
        """POST /api/plugin-services/{id}/start — start a plugin service."""
        if plugin_id not in self.state.plug_svcs:
            return self._send_json({"error": f"Plugin service '{plugin_id}' not found"}, 404)
        psp = self.state.plug_svcs[plugin_id]
        # Idempotent: already running or in `starting` returns 200 (not 400)
        # so the UI doesn't alert an error on a duplicate Start click. See
        # launcher_main.py:_handle_plugin_service_start for full rationale.
        if psp.is_alive() or psp.state == "starting":
            return self._send_json(
                {
                    "message": f"{plugin_id} already running",
                    "already_running": True,
                    "state": psp.state,
                    "pid": psp.process.pid if psp.process else None,
                    "port": psp.port,
                }
            )
        self._set_service_enabled_in_config(plugin_id, True)
        psp.port = psp._resolve_port()
        psp.start()
        pid_val = psp.process.pid if psp.process else None
        return self._send_json({"message": f"{plugin_id} started", "pid": pid_val, "port": psp.port})

    def _handle_plugin_service_stop(self, plugin_id: str):
        """POST /api/plugin-services/{id}/stop — stop a plugin service."""
        if plugin_id not in self.state.plug_svcs:
            return self._send_json({"error": f"Plugin service '{plugin_id}' not found"}, 404)
        psp = self.state.plug_svcs[plugin_id]
        if not psp.is_alive():
            psp.should_run = False
            self._set_service_enabled_in_config(plugin_id, False)
            return self._send_json({"message": f"{plugin_id} already stopped"})
        psp.stop()
        self._set_service_enabled_in_config(plugin_id, False)
        return self._send_json({"message": f"{plugin_id} stopped"})

    def _handle_plugin_service_auto_start(self, plugin_id: str, body: dict):
        """PUT /api/plugin-services/{id}/auto-start — toggle auto-start."""
        enabled = body.get("enabled", True) if isinstance(body, dict) else True
        self._set_service_enabled_in_config(plugin_id, enabled)
        return self._send_json({"ok": True, "plugin_id": plugin_id, "auto_start": enabled})

    def _set_service_enabled_in_config(self, plugin_id: str, enabled: bool):
        """Persist plugin service enabled state to system_config.json."""
        try:
            sys_cfg_path = self.state.syscfg.workspace_config_path()
            with open(sys_cfg_path, encoding="utf-8") as f:
                full_cfg = json.load(f)
            if "services" not in full_cfg:
                full_cfg["services"] = {}
            if plugin_id not in full_cfg["services"]:
                full_cfg["services"][plugin_id] = {}
            full_cfg["services"][plugin_id]["enabled"] = enabled
            with open(sys_cfg_path, "w", encoding="utf-8") as f:
                json.dump(full_cfg, f, indent=2, ensure_ascii=False)
            from opensquad import system_config as _syscfg_mod

            _syscfg_mod._cache = None
        except Exception as e:
            self.state.logger.warning(f"[Launcher] Failed to sync services.{plugin_id}.enabled: {e}", exc_info=True)

    def _handle_shutdown(self, body: dict):
        """POST /api/shutdown — gracefully shutdown all managed processes."""
        timeout = body.get("timeout", 10) if isinstance(body, dict) else 10
        stopped = 0
        for _name, ap in list(self.state.procesos.items()):
            if ap.is_alive():
                try:
                    ap.should_run = False
                    if ap.process and ap.process.poll() is None:
                        ap.process.terminate()
                        try:
                            ap.process.wait(timeout=timeout)
                        except subprocess.TimeoutExpired:
                            ap.process.kill()
                        stopped += 1
                except (OSError, ValueError):
                    pass
        for _pid, psp in list(self.state.plug_svcs.items()):
            if psp.is_alive():
                try:
                    psp.stop()
                    stopped += 1
                except (OSError, ValueError):
                    pass
        return self._send_json({"message": f"Shutdown: {stopped} processes stopped", "ok": True})

    def _handle_plugin_service_restart(self, plugin_id: str):
        """POST /api/plugin-services/{id}/restart — restart a plugin service."""
        if plugin_id not in self.state.plug_svcs:
            return self._send_json({"error": f"Plugin service '{plugin_id}' not found"}, 404)
        psp = self.state.plug_svcs[plugin_id]
        if psp.is_alive():
            psp.stop()
            for _ in range(50):
                if not psp.is_alive():
                    break
                time.sleep(0.1)
        psp.port = psp._resolve_port()
        psp.should_run = True
        psp.start()
        pid_val = psp.process.pid if psp.process else None
        return self._send_json({"message": f"{plugin_id} restarted", "pid": pid_val, "port": psp.port})

    def _handle_plugin_service_logs(self, plugin_id: str, lines: int = 200):
        """GET /api/plugin-services/{id}/logs — get plugin service logs."""
        if plugin_id not in self.state.plug_svcs:
            return self._send_json({"error": f"Plugin service '{plugin_id}' not found"}, 404)
        logs = self.state.plug_svcs[plugin_id].get_logs(lines)
        return self._send_json({"plugin_id": plugin_id, "logs": logs, "total": len(logs)})

    def _handle_runtime_cleanup(self, body: dict):
        """POST /api/runtime/cleanup — clean up runtime registry."""
        force_kill = body.get("force_kill", False) if isinstance(body, dict) else False
        result = self.state.cln_reg(force_kill=force_kill)
        return self._send_json(result)

    # ── Session handlers ───────────────────────────────────────────────

    def _get_session_reader(self, agent_id: str):
        """Get a session reader for the given agent."""
        try:
            import importlib.util as _ilu

            # __file__ is in _launcher_api/, go up to opensquad/ then into gateway/
            _mod_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "gateway",
                "backend",
                "app",
                "ai_web",
                "agent_sessions.py",
            )
            _spec = _ilu.spec_from_file_location("opensquad._agent_sessions_standalone", _mod_path)
            _mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            return _mod.get_reader(agent_id)
        except Exception as e:
            self.state.logger.warning(f"[Launcher] Failed to get session reader for {agent_id}: {e}", exc_info=True)
            return None

    def _handle_session_list(self, agent_id: str):
        """GET /api/sessions/{agent_id}/list — list agent sessions."""
        reader = self._get_session_reader(agent_id)
        if reader is None:
            return self._send_json({"error": f"Agent not found: {agent_id}"}, 404)
        try:
            sessions = reader.get_session_list()
            current_id = reader.get_current_session_id()
        except Exception as e:
            import httpx

            if isinstance(e, httpx.TimeoutException):
                return self._send_json(
                    {"error": "Agent session request timed out", "sessions": [], "current_session_id": None}, 504
                )
            return self._send_json({"error": f"Failed to get sessions: {e!s}"}, 500)
        return self._send_json({"sessions": sessions, "current_session_id": current_id})

    def _handle_session_current(self, agent_id: str, offset: int = 0, limit: int = 50):
        """GET /api/sessions/{agent_id}/current — get current session."""
        reader = self._get_session_reader(agent_id)
        if reader is None:
            return self._send_json({"error": f"Agent not found: {agent_id}"}, 404)
        try:
            current_id = reader.get_current_session_id()
            session = reader.get_session_history_paged(current_id, offset, limit)
        except Exception as e:
            import httpx

            if isinstance(e, httpx.TimeoutException):
                return self._send_json({"error": "Agent session request timed out"}, 504)
            return self._send_json({"error": f"Failed to get current session: {e!s}"}, 500)
        return self._send_json({"current_session_id": current_id, "session": session})

    def _handle_session_paged(self, agent_id: str, session_id: str, offset: int = 0, limit: int = 50):
        """GET /api/sessions/{agent_id}/{session_id}/paged — get session history paged."""
        reader = self._get_session_reader(agent_id)
        if reader is None:
            return self._send_json({"error": f"Agent not found: {agent_id}"}, 404)
        try:
            session = reader.get_session_history_paged(session_id, offset, limit)
        except Exception as e:
            import httpx

            if isinstance(e, httpx.TimeoutException):
                return self._send_json({"error": "Agent session request timed out"}, 504)
            return self._send_json({"error": f"Failed to get session: {e!s}"}, 500)
        if session is None:
            return self._send_json({"error": f"Session not found: {session_id}"}, 404)
        return self._send_json({"session": session})

    def _handle_session_get(self, agent_id: str, session_id: str):
        """GET /api/sessions/{agent_id}/{session_id} — get a specific session."""
        reader = self._get_session_reader(agent_id)
        if reader is None:
            return self._send_json({"error": f"Agent not found: {agent_id}"}, 404)
        try:
            session = reader.get_session_history(session_id)
        except Exception as e:
            import httpx

            if isinstance(e, httpx.TimeoutException):
                return self._send_json({"error": "Agent session request timed out"}, 504)
            return self._send_json({"error": f"Failed to get session: {e!s}"}, 500)
        if session is None:
            return self._send_json({"error": f"Session not found: {session_id}"}, 404)
        return self._send_json({"session": session})

    def _handle_session_delete(self, agent_id: str, session_id: str):
        """DELETE /api/sessions/{agent_id}/{session_id}/delete — delete a session."""
        reader = self._get_session_reader(agent_id)
        if reader is None:
            return self._send_json({"error": f"Agent not found: {agent_id}"}, 404)
        ok = reader.delete_session(session_id)
        return self._send_json({"ok": ok})

    def _handle_session_rename(self, agent_id: str, session_id: str, body: bytes | str | None = None):
        """POST /api/sessions/{agent_id}/{session_id}/rename — rename a session."""
        reader = self._get_session_reader(agent_id)
        if reader is None:
            return self._send_json({"error": f"Agent not found: {agent_id}"}, 404)
        try:
            import json as _json

            raw = body if isinstance(body, (bytes, bytearray, str)) else b""
            data = _json.loads(raw or b"{}") if raw else {}
            title = (data.get("title") or "").strip()
        except Exception:
            return self._send_json({"error": "Invalid JSON body"}, 400)
        if not title:
            return self._send_json({"error": "Title is required"}, 400)
        rename = getattr(reader, "rename_session", None)
        if rename is None:
            return self._send_json({"error": "Rename not supported"}, 501)
        ok = rename(session_id, title)
        if not ok:
            return self._send_json({"error": f"Session not found: {session_id}", "ok": False}, 404)
        return self._send_json({"ok": True, "session_id": session_id, "title": title})

    # ── MCP handlers ───────────────────────────────────────────────────

    def _handle_get_mcp_central(self):
        """GET /api/mcp/config — get central MCP config."""
        central_path = self.state.syscfg.workspace_data_dir("mcp_config.json")
        if not os.path.isfile(central_path):
            merged = {}
            if os.path.isdir(self.state.agents_dir):
                for dname in sorted(os.listdir(self.state.agents_dir)):
                    agent_mcp = os.path.join(self.state.agents_dir, dname, "mcp_config.json")
                    if os.path.isfile(agent_mcp):
                        try:
                            with open(agent_mcp, encoding="utf-8-sig") as f:
                                data = json.load(f)
                            for k, v in (data.get("mcpServers") or {}).items():
                                if k not in merged:
                                    merged[k] = v
                        except (OSError, ValueError):
                            pass
                        break
            if merged:
                os.makedirs(os.path.dirname(central_path), exist_ok=True)
                with open(central_path, "w", encoding="utf-8") as f:
                    json.dump({"mcpServers": merged}, f, ensure_ascii=False, indent=2)
            return self._send_json({"mcpServers": merged})
        try:
            with open(central_path, encoding="utf-8-sig") as f:
                data = json.load(f)
            return self._send_json({"mcpServers": data.get("mcpServers", {})})
        except (OSError, ValueError) as e:
            return self._send_json({"error": f"Failed to read central mcp_config.json: {e}"}, 500)

    def _handle_put_mcp_central(self, body: dict):
        """PUT /api/mcp/config — update central MCP config."""
        mcp_servers = body.get("mcpServers")
        if mcp_servers is None:
            return self._send_json({"error": "Missing 'mcpServers' in body"}, 400)
        central_path = self.state.syscfg.workspace_data_dir("mcp_config.json")
        os.makedirs(os.path.dirname(central_path), exist_ok=True)
        try:
            payload = {"mcpServers": mcp_servers}
            with open(central_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            synced = []
            if os.path.isdir(self.state.agents_dir):
                for dname in os.listdir(self.state.agents_dir):
                    agent_dir = os.path.join(self.state.agents_dir, dname)
                    if os.path.isdir(agent_dir):
                        agent_mcp = os.path.join(agent_dir, "mcp_config.json")
                        try:
                            with open(agent_mcp, "w", encoding="utf-8") as f:
                                json.dump(payload, f, ensure_ascii=False, indent=2)
                            synced.append(dname)
                        except (OSError, ValueError):
                            pass
            restarted = []
            for name, ap in list(self.state.procesos.items()):
                if ap.is_alive():
                    try:
                        ap.restart()
                        restarted.append(name)
                    except (OSError, ValueError):
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
        """GET /api/agents/{name}/mcp — get agent MCP config."""
        agent_dir = os.path.join(self.state.agents_dir, name)
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
        """PUT /api/agents/{name}/mcp — update agent MCP config."""
        agent_dir = os.path.join(self.state.agents_dir, name)
        if not os.path.isdir(agent_dir):
            return self._send_json({"error": f"Agent '{name}' not found"}, 404)
        mcp_servers = body.get("mcpServers")
        if mcp_servers is None:
            return self._send_json({"error": "Missing 'mcpServers' in body"}, 400)
        mcp_path = os.path.join(agent_dir, "mcp_config.json")
        try:
            with open(mcp_path, "w", encoding="utf-8") as f:
                json.dump({"mcpServers": mcp_servers}, f, ensure_ascii=False, indent=2)
            # Restart agent if running
            if name in self.state.procesos:
                ap = self.state.procesos[name]
                if ap.is_alive():
                    ap.restart()
            return self._send_json({"ok": True, "message": f"MCP config saved for '{name}'"})
        except Exception as e:
            return self._send_json({"error": f"Failed to write mcp_config.json: {e}"}, 500)

    def _handle_get_mcp_global(self):
        """GET /api/mcp/global — get global MCP config."""
        global_path = self.state.syscfg.workspace_data_dir("mcp_global.json")
        if not os.path.isfile(global_path):
            return self._send_json({"mcpServers": {}})
        try:
            with open(global_path, encoding="utf-8-sig") as f:
                data = json.load(f)
            return self._send_json({"mcpServers": data.get("mcpServers", {})})
        except (OSError, ValueError) as e:
            return self._send_json({"error": f"Failed to read global mcp config: {e}"}, 500)

    def _handle_put_mcp_server_global(self, srv: str, enabled: bool):
        """PUT /api/mcp/global/servers/{name}/enable|disable — toggle global MCP server."""
        global_path = self.state.syscfg.workspace_data_dir("mcp_global.json")
        data = {}
        if os.path.isfile(global_path):
            try:
                with open(global_path, encoding="utf-8-sig") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                pass
        servers = data.get("mcpServers", {})
        if srv not in servers:
            return self._send_json({"error": f"MCP server '{srv}' not found"}, 404)
        if "disabled" not in servers[srv]:
            servers[srv]["disabled"] = not enabled
        else:
            servers[srv]["disabled"] = not enabled
        try:
            with open(global_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return self._send_json({"ok": True, "server": srv, "enabled": enabled})
        except Exception as e:
            return self._send_json({"error": f"Failed to write global mcp config: {e}"}, 500)

    # ── Skill handlers ─────────────────────────────────────────────────

    def _handle_list_skills(self):
        """GET /api/skills — list all skills."""
        skills = []
        if not os.path.isdir(self.state.skills_dir):
            return self._send_json({"skills": []})
        for skill_name in sorted(os.listdir(self.state.skills_dir)):
            skill_dir = os.path.join(self.state.skills_dir, skill_name)
            if not os.path.isdir(skill_dir):
                continue
            skill_json_path = os.path.join(skill_dir, "skill.json")
            skill_md_path = os.path.join(skill_dir, "SKILL.md")
            if os.path.isfile(skill_json_path):
                try:
                    with open(skill_json_path, encoding="utf-8") as f:
                        meta = json.load(f)
                except (OSError, ValueError):
                    meta = {}
                skills.append(
                    {
                        "name": meta.get("name", skill_name),
                        "display_name": meta.get("name", skill_name),
                        "version": meta.get("version", ""),
                        "description": meta.get("description", ""),
                        "author": meta.get("author", ""),
                        "license": meta.get("license", ""),
                        "keywords": meta.get("keywords", []),
                        "requires": meta.get("requires", {}),
                        "install": meta.get("install", []),
                        "entry": meta.get("entry", {}),
                        "has_skill_json": True,
                        "dir": skill_name,
                    }
                )
            elif os.path.isfile(skill_md_path):
                fm = {}
                try:
                    with open(skill_md_path, encoding="utf-8") as f:
                        content = f.read()
                    if content.startswith("---"):
                        end = content.find("\n---", 3)
                        if end != -1:
                            fm_text = content[3:end].strip()
                            for line in fm_text.splitlines():
                                if ":" in line:
                                    k, _, v = line.partition(":")
                                    fm[k.strip()] = v.strip()
                except (OSError, ValueError):
                    pass
                skills.append(
                    {
                        "name": fm.get("name", skill_name),
                        "display_name": fm.get("name", skill_name),
                        "version": "",
                        "description": fm.get("description", ""),
                        "author": "",
                        "license": "",
                        "keywords": [],
                        "requires": {},
                        "install": [],
                        "entry": {},
                        "has_skill_json": False,
                        "dir": skill_name,
                    }
                )
        return self._send_json({"skills": skills})

    def _handle_get_skill_source(self, name: str):
        """GET /api/skills/{name}/source — get skill source files."""
        if not re.match(r"^[a-zA-Z0-9_\-]+$", name):
            return self._send_json({"error": "Invalid skill name"}, 400)
        skill_dir = os.path.join(self.state.skills_dir, name)
        if not os.path.isdir(skill_dir):
            return self._send_json({"error": f"Skill '{name}' not found"}, 404)
        files_info = []
        for fname in sorted(os.listdir(skill_dir)):
            fpath = os.path.join(skill_dir, fname)
            if os.path.isfile(fpath):
                files_info.append({"name": fname, "size": os.path.getsize(fpath)})
        skill_md = ""
        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        if os.path.isfile(skill_md_path):
            try:
                with open(skill_md_path, encoding="utf-8") as f:
                    skill_md = f.read()
            except (OSError, ValueError):
                skill_md = "(Failed to read SKILL.md)"
        skill_json_data = None
        skill_json_path = os.path.join(skill_dir, "skill.json")
        if os.path.isfile(skill_json_path):
            try:
                with open(skill_json_path, encoding="utf-8") as f:
                    skill_json_data = json.load(f)
            except (OSError, ValueError):
                pass
        py_sources = {}
        other_sources = {}
        _TEXT_EXTS = {".py", ".md", ".txt", ".yaml", ".yml", ".json", ".toml", ".cfg", ".ini", ".sh", ".bat", ".ps1"}
        for fi in files_info:
            ext = os.path.splitext(fi["name"])[1].lower()
            fpath = os.path.join(skill_dir, fi["name"])
            if fi["name"] in ("SKILL.md", "skill.json"):
                continue
            if ext == ".py":
                try:
                    with open(fpath, encoding="utf-8") as f:
                        py_sources[fi["name"]] = f.read()
                except (OSError, ValueError):
                    py_sources[fi["name"]] = "(Failed to read)"
            elif ext in _TEXT_EXTS:
                try:
                    with open(fpath, encoding="utf-8") as f:
                        other_sources[fi["name"]] = f.read()
                except (OSError, ValueError):
                    other_sources[fi["name"]] = "(Failed to read)"
        return self._send_json(
            {
                "name": name,
                "files": files_info,
                "skill_md": skill_md,
                "skill_json": skill_json_data,
                "py_sources": py_sources,
                "other_sources": other_sources,
            }
        )

    # ── Role/Collab/Model card handlers ────────────────────────────────

    def _list_cards(self, dir_path: str) -> list:
        """List card files in a directory, returning name and metadata.

        Supports both .json (model cards) and .md (role/collab cards with YAML frontmatter).
        """
        cards = []
        if not os.path.isdir(dir_path):
            return cards
        for fname in sorted(os.listdir(dir_path)):
            fpath = os.path.join(dir_path, fname)
            if not os.path.isfile(fpath):
                continue
            if fname.endswith(".json"):
                # Model cards: JSON format
                try:
                    with open(fpath, encoding="utf-8") as f:
                        data = json.load(f)
                    cards.append(
                        {
                            "name": data.get("name", fname[:-5]),
                            "filename": fname,
                            "title": data.get("title", data.get("name", fname[:-5])),
                            "description": data.get("description", ""),
                            "api_protocol": data.get("api_protocol", ""),
                            "provider": data.get("provider", ""),
                            "model_name": data.get("model_name", ""),
                            "is_think": bool(data.get("is_think", False)),
                            "size": os.path.getsize(fpath),
                            "updated": os.path.getmtime(fpath),
                        }
                    )
                except (OSError, ValueError):
                    pass
            elif fname.endswith(".md"):
                # Role/Collab cards: Markdown with optional YAML frontmatter
                card_name = fname[:-3]
                try:
                    with open(fpath, encoding="utf-8") as f:
                        content = f.read()
                except Exception:
                    content = ""
                title = card_name
                description = ""
                if content.startswith("---"):
                    end = content.find("\n---", 3)
                    if end != -1:
                        fm_text = content[3:end].strip()
                        for line in fm_text.splitlines():
                            if ":" in line:
                                k, _, v = line.partition(":")
                                k = k.strip()
                                v = v.strip()
                                if k == "title":
                                    title = v
                                elif k == "description":
                                    description = v
                cards.append(
                    {
                        "name": card_name,
                        "filename": fname,
                        "title": title,
                        "description": description,
                        "size": os.path.getsize(fpath),
                        "updated": os.path.getmtime(fpath),
                    }
                )
        return cards

    def _handle_list_role_cards(self):
        """GET /api/role-cards — list role cards."""
        cards = self._list_cards(self.state.role_cards_dir)
        return self._send_json({"cards": cards})

    def _handle_get_role_card(self, card_name: str):
        """GET /api/role-cards/{name} — get a role card."""
        fpath = os.path.join(self.state.role_cards_dir, f"{card_name}.md")
        if not os.path.isfile(fpath):
            return self._send_json({"error": f"Role card '{card_name}' not found"}, 404)
        try:
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            return self._send_json({"name": card_name, "content": content})
        except (OSError, ValueError) as e:
            return self._send_json({"error": f"Failed to read role card: {e}"}, 500)

    def _handle_put_role_card(self, card_name: str, body: dict):
        """PUT /api/role-cards/{name} — save/update a role card."""
        fpath = os.path.join(self.state.role_cards_dir, f"{card_name}.md")
        try:
            os.makedirs(self.state.role_cards_dir, exist_ok=True)
            content = body.get("content", "")
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)
            return self._send_json({"ok": True, "message": f"Role card '{card_name}' saved"})
        except Exception as e:
            return self._send_json({"error": f"Failed to save role card: {e}"}, 500)

    def _handle_delete_role_card(self, card_name: str):
        """DELETE /api/role-cards/{name} — delete a role card."""
        fpath = os.path.join(self.state.role_cards_dir, f"{card_name}.md")
        if not os.path.isfile(fpath):
            return self._send_json({"error": f"Role card '{card_name}' not found"}, 404)
        try:
            os.remove(fpath)
            return self._send_json({"ok": True, "message": f"Role card '{card_name}' deleted"})
        except Exception as e:
            return self._send_json({"error": f"Failed to delete role card: {e}"}, 500)

    def _handle_list_collab_cards(self):
        """GET /api/collab-cards — list collaboration cards."""
        cards = self._list_cards(self.state.collab_cards_dir)
        return self._send_json({"cards": cards})

    def _handle_get_collab_card(self, card_name: str):
        """GET /api/collab-cards/{name} — get a collaboration card."""
        fpath = os.path.join(self.state.collab_cards_dir, f"{card_name}.md")
        if not os.path.isfile(fpath):
            return self._send_json({"error": f"Collab card '{card_name}' not found"}, 404)
        try:
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            return self._send_json({"name": card_name, "content": content})
        except (OSError, ValueError) as e:
            return self._send_json({"error": f"Failed to read collab card: {e}"}, 500)

    def _handle_put_collab_card(self, card_name: str, body: dict):
        """PUT /api/collab-cards/{name} — save/update a collaboration card."""
        fpath = os.path.join(self.state.collab_cards_dir, f"{card_name}.md")
        try:
            os.makedirs(self.state.collab_cards_dir, exist_ok=True)
            content = body.get("content", "")
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)
            return self._send_json({"ok": True, "message": f"Collab card '{card_name}' saved"})
        except Exception as e:
            return self._send_json({"error": f"Failed to save collab card: {e}"}, 500)

    def _handle_delete_collab_card(self, card_name: str):
        """DELETE /api/collab-cards/{name} — delete a collaboration card."""
        fpath = os.path.join(self.state.collab_cards_dir, f"{card_name}.md")
        if not os.path.isfile(fpath):
            return self._send_json({"error": f"Collab card '{card_name}' not found"}, 404)
        try:
            os.remove(fpath)
            return self._send_json({"ok": True, "message": f"Collab card '{card_name}' deleted"})
        except Exception as e:
            return self._send_json({"error": f"Failed to delete collab card: {e}"}, 500)

    def _handle_put_role_prompt(self, name: str, body: dict):
        """PUT /api/agents/{name}/role-prompt — update agent's role prompt structure."""
        config_path = os.path.join(self.state.agents_dir, name, "config.json")
        if not os.path.isfile(config_path):
            return self._send_json({"error": f"Agent '{name}' config not found"}, 404)
        try:
            with open(config_path, encoding="utf-8") as f:
                cfg = json.load(f)
            cfg.setdefault("prompt", {})["cards"] = body.get("cards", [])
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            return self._send_json({"ok": True, "message": f"Role prompt updated for '{name}'"})
        except Exception as e:
            return self._send_json({"error": f"Failed to update role prompt: {e}"}, 500)

    def _handle_delete_role_prompt(self, name: str):
        """DELETE /api/agents/{name}/role-prompt — clear agent's role prompt cards."""
        config_path = os.path.join(self.state.agents_dir, name, "config.json")
        if not os.path.isfile(config_path):
            return self._send_json({"error": f"Agent '{name}' config not found"}, 404)
        try:
            with open(config_path, encoding="utf-8") as f:
                cfg = json.load(f)
            cfg.setdefault("prompt", {})["cards"] = []
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            return self._send_json({"ok": True, "message": f"Role prompt cleared for '{name}'"})
        except Exception as e:
            return self._send_json({"error": f"Failed to clear role prompt: {e}"}, 500)

    def _handle_list_model_cards(self):
        """GET /api/model-cards — list model cards."""
        cards = self._list_cards(self.state.model_cards_dir)
        return self._send_json({"cards": cards})

    def _handle_get_model_card(self, card_name: str):
        """GET /api/model-cards/{name} — get a model card."""
        fpath = os.path.join(self.state.model_cards_dir, f"{card_name}.json")
        if not os.path.isfile(fpath):
            return self._send_json({"error": f"Model card '{card_name}' not found"}, 404)
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
            return self._send_json({"name": card_name, "card": data})
        except (OSError, ValueError) as e:
            return self._send_json({"error": f"Failed to read model card: {e}"}, 500)

    def _handle_put_model_card(self, card_name: str, body: dict):
        """PUT /api/model-cards/{name} — save/update a model card."""
        fpath = os.path.join(self.state.model_cards_dir, f"{card_name}.json")
        try:
            os.makedirs(self.state.model_cards_dir, exist_ok=True)
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(body, f, ensure_ascii=False, indent=2)
            return self._send_json({"ok": True, "message": f"Model card '{card_name}' saved"})
        except Exception as e:
            return self._send_json({"error": f"Failed to save model card: {e}"}, 500)

    def _handle_delete_model_card(self, card_name: str):
        """DELETE /api/model-cards/{name} — delete a model card."""
        fpath = os.path.join(self.state.model_cards_dir, f"{card_name}.json")
        if not os.path.isfile(fpath):
            return self._send_json({"error": f"Model card '{card_name}' not found"}, 404)
        try:
            os.remove(fpath)
            return self._send_json({"ok": True, "message": f"Model card '{card_name}' deleted"})
        except Exception as e:
            return self._send_json({"error": f"Failed to delete model card: {e}"}, 500)

    def _handle_put_model_card_assign(self, name: str, body: dict):
        """PUT /api/agents/{name}/model-card — assign a model card to agent."""
        config_path = os.path.join(self.state.agents_dir, name, "config.json")
        if not os.path.isfile(config_path):
            return self._send_json({"error": f"Agent '{name}' config not found"}, 404)
        try:
            with open(config_path, encoding="utf-8") as f:
                cfg = json.load(f)
            card_name = body.get("card_name", "")
            if card_name:
                cfg.setdefault("model", {})["_card"] = card_name
            else:
                cfg.get("model", {}).pop("_card", None)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            return self._send_json({"ok": True, "message": f"Model card '{card_name}' assigned to '{name}'"})
        except Exception as e:
            return self._send_json({"error": f"Failed to assign model card: {e}"}, 500)

    def _handle_delete_model_card_unassign(self, name: str):
        """DELETE /api/agents/{name}/model-card — unassign model card from agent."""
        config_path = os.path.join(self.state.agents_dir, name, "config.json")
        if not os.path.isfile(config_path):
            return self._send_json({"error": f"Agent '{name}' config not found"}, 404)
        try:
            with open(config_path, encoding="utf-8") as f:
                cfg = json.load(f)
            cfg.get("model", {}).pop("_card", None)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            return self._send_json({"ok": True, "message": f"Model card unassigned from '{name}'"})
        except Exception as e:
            return self._send_json({"error": f"Failed to unassign model card: {e}"}, 500)

    # ── Workspace handlers ─────────────────────────────────────────────

    def _handle_workspace_list(self):
        """GET /api/workspace/list — list available workspaces."""
        base = os.path.dirname(self.state.syscfg.workspace_agents_dir())
        workspaces = []
        if os.path.isdir(base):
            for entry in sorted(os.listdir(base)):
                wpath = os.path.join(base, entry)
                if os.path.isdir(wpath) and not entry.startswith("."):
                    workspaces.append(
                        {
                            "name": entry,
                            "path": wpath,
                            "has_agents": os.path.isdir(os.path.join(wpath, "agents")),
                        }
                    )
        return self._send_json({"workspaces": workspaces, "current": self.state.syscfg.get_workspace()})

    def _handle_workspace_create(self, body: dict):
        """POST /api/workspace/create — create a new workspace."""
        name = body.get("name", "")
        if not name or not re.match(r"^[a-zA-Z0-9_\-]+$", name):
            return self._send_json({"error": "Invalid workspace name"}, 400)
        base = os.path.dirname(self.state.syscfg.workspace_agents_dir())
        wpath = os.path.join(base, name)
        if os.path.exists(wpath):
            return self._send_json({"error": f"Workspace '{name}' already exists"}, 400)
        try:
            os.makedirs(os.path.join(wpath, "agents"))
            os.makedirs(os.path.join(wpath, "plugins"))
            os.makedirs(os.path.join(wpath, "data"))
            return self._send_json({"ok": True, "message": f"Workspace '{name}' created", "path": wpath})
        except Exception as e:
            return self._send_json({"error": f"Failed to create workspace: {e}"}, 500)

    def _handle_workspace_switch(self, body: dict):
        """POST /api/workspace/switch — switch to another workspace."""
        name = body.get("name", "")
        if not name:
            return self._send_json({"error": "Missing workspace name"}, 400)
        base = os.path.dirname(self.state.syscfg.workspace_agents_dir())
        wpath = os.path.join(base, name)
        if not os.path.isdir(wpath):
            return self._send_json({"error": f"Workspace '{name}' not found"}, 404)
        try:
            self.state.syscfg.set_workspace(name)
            return self._send_json({"ok": True, "message": f"Switched to workspace '{name}'"})
        except Exception as e:
            return self._send_json({"error": f"Failed to switch workspace: {e}"}, 500)

    def _handle_workspace_detect_legacy(self):
        """GET /api/workspace/detect-legacy — detect legacy workspace layout."""
        legacy = self.state.syscfg.detect_legacy_workspace()
        return self._send_json({"legacy": legacy})

    def _handle_workspace_migrate(self, body: dict):
        """POST /api/workspace/migrate — migrate legacy workspace to new layout."""
        try:
            task_id = self.state.syscfg.migrate_workspace()
            if task_id:
                self.state.ws_mig[task_id] = self.state.syscfg.get_migration_status(task_id)
            return self._send_json({"ok": True, "task_id": task_id, "message": "Migration started"})
        except Exception as e:
            return self._send_json({"error": f"Migration failed: {e}"}, 500)

    def _handle_workspace_migrate_status(self, task_id: str):
        """GET /api/workspace/migrate/status/{task_id} — get migration status."""
        task = self.state.ws_mig.get(task_id)
        if not task:
            return self._send_json({"error": f"Migration task '{task_id}' not found"}, 404)
        return self._send_json(
            {
                "task_id": task_id,
                "status": task.get("status", "unknown"),
                "progress": task.get("progress", 0),
                "error": task.get("error"),
                "report": task.get("report"),
            }
        )
