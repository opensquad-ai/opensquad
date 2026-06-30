"""
Launcher Management API package — __init__.py re-exports the factory + server.

This module provides the full HTTP management API that was extracted from
launcher.py during commit 9cefa8a's refactor.  It exposes all /api/* and
/_internal/* endpoints via a ThreadingHTTPServer running on the management port.

Launcher.py imports _start_management_server from here and passes all required
runtime state as arguments.  create_management_handler() builds a
BaseHTTPRequestHandler subclass bound to that state.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from opensquad._launcher_api._agent_handler import AgentHandlerMixin
from opensquad._launcher_api._auth import (
    check_auth,
    encrypt_password,
    get_launcher_token,
    verify_password,
)
from opensquad._launcher_api._plugin_handler import PluginHandlerMixin
from opensquad._launcher_api._system_handler import SystemHandlerMixin
from opensquad._launcher_api._types import HandlerState

# ── imports that match what launcher.py passes in ──────────────────────────
# (listed here so py_compile succeeds; in practice all are provided by the caller)
AGENTS_DIR: str = ""
PLUGINS_DIR: str = ""
SKILLS_DIR: str = ""
ROLE_CARDS_DIR: str = ""
COLLAB_CARDS_DIR: str = ""
MODEL_CARDS_DIR: str = ""
MANAGEMENT_PORT: int = 9600
STALL_THRESHOLD: int = 300

BUILTIN_PLUGINS: dict = {}
_processes: dict[str, Any] = {}
_plugin_services: dict[str, Any] = {}
_task_watch_heartbeats: dict[str, dict] = {}
_task_watch_stalled_notified: set = set()
_shutdown_event: Any = None
_workspace_migration_tasks: dict[str, Any] = {}
syscfg: Any = None
read_json: Any = None
chk_port: Any = None
res_disc_port: Any = None
cln_reg: Any = None
appl_def: Any = None
val_cfg: Any = None
disc_agents: Any = None
disc_plug_svcs: Any = None
AgentProcess: Any = None
PluginServiceProcess: Any = None
logger: Any = None

# ────────────────────────────────────────────────────────────────────────────

_log = logging.getLogger("launcher_api")


def build_handler_state(**kwargs) -> HandlerState:
    """Build a HandlerState from keyword arguments (backward-compat helper).

    Accepts the same parameter names as the old ``create_management_handler``
    signature and returns a typed ``HandlerState``.  New code should prefer
    constructing ``HandlerState`` directly.
    """
    # Map legacy parameter names to HandlerState field names
    legacy_map = {
        "procesos": "procesos",
        "plug_svcs": "plug_svcs",
        "task_hb": "task_hb",
        "task_sn": "task_sn",
        "shut_ev": "shut_ev",
        "ws_mig": "ws_mig",
        "agents_dir": "agents_dir",
        "plugins_dir": "plugins_dir",
        "skills_dir": "skills_dir",
        "role_cards_dir": "role_cards_dir",
        "collab_cards_dir": "collab_cards_dir",
        "model_cards_dir": "model_cards_dir",
        "mgmt_port": "mgmt_port",
        "stall_thresh": "stall_thresh",
        "syscfg": "syscfg",
        "logger": "logger",
        "launcher_lock": "launcher_lock",
        "read_json": "read_json",
        "chk_port": "chk_port",
        "res_disc_port": "res_disc_port",
        "cln_reg": "cln_reg",
        "appl_def": "appl_def",
        "val_cfg": "val_cfg",
        "disc_agents": "disc_agents",
        "disc_plug_svcs": "disc_plug_svcs",
        "AgentProcess": "AgentProcess",
        "PluginServiceProcess": "PluginServiceProcess",
        "builtin_plugins": "builtin_plugins",
    }
    state_kw = {}
    for legacy_key, state_key in legacy_map.items():
        if legacy_key in kwargs:
            state_kw[state_key] = kwargs[legacy_key]
    return HandlerState(**state_kw)


def create_management_handler(
    *,
    launcher_lock: Any = None,
    shut_ev: Any,
    logger: Any,
    procesos: dict[str, Any],
    plug_svcs: dict[str, Any],
    task_hb: dict[str, dict[str, Any]],
    task_sn: set,
    ws_mig: dict[str, dict[str, Any]],
    agents_dir: str,
    plugins_dir: str,
    skills_dir: str,
    role_cards_dir: str,
    collab_cards_dir: str,
    model_cards_dir: str,
    mgmt_port: int,
    stall_thresh: int,
    syscfg: Any,
    read_json: Any,
    chk_port: Any,
    res_disc_port: Any,
    cln_reg: Any,
    appl_def: Any,
    val_cfg: Any,
    disc_agents: Any,
    disc_plug_svcs: Any,
    AgentProcess: Any,
    PluginServiceProcess: Any,
    builtin_plugins: dict,
) -> type[BaseHTTPRequestHandler]:
    """
    Build a ManagementHandler bound to the given runtime state.

    All state is captured in the closure so the handler class has access to
    everything it needs without relying on module-level globals.
    """

    class ManagementHandler(BaseHTTPRequestHandler, AgentHandlerMixin, PluginHandlerMixin, SystemHandlerMixin):
        """Lightweight HTTP handler — no FastAPI/uvicorn dependency, minimal external deps"""

        # Runtime state — set by create_management_handler before first request.
        state: HandlerState = None

        # ── Token authentication ───────────────────────────────────────────

        def _get_launcher_token(self) -> str:
            return get_launcher_token()

        def _encrypt_password(self, password: str) -> str:
            return encrypt_password(password)

        def _verify_password(self, password: str, stored: str) -> bool:
            return verify_password(password, stored)

        def _check_auth(self) -> bool:
            return check_auth(self)

        def log_message(self, format, *args):
            pass

        def _send_json(self, data: dict, status: int = 200):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError, OSError):
                pass

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                self._send_json({"error": f"Invalid JSON body: {e}"}, 400)
                raise

        def _require_auth_and_call(self, handler_fn):
            if self._check_auth():
                try:
                    handler_fn()
                except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError, OSError):
                    pass
                except Exception as e:
                    with contextlib.suppress(ConnectionAbortedError, BrokenPipeError, ConnectionResetError, OSError):
                        self._send_json({"error": f"Internal server error: {e!s}"}, 500)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.end_headers()

        # ════════════════════════════════════════════════════════════════════
        #  GET
        # ════════════════════════════════════════════════════════════════════

        def do_GET(self):
            self._require_auth_and_call(self._do_get_impl)

        def _do_get_impl(self):
            import urllib.parse

            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/")
            qs = urllib.parse.parse_qs(parsed.query)

            # ── Agent endpoints ──────────────────────────────────────────
            if path == "/api/agents":
                return self._handle_list_agents()
            elif path.startswith("/api/agents/") and path.endswith("/stats"):
                name = path.split("/")[3]
                return self._handle_get_stats(name)
            elif path.startswith("/api/agents/") and path.endswith("/logs"):
                name = path.split("/")[3]
                lines = int(qs.get("lines", ["200"])[0])
                return self._handle_get_logs(name, lines)
            elif path.startswith("/api/agents/") and path.endswith("/config"):
                name = path.split("/")[3]
                return self._handle_get_config(name)
            elif path.startswith("/api/agents/") and path.endswith("/role"):
                name = path.split("/")[3]
                return self._handle_get_role(name)
            elif path.startswith("/api/agents/") and path.endswith("/mcp"):
                name = path.split("/")[3]
                return self._handle_get_mcp(name)

            # ── Plugin endpoints ─────────────────────────────────────────
            elif path == "/api/plugins":
                return self._handle_list_plugins()
            elif path.startswith("/api/plugins/") and path.endswith("/config"):
                name = path.split("/")[3]
                return self._handle_get_plugin_config(name)
            elif path.startswith("/api/plugins/") and path.endswith("/data"):
                name = path.split("/")[3]
                return self._handle_get_plugin_data(name, qs)

            # ── Skills endpoints ─────────────────────────────────────────
            elif path == "/api/skills":
                return self._handle_list_skills()
            elif path.startswith("/api/skills/") and path.endswith("/source"):
                skill_name = path.split("/")[3]
                return self._handle_get_skill_source(skill_name)

            # ── Role/Collab/Model card endpoints ─────────────────────────
            elif path == "/api/role-cards":
                return self._handle_list_role_cards()
            elif path.startswith("/api/role-cards/"):
                card_name = path[len("/api/role-cards/") :]
                return self._handle_get_role_card(card_name)
            elif path == "/api/collab-cards":
                return self._handle_list_collab_cards()
            elif path.startswith("/api/collab-cards/"):
                card_name = path[len("/api/collab-cards/") :]
                return self._handle_get_collab_card(card_name)
            elif path == "/api/model-cards":
                return self._handle_list_model_cards()
            elif path.startswith("/api/model-cards/"):
                card_name = path[len("/api/model-cards/") :]
                return self._handle_get_model_card(card_name)

            # ── MCP endpoints ────────────────────────────────────────────
            elif path == "/api/mcp/config":
                return self._handle_get_mcp_central()
            elif path == "/api/mcp/global":
                return self._handle_get_mcp_global()

            # ── System / task-watch endpoints ────────────────────────────
            elif path == "/api/task_watch_status":
                result = {}
                now = time.time()
                for aid, hb in self.state.task_hb.items():
                    result[aid] = {
                        "event": hb.get("event", "unknown"),
                        "detail": hb.get("detail", ""),
                        "elapsed_sec": round(now - hb.get("last_update", 0), 1),
                        "stalled": (now - hb.get("last_update", 0)) > self.stall_thresh,
                    }
                return self._send_json({"workers": result})
            elif path == "/api/ping":
                return self._send_json({"status": "ok", "service": "launcher"})

            # ── Workspace endpoints ───────────────────────────────────────
            elif path == "/api/workspace":
                return self._send_json(
                    {
                        "workspace": state.syscfg.get_workspace(),
                        "agents_dir": state.syscfg.workspace_agents_dir(),
                    }
                )
            elif path == "/api/workspace/list":
                return self._handle_workspace_list()
            elif path == "/api/workspace/detect-legacy":
                return self._handle_workspace_detect_legacy()
            elif path.startswith("/api/workspace/migrate/status/"):
                task_id = path[len("/api/workspace/migrate/status/") :]
                return self._handle_workspace_migrate_status(task_id)

            # ── Service / plugin-service endpoints ────────────────────────
            elif path == "/api/services/manage":
                return self._handle_services_manage()
            elif path == "/api/plugin-services":
                return self._handle_list_plugin_services()
            elif path.startswith("/api/plugin-services/") and path.endswith("/logs"):
                pid = path.split("/")[3]
                lines = int(qs.get("lines", ["200"])[0])
                return self._handle_plugin_service_logs(pid, lines)

            # ── Runtime endpoint ─────────────────────────────────────────
            elif path == "/api/runtime/list":
                return self._handle_runtime_list()

            # ── Session endpoints ─────────────────────────────────────────
            elif path.startswith("/api/sessions/") and path.endswith("/list"):
                agent_id = path.split("/")[3]
                return self._handle_session_list(agent_id)
            elif path.startswith("/api/sessions/") and path.endswith("/current"):
                agent_id = path.split("/")[3]
                offset = int(qs.get("offset", ["0"])[0])
                limit = int(qs.get("limit", ["50"])[0])
                return self._handle_session_current(agent_id, offset, limit)
            elif re.search(r"^/api/sessions/[^/]+/[^/]+/paged$", path):
                parts = path.split("/")
                agent_id, session_id = parts[3], parts[4]
                offset = int(qs.get("offset", ["0"])[0])
                limit = int(qs.get("limit", ["50"])[0])
                return self._handle_session_paged(agent_id, session_id, offset, limit)
            elif re.search(r"^/api/sessions/[^/]+/[^/]+$", path):
                parts = path.split("/")
                agent_id, session_id = parts[3], parts[4]
                return self._handle_session_get(agent_id, session_id)

            else:
                return self._send_json({"error": "Not found"}, 404)

        # ════════════════════════════════════════════════════════════════════
        #  POST
        # ════════════════════════════════════════════════════════════════════

        def do_POST(self):
            import urllib.parse

            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/")
            # Internal endpoint: bypass auth (agent → launcher on localhost)
            if path == "/_internal/task_watch_heartbeat":
                self._handle_task_watch_heartbeat()
                return
            self._require_auth_and_call(self._do_post_impl)

        def _do_post_impl(self):
            import urllib.parse

            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/")

            # ── Agent action endpoints ───────────────────────────────────
            if path.startswith("/api/agents/") and path.endswith("/start"):
                name = path.split("/")[3]
                return self._handle_start(name)
            elif path.startswith("/api/agents/") and path.endswith("/stop"):
                name = path.split("/")[3]
                return self._handle_stop(name)
            elif path.startswith("/api/agents/") and path.endswith("/restart"):
                name = path.split("/")[3]
                return self._handle_restart(name)
            elif path == "/api/agents/create":
                body = self._read_body()
                return self._handle_create(body)
            elif path == "/api/agents/rescan":
                return self._handle_rescan()

            # ── Plugin action endpoints ──────────────────────────────────
            elif path == "/api/plugin-view-error":
                body = self._read_body()
                return self._handle_plugin_view_error(body)
            elif path.startswith("/api/plugins/") and path.endswith("/action"):
                name = path.split("/")[3]
                body = self._read_body()
                return self._handle_plugin_action(name, body)
            elif path == "/api/plugins/install-zip":
                body = self._read_body()
                return self._handle_install_zip_plugin(body)

            # ── Resource upload ───────────────────────────────────────────
            elif path == "/api/resources/upload":
                body = self._read_body()
                return self._handle_resource_upload(body)

            # ── Plugin-service action endpoints ──────────────────────────
            elif path.startswith("/api/plugin-services/") and path.endswith("/restart"):
                pid = path.split("/")[3]
                return self._handle_plugin_service_restart(pid)
            elif path.startswith("/api/plugin-services/") and path.endswith("/start"):
                pid = path.split("/")[3]
                return self._handle_plugin_service_start(pid)
            elif path.startswith("/api/plugin-services/") and path.endswith("/stop"):
                pid = path.split("/")[3]
                return self._handle_plugin_service_stop(pid)

            # ── Session delete ────────────────────────────────────────────
            elif re.search(r"^/api/sessions/[^/]+/[^/]+/delete$", path):
                parts = path.split("/")
                agent_id, session_id = parts[3], parts[4]
                return self._handle_session_delete(agent_id, session_id)

            # ── Workspace endpoints ─────────────────────────────────────
            elif path == "/api/workspace/create":
                body = self._read_body()
                return self._handle_workspace_create(body)
            elif path == "/api/workspace/switch":
                body = self._read_body()
                return self._handle_workspace_switch(body)
            elif path == "/api/workspace/migrate":
                body = self._read_body()
                return self._handle_workspace_migrate(body)

            # ── Runtime cleanup ───────────────────────────────────────────
            elif path == "/api/runtime/cleanup":
                body = self._read_body()
                return self._handle_runtime_cleanup(body)

            # ── Shutdown ────────────────────────────────────────────────
            elif path == "/api/shutdown":
                body = self._read_body()
                return self._handle_shutdown(body)

            else:
                return self._send_json({"error": "Not found"}, 404)

        # ════════════════════════════════════════════════════════════════════
        #  PUT
        # ════════════════════════════════════════════════════════════════════

        def do_PUT(self):
            self._require_auth_and_call(self._do_put_impl)

        def _do_put_impl(self):
            import urllib.parse

            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/")

            # ── Agent config/role endpoints ─────────────────────────────
            if path.startswith("/api/agents/") and path.endswith("/config"):
                name = path.split("/")[3]
                body = self._read_body()
                return self._handle_put_config(name, body)
            elif path.startswith("/api/agents/") and path.endswith("/role"):
                name = path.split("/")[3]
                body = self._read_body()
                return self._handle_put_role(name, body)
            elif path.startswith("/api/agents/") and path.endswith("/mcp"):
                name = path.split("/")[3]
                body = self._read_body()
                return self._handle_put_mcp(name, body)
            elif path.startswith("/api/agents/") and path.endswith("/model-card"):
                name = path.split("/")[3]
                body = self._read_body()
                return self._handle_put_model_card_assign(name, body)
            elif path.startswith("/api/agents/") and path.endswith("/role-prompt"):
                name = path.split("/")[3]
                body = self._read_body()
                return self._handle_put_role_prompt(name, body)

            # ── Plugin endpoints ─────────────────────────────────────────
            elif path.startswith("/api/plugins/") and path.endswith("/enable"):
                name = path.split("/")[3]
                return self._handle_plugin_set_enabled(name, True)
            elif path.startswith("/api/plugins/") and path.endswith("/disable"):
                name = path.split("/")[3]
                return self._handle_plugin_set_enabled(name, False)
            elif path.startswith("/api/plugins/") and path.endswith("/config"):
                name = path.split("/")[3]
                body = self._read_body()
                return self._handle_put_plugin_config(name, body)

            # ── Plugin-service endpoints ─────────────────────────────────
            elif path.startswith("/api/plugin-services/") and path.endswith("/auto-start"):
                name = path.split("/")[3]
                body = self._read_body()
                return self._handle_plugin_service_auto_start(name, body)

            # ── MCP endpoints ────────────────────────────────────────────
            elif path == "/api/mcp/config":
                body = self._read_body()
                return self._handle_put_mcp_central(body)
            elif path.startswith("/api/mcp/global/servers/") and path.endswith("/enable"):
                srv = path.split("/")[5]
                return self._handle_put_mcp_server_global(srv, True)
            elif path.startswith("/api/mcp/global/servers/") and path.endswith("/disable"):
                srv = path.split("/")[5]
                return self._handle_put_mcp_server_global(srv, False)

            # ── Role/Collab/Model card endpoints ─────────────────────────
            elif path.startswith("/api/role-cards/"):
                card_name = path[len("/api/role-cards/") :]
                body = self._read_body()
                return self._handle_put_role_card(card_name, body)
            elif path.startswith("/api/collab-cards/"):
                card_name = path[len("/api/collab-cards/") :]
                body = self._read_body()
                return self._handle_put_collab_card(card_name, body)
            elif path.startswith("/api/model-cards/"):
                card_name = path[len("/api/model-cards/") :]
                body = self._read_body()
                return self._handle_put_model_card(card_name, body)

            else:
                return self._send_json({"error": "Not found"}, 404)

        # ════════════════════════════════════════════════════════════════════
        #  DELETE
        # ════════════════════════════════════════════════════════════════════

        def do_DELETE(self):
            self._require_auth_and_call(self._do_delete_impl)

        def _do_delete_impl(self):
            import urllib.parse

            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/")
            parts = path.split("/")

            # DELETE /api/agents/{name}
            if len(parts) == 4 and parts[1] == "api" and parts[2] == "agents":
                return self._handle_delete(parts[3])
            # DELETE /api/agents/{name}/role-prompt
            elif len(parts) == 5 and parts[1] == "api" and parts[2] == "agents" and parts[4] == "role-prompt":
                return self._handle_delete_role_prompt(parts[3])
            # DELETE /api/agents/{name}/model-card
            elif len(parts) == 5 and parts[1] == "api" and parts[2] == "agents" and parts[4] == "model-card":
                return self._handle_delete_model_card_unassign(parts[3])
            # DELETE /api/role-cards/{name}
            elif len(parts) == 4 and parts[1] == "api" and parts[2] == "role-cards":
                return self._handle_delete_role_card(parts[3])
            # DELETE /api/collab-cards/{name}
            elif len(parts) == 4 and parts[1] == "api" and parts[2] == "collab-cards":
                return self._handle_delete_collab_card(parts[3])
            # DELETE /api/model-cards/{name}
            elif len(parts) == 4 and parts[1] == "api" and parts[2] == "model-cards":
                return self._handle_delete_model_card(parts[3])
            # DELETE /api/resources/{type}/{name}
            elif len(parts) == 5 and parts[1] == "api" and parts[2] == "resources":
                return self._handle_delete_resource(parts[3], parts[4])
            else:
                return self._send_json({"error": "Not found"}, 404)

        # ════════════════════════════════════════════════════════════════════
        #  Internal heartbeat handler
        # ════════════════════════════════════════════════════════════════════

        def _handle_task_watch_heartbeat(self):
            """POST /_internal/task_watch_heartbeat — agent reports task progress to launcher."""
            try:
                body = self._read_body()
                data = json.loads(body)
                agent_id = data.get("agent_id", "")
                if not agent_id:
                    return self._send_json({"error": "Missing agent_id"}, 400)
                self.state.task_hb[agent_id] = {
                    "event": data.get("event", "unknown"),
                    "task_id": data.get("task_id", ""),
                    "detail": data.get("detail", ""),
                    "last_update": data.get("timestamp", time.time()),
                }
                self.state.task_sn.discard(agent_id)
                return self._send_json({"ok": True})
            except Exception as e:
                return self._send_json({"error": str(e)}, 500)

            # ════════════════════════════════════════════════════════════════════
            #  Agent handlers
            # ════════════════════════════════════════════════════════════════════

            result = []
            with launcher_lock:
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

            if name in procesos:
                ap = self.state.procesos[name]
                if ap.is_alive():
                    return self._send_json({"error": f"{name} already running"}, 400)
                ap.reload_config()
                self.state.appl_def(ap.config)
                errs = self.state.val_cfg(ap.config)
                if errs:
                    detail = "\n".join(f"- {e}" for e in errs)
                    return self._send_json(
                        {"error": f"Start failed: config.json validation failed with {len(errs)} error(s):\n{detail}"},
                        400,
                    )
                port_err = self.state.chk_port(ap.config)
                if port_err:
                    return self._send_json({"error": f"Start failed: {port_err}"}, 400)
                with launcher_lock:
                    used_ports = [p.actual_port for p in self.state.procesos.values() if p.is_alive()]
                ap.start(allocated_ports=used_ports)
                return self._send_json({"message": f"{name} started", "pid": ap.process.pid, "port": ap.actual_port})
            agent_dir = os.path.join(self.agents_dir, name)
            config_path = os.path.join(agent_dir, "config.json")
            if not os.path.isfile(config_path):
                return self._send_json({"error": f"Agent '{name}' not found"}, 404)
            config = self.state.read_json(config_path)
            self.state.appl_def(config)
            errs = self.state.val_cfg(config)
            if errs:
                detail = "\n".join(f"- {e}" for e in errs)
                return self._send_json(
                    {"error": f"Start failed: config.json validation failed with {len(errs)} error(s):\n{detail}"}, 400
                )
            port_err = self.state.chk_port(config)
            if port_err:
                return self._send_json({"error": f"Start failed: {port_err}"}, 400)
            ap = self.state.AgentProcess(agent_dir, config)
            used_ports = [p.actual_port for p in self.state.procesos.values() if p.is_alive()]
            ap.start(allocated_ports=used_ports)
            self.state.procesos[name] = ap
            return self._send_json({"message": f"{name} started", "pid": ap.process.pid, "port": ap.actual_port})

            if name not in procesos:
                return self._send_json({"error": f"Agent '{name}' not found"}, 404)
            ap = self.state.procesos[name]
            if not ap.is_alive():
                ap.should_run = False
                return self._send_json({"message": f"{name} already stopped"})
            ap.stop()
            return self._send_json({"message": f"{name} stopped"})

            if name not in procesos:
                agent_dir = os.path.join(self.agents_dir, name)
                config_path = os.path.join(agent_dir, "config.json")
                if not os.path.isfile(config_path):
                    return self._send_json({"error": f"Agent '{name}' not found"}, 404)
                config = self.state.read_json(config_path)
                self.state.appl_def(config)
                errs = self.state.val_cfg(config)
                if errs:
                    detail = "\n".join(f"- {e}" for e in errs)
                    return self._send_json(
                        {"error": f"Start failed: config.json validation failed with {len(errs)} error(s):\n{detail}"},
                        400,
                    )
                port_err = self.state.chk_port(config)
                if port_err:
                    return self._send_json({"error": f"Start failed: {port_err}"}, 400)
                ap = self.state.AgentProcess(agent_dir, config)
                with launcher_lock:
                    used_ports = [p.actual_port for p in self.state.procesos.values() if p.is_alive()]
                ap.start(allocated_ports=used_ports)
                self.state.procesos[name] = ap
                return self._send_json({"message": f"{name} started", "pid": ap.process.pid, "port": ap.actual_port})
            ap = self.state.procesos[name]
            if ap.is_alive():
                ap.stop()
                time.sleep(1)
            ap.reload_config()
            self.state.appl_def(ap.config)
            errs = self.state.val_cfg(ap.config)
            if errs:
                detail = "\n".join(f"- {e}" for e in errs)
                return self._send_json(
                    {"error": f"Restart failed: config.json validation failed with {len(errs)} error(s):\n{detail}"},
                    400,
                )
            port_err = self.state.chk_port(ap.config)
            if port_err:
                return self._send_json({"error": f"Restart failed: {port_err}"}, 400)
            ap.should_run = True
            ap.restart_count = 0
            ap.start()
            return self._send_json({"message": f"{name} restarted", "pid": ap.process.pid})

            if name not in procesos:
                return self._send_json({"error": f"Agent '{name}' not found"}, 404)
            logs = self.state.procesos[name].get_logs(lines)
            return self._send_json({"agent": name, "logs": logs, "total": len(logs)})

            config_path = os.path.join(self.agents_dir, name, "config.json")
            if not os.path.isfile(config_path):
                return self._send_json({"error": "Config not found"}, 404)
            config = self.state.read_json(config_path)
            if "group_chat" in config and "password" in config.get("group_chat", {}):
                config["group_chat"]["password"] = "********"
            return self._send_json({"agent": name, "config": config})

            config_path = os.path.join(self.agents_dir, name, "config.json")
            agent_dir = os.path.join(self.agents_dir, name)
            if not os.path.isdir(agent_dir):
                return self._send_json({"error": "Agent directory not found"}, 404)
            config_data = body.get("config")
            if not config_data:
                return self._send_json({"error": "Missing 'config' in body"}, 400)

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
                return list(dict.fromkeys(out))

            if isinstance(config_data, dict):
                pp = config_data.get("prompt_preload")
                if isinstance(pp, dict):
                    pp["hidden_plugins"] = _norm_str_list(pp.get("hidden_plugins", []))
                    pp["full_skills"] = _norm_str_list(pp.get("full_skills", []))
                    pp["hidden_skills"] = _norm_str_list(pp.get("hidden_skills", []))
                    pp["mcp_full_servers"] = _norm_str_list(pp.get("mcp_full_servers", []))
                    pp["mcp_hidden_servers"] = _norm_str_list(pp.get("mcp_hidden_servers", []))

            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config_data, f, ensure_ascii=False, indent=2)
            if name in procesos:
                self.state.procesos[name].reload_config()
            return self._send_json({"message": f"Config saved for {name}"})

            agent_dir = os.path.join(self.agents_dir, name)
            role_filename = "role.md"
            config_path = os.path.join(agent_dir, "config.json")
            if os.path.isfile(config_path):
                try:
                    with open(config_path, encoding="utf-8") as f:
                        cfg = json.load(f)
                    role_filename = cfg.get("prompt", {}).get("role", "role.md") or "role.md"
                except (OSError, ValueError):
                    pass
            role_path = os.path.join(agent_dir, role_filename)
            if not os.path.isfile(role_path) and role_filename != "role.md":
                role_path = os.path.join(agent_dir, "role.md")
            if not os.path.isfile(role_path):
                return self._send_json({"agent": name, "content": ""})
            with open(role_path, encoding="utf-8") as f:
                content = f.read()
            return self._send_json({"agent": name, "content": content})

            agent_dir = os.path.join(self.agents_dir, name)
            if not os.path.isdir(agent_dir):
                return self._send_json({"error": "Agent directory not found"}, 404)
            content = body.get("content", "")
            role_filename = "role.md"
            config_path = os.path.join(agent_dir, "config.json")
            if os.path.isfile(config_path):
                try:
                    with open(config_path, encoding="utf-8") as f:
                        cfg = json.load(f)
                    role_filename = cfg.get("prompt", {}).get("role", "role.md") or "role.md"
                except (OSError, ValueError):
                    pass
            role_path = os.path.join(agent_dir, role_filename)
            with open(role_path, "w", encoding="utf-8") as f:
                f.write(content)
            return self._send_json({"message": f"Role saved for {name}"})

            name = (body.get("name") or "").strip()
            if not name:
                return self._send_json({"error": "Missing 'name'"}, 400)
            if not all(c.isalnum() or c == "_" for c in name):
                return self._send_json({"error": "Name must be alphanumeric/underscore"}, 400)
            agent_dir = os.path.join(self.agents_dir, name)
            if os.path.exists(agent_dir):
                return self._send_json({"error": f"Agent '{name}' already exists"}, 400)

            agent_id = body.get("agent_id", f"{name}-001")
            chat_email = body.get("chat_email", "")
            chat_password = body.get("chat_password", "")
            group_chat_enabled = bool(chat_email and chat_password)

            os.makedirs(agent_dir, exist_ok=True)
            default_config = {
                "agent_id": agent_id,
                "agent_name": body.get("agent_name", name),
                "agent_type": body.get("agent_type", "general"),
                "description": body.get("description", ""),
                "capabilities": [],
                "model": {
                    "api_key": "",
                    "base_url": "",
                    "model_name": "",
                    "tool_call_mode": "auto",
                    "tool_filter": "high",
                },
                "tools": [
                    "system",
                    "filesystem",
                    "agent_setup",
                    "im",
                    "collaboration",
                    "delegate_task",
                    "workspace",
                    "task_watch",
                    "websearch",
                    "reminder",
                    "vision",
                    "mcp_query",
                    "plugin_admin",
                ],
                "group_chat": {
                    "enabled": group_chat_enabled,
                    "email": chat_email,
                    "password": chat_password,
                },
                "web_server": {"enabled": True},
                "gateway": {"enabled": True, "url": state.syscfg.gateway_register_url()},
                "prompt": {"role": "role.md"},
                "mcp": {"enabled": True},
                "skills": {"enabled": True, "active": []},
            }
            with open(os.path.join(agent_dir, "config.json"), "w", encoding="utf-8") as f:
                json.dump(default_config, f, ensure_ascii=False, indent=2)

            # Default MCP config
            default_mcp_config = {
                "mcpServers": {
                    "filesystem": {
                        "enabled": True,
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
                        "timeout": 30,
                    },
                    "sequential-thinking": {
                        "enabled": True,
                        "command": "npx",
                        "args": ["-y", "@langgpt/sequential-thinking-mcp"],
                        "timeout": 30,
                    },
                    "windows-cli": {
                        "enabled": True,
                        "command": "npx",
                        "args": ["-y", "@simonb97/server-win-cli"],
                        "timeout": 30,
                        "autoApprove": ["execute_command"],
                    },
                }
            }
            with open(os.path.join(agent_dir, "mcp_config.json"), "w", encoding="utf-8") as f:
                json.dump(default_mcp_config, f, ensure_ascii=False, indent=2)

            with open(os.path.join(agent_dir, "role.md"), "w", encoding="utf-8") as f:
                f.write(f"# {body.get('agent_name', name)}\n\nWrite the role definition here.\n")

            return self._send_json(
                {
                    "message": f"Agent '{name}' created",
                    "dir": name,
                    "mcp_config": "Created with lightweight services enabled",
                }
            )

            all_discovered = self.state.disc_agents(self.state.agents_dir)
            new_count = 0
            for info in all_discovered:
                if info["name"] not in procesos:
                    ap = self.state.AgentProcess(info["dir"], info["config"])
                    self.state.procesos[info["name"]] = ap
                    new_count += 1
            return self._send_json({"message": f"Rescan complete, {new_count} new agent(s) found"})

            if not all(c.isalnum() or c == "_" for c in name):
                return self._send_json({"error": "Invalid agent name"}, 400)
            agent_dir = os.path.join(self.agents_dir, name)
            if not os.path.isdir(agent_dir):
                return self._send_json({"error": f"Agent '{name}' not found"}, 404)
            if name in procesos:
                ap = self.state.procesos[name]
                if ap.is_alive():
                    ap.stop()
                del self.state.procesos[name]
            try:
                shutil.rmtree(agent_dir)
            except Exception as e:
                return self._send_json({"error": f"Failed to delete directory: {e}"}, 500)
            return self._send_json({"message": f"Agent '{name}' deleted"})

            stats = self._read_token_stats(name)
            if stats is None:
                return self._send_json({"agent": name, "token_stats": None})
            return self._send_json({"agent": name, "token_stats": stats})

            candidates = [
                os.path.join(self.agents_dir, name, "data", "ai_his_talk", "token_stats.json"),
                os.path.join(self.agents_dir, name, "ai_his_talk", "token_stats.json"),
            ]
            for path in candidates:
                if os.path.isfile(path):
                    try:
                        with open(path, encoding="utf-8") as f:
                            return json.load(f)
                    except (OSError, ValueError):
                        pass
            return None

            path = os.path.join(self.agents_dir, name, "data", "profile.json")
            if os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        return json.load(f)
                except (OSError, ValueError):
                    pass
            return None

        # ════════════════════════════════════════════════════════════════════
        #  Plugin handlers
        # ════════════════════════════════════════════════════════════════════

        _skipped_dirs: set = set()

        def _find_plugin_dir_OLD(self, plugin_name: str):
            # Strategy 1: directory name matches directly
            direct = os.path.join(self.plugins_dir, plugin_name)
            if os.path.isdir(direct) and os.path.isfile(os.path.join(direct, "plugin.py")):
                return direct, plugin_name
            # Strategy 2: scan for matching plugin.json["name"]
            if os.path.isdir(self.state.plugins_dir):
                for entry in os.listdir(self.state.plugins_dir):
                    plugin_dir = os.path.join(self.plugins_dir, entry)
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

            plugins = []
            if not os.path.isdir(self.state.plugins_dir):
                return self._send_json({"plugins": []})
            for name in sorted(os.listdir(self.state.plugins_dir)):
                plugin_dir = os.path.join(self.plugins_dir, name)
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
                    sys_cfg_path = state.syscfg.workspace_config_path()
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
                    state.logger.warning(f"[Launcher] Failed to sync services config: {e}", exc_info=True)
            # Reload signal
            try:
                reload_ts_path = os.path.join(self.plugins_dir, ".reload_ts")
                with open(reload_ts_path, "w") as f:
                    f.write(str(time.time()))
            except (OSError, ValueError):
                pass
            # Auto-start/stop service
            if meta.get("service_toggle") and name in plug_svcs:
                psp = self.state.plug_svcs[name]
                if enabled:
                    if not psp.is_alive():
                        service_cfg = meta.get("service", {})
                        if service_cfg.get("auto_start"):
                            psp.start()
                else:
                    if psp.is_alive():
                        psp.stop()
            action = "enabled" if enabled else "disabled"

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
                    sys_cfg_path = state.syscfg.workspace_config_path()
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
                config_path = state.syscfg.workspace_data_dir("plugins", name, "config.json")
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
                    sys_cfg_path = state.syscfg.workspace_config_path()
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
                config_dir = state.syscfg.workspace_data_dir("plugins", name)
                config_path = os.path.join(config_dir, "config.json")
                try:
                    os.makedirs(config_dir, exist_ok=True)
                    with open(config_path, "w", encoding="utf-8") as f:
                        json.dump(config_values, f, indent=2, ensure_ascii=False)
                except (OSError, ValueError) as e:
                    return self._send_json({"error": f"Failed to write config: {e}"}, 500)
            try:
                reload_ts_path = os.path.join(self.plugins_dir, ".reload_ts")
                with open(reload_ts_path, "w") as f:
                    f.write(str(time.time()))
            except (OSError, ValueError):
                pass
            return self._send_json({"ok": True, "message": f"Config saved for '{name}'"})

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
                result = mod.query_data(state.syscfg.project_root(), params)
                return self._send_json(result)
            except Exception as e:
                return self._send_json({"error": f"Query failed: {e}"}, 500)

            import datetime as _dt

            plugin_name = body.get("plugin_name", "unknown")
            view_key = body.get("view_key", "")
            error_msg = body.get("error", "")
            stack = body.get("stack", "")
            log_path = os.path.join(self.plugins_dir, plugin_name, "view_errors.log")
            try:
                os.makedirs(os.path.dirname(log_path), exist_ok=True)
                ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                entry = f"[{ts}] view={view_key}\n  error: {error_msg}\n  stack: {stack[:800]}\n{'─' * 60}\n"
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(entry)
                return self._send_json({"ok": True, "log": log_path})
            except Exception as e:
                return self._send_json({"ok": False, "error": str(e)}, 500)

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
                result = mod.handle_action(state.syscfg.project_root(), action, data)
                return self._send_json(result)
            except Exception as e:
                return self._send_json({"error": f"Action failed: {e}"}, 500)

            plugin_id = (body.get("plugin_id") or "").strip()
            zip_b64 = body.get("zip_b64", "")
            if not plugin_id or not zip_b64:
                return self._send_json({"error": "plugin_id and zip_b64 are required"}, 400)
            try:
                zip_bytes = base64.b64decode(zip_b64)
            except Exception as e:
                return self._send_json({"error": f"Invalid base64: {e}"}, 400)
            plugin_dest = os.path.join(self.plugins_dir, plugin_id)
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
            os.makedirs(self.plugins_dir, exist_ok=True)
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
                reload_ts_path = os.path.join(self.plugins_dir, ".reload_ts")
                with open(reload_ts_path, "w") as f:
                    f.write(str(time.time()))
            except (OSError, ValueError):
                pass
            action = "updated" if existing_version else "installed"
            return self._send_json({"ok": True, "action": action, "plugin_id": plugin_id})

            # ════════════════════════════════════════════════════════════════════
            #  Plugin service handlers
            # ════════════════════════════════════════════════════════════════════

            with launcher_lock:
                result = [psp.get_status() for psp in self.state.plug_svcs.values()]
            return self._send_json({"plugin_services": result})

            discovered = self.state.disc_plug_svcs(self.state.plugins_dir)
            services = []
            for info in discovered:
                pid = info["plugin_id"]
                if pid in plug_svcs:
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
                        "auto_start": state.syscfg.is_service_enabled(pid),
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

            if plugin_id not in plug_svcs:
                return self._send_json({"error": f"Plugin service '{plugin_id}' not found"}, 404)
            psp = self.state.plug_svcs[plugin_id]
            if psp.is_alive():
                return self._send_json({"error": f"{plugin_id} already running"}, 400)
            self._set_service_enabled_in_config(plugin_id, True)
            psp.port = psp._resolve_port()
            psp.start()
            pid_val = psp.process.pid if psp.process else None
            return self._send_json({"message": f"{plugin_id} started", "pid": pid_val, "port": psp.port})

            if plugin_id not in plug_svcs:
                return self._send_json({"error": f"Plugin service '{plugin_id}' not found"}, 404)
            psp = self.state.plug_svcs[plugin_id]
            if not psp.is_alive():
                psp.should_run = False
                self._set_service_enabled_in_config(plugin_id, False)
                return self._send_json({"message": f"{plugin_id} already stopped"})
            psp.stop()
            self._set_service_enabled_in_config(plugin_id, False)
            return self._send_json({"message": f"{plugin_id} stopped"})

            enabled = body.get("enabled", True) if isinstance(body, dict) else True
            self._set_service_enabled_in_config(plugin_id, enabled)
            return self._send_json({"ok": True, "plugin_id": plugin_id, "auto_start": enabled})

        def _set_service_enabled_in_config_OLD(self, plugin_id: str, enabled: bool):
            try:
                sys_cfg_path = state.syscfg.workspace_config_path()
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
                state.logger.warning(f"[Launcher] Failed to sync services.{plugin_id}.enabled: {e}", exc_info=True)

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

            if plugin_id not in plug_svcs:
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

            if plugin_id not in plug_svcs:
                return self._send_json({"error": f"Plugin service '{plugin_id}' not found"}, 404)
            logs = self.state.plug_svcs[plugin_id].get_logs(lines)
            return self._send_json({"plugin_id": plugin_id, "logs": logs, "total": len(logs)})

            force_kill = body.get("force_kill", False) if isinstance(body, dict) else False
            result = self.state.cln_reg(force_kill=force_kill)
            return self._send_json(result)

        # ════════════════════════════════════════════════════════════════════
        #  Session handlers (for remote Gateway access)
        # ════════════════════════════════════════════════════════════════════

        def _get_session_reader_OLD(self, agent_id: str):
            try:
                import importlib.util as _ilu

                _mod_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
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
                state.logger.warning(f"[Launcher] Failed to get session reader for {agent_id}: {e}", exc_info=True)
                return None

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

            reader = self._get_session_reader(agent_id)
            if reader is None:
                return self._send_json({"error": f"Agent not found: {agent_id}"}, 404)
            ok = reader.delete_session(session_id)
            return self._send_json({"ok": ok})

            # ════════════════════════════════════════════════════════════════════
            #  MCP handlers (central + per-agent)
            # ════════════════════════════════════════════════════════════════════

            central_path = syscfg.workspace_data_dir("mcp_config.json")
            if not os.path.isfile(central_path):
                merged = {}
                if os.path.isdir(self.state.agents_dir):
                    for dname in sorted(os.listdir(self.state.agents_dir)):
                        agent_mcp = os.path.join(self.agents_dir, dname, "mcp_config.json")
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

            mcp_servers = body.get("mcpServers")
            if mcp_servers is None:
                return self._send_json({"error": "Missing 'mcpServers' in body"}, 400)
            central_path = state.syscfg.workspace_data_dir("mcp_config.json")
            os.makedirs(os.path.dirname(central_path), exist_ok=True)
            try:
                payload = {"mcpServers": mcp_servers}
                with open(central_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                synced = []
                if os.path.isdir(self.state.agents_dir):
                    for dname in os.listdir(self.state.agents_dir):
                        agent_dir = os.path.join(self.agents_dir, dname)
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

            agent_dir = os.path.join(self.agents_dir, name)
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

            agent_dir = os.path.join(self.agents_dir, name)
            if not os.path.isdir(agent_dir):
                return self._send_json({"error": f"Agent '{name}' not found"}, 404)
            mcp_servers = body.get("mcpServers")
            if mcp_servers is None:
                return self._send_json({"error": "Missing 'mcpServers' in body"}, 400)
            mcp_path = os.path.join(agent_dir, "mcp_config.json")
            try:
                with open(mcp_path, "w", encoding="utf-8") as f:
                    json.dump({"mcpServers": mcp_servers}, f, ensure_ascii=False, indent=2)
                restarted = False
                if name in self.state.procesos and self.state.procesos[name].is_alive():
                    try:
                        self.state.procesos[name].restart()
                        restarted = True
                    except (OSError, ValueError):
                        pass
                return self._send_json(
                    {"ok": True, "message": f"MCP config saved for '{name}'", "restarted": restarted}
                )
            except (OSError, ValueError) as e:
                return self._send_json({"error": f"Failed to write mcp_config.json: {e}"}, 500)

            global_path = syscfg.workspace_data_dir("mcp_global.json")
            if not os.path.isfile(global_path):
                return self._send_json({"servers": {}})
            try:
                with open(global_path, encoding="utf-8-sig") as f:
                    data = json.load(f)
                return self._send_json({"servers": data.get("servers", {})})
            except (OSError, ValueError) as e:
                return self._send_json({"error": f"Failed to read mcp_global.json: {e}"}, 500)

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
            except (OSError, ValueError) as e:
                return self._send_json({"error": f"Failed to write mcp_global.json: {e}"}, 500)

            # ════════════════════════════════════════════════════════════════════
            #  Skills handlers
            # ════════════════════════════════════════════════════════════════════

            skills = []
            if not os.path.isdir(self.state.skills_dir):
                return self._send_json({"skills": []})
            for skill_name in sorted(os.listdir(self.state.skills_dir)):
                skill_dir = os.path.join(self.skills_dir, skill_name)
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

            if not re.match(r"^[a-zA-Z0-9_\-]+$", name):
                return self._send_json({"error": "Invalid skill name"}, 400)
            skill_dir = os.path.join(self.skills_dir, name)
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
            _TEXT_EXTS = {
                ".py",
                ".md",
                ".txt",
                ".yaml",
                ".yml",
                ".json",
                ".toml",
                ".cfg",
                ".ini",
                ".sh",
                ".bat",
                ".ps1",
            }
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

        # ════════════════════════════════════════════════════════════════════
        #  Role/Collab/Model card handlers
        # ════════════════════════════════════════════════════════════════════

        def _list_cards_OLD(self, cards_dir: str):
            cards = []
            if not os.path.isdir(cards_dir):
                return cards
            for fname in sorted(os.listdir(cards_dir)):
                if not fname.endswith(".md"):
                    continue
                card_name = fname[:-3]
                fpath = os.path.join(cards_dir, fname)
                try:
                    with open(fpath, encoding="utf-8") as f:
                        content = f.read()
                except Exception:
                    content = ""
                fm = {}
                body = content
                if content.startswith("---"):
                    end = content.find("\n---", 3)
                    if end != -1:
                        fm_text = content[3:end].strip()
                        for line in fm_text.splitlines():
                            if ":" in line:
                                k, _, v = line.partition(":")
                                fm[k.strip()] = v.strip()
                        body = content[end + 4 :].lstrip("\n")
                title = fm.get("name", card_name)
                for line in body.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("# "):
                        title = stripped[2:].strip()
                        break
                tags_raw = fm.get("tags", "")
                tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
                description = fm.get("description", "") or " ".join(body.split())[:100]
                cards.append(
                    {
                        "name": card_name,
                        "title": title,
                        "description": description,
                        "tags": tags,
                        "char_count": len(content),
                    }
                )
            return cards

            return self._send_json({"cards": self._list_cards(self.state.role_cards_dir)})

            fpath = os.path.join(self.role_cards_dir, f"{card_name}.md")
            if not os.path.isfile(fpath):
                return self._send_json({"error": "Card not found"}, 404)
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            return self._send_json({"name": card_name, "content": content})

            os.makedirs(self.role_cards_dir, exist_ok=True)
            content = body.get("content", "")
            fpath = os.path.join(self.role_cards_dir, f"{card_name}.md")
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)
            return self._send_json({"ok": True, "name": card_name})

            fpath = os.path.join(self.role_cards_dir, f"{card_name}.md")
            if not os.path.isfile(fpath):
                return self._send_json({"error": "Card not found"}, 404)
            os.remove(fpath)
            return self._send_json({"ok": True, "name": card_name})

            return self._send_json({"cards": self._list_cards(self.state.collab_cards_dir)})

            fpath = os.path.join(self.collab_cards_dir, f"{card_name}.md")
            if not os.path.isfile(fpath):
                return self._send_json({"error": "Card not found"}, 404)
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            return self._send_json({"name": card_name, "content": content})

            os.makedirs(self.collab_cards_dir, exist_ok=True)
            content = body.get("content", "")
            fpath = os.path.join(self.collab_cards_dir, f"{card_name}.md")
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)
            return self._send_json({"ok": True, "name": card_name})

            fpath = os.path.join(self.collab_cards_dir, f"{card_name}.md")
            if not os.path.isfile(fpath):
                return self._send_json({"error": "Card not found"}, 404)
            os.remove(fpath)
            return self._send_json({"ok": True, "name": card_name})

            agent_dir = os.path.join(self.agents_dir, name)
            if not os.path.isdir(agent_dir):
                return self._send_json({"error": "Agent not found"}, 404)
            content = body.get("content", "")
            card_name = body.get("card_name", "")
            with open(os.path.join(agent_dir, "role_prompt.md"), "w", encoding="utf-8") as f:
                f.write(content)
            config_path = os.path.join(agent_dir, "config.json")
            cfg = self.state.read_json(config_path)
            cfg.setdefault("prompt", {})["role"] = "role_prompt.md"
            cfg["prompt"]["role_card"] = card_name
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            if name in procesos:
                self.state.procesos[name].reload_config()
            return self._send_json({"ok": True})

            agent_dir = os.path.join(self.agents_dir, name)
            if not os.path.isdir(agent_dir):
                return self._send_json({"error": "Agent not found"}, 404)
            config_path = os.path.join(agent_dir, "config.json")
            cfg = self.state.read_json(config_path)
            cfg.setdefault("prompt", {})["role"] = "role.md"
            cfg["prompt"].pop("role_card", None)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            rp = os.path.join(agent_dir, "role_prompt.md")
            if os.path.isfile(rp):
                os.remove(rp)
            if name in procesos:
                self.state.procesos[name].reload_config()
            return self._send_json({"ok": True})

            # ── Model Cards ──────────────────────────────────────────────────────

            cards = []
            if not os.path.isdir(self.state.model_cards_dir):
                return self._send_json({"cards": cards})
            for fname in sorted(os.listdir(self.state.model_cards_dir)):
                if not fname.endswith(".json"):
                    continue
                fpath = os.path.join(self.model_cards_dir, fname)
                try:
                    with open(fpath, encoding="utf-8") as f:
                        data = json.load(f)
                except (json.JSONDecodeError, OSError):
                    data = {}
                card_name = fname[:-5]
                cards.append(
                    {
                        "name": card_name,
                        "title": data.get("title", card_name),
                        "api_protocol": data.get("api_protocol", ""),
                        "provider": data.get("provider", ""),
                        "model_name": data.get("model_name", ""),
                        "base_url": data.get("base_url", ""),
                        "token_max": data.get("token_max", 0),
                        "temperature": data.get("temperature", 0),
                        "frequency_penalty": data.get("frequency_penalty", 0.0),
                        "presence_penalty": data.get("presence_penalty", 0.0),
                        "top_k": data.get("top_k", 0),
                        "is_think": data.get("is_think", False),
                        "is_image": data.get("is_image", False),
                        "is_audio": data.get("is_audio", False),
                        "is_video": data.get("is_video", False),
                        "is_audio_output": data.get("is_audio_output", False),
                        "is_image_output": data.get("is_image_output", False),
                        "audio_output_voice": data.get("audio_output_voice", "alloy"),
                        "render_mode": data.get("render_mode", "strict"),
                    }
                )
            return self._send_json({"cards": cards})

            fpath = os.path.join(self.model_cards_dir, f"{card_name}.json")
            if not os.path.isfile(fpath):
                return self._send_json({"error": "Card not found"}, 404)
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
            if "render_mode" not in data:
                data["render_mode"] = "strict"
            return self._send_json({"name": card_name, "card": data})

            os.makedirs(self.model_cards_dir, exist_ok=True)
            card = {
                "name": card_name,
                "title": body.get("title", card_name),
                "api_protocol": body.get("api_protocol", "openai_compat"),
                "provider": body.get("provider", ""),
                "api_key": body.get("api_key", ""),
                "base_url": body.get("base_url", ""),
                "model_name": body.get("model_name", ""),
                "token_max": body.get("token_max", 128000),
                "tool_output_max_chars": body.get("tool_output_max_chars", 50000),
                "temperature": body.get("temperature", 0),
                "frequency_penalty": body.get("frequency_penalty", 0.0),
                "presence_penalty": body.get("presence_penalty", 0.0),
                "top_k": body.get("top_k", 0),
                "is_think": body.get("is_think", False),
                "is_image": body.get("is_image", False),
                "is_audio": body.get("is_audio", False),
                "is_video": body.get("is_video", False),
                "is_audio_output": body.get("is_audio_output", False),
                "is_image_output": body.get("is_image_output", False),
                "audio_output_voice": body.get("audio_output_voice", "alloy"),
                "render_mode": body.get("render_mode", "strict"),
            }
            fpath = os.path.join(self.model_cards_dir, f"{card_name}.json")
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(card, f, ensure_ascii=False, indent=2)
            return self._send_json({"ok": True, "name": card_name})

            fpath = os.path.join(self.model_cards_dir, f"{card_name}.json")
            if not os.path.isfile(fpath):
                return self._send_json({"error": "Card not found"}, 404)
            os.remove(fpath)
            return self._send_json({"ok": True, "name": card_name})

            agent_dir = os.path.join(self.agents_dir, name)
            if not os.path.isdir(agent_dir):
                return self._send_json({"error": "Agent not found"}, 404)
            config_path = os.path.join(agent_dir, "config.json")
            cfg = self.state.read_json(config_path)
            cfg["model"] = {
                "api_protocol": body.get("api_protocol", "openai_compat"),
                "provider": body.get("provider", ""),
                "api_key": body.get("api_key", ""),
                "base_url": body.get("base_url", ""),
                "model_name": body.get("model_name", ""),
                "token_max": body.get("token_max", 128000),
                "tool_output_max_chars": body.get("tool_output_max_chars", 50000),
                "temperature": body.get("temperature", 0),
                "frequency_penalty": body.get("frequency_penalty", 0.0),
                "presence_penalty": body.get("presence_penalty", 0.0),
                "top_k": body.get("top_k", 0),
                "is_think": body.get("is_think", False),
                "is_image": body.get("is_image", False),
                "is_audio_model": body.get("is_audio", False),
                "is_video": body.get("is_video", False),
                "is_audio_output": body.get("is_audio_output", False),
                "is_image_output": body.get("is_image_output", False),
                "audio_output_voice": body.get("audio_output_voice", "alloy"),
                "render_mode": body.get("render_mode", "strict"),
                "_card": body.get("name", ""),
            }
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            if name in procesos:
                self.state.procesos[name].reload_config()
            return self._send_json({"ok": True})

            agent_dir = os.path.join(self.agents_dir, name)
            if not os.path.isdir(agent_dir):
                return self._send_json({"error": "Agent not found"}, 404)
            config_path = os.path.join(agent_dir, "config.json")
            cfg = self.state.read_json(config_path)
            cfg.setdefault("model", {}).pop("_card", None)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            if name in procesos:
                self.state.procesos[name].reload_config()
            return self._send_json({"ok": True})

            # ════════════════════════════════════════════════════════════════════
            #  Workspace handlers
            # ════════════════════════════════════════════════════════════════════

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
                except Exception as e:
                    self.state.logger.debug(f"[LauncherAPI] Suppressed: {e}")
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
                    except Exception as e:
                        self.state.logger.debug(f"[LauncherAPI] Suppressed: {e}")
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

            from datetime import datetime as _dt

            from opensquad.workspace_utils import get_default_workspace_path, save_last_workspace

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
                    state.syscfg.init_workspace(workspace_path, copy_config=True)
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
                state.syscfg.init_workspace(workspace_path, copy_config=True)
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

            from datetime import datetime as _dt

            from opensquad.workspace_utils import save_last_workspace

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
                return self._send_json(
                    {
                        "success": True,
                        "message": "Workspace switched; please restart the Launcher for the change to take effect",
                        "path": workspace_path,
                        "requires_restart": True,
                    }
                )
            except Exception as e:
                return self._send_json({"error": f"Failed to switch workspace: {e}"}, 500)

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

            import uuid

            source = (body.get("source") or "").strip()
            target = (body.get("target") or "").strip()
            mode = body.get("mode", "copy")
            conflict = body.get("conflict", "skip")
            if not source or not target:
                return self._send_json({"error": "Missing 'source' or 'target'"}, 400)
            task_id = str(uuid.uuid4())
            ws_mig[task_id] = {
                "status": "pending",
                "progress": 0.0,
                "message": "Waiting to start...",
                "report": None,
            }

            def _run():
                try:
                    ws_mig[task_id]["status"] = "running"
                    ws_mig[task_id]["message"] = "Migrating data..."

                    def _progress(msg: str):
                        import re as _re

                        m = _re.search(r"\[(\d+)/(\d+)\]", msg)
                        if m:
                            cur, tot = int(m.group(1)), int(m.group(2))
                            ws_mig[task_id]["progress"] = round(cur / tot, 2) if tot else 0.0
                        ws_mig[task_id]["message"] = msg

                    from opensquad.migration_tool import LegacyDataMigrator

                    migrator = LegacyDataMigrator(
                        install_dir=source,
                        target_workspace=target,
                        mode=mode,
                        overwrite=(conflict == "overwrite"),
                    )
                    report = migrator.migrate(progress_callback=_progress)
                    ws_mig[task_id]["status"] = "completed"
                    ws_mig[task_id]["progress"] = 1.0
                    ws_mig[task_id]["message"] = f"Migration complete: {len(report.success)} item(s) succeeded"
                    ws_mig[task_id]["report"] = report.to_dict()
                except Exception as e:
                    ws_mig[task_id]["status"] = "failed"
                    ws_mig[task_id]["message"] = f"Migration failed: {e}"

            _threading = __import__("threading")
            _threading.Thread(target=_run, daemon=True, name=f"ws-migrate-{task_id[:8]}").start()
            return self._send_json(
                {
                    "success": True,
                    "task_id": task_id,
                    "message": "Migration task started",
                }
            )

            task = ws_mig.get(task_id)
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

    # ── Build HandlerState and attach to class ──
    ManagementHandler.state = HandlerState(
        procesos=procesos,
        plug_svcs=plug_svcs,
        task_hb=task_hb,
        task_sn=task_sn,
        shut_ev=shut_ev,
        ws_mig=ws_mig,
        agents_dir=agents_dir,
        plugins_dir=plugins_dir,
        skills_dir=skills_dir,
        role_cards_dir=role_cards_dir,
        collab_cards_dir=collab_cards_dir,
        model_cards_dir=model_cards_dir,
        mgmt_port=mgmt_port,
        stall_thresh=stall_thresh,
        launcher_lock=launcher_lock,
        syscfg=syscfg,
        logger=logger,
        read_json=read_json,
        chk_port=chk_port,
        res_disc_port=res_disc_port,
        cln_reg=cln_reg,
        appl_def=appl_def,
        val_cfg=val_cfg,
        disc_agents=disc_agents,
        disc_plug_svcs=disc_plug_svcs,
        AgentProcess=AgentProcess,
        PluginServiceProcess=PluginServiceProcess,
        builtin_plugins=builtin_plugins,
    )
    return ManagementHandler


def _start_management_server(
    port: int,
    *,
    launcher_lock: Any = None,
    procesos: dict[str, Any],
    plug_svcs: dict[str, Any],
    task_hb: dict[str, dict[str, Any]],
    task_sn: set,
    shut_ev: Any,
    ws_mig: dict[str, Any],
    agents_dir: str,
    plugins_dir: str,
    skills_dir: str,
    role_cards_dir: str,
    collab_cards_dir: str,
    model_cards_dir: str,
    mgmt_port: int,
    stall_thresh: int,
    syscfg: Any,
    logger: Any,
    read_json: Any,
    chk_port: Any,
    res_disc_port: Any,
    cln_reg: Any,
    appl_def: Any,
    val_cfg: Any,
    disc_agents: Any,
    disc_plug_svcs: Any,
    AgentProcess: Any,
    PluginServiceProcess: Any,
) -> None:
    """
    Start the HTTP management server bound to the runtime state provided
    by launcher.py.

    This function is called by launcher.py's ``_start_management_server`` wrapper.
    It builds a ManagementHandler via ``create_management_handler`` and runs
    a ThreadingHTTPServer on the given port.
    """
    # Resolve builtin plugins (same logic as module-level in original launcher.py)
    _builtin_plugins: dict = {}
    _builtin_plugins_path = os.path.join(plugins_dir, "builtin_plugins.json")
    if os.path.isfile(_builtin_plugins_path):
        try:
            with open(_builtin_plugins_path, encoding="utf-8") as _bf:
                _bp_data = json.load(_bf)
                _builtin_plugins = _bp_data.get("plugins", {})
        except Exception as e:
            logger.debug(f"[LauncherAPI] Suppressed: {e}")

    handler = create_management_handler(
        shut_ev=shut_ev,
        launcher_lock=launcher_lock,
        logger=logger,
        procesos=procesos,
        plug_svcs=plug_svcs,
        task_hb=task_hb,
        task_sn=task_sn,
        ws_mig=ws_mig,
        agents_dir=agents_dir,
        plugins_dir=plugins_dir,
        skills_dir=skills_dir,
        role_cards_dir=role_cards_dir,
        collab_cards_dir=collab_cards_dir,
        model_cards_dir=model_cards_dir,
        mgmt_port=mgmt_port,
        stall_thresh=stall_thresh,
        syscfg=syscfg,
        read_json=read_json,
        chk_port=chk_port,
        res_disc_port=res_disc_port,
        cln_reg=cln_reg,
        appl_def=appl_def,
        val_cfg=val_cfg,
        disc_agents=disc_agents,
        disc_plug_svcs=disc_plug_svcs,
        AgentProcess=AgentProcess,
        PluginServiceProcess=PluginServiceProcess,
        builtin_plugins=_builtin_plugins,
    )
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    logger.info("[Launcher] Management API started on http://0.0.0.0:%s", port)
    server.serve_forever()
