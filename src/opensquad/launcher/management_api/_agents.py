"""Agent lifecycle plus per-agent config / role / working-directory for the Launcher management API.

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
import time

from opensquad.agent_config_schema import apply_config_defaults, validate_agent_config
from opensquad.launcher.process_manager import AgentProcess, _read_json, check_port_conflict
from opensquad.launcher_main import (
    _AGENTS_LIST_TTL_S,
    AGENTS_DIR,
    _log,
    _processes,
    discover_agents,
)
from opensquad.system_config import syscfg
from opensquad.ui_prefs import snapshot_has_workspaces

# Must live in THIS module: ``global`` in the mixin binds here, not launcher_main.
_agents_list_cache_at: float = 0.0
_agents_list_cache_result: dict | None = None


class AgentsMixin:
    """Agent lifecycle plus per-agent config / role / working-directory."""

    def _handle_list_agents(self):
        """Return all discovered agents and their process status.

        5s TTL cache: the web UI polls this endpoint and every call used to
        read token_stats.json + profile.json per agent from disk.
        """
        global _agents_list_cache_at, _agents_list_cache_result
        now = time.monotonic()
        if _agents_list_cache_result is not None and now - _agents_list_cache_at < _AGENTS_LIST_TTL_S:
            return self._send_json(_agents_list_cache_result)
        result = []
        for name, ap in _processes.items():
            info = ap.get_status()
            # Embed token statistics
            info["token_stats"] = self._read_token_stats(name)
            # Embed group chat account profile (name, avatar)
            info["chat_profile"] = self._read_chat_profile(name)
            result.append(info)

        # Also scan disk for agent directories not yet in _processes
        all_discovered = discover_agents(AGENTS_DIR)
        known_names = set(_processes.keys())
        for info in all_discovered:
            if info["name"] not in known_names:
                cfg = info.get("config", {})
                # Robustness: malformed config may be non-dict (e.g. string from bad generator/plugin).
                # Never crash listing endpoint; keep agent visible and mark config issue.
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
                        "auto_start_on_boot": bool(
                            (cfg.get("ui") or {}).get("auto_start_on_boot", False)
                            if isinstance(cfg.get("ui"), dict)
                            else False
                        ),
                        "token_stats": None,
                        "chat_profile": self._read_chat_profile(info["name"]),
                    }
                )

        # Inject current role card name and model card name for each agent
        for item in result:
            cfg = item.get("config") or {}
            # Safely get role_card
            prompt_cfg = cfg.get("prompt", {})
            if isinstance(prompt_cfg, dict):
                item["role_card"] = prompt_cfg.get("role_card")
            else:
                item["role_card"] = None

            # Safely get model_card
            model_cfg = cfg.get("model", {})
            if isinstance(model_cfg, dict):
                item["model_card"] = model_cfg.get("_card")
            else:
                item["model_card"] = None

        _agents_list_cache_at = time.monotonic()
        _agents_list_cache_result = {"agents": result}
        self._send_json(_agents_list_cache_result)

    def _handle_start(self, name: str):
        """Start the specified agent"""
        if name in _processes:
            ap = _processes[name]
            if ap.is_alive():
                return self._send_json({"error": f"{name} already running"}, 400)
            ap.reload_config()
            apply_config_defaults(ap.config)
            errs = validate_agent_config(ap.config)
            if errs:
                detail = "\n".join(f"- {e}" for e in errs)
                return self._send_json(
                    {"error": f"Start failed: config.json validation failed with {len(errs)} error(s):\n{detail}"},
                    400,
                )
            port_err = check_port_conflict(ap.config)
            if port_err:
                return self._send_json({"error": f"Start failed: {port_err}"}, 400)
            # Pass list of already-allocated ports
            used_ports = [p.actual_port for p in _processes.values() if p.is_alive()]
            ap.start(allocated_ports=used_ports)
            return self._send_json({"message": f"{name} started", "pid": ap.process.pid, "port": ap.actual_port})

        # Not in process table — try to discover and create
        agent_dir = os.path.join(AGENTS_DIR, name)
        config_path = os.path.join(agent_dir, "config.json")
        if not os.path.isfile(config_path):
            return self._send_json({"error": f"Agent '{name}' not found"}, 404)

        config = _read_json(config_path)
        apply_config_defaults(config)
        errs = validate_agent_config(config)
        if errs:
            detail = "\n".join(f"- {e}" for e in errs)
            return self._send_json(
                {"error": f"Start failed: config.json validation failed with {len(errs)} error(s):\n{detail}"}, 400
            )
        port_err = check_port_conflict(config)
        if port_err:
            return self._send_json({"error": f"Start failed: {port_err}"}, 400)
        ap = AgentProcess(agent_dir, config)
        used_ports = [p.actual_port for p in _processes.values() if p.is_alive()]
        ap.start(allocated_ports=used_ports)
        _processes[name] = ap
        return self._send_json({"message": f"{name} started", "pid": ap.process.pid, "port": ap.actual_port})

    def _handle_stop(self, name: str):
        """Stop the specified agent"""
        if name not in _processes:
            return self._send_json({"error": f"Agent '{name}' not found"}, 404)
        ap = _processes[name]
        if not ap.is_alive():
            ap.should_run = False
            return self._send_json({"message": f"{name} already stopped"})
        ap.stop()
        return self._send_json({"message": f"{name} stopped"})

    def _handle_restart(self, name: str):
        """Restart the specified agent (equivalent to first-time start if process does not exist)"""
        if name not in _processes:
            # Not in process table — try to discover from directory and start (same as start)
            agent_dir = os.path.join(AGENTS_DIR, name)
            config_path = os.path.join(agent_dir, "config.json")
            if not os.path.isfile(config_path):
                return self._send_json({"error": f"Agent '{name}' not found"}, 404)
            config = _read_json(config_path)
            apply_config_defaults(config)
            errs = validate_agent_config(config)
            if errs:
                detail = "\n".join(f"- {e}" for e in errs)
                return self._send_json(
                    {"error": f"Start failed: config.json validation failed with {len(errs)} error(s):\n{detail}"},
                    400,
                )
            port_err = check_port_conflict(config)
            if port_err:
                return self._send_json({"error": f"Start failed: {port_err}"}, 400)
            ap = AgentProcess(agent_dir, config)
            used_ports = [p.actual_port for p in _processes.values() if p.is_alive()]
            ap.start(allocated_ports=used_ports)
            _processes[name] = ap
            return self._send_json({"message": f"{name} started", "pid": ap.process.pid, "port": ap.actual_port})

        ap = _processes[name]
        if ap.is_alive():
            ap.stop()
            time.sleep(1)
        ap.reload_config()
        apply_config_defaults(ap.config)
        errs = validate_agent_config(ap.config)
        if errs:
            detail = "\n".join(f"- {e}" for e in errs)
            return self._send_json(
                {"error": f"Restart failed: config.json validation failed with {len(errs)} error(s):\n{detail}"},
                400,
            )
        port_err = check_port_conflict(ap.config)
        if port_err:
            return self._send_json({"error": f"Restart failed: {port_err}"}, 400)
        ap.should_run = True
        ap.restart_count = 0
        ap.start()
        return self._send_json({"message": f"{name} restarted", "pid": ap.process.pid})

    def _handle_get_logs(self, name: str, lines: int):
        """Return the last N lines of logs"""
        if name not in _processes:
            return self._send_json({"error": f"Agent '{name}' not found"}, 404)
        logs = _processes[name].get_logs(lines)
        return self._send_json({"agent": name, "logs": logs, "total": len(logs)})

    def _handle_get_config(self, name: str):
        """Read agent config.json — redact sensitive fields"""
        config_path = os.path.join(AGENTS_DIR, name, "config.json")
        if not os.path.isfile(config_path):
            return self._send_json({"error": "Config not found"}, 404)
        config = _read_json(config_path)
        # Redact sensitive fields in API responses
        if "group_chat" in config and "password" in config.get("group_chat", {}):
            config["group_chat"]["password"] = "********"
        return self._send_json({"agent": name, "config": config})

    def _handle_get_working_directory(self, name: str):
        """GET /api/agents/{name}/working-directory

        Returns the agent's current session working directory (if set
        via the folder-picker UI) and the permanent workspace root.
        """

        agent_dir = os.path.join(syscfg.workspace_agents_dir(), name)
        if not os.path.isdir(agent_dir):
            return self._send_json({"error": "Agent directory not found"}, 404)

        # Read .session_cwd signal file (written by PUT handler)
        session_cwd = ""
        try:
            from opensquad.utils.session_cwd import read_session_cwd

            data = read_session_cwd(agent_dir)
            if data:
                session_cwd = data.get("path", "")
        except Exception:
            pass

        # Get permanent workspace root
        workspace_root = ""
        try:
            workspace_root = syscfg.get_workspace()
        except Exception:
            pass

        return self._send_json(
            {
                "agent": name,
                "session_cwd": session_cwd,
                "workspace_root": workspace_root,
                "active_cwd": session_cwd if session_cwd else workspace_root,
            }
        )

    def _resolve_agent_dir_name(self, name: str) -> str | None:
        """Map API *name* (dir_name or agent_id) to on-disk agent folder name."""
        name = (name or "").strip()
        if not name:
            return None
        agents_root = syscfg.workspace_agents_dir()
        direct = os.path.join(agents_root, name)
        if os.path.isdir(direct):
            return name
        # Running processes are keyed by dir_name; also match agent_id.
        if name in _processes:
            return name
        for dir_name, ap in _processes.items():
            if getattr(ap, "agent_id", None) == name:
                return dir_name
            cfg = getattr(ap, "config", None) or {}
            if isinstance(cfg, dict) and str(cfg.get("agent_id") or "") == name:
                return dir_name
        try:
            for entry in os.listdir(agents_root):
                entry_path = os.path.join(agents_root, entry)
                if not os.path.isdir(entry_path):
                    continue
                cfg_path = os.path.join(entry_path, "config.json")
                if not os.path.isfile(cfg_path):
                    continue
                try:
                    with open(cfg_path, encoding="utf-8") as f:
                        cfg = json.load(f)
                    if isinstance(cfg, dict) and str(cfg.get("agent_id") or "") == name:
                        return entry
                except Exception:
                    continue
        except OSError:
            pass
        return None

    def _agent_fs_root(self, name: str, root_override: str = "") -> tuple[str | None, dict | None]:
        """Return (root_abs, error_response) for agent project browse.

        Optional *root_override* (absolute path) lets the UI browse a
        per-session project folder that may differ from the live session_cwd.
        """
        dir_name = self._resolve_agent_dir_name(name)
        if not dir_name:
            return None, self._send_json({"error": "Agent directory not found"}, 404)

        override = (root_override or "").strip()
        if override:
            abs_override = os.path.normcase(os.path.abspath(override))
            if not os.path.isdir(abs_override):
                return None, self._send_json(
                    {"error": f"Root not found: {override}"},
                    404,
                )
            return abs_override, None

        agent_dir = os.path.join(syscfg.workspace_agents_dir(), dir_name)
        workspace_root = ""
        try:
            workspace_root = syscfg.get_workspace()
        except Exception:
            pass
        from opensquad.utils.project_fs import resolve_agent_root

        root = resolve_agent_root(agent_dir, workspace_root or "")
        if not root or not os.path.isdir(root):
            return None, self._send_json(
                {"error": "No project directory set. Choose a folder in the chat footer first."},
                400,
            )
        return root, None

    def _handle_set_working_directory(self, name: str, body: dict):
        """PUT /api/agents/{name}/working-directory

        Sets the agent's session-level working directory by writing a
        ``.session_cwd`` signal file. The agent process picks this up
        at the start of the next conversation turn (in
        ``InputHub.get_user_response()``) and calls
        ``filesystem.set_session_cwd()`` to apply it.

        Body: ``{"path": "C:\\Users\\admin\\projects\\my-app"}``

        To reset back to the permanent workspace root, send
        ``{"path": ""}`` or ``{"path": null}``.
        """

        agent_dir = os.path.join(syscfg.workspace_agents_dir(), name)
        if not os.path.isdir(agent_dir):
            return self._send_json({"error": "Agent directory not found"}, 404)

        path = body.get("path", "").strip() if body else ""

        if not path:
            # Reset to workspace root: remove the signal file
            try:
                from opensquad.utils.session_cwd import clear_session_cwd

                clear_session_cwd(agent_dir)
            except Exception:
                pass
            _log.info(f"[Launcher] Reset working directory for agent '{name}' to workspace root")
            return self._send_json(
                {
                    "status": "success",
                    "message": "Working directory reset to workspace root",
                    "path": "",
                }
            )

        # Validate directory exists
        if not os.path.isdir(path):
            return self._send_json({"error": f"Directory does not exist: {path}"}, 400)

        # Write signal file atomically
        try:
            from opensquad.utils.session_cwd import write_session_cwd

            payload = write_session_cwd(agent_dir, path)
        except Exception as e:
            return self._send_json({"error": f"Failed to write session cwd file: {e}"}, 500)

        _log.info(f"[Launcher] Set working directory for agent '{name}' to: {path}")
        return self._send_json(
            {
                "status": "success",
                "message": f"Working directory set to: {path}",
                "path": payload.get("path") or os.path.abspath(path),
            }
        )

    def _web_ui_state_path(self, agent_dir: str) -> str:
        return os.path.join(agent_dir, ".agent_web_ui.json")

    def _handle_get_web_ui_state(self, name: str):
        """GET /api/agents/{name}/web-ui-state

        Agent Web workspace registry + session↔project bindings.
        Stored on the agent host so LAN / different browser origins
        (localhost vs 192.168.x.x) share the same chrome.
        """
        dir_name = self._resolve_agent_dir_name(name)
        if not dir_name:
            return self._send_json({"error": "Agent directory not found"}, 404)
        agent_dir = os.path.join(syscfg.workspace_agents_dir(), dir_name)
        fp = self._web_ui_state_path(agent_dir)
        if not os.path.isfile(fp):
            return self._send_json(
                {
                    "agent": name,
                    "savedAt": 0,
                    "workspaces": None,
                    "session_project_meta": {},
                }
            )
        try:
            with open(fp, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        except Exception as e:
            return self._send_json({"error": f"Failed to read web UI state: {e}"}, 500)
        return self._send_json(
            {
                "agent": name,
                "savedAt": int(data.get("savedAt") or 0),
                "workspaces": data.get("workspaces"),
                "session_project_meta": data.get("session_project_meta")
                if isinstance(data.get("session_project_meta"), dict)
                else {},
            }
        )

    def _handle_put_web_ui_state(self, name: str, body: dict):
        """PUT /api/agents/{name}/web-ui-state — persist Agent Web UI chrome."""
        dir_name = self._resolve_agent_dir_name(name)
        if not dir_name:
            return self._send_json({"error": "Agent directory not found"}, 404)
        agent_dir = os.path.join(syscfg.workspace_agents_dir(), dir_name)
        if not isinstance(body, dict):
            return self._send_json({"error": "Invalid body"}, 400)

        fp = self._web_ui_state_path(agent_dir)
        existing: dict = {}
        if os.path.isfile(fp):
            try:
                with open(fp, encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    existing = raw
            except Exception:
                existing = {}

        incoming_at = int(body.get("savedAt") or 0)
        existing_at = int(existing.get("savedAt") or 0)
        # Last-write-wins by savedAt; equal/older keeps existing to avoid
        # racing two browsers into a silent wipe.
        if existing_at and incoming_at and incoming_at < existing_at:
            return self._send_json(
                {
                    "status": "skipped",
                    "message": "stale client state",
                    "savedAt": existing_at,
                    "workspaces": existing.get("workspaces"),
                    "session_project_meta": existing.get("session_project_meta") or {},
                }
            )

        # A fresh origin (packaged :9555 vs Vite :5173) may migrate an
        # empty chrome with a *newer* savedAt. Never let that wipe a
        # host snapshot that already has workspaces.
        incoming_ws = body.get("workspaces")
        if (
            incoming_ws is not None
            and not snapshot_has_workspaces(incoming_ws)
            and snapshot_has_workspaces(existing.get("workspaces"))
        ):
            return self._send_json(
                {
                    "status": "skipped",
                    "message": "empty client chrome must not replace host workspaces",
                    "savedAt": existing_at,
                    "workspaces": existing.get("workspaces"),
                    "session_project_meta": existing.get("session_project_meta") or {},
                }
            )

        next_state = {
            "version": 1,
            "savedAt": incoming_at or int(time.time() * 1000),
            "workspaces": body.get("workspaces", existing.get("workspaces")),
            "session_project_meta": body.get("session_project_meta")
            if isinstance(body.get("session_project_meta"), dict)
            else (existing.get("session_project_meta") or {}),
        }
        tmp = fp + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(next_state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, fp)
        except Exception as e:
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            return self._send_json({"error": f"Failed to write web UI state: {e}"}, 500)

        return self._send_json(
            {
                "status": "success",
                "savedAt": next_state["savedAt"],
                "workspaces": next_state.get("workspaces"),
                "session_project_meta": next_state.get("session_project_meta") or {},
            }
        )

    def _handle_put_config(self, name: str, body: dict):
        """Write agent config.json"""
        config_path = os.path.join(AGENTS_DIR, name, "config.json")
        agent_dir = os.path.join(AGENTS_DIR, name)
        if not os.path.isdir(agent_dir):
            return self._send_json({"error": "Agent directory not found"}, 404)
        config_data = body.get("config")
        if not config_data:
            return self._send_json({"error": "Missing 'config' in body"}, 400)

        # Normalize prompt_preload list-like fields to avoid nested/invalid shapes.
        def _norm_str_list(value):
            out = []
            if value is None:
                return out

            def _walk(v):
                if v is None:
                    return
                if isinstance(v, list | tuple | set):
                    for item in v:
                        _walk(item)
                    return
                s = str(v).strip()
                if not s:
                    return
                if "," in s:
                    for part in s.split(","):
                        p = part.strip()
                        if p:
                            out.append(p)
                else:
                    out.append(s)

            _walk(value)
            # Keep order but deduplicate
            return list(dict.fromkeys(out))

        if isinstance(config_data, dict):
            pp = config_data.get("prompt_preload")
            if isinstance(pp, dict):
                pp["hidden_plugins"] = _norm_str_list(pp.get("hidden_plugins", []))
                pp["full_skills"] = _norm_str_list(pp.get("full_skills", []))
                pp["hidden_skills"] = _norm_str_list(pp.get("hidden_skills", []))
                pp["mcp_full_servers"] = _norm_str_list(pp.get("mcp_full_servers", []))
                pp["mcp_hidden_servers"] = _norm_str_list(pp.get("mcp_hidden_servers", []))

            apply_config_defaults(config_data)
            # Ensure required model.api_protocol is not lost during save
            model = config_data.get("model")
            if isinstance(model, dict) and not model.get("api_protocol"):
                try:
                    old_cfg = _read_json(config_path)
                    old_proto = (old_cfg.get("model") or {}).get("api_protocol", "")
                    model["api_protocol"] = old_proto or "openai_compat"
                except Exception:
                    model["api_protocol"] = "openai_compat"
            gc = config_data.get("group_chat")
            if isinstance(gc, dict) and os.path.isfile(config_path):
                new_pw = gc.get("password")
                if new_pw in ("********", None, ""):
                    try:
                        old_cfg = _read_json(config_path)
                        old_pw = (old_cfg.get("group_chat") or {}).get("password")
                        if old_pw and old_pw != "********":
                            gc["password"] = old_pw
                    except Exception:
                        pass

        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)
        # Update in-memory config
        if name in _processes:
            _processes[name].reload_config()
        return self._send_json({"message": f"Config saved for {name}"})

    def _handle_put_agent_profile(self, name: str, body: dict):
        """Write the agent's group-chat profile (``data/profile.json``).

        The avatar is a free-form string: a ``/uploads/...`` path for a custom
        upload, the generated default data-URI, or "" to clear it. The upload
        itself belongs to the gateway — it owns both the ``/uploads`` mount and
        the group-chat user row — so this endpoint only persists the value.
        ``GET /api/agents`` then reports it, and every consumer that renders
        ``chat_profile`` (Agent Workstation, sidebar, nav shortcuts) follows
        without any extra plumbing.
        """
        global _agents_list_cache_at, _agents_list_cache_result

        # Reject traversal before touching the filesystem. _resolve_agent_dir_name
        # accepts any directory that exists under the agents root, and ".." is one.
        raw_name = (name or "").strip()
        if not raw_name or raw_name in (".", "..") or "/" in raw_name or "\\" in raw_name or "\x00" in raw_name:
            return self._send_json({"error": "Invalid agent name"}, 400)
        dir_name = self._resolve_agent_dir_name(raw_name) or raw_name
        agent_dir = os.path.join(AGENTS_DIR, dir_name)
        if not os.path.isdir(agent_dir) or os.path.commonpath(
            [os.path.realpath(agent_dir), os.path.realpath(AGENTS_DIR)]
        ) != os.path.realpath(AGENTS_DIR):
            return self._send_json({"error": "Agent directory not found"}, 404)
        if "avatar" not in body:
            return self._send_json({"error": "Missing 'avatar' in body"}, 400)
        raw_avatar = body.get("avatar")
        if raw_avatar is not None and not isinstance(raw_avatar, str):
            return self._send_json({"error": "'avatar' must be a string or null"}, 400)
        avatar = (raw_avatar or "").strip()

        from opensquad.avatar_utils import normalize_chat_profile
        from opensquad.json_cache import invalidate_json_cache, load_json_cached

        canonical = os.path.join(agent_dir, "data", "profile.json")
        legacy = os.path.join(agent_dir, "data", "group_chat", "profile.json")
        existing = load_json_cached(canonical, default=None)
        if not isinstance(existing, dict):
            existing = load_json_cached(legacy, default=None)
        if not isinstance(existing, dict):
            existing = {}

        # Preserve the display name the group-chat account already reports: a
        # rename happens in the chat UI, and overwriting it here from config
        # would silently rename the account. Only a profile that does not exist
        # yet falls back to config.agent_name — the same value the agent itself
        # writes on boot.
        display_name = existing.get("chat_user_name") or existing.get("name")
        if not display_name:
            cfg = _read_json(os.path.join(agent_dir, "config.json"))
            display_name = cfg.get("agent_name") or dir_name
        payload = {"name": display_name, "avatar": avatar}

        try:
            for profile_path in (canonical, legacy):
                invalidate_json_cache(profile_path)
                os.makedirs(os.path.dirname(profile_path), exist_ok=True)
                with open(profile_path, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
        except OSError as exc:
            return self._send_json({"error": f"Could not write profile.json: {exc}"}, 500)

        # GET /api/agents carries a short TTL cache; without dropping it the new
        # avatar would need up to _AGENTS_LIST_TTL_S to appear in the very UI
        # that just uploaded it.
        _agents_list_cache_result = None
        _agents_list_cache_at = 0.0

        return self._send_json({"ok": True, "profile": normalize_chat_profile(payload)})

    def _handle_get_role(self, name: str):
        """Read agent role prompt file (filename read from config.json prompt.role, default: role.md)"""
        agent_dir = os.path.join(AGENTS_DIR, name)
        # Read actual role filename from config.json
        role_filename = "role.md"
        config_path = os.path.join(agent_dir, "config.json")
        if os.path.isfile(config_path):
            try:
                with open(config_path, encoding="utf-8") as f:
                    cfg = json.load(f)
                role_filename = cfg.get("prompt", {}).get("role", "role.md") or "role.md"
            except Exception:
                pass
        role_path = os.path.join(agent_dir, role_filename)
        # Fallback: try role.md if configured file does not exist
        if not os.path.isfile(role_path) and role_filename != "role.md":
            role_path = os.path.join(agent_dir, "role.md")
        if not os.path.isfile(role_path):
            return self._send_json({"agent": name, "content": ""})
        with open(role_path, encoding="utf-8") as f:
            content = f.read()
        return self._send_json({"agent": name, "content": content})

    def _handle_put_role(self, name: str, body: dict):
        """Write agent role prompt file (filename read from config.json prompt.role, default: role.md)"""
        agent_dir = os.path.join(AGENTS_DIR, name)
        if not os.path.isdir(agent_dir):
            return self._send_json({"error": "Agent directory not found"}, 404)
        content = body.get("content", "")
        # Read actual role filename from config.json
        role_filename = "role.md"
        config_path = os.path.join(agent_dir, "config.json")
        if os.path.isfile(config_path):
            try:
                with open(config_path, encoding="utf-8") as f:
                    cfg = json.load(f)
                role_filename = cfg.get("prompt", {}).get("role", "role.md") or "role.md"
            except Exception:
                pass
        role_path = os.path.join(agent_dir, role_filename)
        with open(role_path, "w", encoding="utf-8") as f:
            f.write(content)
        return self._send_json({"message": f"Role saved for {name}"})
