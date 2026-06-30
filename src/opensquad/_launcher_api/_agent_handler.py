"""
Agent Handler Mixin — agent management HTTP handler methods.

Extracted from _launcher_api/__init__.py to reduce its size.
This mixin provides all agent-related handler methods for the
ManagementHandler class.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)


class AgentHandlerMixin:
    """Mixin providing agent management handler methods.

    Used by ManagementHandler in _launcher_api.__init__.
    All methods rely on self.state (HandlerState) and self._send_json().
    """

    def _handle_list_agents(self) -> None:
        """GET /api/agents — list all agents (running + discovered)."""
        result = []
        with self.state.launcher_lock:
            _snap = [(n, a.get_status()) for n, a in self.state.procesos.items()]
        for name, info in _snap:
            info["token_stats"] = self._read_token_stats(name)
            info["chat_profile"] = self._read_chat_profile(name)
            result.append(info)
        all_discovered = self.state.disc_agents(self.state.agents_dir)
        known_names = set(self.state.procesos.keys())
        for info in all_discovered:
            if info["name"] not in known_names:
                cfg = info.get("config", {})
                if not isinstance(cfg, dict):
                    cfg = {
                        "_config_error": f"Invalid config type: {type(info.get('config')).__name__}",
                        "_raw_config": str(info.get("config")),
                    }
                result.append(
                    {
                        "dir_name": info["name"],
                        "agent_id": cfg.get("agent_id", info["name"]),
                        "agent_name": cfg.get("agent_name", info["name"]),
                        "alive": False,
                        "pid": None,
                        "should_run": False,
                        "restart_count": 0,
                        "started_at": None,
                        "config": cfg,
                        "token_stats": None,
                        "chat_profile": self._read_chat_profile(info["name"]),
                    }
                )
        for item in result:
            cfg = item.get("config") or {}
            prompt_cfg = cfg.get("prompt", {})
            if isinstance(prompt_cfg, dict):
                item["role_card"] = prompt_cfg.get("role_card")
            else:
                item["role_card"] = None
            model_cfg = cfg.get("model", {})
            if isinstance(model_cfg, dict):
                item["model_card"] = model_cfg.get("_card")
            else:
                item["model_card"] = None
        self._send_json({"agents": result})

    def _handle_start(self, name: str) -> None:
        """POST /api/agents/{name}/start — start an agent."""
        if name in self.state.procesos:
            ap = self.state.procesos[name]
            if ap.is_alive():
                self._send_json({"error": f"{name} already running"}, 400)
                return
            ap.reload_config()
            self.state.appl_def(ap.config)
            errs = self.state.val_cfg(ap.config)
            if errs:
                detail = "\n".join(f"- {e}" for e in errs)
                self._send_json(
                    {"error": f"Start failed: config.json validation failed with {len(errs)} error(s):\n{detail}"}, 400
                )
                return
            port_err = self.state.chk_port(ap.config)
            if port_err:
                self._send_json({"error": f"Start failed: {port_err}"}, 400)
                return
            with self.state.launcher_lock:
                used_ports = [p.actual_port for p in self.state.procesos.values() if p.is_alive()]
            ap.start(allocated_ports=used_ports)
            self._send_json({"message": f"{name} started", "pid": ap.process.pid, "port": ap.actual_port})
            return
        agent_dir = os.path.join(self.state.agents_dir, name)
        config_path = os.path.join(agent_dir, "config.json")
        if not os.path.isfile(config_path):
            self._send_json({"error": f"Agent '{name}' not found"}, 404)
            return
        config = self.state.read_json(config_path)
        self.state.appl_def(config)
        errs = self.state.val_cfg(config)
        if errs:
            detail = "\n".join(f"- {e}" for e in errs)
            self._send_json(
                {"error": f"Start failed: config.json validation failed with {len(errs)} error(s):\n{detail}"}, 400
            )
            return
        port_err = self.state.chk_port(config)
        if port_err:
            self._send_json({"error": f"Start failed: {port_err}"}, 400)
            return
        ap = self.state.AgentProcess(agent_dir, config)
        with self.state.launcher_lock:
            used_ports = [p.actual_port for p in self.state.procesos.values() if p.is_alive()]
        ap.start(allocated_ports=used_ports)
        self.state.procesos[name] = ap
        self._send_json({"message": f"{name} started", "pid": ap.process.pid, "port": ap.actual_port})

    def _handle_stop(self, name: str) -> None:
        """POST /api/agents/{name}/stop — stop an agent."""
        if name not in self.state.procesos:
            self._send_json({"error": f"Agent '{name}' not found"}, 404)
            return
        ap = self.state.procesos[name]
        if not ap.is_alive():
            ap.should_run = False
            self._send_json({"message": f"{name} already stopped"})
            return
        ap.stop()
        self._send_json({"message": f"{name} stopped"})

    def _handle_restart(self, name: str) -> None:
        """POST /api/agents/{name}/restart — restart an agent."""
        if name not in self.state.procesos:
            self._send_json({"error": f"Agent '{name}' not found"}, 404)
            return
        ap = self.state.procesos[name]
        ap.restart()
        self._send_json({"message": f"{name} restarted"})

    def _handle_get_logs(self, name: str, lines: int = 200) -> None:
        """GET /api/agents/{name}/logs — get agent logs."""
        log_path = os.path.join(self.state.agents_dir, name, "data", "logs", "agent.log")
        if not os.path.isfile(log_path):
            self._send_json({"logs": ""})
            return
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            self._send_json({"logs": "".join(all_lines[-lines:])})
        except Exception as e:
            self._send_json({"logs": f"Error reading log: {e}"})

    def _handle_get_config(self, name: str) -> None:
        """GET /api/agents/{name}/config — get agent config."""
        config_path = os.path.join(self.state.agents_dir, name, "config.json")
        if not os.path.isfile(config_path):
            self._send_json({"error": "config.json not found"}, 404)
            return
        try:
            cfg = self.state.read_json(config_path)
            self._send_json({"config": cfg})
        except Exception as e:
            self._send_json({"error": f"Failed to read config: {e}"}, 500)

    def _handle_put_config(self, name: str, body: dict) -> None:
        """PUT /api/agents/{name}/config — update agent config."""
        config_path = os.path.join(self.state.agents_dir, name, "config.json")
        if not os.path.isfile(config_path):
            self._send_json({"error": "config.json not found"}, 404)
            return
        try:
            import json as _json

            new_cfg = body.get("config", body)
            with open(config_path, "w", encoding="utf-8") as f:
                _json.dump(new_cfg, f, ensure_ascii=False, indent=2)
            self._send_json({"message": "Config updated"})
        except Exception as e:
            self._send_json({"error": f"Failed to write config: {e}"}, 500)

    def _handle_get_role(self, name: str) -> None:
        """GET /api/agents/{name}/role — get agent role config."""
        config_path = os.path.join(self.state.agents_dir, name, "config.json")
        if not os.path.isfile(config_path):
            self._send_json({"error": "config.json not found"}, 404)
            return
        try:
            cfg = self.state.read_json(config_path)
            self._send_json({"role": cfg.get("prompt", {})})
        except Exception as e:
            self._send_json({"error": f"Failed to read config: {e}"}, 500)

    def _handle_put_role(self, name: str, body: dict) -> None:
        """PUT /api/agents/{name}/role — update agent role config."""
        config_path = os.path.join(self.state.agents_dir, name, "config.json")
        if not os.path.isfile(config_path):
            self._send_json({"error": "config.json not found"}, 404)
            return
        try:
            import json as _json

            with open(config_path, encoding="utf-8") as f:
                cfg = _json.load(f)
            cfg["prompt"] = body.get("prompt", body)
            with open(config_path, "w", encoding="utf-8") as f:
                _json.dump(cfg, f, ensure_ascii=False, indent=2)
            self._send_json({"message": "Role updated"})
        except Exception as e:
            self._send_json({"error": f"Failed to update role: {e}"}, 500)

    def _handle_create(self, body: dict) -> None:
        """POST /api/agents/create — create a new agent."""
        name = body.get("name", "")
        if not name:
            self._send_json({"error": "Missing agent name"}, 400)
            return
        agent_dir = os.path.join(self.state.agents_dir, name)
        if os.path.exists(agent_dir):
            self._send_json({"error": f"Agent '{name}' already exists"}, 400)
            return
        os.makedirs(agent_dir, exist_ok=True)
        config = body.get("config", {})
        config_path = os.path.join(agent_dir, "config.json")
        try:
            import json as _json

            with open(config_path, "w", encoding="utf-8") as f:
                _json.dump(config, f, ensure_ascii=False, indent=2)
            self._send_json({"message": f"Agent '{name}' created"})
        except Exception as e:
            self._send_json({"error": f"Failed to create agent: {e}"}, 500)

    def _handle_rescan(self) -> None:
        """POST /api/agents/rescan — rescan agents directory."""
        self.state.disc_agents(self.state.agents_dir)
        self._send_json({"message": "Rescan complete"})

    def _handle_delete(self, name: str) -> None:
        """DELETE /api/agents/{name} — delete an agent."""
        import shutil

        agent_dir = os.path.join(self.state.agents_dir, name)
        if not os.path.isdir(agent_dir):
            self._send_json({"error": f"Agent '{name}' not found"}, 404)
            return
        if name in self.state.procesos:
            ap = self.state.procesos[name]
            if ap.is_alive():
                ap.stop()
            del self.state.procesos[name]
        shutil.rmtree(agent_dir, ignore_errors=True)
        self._send_json({"message": f"Agent '{name}' deleted"})

    def _handle_get_stats(self, name: str) -> None:
        """GET /api/agents/{name}/stats — get agent stats."""
        stats = self._read_token_stats(name)
        self._send_json({"stats": stats})

    def _read_token_stats(self, agent_name: str) -> dict:
        """Read token_stats.json for a specific agent from its data directory.

        The runner writes token_stats.json into the agent's history_dir
        (data/ai_his_talk/), but older configs may have it directly in
        data/.  Try both locations.
        """
        data_root = os.path.join(self.state.agents_dir, agent_name, "data")
        candidates = [
            os.path.join(data_root, "ai_his_talk", "token_stats.json"),
            os.path.join(data_root, "token_stats.json"),
        ]
        for path in candidates:
            if os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass
        return {}

    def _read_chat_profile(self, agent_name: str) -> dict:
        """Read agent profile for chat stats."""
        profile_path = os.path.join(self.state.agents_dir, agent_name, "data", "profile.json")
        if os.path.isfile(profile_path):
            try:
                with open(profile_path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}
