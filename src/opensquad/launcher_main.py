"""
launcher.py - Multi-Agent Process Manager + HTTP Management API

Scans the agents/ directory for all subdirectories with config.json,
and launches an independent boot.py process for each agent.
Also provides an HTTP management API (:9600) for the Gateway to call,
enabling Web UI start/stop/status/log operations.

Usage:
    python launcher.py                    # Start all agents + management port
    python launcher.py --only ultimate    # Start only the specified agent
    python launcher.py --exclude coder    # Exclude the specified agent
    python launcher.py --no-auto-start    # Don't auto-start agents, only open management port (wait for Web UI to start manually)

Features:
    - Auto-discover agent directories
    - Independent process launch (fully isolated global state)
    - Auto-restart on crash (configurable max retry count)
    - Graceful Ctrl+C shutdown of all processes
    - Console log aggregation (with agent name prefix)
    - HTTP management API (:9600): list/start/stop/restart/logs/config read-write
"""

import argparse
import asyncio
import base64
import contextlib
import io
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import zipfile

_log = logging.getLogger("launcher")

# ── Force UTF-8 console output on Windows ──────────────────────────────────
# The launcher is started directly by the user whose terminal may default to
# cp936 (GBK).  Reconfiguring stdout/stderr here ensures all print() calls
# and log lines (including forwarded child-process lines) appear correctly in
# UTF-8-capable terminals (Windows Terminal, VS Code, etc.).
# The PYTHONIOENCODING/PYTHONUTF8 env vars are also set so that any NEW child
# processes inherit UTF-8 by default (child agent processes already get these
# via child_env in AgentProcess.start()).
if sys.platform == "win32":
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            with contextlib.suppress(Exception):
                _s.reconfigure(encoding="utf-8", errors="replace")

from opensquad.system_config import syscfg

# Project root is where agents/ and plugins/ live (the workspace)
PROJECT_ROOT = syscfg.project_root()

# ── Workspace path (user data) ──
AGENTS_DIR = syscfg.workspace_agents_dir()

# ── Writable workspace resource paths (user installs / edits) ──
PLUGINS_DIR = syscfg.workspace_plugins_dir()
SKILLS_DIR = syscfg.workspace_skills_dir()
ROLE_CARDS_DIR = syscfg.workspace_role_cards_dir()
COLLAB_CARDS_DIR = syscfg.workspace_collab_cards_dir()
MODEL_CARDS_DIR = syscfg.workspace_model_cards_dir()

# ── Read-only bundled seeds (PyInstaller _internal/ in frozen desktop) ──
BUILTIN_PLUGINS_DIR = syscfg.builtin_resources_dir("plugins")
BUILTIN_SKILLS_DIR = syscfg.builtin_resources_dir("skills")

# Built-in plugin registry: loaded from builtin_plugins.json at startup
_BUILTIN_PLUGINS: dict = {}  # name -> {"default_enabled": bool}
_builtin_plugins_path = os.path.join(BUILTIN_PLUGINS_DIR, "builtin_plugins.json")
if os.path.isfile(_builtin_plugins_path):
    try:
        with open(_builtin_plugins_path, encoding="utf-8") as _bf:
            _bp_data = json.load(_bf)
            _BUILTIN_PLUGINS = _bp_data.get("plugins", {})
    except Exception:
        pass


def _plugin_search_dirs() -> list[str]:
    return syscfg.resource_search_dirs("plugins")


def _skill_search_dirs() -> list[str]:
    return syscfg.resource_search_dirs("skills")


def discover_all_plugin_services() -> list[dict]:
    """Scan workspace + builtin plugin dirs; workspace wins on duplicate ids."""
    seen: set[str] = set()
    result: list[dict] = []
    for plugins_dir in _plugin_search_dirs():
        for info in discover_plugin_services(plugins_dir):
            pid = info["plugin_id"]
            if pid in seen:
                continue
            seen.add(pid)
            result.append(info)
    return result


def _collect_plugin_dirs() -> dict[str, str]:
    """Map dir_name -> plugin_dir; workspace entries override builtin."""
    out: dict[str, str] = {}
    for root in (BUILTIN_PLUGINS_DIR, PLUGINS_DIR):
        if not os.path.isdir(root):
            continue
        for entry in os.listdir(root):
            plugin_dir = os.path.join(root, entry)
            if not os.path.isdir(plugin_dir):
                continue
            if not os.path.isfile(os.path.join(plugin_dir, "plugin.py")):
                continue
            out[entry] = plugin_dir
    return out


def _find_skill_dir(name: str) -> str | None:
    for root in _skill_search_dirs():
        skill_dir = os.path.join(root, name)
        if os.path.isdir(skill_dir):
            return skill_dir
    return None


def _collect_skill_dirs() -> dict[str, str]:
    """Map skill dir_name -> path; workspace overrides builtin."""
    out: dict[str, str] = {}
    for root in (BUILTIN_SKILLS_DIR, SKILLS_DIR):
        if not os.path.isdir(root):
            continue
        for entry in os.listdir(root):
            skill_dir = os.path.join(root, entry)
            if os.path.isdir(skill_dir):
                out[entry] = skill_dir
    return out


# BOOT_SCRIPT is now inside the package
import opensquad

BOOT_SCRIPT_DIR = os.path.dirname(os.path.abspath(opensquad.__file__))
BOOT_MODULE = "opensquad.agents_boot"

# ── Process management (extracted to opensquad.launcher.process_manager) ──
from opensquad.launcher.process_manager import (
    MANAGEMENT_PORT,
    MAX_RESTART_ATTEMPTS,
    RUNTIME_REGISTRY_DIR,
    STABLE_RESET_SECONDS,
    AgentProcess,
    PluginServiceProcess,
    _cleanup_runtime_registry,
    _install_builtin_plugin_deps,
    _kill_port_owner,
    _read_json,
    _resolve_discovery_port,
    check_port_conflict,
)

# Workspace migration background task status table (shared across requests)
_workspace_migration_tasks: dict = {}


import contextlib

from opensquad.agent_config_schema import apply_config_defaults, validate_agent_config


def discover_plugin_services(plugins_dir: str) -> list[dict]:
    """
    Scan the plugins/ directory, return info for all plugins that have a service field.
    Returns [{plugin_id, plugin_dir, service_cfg, plugin_enabled, display_name, plugin_type, dependencies}, ...]
    """
    result = []
    if not os.path.isdir(plugins_dir):
        return result
    for name in sorted(os.listdir(plugins_dir)):
        plugin_dir = os.path.join(plugins_dir, name)
        plugin_json_path = os.path.join(plugin_dir, "plugin.json")
        if not os.path.isdir(plugin_dir) or not os.path.isfile(plugin_json_path):
            continue
        try:
            with open(plugin_json_path, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue
        service_cfg = meta.get("service")
        if not service_cfg:
            continue
        result.append(
            {
                "plugin_id": name,
                "plugin_dir": plugin_dir,
                "service_cfg": service_cfg,
                "plugin_enabled": meta.get("enabled", True),
                "display_name": meta.get("display_name", name),
                "plugin_type": meta.get("type", "tool"),
                "dependencies": meta.get("dependencies", {}),
            }
        )
    return result


def discover_agents(agents_dir: str, only: list[str] | None = None, exclude: list[str] | None = None) -> list[dict]:
    """
    Scan the agents/ directory for all subdirectories with config.json.
    Returns [{dir, name, config}, ...]
    """
    agents = []

    if not os.path.isdir(agents_dir):
        _log.info(f"[Launcher] Agents directory not found: {agents_dir}")
        return agents

    for entry in sorted(os.listdir(agents_dir)):
        entry_path = os.path.join(agents_dir, entry)
        config_path = os.path.join(entry_path, "config.json")

        if not os.path.isdir(entry_path) or not os.path.exists(config_path):
            continue

        # boot.py itself is not an agent directory
        if entry in ("__pycache__", ".git") or entry.startswith(".") or entry.startswith("_"):
            continue

        # Filter
        if only and entry not in only:
            continue
        if exclude and entry in exclude:
            continue

        try:
            with open(config_path, encoding="utf-8-sig") as f:
                config = json.load(f)
            if not isinstance(config, dict):
                _log.info(
                    f"[Launcher] Invalid config type in {config_path}: expected object, got {type(config).__name__}. Skipping this agent."
                )
                continue
            agents.append({"dir": entry_path, "name": entry, "config": config})
        except Exception as e:
            _log.error(f"[Launcher] Error loading {config_path}: {e}")

    return agents


# ═══════════════════════════════════════════════════════════
#  HTTP Management API — for Gateway / Web UI use
# ═══════════════════════════════════════════════════════════

# Global process table, populated by main(), accessible to HTTP handler
_processes: dict[str, AgentProcess] = {}

# Plugin service process table, populated by main(), accessible to HTTP handler
_plugin_services: dict[str, "PluginServiceProcess"] = {}

# Task watch heartbeats from agent processes (agent_id -> last heartbeat info)
# Format: {"agent_id": {"description": "...", "last_update": 1234567890, "event": "start"|"update"|"complete"}}
_task_watch_heartbeats: dict[str, dict] = {}
_task_watch_stalled_notified: set = set()  # agent IDs already notified to avoid flooding

# Global shutdown event — set by signal handler so all daemon threads exit promptly
_shutdown_event = threading.Event()

# Parsed CLI args (set by _parse_args_and_discover_agents). Phase functions that
# take no args read flags off this (e.g. _init_and_start_plugin_services checks
# _ARGS.no_services to skip plugin auto-start in frozen-bundle safe mode).
_ARGS = None


def _start_node_registration_thread(mgmt_port: int):
    """
    Multi-node deployment: Launcher registers this node with Gateway and sends heartbeat every 60s.

    Trigger condition: system_config.json node.register_to_gateway = true
    Gateway address: syscfg.gateway_http()
    This node's launcher_url: http://{externally-reachable IP}:{mgmt_port}
    """
    import urllib.error
    import urllib.request

    node_id = syscfg.node_id()
    node_label = syscfg.node_label()
    gateway = syscfg.gateway_http()
    token = syscfg.auth("gateway_token")

    # If system_config.json has node.launcher_url configured, use it directly; otherwise build from local IP
    launcher_url = syscfg.launcher_url()

    def _post(path: str, payload: dict):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{gateway}/api/ai-web{path}",
            data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read())

    def _put(path: str, payload: dict):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{gateway}/api/ai-web{path}",
            data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read())

    def _run():
        # Wait for management server to start (up to 10s)
        time.sleep(3)
        registered = False
        while not _shutdown_event.is_set():
            try:
                if not registered:
                    _post(
                        "/nodes/register",
                        {
                            "node_id": node_id,
                            "node_label": node_label,
                            "launcher_url": launcher_url,
                        },
                    )
                    _log.info(f"[Launcher] Registered node {node_id!r} to Gateway at {gateway}")
                    registered = True
                else:
                    agent_count = sum(1 for ap in _processes.values() if ap.is_alive())
                    _put(f"/nodes/{node_id}/heartbeat", {"agent_count": agent_count})
            except Exception as e:
                _log.info(f"[Launcher] Node registration/heartbeat failed: {e}")
                registered = False
            _shutdown_event.wait(timeout=60)

    t = threading.Thread(target=_run, daemon=True, name="node-heartbeat")
    t.start()
    _log.info(f"[Launcher] Node self-registration thread started (node_id={node_id!r})")


def _start_launcher_ws_tunnel(management_port: int):
    """
    Establish a Launcher → Gateway WebSocket management tunnel.

    Launcher (on-premise) proactively connects to the cloud Gateway's /ai-ws/launcher.
    Gateway can reverse-send admin_requests (GET/POST/PUT/DELETE) over this connection.
    Launcher forwards requests to the local HTTP management server (localhost:{management_port})
    and returns responses to Gateway via WS.

    This allows the cloud Web UI to manage on-premise Agents with no port forwarding or frp needed.
    """
    import urllib.error as _uerr
    import urllib.request as _ureq

    node_id = syscfg.node_id()
    node_label = syscfg.node_label()
    local_base = f"http://127.0.0.1:{management_port}"

    async def _ws_rpc_loop():
        import websockets  # pip install websockets (already in backend requirements)

        gateway_ws = syscfg.gateway_ws()  # e.g. ws://cloud:9555
        url = f"{gateway_ws}/ai-ws/launcher"

        while not _shutdown_event.is_set():
            try:
                async with websockets.connect(
                    url,
                    ping_interval=15,
                    ping_timeout=8,
                    open_timeout=15,
                    proxy=None,
                ) as ws:
                    # Register this node
                    await ws.send(
                        json.dumps(
                            {
                                "type": "launcher_register",
                                "node_id": node_id,
                                "node_label": node_label,
                                "node_secret": syscfg.node_secret(),
                            }
                        )
                    )
                    _log.info(f"[Launcher] WS admin tunnel connected → {url} (node={node_id!r})")

                    # ── Keepalive task: send a lightweight heartbeat every 12s ──
                    # Uvicorn's default ws_ping_interval is 20s; if the server-side
                    # ping doesn't arrive (Windows timing quirks), the connection
                    # sits idle and may be dropped. Our keepalive ensures there is
                    # *always* application-level traffic well inside that window.
                    _keepalive_interval = 12

                    async def _keepalive():
                        while True:
                            await asyncio.sleep(_keepalive_interval)
                            try:
                                await ws.send(json.dumps({"type": "keepalive"}))
                            except Exception:
                                break

                    _ka_task = asyncio.create_task(_keepalive())

                    try:
                        async for raw in ws:
                            try:
                                msg = json.loads(raw)
                            except Exception:
                                continue

                            if msg.get("type") == "keepalive":
                                continue

                            if msg.get("type") != "admin_request":
                                continue

                            req_id = msg.get("req_id", "")
                            method = msg.get("method", "GET").upper()
                            path = msg.get("path", "/")
                            body = msg.get("body")

                            # Relay to local HTTP management server
                            try:
                                data = json.dumps(body).encode("utf-8") if body else None
                                headers = {"Content-Type": "application/json"} if data else {}
                                req = _ureq.Request(
                                    f"{local_base}{path}",
                                    data=data,
                                    headers=headers,
                                    method=method,
                                )
                                with _ureq.urlopen(req, timeout=15) as resp:
                                    resp_body = json.loads(resp.read())
                                await ws.send(
                                    json.dumps(
                                        {
                                            "type": "admin_response",
                                            "req_id": req_id,
                                            "status": 200,
                                            "body": resp_body,
                                        }
                                    )
                                )
                            except _uerr.HTTPError as e:
                                err_body = {}
                                with contextlib.suppress(Exception):
                                    err_body = json.loads(e.read())
                                await ws.send(
                                    json.dumps(
                                        {
                                            "type": "admin_response",
                                            "req_id": req_id,
                                            "status": e.code,
                                            "body": err_body,
                                        }
                                    )
                                )
                            except Exception as e:
                                await ws.send(
                                    json.dumps(
                                        {
                                            "type": "admin_response",
                                            "req_id": req_id,
                                            "status": 502,
                                            "body": {"error": str(e)},
                                        }
                                    )
                                )
                    finally:
                        _ka_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await _ka_task

            except websockets.exceptions.ConnectionClosed:
                _log.info("[Launcher] WS tunnel disconnected (normal). Reconnecting in 3s…")
                await asyncio.sleep(3)
            except OSError as e:
                _log.error(f"[Launcher] WS tunnel disconnected (Windows socket error {e.errno}). Reconnecting in 2s…")
                await asyncio.sleep(2)
            except Exception as e:
                _log.info(f"[Launcher] WS tunnel disconnected: {e}. Reconnecting in 3s…")
                await asyncio.sleep(3)

    def _run():
        asyncio.run(_ws_rpc_loop())

    t = threading.Thread(target=_run, daemon=True, name="launcher-ws-tunnel")
    t.start()
    _log.info(f"[Launcher] WS admin tunnel thread started (connecting to {syscfg.gateway_ws()}/ai-ws/launcher)")


def _start_management_server(port: int = MANAGEMENT_PORT):
    """Start the HTTP management server in a dedicated thread"""
    import hashlib
    import secrets
    import urllib.parse
    from http.server import BaseHTTPRequestHandler
    from http.server import ThreadingHTTPServer as _ThreadingHTTPServer

    class ManagementHandler(BaseHTTPRequestHandler):
        """Lightweight HTTP handler — no FastAPI/uvicorn dependency, minimal external deps"""

        # --- Token authentication ---
        @staticmethod
        def _get_launcher_token() -> str:
            """Read launcher token from system_config, fallback to empty string."""
            try:
                return syscfg.get("launcher_token", "")
            except Exception:
                return ""

        @staticmethod
        def _encrypt_password(password: str) -> str:
            """Encrypt password using SHA-256 with a salt."""
            salt = secrets.token_hex(16)
            hashed = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
            return f"{salt}${hashed}"

        @staticmethod
        def _verify_password(password: str, stored: str) -> bool:
            """Verify password against stored salt$hash."""
            if "$" not in stored:
                # Backward compatibility: plain text fallback (will be upgraded on next write)
                return password == stored
            salt, hashed = stored.split("$", 1)
            return hashlib.sha256((salt + password).encode("utf-8")).hexdigest() == hashed

        def _check_auth(self) -> bool:
            """Verify Bearer token from Authorization header. Returns True if valid or no token required."""
            token = self._get_launcher_token()
            if not token:
                return True  # No token configured, allow all
            auth_header = self.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                self._send_json({"error": "Unauthorized", "message": "Bearer token required"}, 401)
                return False
            provided = auth_header[7:]
            if provided != token:
                self._send_json({"error": "Forbidden", "message": "Invalid token"}, 403)
                return False
            return True

        def log_message(self, format, *args):
            # Silence logs to avoid console spam
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
                # Client already disconnected — nothing we can do, just skip
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
                raise  # Let the caller catch this and abort processing

        def _require_auth_and_call(self, handler_fn):
            """Wrapper: check auth, then call the actual handler function."""
            if self._check_auth():
                try:
                    handler_fn()
                except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError, OSError):
                    # Client disconnected before we could respond — skip silently
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

        def do_GET(self):
            self._require_auth_and_call(self._do_get_impl)

        def _do_get_impl(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/")
            qs = urllib.parse.parse_qs(parsed.query)

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
            elif path.startswith("/api/agents/") and path.endswith("/working-directory"):
                # GET /api/agents/{name}/working-directory
                # Returns the agent's current session working directory (if set)
                # plus the permanent workspace root.
                name = path.split("/")[3]
                return self._handle_get_working_directory(name)
            elif path.startswith("/api/agents/") and path.endswith("/role"):
                name = path.split("/")[3]
                return self._handle_get_role(name)
            elif path == "/api/plugins":
                return self._handle_list_plugins()
            elif path.startswith("/api/plugins/") and path.endswith("/config"):
                name = path.split("/")[3]
                return self._handle_get_plugin_config(name)
            elif path.startswith("/api/plugins/") and path.endswith("/data"):
                name = path.split("/")[3]
                return self._handle_get_plugin_data(name, qs)
            elif path == "/api/skills":
                return self._handle_list_skills()
            elif path.startswith("/api/skills/") and path.endswith("/source"):
                skill_name = path.split("/")[3]
                return self._handle_get_skill_source(skill_name)
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
            elif path.startswith("/api/agents/") and path.endswith("/mcp"):
                name = path.split("/")[3]
                return self._handle_get_mcp(name)
            elif path == "/api/mcp/config":
                return self._handle_get_mcp_central()
            elif path == "/api/mcp/global":
                return self._handle_get_mcp_global()
            elif path == "/api/task_watch_status":
                # PM can query all workers' heartbeats
                result = {}
                now = time.time()
                for aid, hb in _task_watch_heartbeats.items():
                    result[aid] = {
                        "event": hb.get("event", "unknown"),
                        "detail": hb.get("detail", ""),
                        "elapsed_sec": round(now - hb.get("last_update", 0), 1),
                        "stalled": (now - hb.get("last_update", 0)) > STALL_THRESHOLD,
                    }
                return self._send_json({"workers": result})
            elif path == "/api/ping":
                return self._send_json({"status": "ok", "service": "launcher"})
            elif path == "/api/system/pick-directory":
                # GET kept for discovery; real pick is POST (blocks until dialog closes)
                return self._send_json(
                    {
                        "status": "ok",
                        "message": "POST /api/system/pick-directory to open a native folder dialog",
                    }
                )
            elif path == "/api/workspace":
                return self._send_json(
                    {
                        "workspace": syscfg.get_workspace(),
                        "agents_dir": syscfg.workspace_agents_dir(),
                    }
                )
            elif path == "/api/workspace/list":
                return self._handle_workspace_list()
            elif path == "/api/workspace/detect-legacy":
                return self._handle_workspace_detect_legacy()
            elif path.startswith("/api/workspace/migrate/status/"):
                task_id = path[len("/api/workspace/migrate/status/") :]
                return self._handle_workspace_migrate_status(task_id)
            elif path == "/api/services/manage":
                return self._handle_services_manage()
            elif path == "/api/plugin-services":
                return self._handle_list_plugin_services()
            elif path.startswith("/api/plugin-services/") and path.endswith("/logs"):
                pid = path.split("/")[3]
                lines = int(qs.get("lines", ["200"])[0])
                return self._handle_plugin_service_logs(pid, lines)
            elif path == "/api/shutdown":
                body = self._read_body()
                return self._handle_shutdown(body)
            elif path == "/api/runtime/list":
                return self._handle_runtime_list()
            # ── Agent session endpoints (for remote Gateway access) ──
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

        def do_POST(self):
            # Internal endpoints: bypass auth (agent → launcher communication on localhost)
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/")
            if path == "/_internal/task_watch_heartbeat":
                self._handle_task_watch_heartbeat()
                return
            self._require_auth_and_call(self._do_post_impl)

        def _do_post_impl(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/")

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
            elif path == "/api/plugin-view-error":
                body = self._read_body()
                return self._handle_plugin_view_error(body)
            elif path == "/api/resources/upload":
                body = self._read_body()
                return self._handle_resource_upload(body)
            elif path.startswith("/api/plugins/") and path.endswith("/action"):
                name = path.split("/")[3]
                body = self._read_body()
                return self._handle_plugin_action(name, body)
            elif path == "/api/plugins/install-zip":
                body = self._read_body()
                return self._handle_install_zip_plugin(body)
            elif path.startswith("/api/plugin-services/") and path.endswith("/restart"):
                pid = path.split("/")[3]
                return self._handle_plugin_service_restart(pid)
            elif path.startswith("/api/plugin-services/") and path.endswith("/start"):
                pid = path.split("/")[3]
                return self._handle_plugin_service_start(pid)
            elif path.startswith("/api/plugin-services/") and path.endswith("/stop"):
                pid = path.split("/")[3]
                return self._handle_plugin_service_stop(pid)
            # ── Agent session delete / rename ──
            elif re.search(r"^/api/sessions/[^/]+/[^/]+/delete$", path):
                parts = path.split("/")
                agent_id, session_id = parts[3], parts[4]
                return self._handle_session_delete(agent_id, session_id)
            elif re.search(r"^/api/sessions/[^/]+/[^/]+/rename$", path):
                parts = path.split("/")
                agent_id, session_id = parts[3], parts[4]
                body = self._read_body()
                return self._handle_session_rename(agent_id, session_id, body)
            elif path == "/api/workspace/create":
                body = self._read_body()
                return self._handle_workspace_create(body)
            elif path == "/api/workspace/switch":
                body = self._read_body()
                return self._handle_workspace_switch(body)
            elif path == "/api/workspace/migrate":
                body = self._read_body()
                return self._handle_workspace_migrate(body)
            elif path == "/api/system/pick-directory":
                body = self._read_body() or {}
                return self._handle_pick_directory(body)
            elif path == "/api/runtime/cleanup":
                body = self._read_body()
                return self._handle_runtime_cleanup(body)
            else:
                return self._send_json({"error": "Not found"}, 404)

        def _handle_task_watch_heartbeat(self):
            """POST /_internal/task_watch_heartbeat — agent reports task progress to launcher."""
            try:
                body = self._read_body()
                import json

                data = json.loads(body)
                agent_id = data.get("agent_id", "")
                if not agent_id:
                    return self._send_json({"error": "Missing agent_id"}, 400)
                global _task_watch_heartbeats, _task_watch_stalled_notified
                _task_watch_heartbeats[agent_id] = {
                    "event": data.get("event", "unknown"),
                    "task_id": data.get("task_id", ""),
                    "detail": data.get("detail", ""),
                    "last_update": data.get("timestamp", time.time()),
                }
                # Worker recovered: clear stall notification flag so future stalls are caught again
                _task_watch_stalled_notified.discard(agent_id)
                return self._send_json({"ok": True})
            except Exception as e:
                return self._send_json({"error": str(e)}, 500)

        def do_PUT(self):
            self._require_auth_and_call(self._do_put_impl)

        def _do_put_impl(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/")

            if path.startswith("/api/agents/") and path.endswith("/config"):
                name = path.split("/")[3]
                body = self._read_body()
                return self._handle_put_config(name, body)
            elif path.startswith("/api/agents/") and path.endswith("/role"):
                name = path.split("/")[3]
                body = self._read_body()
                return self._handle_put_role(name, body)
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
            elif path.startswith("/api/plugin-services/") and path.endswith("/auto-start"):
                name = path.split("/")[3]
                body = self._read_body()
                return self._handle_plugin_service_auto_start(name, body)
            elif path.startswith("/api/agents/") and path.endswith("/mcp"):
                # PUT /api/agents/{name}/mcp  — replace entire mcp_config.json
                name = path.split("/")[3]
                body = self._read_body()
                return self._handle_put_mcp(name, body)
            elif path == "/api/mcp/config":
                body = self._read_body()
                return self._handle_put_mcp_central(body)
            elif path.startswith("/api/mcp/global/servers/") and path.endswith("/enable"):
                srv = path.split("/")[5]
                return self._handle_put_mcp_server_global(srv, True)
            elif path.startswith("/api/mcp/global/servers/") and path.endswith("/disable"):
                srv = path.split("/")[5]
                return self._handle_put_mcp_server_global(srv, False)
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
            elif path.startswith("/api/agents/") and path.endswith("/model-card"):
                name = path.split("/")[3]
                body = self._read_body()
                return self._handle_put_model_card(name, body)
            elif path.startswith("/api/agents/") and path.endswith("/working-directory"):
                # PUT /api/agents/{name}/working-directory
                # Sets the agent's session-level working directory (cwd) for
                # shell commands and file operations. Writes a .session_cwd
                # signal file that the agent process picks up at the start
                # of the next conversation turn.
                name = path.split("/")[3]
                body = self._read_body()
                return self._handle_set_working_directory(name, body)
            elif path.startswith("/api/agents/") and path.endswith("/role-prompt"):
                name = path.split("/")[3]
                body = self._read_body()
                return self._handle_put_role_prompt(name, body)
            else:
                return self._send_json({"error": "Not found"}, 404)

        def do_DELETE(self):
            self._require_auth_and_call(self._do_delete_impl)

        def _do_delete_impl(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/")

            parts = path.split("/")
            # DELETE /api/agents/{name}
            if len(parts) == 4 and parts[1] == "api" and parts[2] == "agents":
                name = parts[3]
                return self._handle_delete(name)
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

        # ── handlers ──

        def _handle_list_agents(self):
            """Return all discovered agents and their process status"""
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

            self._send_json({"agents": result})

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

        def _handle_pick_directory(self, body: dict | None = None):
            """POST /api/system/pick-directory — native OS folder dialog on this host.

            Body (optional): ``{"initial_dir": "C:\\\\ai_test"}``
            """
            initial = ""
            if isinstance(body, dict):
                initial = str(body.get("initial_dir") or body.get("path") or "").strip()
            try:
                from opensquad.utils.pick_directory import pick_directory

                result = pick_directory(initial or None)
            except Exception as e:
                return self._send_json({"path": None, "error": str(e)}, 500)

            path = result.get("path")
            if path:
                _log.info(f"[Launcher] pick-directory selected: {path}")
                return self._send_json({"path": path, "cancelled": False})
            if result.get("cancelled"):
                return self._send_json({"path": None, "cancelled": True})
            return self._send_json(
                {"path": None, "cancelled": False, "error": result.get("error") or "Folder pick failed"},
                500,
            )

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

        def _handle_create(self, body: dict):
            """Create a new agent directory"""
            name = (body.get("name") or "").strip()
            if not name:
                return self._send_json({"error": "Missing 'name'"}, 400)
            # Security check: only allow alphanumeric and underscore
            if not all(c.isalnum() or c == "_" for c in name):
                return self._send_json({"error": "Name must be alphanumeric/underscore"}, 400)
            agent_dir = os.path.join(AGENTS_DIR, name)
            if os.path.exists(agent_dir):
                return self._send_json({"error": f"Agent '{name}' already exists"}, 400)

            # Support externally supplied agent_id (6-digit numeric ID generated by routes.py)
            # Fall back to old format "{name}-001" if not provided
            agent_id = body.get("agent_id", f"{name}-001")

            # Group chat account credentials (generated and passed in by routes.py)
            chat_email = body.get("chat_email", "") or "ai@ai"
            chat_password = body.get("chat_password", "") or "aaaaaa"

            os.makedirs(agent_dir, exist_ok=True)
            # Create default config.json
            default_config = {
                "agent_id": agent_id,
                "agent_name": body.get("agent_name", name),
                "agent_type": body.get("agent_type", "general"),
                "description": body.get("description", ""),
                "capabilities": [],
                "model": {
                    "api_protocol": "openai_compat",  # Default: OpenAI-compatible API (most third-party providers)
                    "provider": "",
                    "api_key": "",
                    "base_url": "",
                    "model_name": "",
                    "tool_call_mode": "auto",  # Default: auto-detect (recommended) — use Native FC if supported, otherwise fall back to XML
                    "tool_filter": "high",  # Default: load common tools (97) — balance between features and performance
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
                    "enabled": True,
                    "email": chat_email,
                    "password": chat_password,
                    "groups": [],
                },
                "web_server": {"enabled": True},
                "gateway": {"enabled": True, "url": syscfg.gateway_register_url()},
                "prompt": {"role": "role.md"},
                "mcp": {"enabled": True},  # Default: MCP enabled; specific services are controlled in mcp_config.json
                "skills": {
                    "enabled": True,
                    "active": [],
                },  # Default: skills enabled; public skill library auto-discovered
            }
            with open(os.path.join(agent_dir, "config.json"), "w", encoding="utf-8") as f:
                json.dump(default_config, f, ensure_ascii=False, indent=2)

            # Create Agent-specific MCP config — copy from central config if it exists
            central_mcp_path = syscfg.workspace_data_dir("mcp_config.json")
            if os.path.isfile(central_mcp_path):
                try:
                    with open(central_mcp_path, encoding="utf-8") as f:
                        default_mcp_config = json.load(f)
                except Exception:
                    default_mcp_config = None
            else:
                default_mcp_config = None
            if not default_mcp_config or "mcpServers" not in default_mcp_config:
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
                        "playwright": {
                            "enabled": True,
                            "command": "npx",
                            "args": ["-y", "@playwright/mcp"],
                            "timeout": 30,
                        },
                        "chrome-devtools": {
                            "enabled": False,
                            "command": "npx",
                            "args": ["chrome-devtools-mcp@latest"],
                            "timeout": 30,
                            "autoApprove": ["start_browser"],
                        },
                        "github": {
                            "enabled": False,
                            "command": "npx",
                            "args": ["-y", "@modelcontextprotocol/server-github"],
                            "timeout": 30,
                            "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": ""},
                        },
                        "zai-mcp-server": {
                            "enabled": False,
                            "command": "npx",
                            "args": ["-y", "@z_ai/mcp-server"],
                            "timeout": 60,
                            "env": {"Z_AI_API_KEY": "", "Z_AI_MODE": "ZHIPU"},
                        },
                    }
                }
            with open(os.path.join(agent_dir, "mcp_config.json"), "w", encoding="utf-8") as f:
                json.dump(default_mcp_config, f, ensure_ascii=False, indent=2)

            # Create empty role.md
            with open(os.path.join(agent_dir, "role.md"), "w", encoding="utf-8") as f:
                f.write(f"# {body.get('agent_name', name)}\n\nWrite the role definition here.\n")

            return self._send_json(
                {
                    "message": f"Agent '{name}' created",
                    "dir": name,
                    "mcp_config": "Created with lightweight services enabled (filesystem, sequential-thinking, windows-cli)",
                }
            )

        def _handle_rescan(self):
            """Re-scan the agents/ directory to discover new agents"""
            all_discovered = discover_agents(AGENTS_DIR)
            new_count = 0
            for info in all_discovered:
                if info["name"] not in _processes:
                    ap = AgentProcess(info["dir"], info["config"])
                    _processes[info["name"]] = ap
                    new_count += 1
            return self._send_json({"message": f"Rescan complete, {new_count} new agent(s) found"})

        def _handle_delete(self, name: str):
            """Delete agent: stop process first, then delete directory and remove from process table"""
            # Security check: only allow alphanumeric and underscore
            if not all(c.isalnum() or c == "_" for c in name):
                return self._send_json({"error": "Invalid agent name"}, 400)

            agent_dir = os.path.join(AGENTS_DIR, name)
            if not os.path.isdir(agent_dir):
                return self._send_json({"error": f"Agent '{name}' not found"}, 404)

            # Stop process if it is running
            if name in _processes:
                ap = _processes[name]
                if ap.is_alive():
                    ap.stop()
                del _processes[name]

            # Delete directory
            try:
                shutil.rmtree(agent_dir)
            except Exception as e:
                return self._send_json({"error": f"Failed to delete directory: {e}"}, 500)

            return self._send_json({"message": f"Agent '{name}' deleted"})

        # ── plugin handlers ──

        def _handle_install_zip_plugin(self, body: dict):
            """
            POST /api/plugins/install-zip
            Receive a plugin zip broadcast from Gateway and extract/install to the local plugins/ directory.
            Request body: { "plugin_id": str, "zip_b64": str (base64-encoded zip content) }
            """
            plugin_id = (body.get("plugin_id") or "").strip()
            zip_b64 = body.get("zip_b64", "")
            if not plugin_id or not zip_b64:
                return self._send_json({"error": "plugin_id and zip_b64 are required"}, 400)

            try:
                zip_bytes = base64.b64decode(zip_b64)
            except Exception as e:
                return self._send_json({"error": f"Invalid base64: {e}"}, 400)

            plugin_dest = os.path.join(PLUGINS_DIR, plugin_id)
            existing_manifest = os.path.join(plugin_dest, "plugin.json")
            existing_plugin_py_path = os.path.join(plugin_dest, "plugin.py")

            # Preserve existing enabled state and plugin.py
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
                except Exception:
                    pass
            if os.path.isfile(existing_plugin_py_path):
                try:
                    with open(existing_plugin_py_path, "rb") as f:
                        existing_plugin_py = f.read()
                except Exception:
                    pass

            # Extract zip
            os.makedirs(PLUGINS_DIR, exist_ok=True)
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

            # Restore plugin.py
            if existing_plugin_py is not None:
                try:
                    with open(existing_plugin_py_path, "wb") as f:
                        f.write(existing_plugin_py)
                except Exception as e:
                    _log.warning(f"[Launcher] Warning: Failed to restore plugin.py for '{plugin_id}': {e}")

            # Restore enabled state and category
            if os.path.isfile(existing_manifest):
                try:
                    with open(existing_manifest, encoding="utf-8") as f:
                        new_manifest = json.load(f)
                    new_manifest["enabled"] = existing_enabled
                    # Restore category only if the new zip does not carry one
                    if existing_category and not new_manifest.get("category"):
                        new_manifest["category"] = existing_category
                    with open(existing_manifest, "w", encoding="utf-8") as f:
                        json.dump(new_manifest, f, indent=2, ensure_ascii=False)
                except Exception as e:
                    _log.warning(f"[Launcher] Warning: Failed to restore enabled state for '{plugin_id}': {e}")

            # Trigger hot-reload
            try:
                reload_ts_path = os.path.join(PLUGINS_DIR, ".reload_ts")
                with open(reload_ts_path, "w") as f:
                    f.write(str(time.time()))
            except Exception as e:
                _log.warning(f"[Launcher] Warning: Failed to write .reload_ts: {e}")

            action = "updated" if existing_version else "installed"
            _log.info(f"[Launcher] Plugin '{plugin_id}' {action} via broadcast zip install")
            return self._send_json({"ok": True, "action": action, "plugin_id": plugin_id})

        def _find_plugin_dir(self, name: str):
            """
            Resolve a plugin name to its directory path.

            Tries two strategies:
            1. Direct match: dir/name exists as a directory with plugin.py
            2. Name field match: scan all plugin dirs, find one where plugin.json["name"] == name

            Returns (plugin_dir, dir_name) tuple, or (None, None) if not found.
            """
            for root in _plugin_search_dirs():
                direct = os.path.join(root, name)
                if os.path.isdir(direct) and os.path.isfile(os.path.join(direct, "plugin.py")):
                    return direct, name

            for root in _plugin_search_dirs():
                if not os.path.isdir(root):
                    continue
                for entry in os.listdir(root):
                    plugin_dir = os.path.join(root, entry)
                    if not os.path.isdir(plugin_dir):
                        continue
                    if not os.path.isfile(os.path.join(plugin_dir, "plugin.py")):
                        continue
                    manifest = os.path.join(plugin_dir, "plugin.json")
                    if os.path.isfile(manifest):
                        try:
                            with open(manifest, encoding="utf-8") as f:
                                meta = json.load(f)
                            if meta.get("name") == name:
                                return plugin_dir, entry
                        except Exception:
                            pass

            return None, None

        def _writable_plugin_json_path(self, name: str) -> tuple[str, str] | tuple[None, None]:
            """Return (plugin_json_path, dir_name) under workspace for writes."""
            plugin_dir, dir_name = self._find_plugin_dir(name)
            if not dir_name:
                return None, None
            writable_dir = os.path.join(PLUGINS_DIR, dir_name)
            os.makedirs(writable_dir, exist_ok=True)
            return os.path.join(writable_dir, "plugin.json"), dir_name

        # 类级别的跳过目录缓存（跨请求持久），避免每次 HTTP 请求都刷屏
        _skipped_dirs: set = set()

        def _handle_list_plugins(self):
            """Scan plugins/ directory, read plugin.json for each, return list."""
            plugins = []
            plugin_dirs = _collect_plugin_dirs()
            if not plugin_dirs:
                return self._send_json({"plugins": []})

            for name in sorted(plugin_dirs):
                plugin_dir = plugin_dirs[name]
                plugin_json_path = os.path.join(plugin_dir, "plugin.json")
                if os.path.isfile(plugin_json_path):
                    try:
                        with open(plugin_json_path, encoding="utf-8") as f:
                            meta = json.load(f)
                    except Exception:
                        meta = {}
                else:
                    meta = {}

                is_builtin = name in _BUILTIN_PLUGINS

                # For built-in plugins without a plugin.json, enforce default enabled state
                if is_builtin and not meta:
                    bp_cfg = _BUILTIN_PLUGINS[name]
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
            """Set plugin enabled/disabled by updating plugin.json."""
            plugin_dir, dir_name = self._find_plugin_dir(name)
            if not plugin_dir or not dir_name:
                return self._send_json({"error": f"Plugin '{name}' not found"}, 404)

            plugin_json_path, _ = self._writable_plugin_json_path(name)
            if not plugin_json_path:
                return self._send_json({"error": f"Plugin '{name}' not found"}, 404)

            if os.path.isfile(plugin_json_path):
                try:
                    with open(plugin_json_path, encoding="utf-8") as f:
                        meta = json.load(f)
                except Exception:
                    meta = {}
            else:
                src_manifest = os.path.join(plugin_dir, "plugin.json")
                meta = {"name": name}
                if os.path.isfile(src_manifest):
                    try:
                        with open(src_manifest, encoding="utf-8") as f:
                            meta = json.load(f)
                    except Exception:
                        meta = {"name": name}

            # service_only plugins cannot be enabled — they have no agent tools
            if enabled and meta.get("service_only"):
                return self._send_json({"error": f"Plugin '{name}' is service_only and cannot be enabled"}, 400)

            meta["enabled"] = enabled

            try:
                with open(plugin_json_path, "w", encoding="utf-8") as f:
                    json.dump(meta, f, indent=2, ensure_ascii=False)
            except Exception as e:
                return self._send_json({"error": f"Failed to write plugin.json: {e}"}, 500)

            # Sync service_toggle plugins to system_config.json services.{name}.enabled
            if meta.get("service_toggle"):
                try:
                    sys_cfg_path = syscfg.workspace_config_path()
                    with open(sys_cfg_path, encoding="utf-8") as f:
                        full_cfg = json.load(f)
                    if "services" not in full_cfg:
                        full_cfg["services"] = {}
                    if name not in full_cfg["services"]:
                        full_cfg["services"][name] = {}
                    full_cfg["services"][name]["enabled"] = enabled
                    with open(sys_cfg_path, "w", encoding="utf-8") as f:
                        json.dump(full_cfg, f, indent=2, ensure_ascii=False)
                    # Invalidate system_config cache
                    from opensquad import system_config as _syscfg_mod

                    _syscfg_mod._cache = None
                    _log.info(f"[Launcher] Synced services.{name}.enabled = {enabled}")
                except Exception as e:
                    _log.warning(f"[Launcher] Warning: Failed to sync services config: {e}")

            # Write reload signal file so running agents detect the change
            try:
                reload_ts_path = os.path.join(PLUGINS_DIR, ".reload_ts")
                with open(reload_ts_path, "w") as f:
                    f.write(str(time.time()))
            except Exception as e:
                _log.warning(f"[Launcher] Warning: Failed to write .reload_ts: {e}")

            # Auto-start/stop service for service_toggle plugins
            if meta.get("service_toggle") and name in _plugin_services:
                psp = _plugin_services[name]
                if enabled:
                    # Start service if not running
                    if not psp.is_alive():
                        service_cfg = meta.get("service", {})
                        if service_cfg.get("auto_start"):
                            _log.info(f"[Launcher] Auto-starting plugin service: {name}")
                            psp.start()
                else:
                    # Stop service if running
                    if psp.is_alive():
                        _log.info(f"[Launcher] Stopping plugin service: {name}")
                        psp.stop()

            action = "enabled" if enabled else "disabled"
            return self._send_json({"ok": True, "message": f"Plugin '{name}' {action}"})

        def _handle_get_plugin_config(self, name: str):
            """GET /api/plugins/{name}/config - Return config values + schema."""
            plugin_dir, _dir_name = self._find_plugin_dir(name)
            if not plugin_dir:
                return self._send_json({"error": f"Plugin '{name}' not found"}, 404)

            # Read config_schema and config.section from plugin.json
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
                except Exception:
                    pass

            # Platform plugins (telegram/feishu/qq etc.) bridge to system_config.json.
            # Tool/hook plugins always use data/plugins/{name}/config.json.
            if section and plugin_type == "platform":
                try:
                    sys_cfg_path = syscfg.workspace_config_path()
                    with open(sys_cfg_path, encoding="utf-8") as f:
                        full_cfg = json.load(f)
                    sec_data = full_cfg.get(section, {})
                    values = {
                        "service_enabled": full_cfg.get("services", {}).get(section, {}).get("enabled", False),
                        "bots": sec_data.get("bots", []),
                    }
                except Exception:
                    values = {}
            else:
                # Read persisted config values from data/plugins/{name}/config.json
                config_path = syscfg.workspace_data_dir("plugins", name, "config.json")
                values = {}
                if os.path.isfile(config_path):
                    try:
                        with open(config_path, encoding="utf-8") as f:
                            values = json.load(f)
                    except Exception:
                        pass

            # Merge defaults from schema for any missing keys
            merged = {}
            for key, field_schema in schema.items():
                if isinstance(field_schema, dict):
                    default_val = field_schema.get("default")
                    if key == "bots":
                        # bot_list: default to empty list, not None
                        merged[key] = values.get(key, default_val if default_val is not None else [])
                    else:
                        merged[key] = values.get(key, default_val)
                else:
                    merged[key] = values.get(key)

            return self._send_json(
                {
                    "name": name,
                    "config_schema": schema,
                    "config": merged,
                }
            )

        def _handle_put_plugin_config(self, name: str, body: dict):
            """PUT /api/plugins/{name}/config - Save config values."""
            plugin_dir, _dir_name = self._find_plugin_dir(name)
            if not plugin_dir:
                return self._send_json({"error": f"Plugin '{name}' not found"}, 404)

            config_values = body.get("config", body)

            # Check if this plugin bridges to system_config.json (platform plugins only)
            plugin_json_path = os.path.join(plugin_dir, "plugin.json")
            section = None
            plugin_type = "tool"
            if os.path.isfile(plugin_json_path):
                try:
                    with open(plugin_json_path, encoding="utf-8") as f:
                        meta = json.load(f)
                    section = meta.get("config", {}).get("section")
                    plugin_type = meta.get("type", "tool")
                except Exception:
                    pass

            if section and plugin_type == "platform":
                try:
                    sys_cfg_path = syscfg.workspace_config_path()
                    with open(sys_cfg_path, encoding="utf-8") as f:
                        full_cfg = json.load(f)
                    # Update bots list under the section key
                    if section not in full_cfg:
                        full_cfg[section] = {}
                    if "bots" in config_values:
                        full_cfg[section]["bots"] = config_values["bots"]
                    # Update service enabled flag
                    if "service_enabled" in config_values:
                        if "services" not in full_cfg:
                            full_cfg["services"] = {}
                        if section not in full_cfg["services"]:
                            full_cfg["services"][section] = {}
                        full_cfg["services"][section]["enabled"] = config_values["service_enabled"]
                        # Also sync plugin.json enabled field for UI consistency
                        if meta.get("service_toggle"):
                            meta["enabled"] = config_values["service_enabled"]
                            try:
                                with open(plugin_json_path, "w", encoding="utf-8") as f:
                                    json.dump(meta, f, indent=2, ensure_ascii=False)
                            except Exception as e:
                                _log.warning(f"[Launcher] Warning: Failed to sync plugin.json enabled: {e}")
                    with open(sys_cfg_path, "w", encoding="utf-8") as f:
                        json.dump(full_cfg, f, indent=2, ensure_ascii=False)
                    # Invalidate system_config module cache so other code sees new values
                    from opensquad import system_config as _syscfg_mod

                    _syscfg_mod._cache = None
                except Exception as e:
                    return self._send_json({"error": f"Failed to write system config: {e}"}, 500)
            else:
                # Persist to data/plugins/{name}/config.json
                config_dir = syscfg.workspace_data_dir("plugins", name)
                config_path = os.path.join(config_dir, "config.json")
                try:
                    os.makedirs(config_dir, exist_ok=True)
                    with open(config_path, "w", encoding="utf-8") as f:
                        json.dump(config_values, f, indent=2, ensure_ascii=False)
                except Exception as e:
                    return self._send_json({"error": f"Failed to write config: {e}"}, 500)

            # Write reload signal so agents pick up new config
            try:
                reload_ts_path = os.path.join(PLUGINS_DIR, ".reload_ts")
                with open(reload_ts_path, "w") as f:
                    f.write(str(time.time()))
            except Exception:
                pass

            return self._send_json({"ok": True, "message": f"Config saved for '{name}'"})

        def _handle_get_plugin_data(self, name: str, qs: dict):
            """
            GET /api/plugins/{name}/data - Dynamic plugin data query.

            Convention: if plugins/{name}/query.py exists and exports
            ``query_data(project_root: str, params: dict) -> dict``,
            it will be called automatically. No per-plugin hardcoding needed.
            """
            # Validate plugin exists
            plugin_dir, _dir_name = self._find_plugin_dir(name)
            if not plugin_dir:
                return self._send_json({"error": f"Plugin '{name}' not found"}, 404)

            # Check for query module
            query_module_path = os.path.join(plugin_dir, "query.py")
            if not os.path.isfile(query_module_path):
                return self._send_json({"error": f"Plugin '{name}' has no data query module (query.py)"}, 404)

            # Dynamic import
            import importlib

            module_name = f"plugins.{name}.query"
            try:
                # Reload if already imported (supports hot-reload of query logic)
                if module_name in sys.modules:
                    mod = importlib.reload(sys.modules[module_name])
                else:
                    mod = importlib.import_module(module_name)
            except Exception as e:
                return self._send_json({"error": f"Failed to import {module_name}: {e}"}, 500)

            if not hasattr(mod, "query_data"):
                return self._send_json({"error": f"Plugin '{name}' query.py missing query_data() function"}, 400)

            # Flatten query-string params: {k: [v1]} -> {k: v1}
            params = {k: v[0] if isinstance(v, list) and v else v for k, v in qs.items()}

            try:
                result = mod.query_data(PROJECT_ROOT, params)
                return self._send_json(result)
            except Exception as e:
                return self._send_json({"error": f"Query failed: {e}"}, 500)

        def _handle_plugin_view_error(self, body: dict):
            """
            POST /api/plugin-view-error
            Append a frontend plugin-view runtime error to
            plugins/{name}/view_errors.log so the agent can read it.
            """
            import datetime as _dt

            plugin_name = body.get("plugin_name", "unknown")
            view_key = body.get("view_key", "")
            error_msg = body.get("error", "")
            stack = body.get("stack", "")

            log_path = os.path.join(PLUGINS_DIR, plugin_name, "view_errors.log")
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
            """
            POST /api/resources/upload
            Saves base64 encoded files to a local resource directory (skills/plugins).
            """
            resource_type = body.get("resource_type")
            files = body.get("files", [])

            if resource_type == "skills":
                base_dir = SKILLS_DIR
            elif resource_type == "plugins":
                base_dir = PLUGINS_DIR
            else:
                return self._send_json({"error": "Invalid resource type"}, 400)

            if not files:
                return self._send_json({"error": "No files provided"}, 400)

            try:
                os.makedirs(base_dir, exist_ok=True)
                # Group files by their top-level directory name to identify the resource name
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

                # Trigger reload for plugins
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
            """
            DELETE /api/resources/{type}/{name}
            Deletes a resource (skill or plugin) directory.
            """
            if resource_type == "skills":
                base_dir = SKILLS_DIR
            elif resource_type == "plugins":
                base_dir = PLUGINS_DIR
            else:
                return self._send_json({"error": "Invalid resource type"}, 400)

            # Validate name to prevent path traversal
            # Allow alphanumeric, underscore, hyphen, dot
            if not re.match(r"^[a-zA-Z0-9_\-\.]+$", name):
                return self._send_json({"error": "Invalid resource name"}, 400)

            target_dir = os.path.join(base_dir, name)

            # Double check that we are staying within base_dir
            if not os.path.abspath(target_dir).startswith(os.path.abspath(base_dir)):
                return self._send_json({"error": "Path traversal detected"}, 400)

            if not os.path.isdir(target_dir):
                return self._send_json({"error": f"{resource_type[:-1].capitalize()} '{name}' not found"}, 404)

            try:
                # On Windows, .git/objects/pack/*.idx files are often locked
                # by AV software (360, Defender) or have read-only attributes
                # that cause WinError 5 (Access Denied). Use a custom onerror
                # handler that clears read-only flags and retries, then falls
                # back to renaming the directory out of the way.
                def _rmtree_onerror(func, path, exc_info):
                    import stat as _stat

                    try:
                        os.chmod(path, _stat.S_IWRITE)
                    except Exception:
                        pass
                    try:
                        func(path)
                    except Exception:
                        # Last resort: rename the stubborn file/dir so the
                        # outer rmtree can continue. The renamed leftover
                        # will be cleaned up on next restart or manually.
                        try:
                            import uuid as _uuid

                            dead_name = path + ".dead_" + _uuid.uuid4().hex[:8]
                            os.rename(path, dead_name)
                        except Exception:
                            pass  # Give up on this single entry

                shutil.rmtree(target_dir, onerror=_rmtree_onerror)

                # Trigger reload for plugins
                if resource_type == "plugins":
                    reload_ts_path = os.path.join(base_dir, ".reload_ts")
                    try:
                        with open(reload_ts_path, "w") as rf:
                            rf.write(str(time.time()))
                    except Exception:
                        pass  # Ignore reload signal failure

                return self._send_json({"ok": True, "message": f"{resource_type[:-1].capitalize()} '{name}' deleted"})
            except Exception as e:
                return self._send_json({"error": f"Failed to delete {resource_type}: {e}"}, 500)

        def _handle_plugin_action(self, name: str, body: dict):
            """
            POST /api/plugins/{name}/action - Execute plugin action.

            Convention: if plugins/{name}/query.py exports
            ``handle_action(project_root: str, action: str, data: dict) -> dict``,
            it will be called automatically.
            """
            # Validate plugin exists
            plugin_dir, _dir_name = self._find_plugin_dir(name)
            if not plugin_dir:
                return self._send_json({"error": f"Plugin '{name}' not found"}, 404)

            # Check for query module with handle_action
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
                result = mod.handle_action(PROJECT_ROOT, action, data)
                return self._send_json(result)
            except Exception as e:
                return self._send_json({"error": f"Action failed: {e}"}, 500)

        def _handle_list_plugin_services(self):
            """GET /api/plugin-services — List all plugin services and their status"""
            result = [psp.get_status() for psp in _plugin_services.values()]
            return self._send_json({"plugin_services": result})

        def _handle_services_manage(self):
            """GET /api/services/manage — Enriched service list for the Service Manager UI.
            Returns ALL discovered services (from plugin.json) merged with runtime status.
            This endpoint is used by the new standalone Service Management page."""
            # 1. Discover all plugin services from plugin.json
            discovered = discover_all_plugin_services()

            # 2. Build result merging discovery info with runtime status
            services = []
            for info in discovered:
                pid = info["plugin_id"]
                if pid in _plugin_services:
                    psp = _plugin_services[pid]
                    status = psp.get_status()
                else:
                    # Not yet registered in _plugin_services (e.g., service was never started)
                    status = {
                        "plugin_id": pid,
                        "display_name": info.get("display_name", pid),
                        "plugin_type": info.get("plugin_type", "tool"),
                        "alive": False,
                        "pid": None,
                        "port": _resolve_discovery_port(info),
                        "host": info.get("service_cfg", {}).get("host", "0.0.0.0"),
                        "auto_start": syscfg.is_service_enabled(pid),
                        "should_run": False,
                        "restart_count": 0,
                        "max_restarts": MAX_RESTART_ATTEMPTS,
                        "started_at": None,
                        "uptime_seconds": None,
                        "health_endpoint": info["service_cfg"].get("health_endpoint", "/health"),
                        "health_ok": None,
                        "service_cfg": info["service_cfg"],
                    }
                services.append(status)

            return self._send_json({"services": services})

        def _handle_runtime_list(self):
            """GET /api/runtime/list — list runtime registry and managed process states."""
            cleanup = _cleanup_runtime_registry(force_kill=False)
            managed_agents = []
            for ap in _processes.values():
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
            for psp in _plugin_services.values():
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
                    "runtime_registry": cleanup.get("remaining", []),
                    "cleanup": {
                        "cleaned": cleanup.get("cleaned", 0),
                        "killed": cleanup.get("killed", 0),
                    },
                    "managed": {
                        "agents": managed_agents,
                        "plugins": managed_plugins,
                    },
                }
            )

        def _handle_plugin_service_start(self, plugin_id: str):
            """POST /api/plugin-services/{id}/start — Start a plugin service"""
            if plugin_id not in _plugin_services:
                return self._send_json({"error": f"Plugin service '{plugin_id}' not found"}, 404)
            psp = _plugin_services[plugin_id]
            # Idempotent: if already running or in `starting` (deps installing),
            # return 200 with already_running=true so the UI doesn't alert an
            # error when the user clicks Start on a service that auto-started
            # in the background. Previously this returned HTTP 400 which made
            # the front-end pop an "Start failed: ... already running" dialog.
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
            # Sync services.X.enabled = true so the service can read its own config
            # (otherwise the service may exit immediately if it sees enabled=false)
            self._set_service_enabled_in_config(plugin_id, True)
            psp.port = psp._resolve_port()  # Re-resolve port (config may have been updated)
            psp.start()
            pid_val = psp.process.pid if psp.process else None
            return self._send_json({"message": f"{plugin_id} started", "pid": pid_val, "port": psp.port})

        def _handle_plugin_service_stop(self, plugin_id: str):
            """POST /api/plugin-services/{id}/stop — Stop a plugin service"""
            if plugin_id not in _plugin_services:
                return self._send_json({"error": f"Plugin service '{plugin_id}' not found"}, 404)
            psp = _plugin_services[plugin_id]
            if not psp.is_alive():
                psp.should_run = False
                # Still sync the flag in case it was previously enabled
                self._set_service_enabled_in_config(plugin_id, False)
                return self._send_json({"message": f"{plugin_id} already stopped"})
            psp.stop()
            # Sync services.X.enabled = false so a future opensquad start won't re-launch
            self._set_service_enabled_in_config(plugin_id, False)
            return self._send_json({"message": f"{plugin_id} stopped"})

        def _handle_plugin_service_auto_start(self, plugin_id: str, body: dict):
            """PUT /api/plugin-services/{id}/auto-start — Toggle auto-start on boot.
            Body: {"enabled": true/false}
            Updates system_config.json services.{plugin_id}.enabled.
            """
            enabled = body.get("enabled", True) if isinstance(body, dict) else True
            self._set_service_enabled_in_config(plugin_id, enabled)
            return self._send_json(
                {
                    "ok": True,
                    "plugin_id": plugin_id,
                    "auto_start": enabled,
                }
            )

        def _set_service_enabled_in_config(self, plugin_id: str, enabled: bool):
            """Update system_config.json services.{plugin_id}.enabled and invalidate cache.

            Used by /api/plugin-services/{id}/start and /stop so the service itself
            sees the right state and won't immediately exit on next launch.
            """
            try:
                sys_cfg_path = syscfg.workspace_config_path()
                with open(sys_cfg_path, encoding="utf-8") as f:
                    full_cfg = json.load(f)
                if "services" not in full_cfg:
                    full_cfg["services"] = {}
                if plugin_id not in full_cfg["services"]:
                    full_cfg["services"][plugin_id] = {}
                full_cfg["services"][plugin_id]["enabled"] = enabled
                with open(sys_cfg_path, "w", encoding="utf-8") as f:
                    json.dump(full_cfg, f, indent=2, ensure_ascii=False)
                # Invalidate system_config cache
                from opensquad import system_config as _syscfg_mod

                _syscfg_mod._cache = None
                _log.info(f"[Launcher] Synced services.{plugin_id}.enabled = {enabled}")
            except Exception as e:
                _log.warning(f"[Launcher] Warning: Failed to sync services.{plugin_id}.enabled: {e}")

        def _handle_shutdown(self, body: dict):
            """POST /api/shutdown — Gracefully stop agents, then confirm for force-kill."""
            timeout = body.get("timeout", 10) if isinstance(body, dict) else 10
            stopped = 0
            for name, ap in list(_processes.items()):
                if ap.is_alive():
                    try:
                        _log.info(f"[Launcher] Graceful shutdown: stopping agent {name}...")
                        ap.should_run = False
                        if ap.process and ap.process.poll() is None:
                            ap.process.terminate()
                            try:
                                ap.process.wait(timeout=timeout)
                            except subprocess.TimeoutExpired:
                                ap.process.kill()
                            stopped += 1
                    except Exception:
                        pass
            # Also stop plugin services
            for _pid, psp in list(_plugin_services.items()):
                if psp.is_alive():
                    try:
                        psp.stop()
                        stopped += 1
                    except Exception:
                        pass
            return self._send_json({"message": f"Shutdown: {stopped} processes stopped", "ok": True})

        def _handle_plugin_service_restart(self, plugin_id: str):
            """POST /api/plugin-services/{id}/restart — Restart a plugin service"""
            if plugin_id not in _plugin_services:
                return self._send_json({"error": f"Plugin service '{plugin_id}' not found"}, 404)
            psp = _plugin_services[plugin_id]
            # Stop if running
            if psp.is_alive():
                psp.stop()
                # Wait up to 5s for the process to exit
                for _ in range(50):
                    if not psp.is_alive():
                        break
                    time.sleep(0.1)
            # Re-resolve port (config may have been updated)
            psp.port = psp._resolve_port()
            psp.should_run = True
            psp.start()
            pid_val = psp.process.pid if psp.process else None
            return self._send_json({"message": f"{plugin_id} restarted", "pid": pid_val, "port": psp.port})

        def _handle_plugin_service_logs(self, plugin_id: str, lines: int):
            """GET /api/plugin-services/{id}/logs — Retrieve plugin service logs"""
            if plugin_id not in _plugin_services:
                return self._send_json({"error": f"Plugin service '{plugin_id}' not found"}, 404)
            logs = _plugin_services[plugin_id].get_logs(lines)
            return self._send_json({"plugin_id": plugin_id, "logs": logs, "total": len(logs)})

        # ── Agent session HTTP endpoints (for remote Gateway access) ──

        def _get_session_reader(self, agent_id: str):
            """Get an AgentSessionReader for the given agent_id, or None."""
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
                _log.error(f"[Launcher] Failed to get session reader for {agent_id}: {e}")
                return None

        def _handle_session_list(self, agent_id: str):
            """GET /api/sessions/{agent_id}/list"""
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

        def _handle_session_current(self, agent_id: str, offset: int, limit: int):
            """GET /api/sessions/{agent_id}/current?offset=0&limit=50"""
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

        def _handle_session_paged(self, agent_id: str, session_id: str, offset: int, limit: int):
            """GET /api/sessions/{agent_id}/{session_id}/paged?offset=0&limit=50"""
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
            """GET /api/sessions/{agent_id}/{session_id}"""
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
            """POST /api/sessions/{agent_id}/{session_id}/delete"""
            reader = self._get_session_reader(agent_id)
            if reader is None:
                return self._send_json({"error": f"Agent not found: {agent_id}"}, 404)
            ok = reader.delete_session(session_id)
            return self._send_json({"ok": ok})

        def _handle_session_rename(self, agent_id: str, session_id: str, body: bytes | str | None):
            """POST /api/sessions/{agent_id}/{session_id}/rename"""
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

        def _read_token_stats(self, name: str) -> dict | None:
            """Read the agent's token_stats.json file"""
            # Try multiple possible paths
            candidates = [
                os.path.join(AGENTS_DIR, name, "data", "ai_his_talk", "token_stats.json"),
                os.path.join(AGENTS_DIR, name, "ai_his_talk", "token_stats.json"),
            ]
            for path in candidates:
                if os.path.isfile(path):
                    try:
                        with open(path, encoding="utf-8") as f:
                            return json.load(f)
                    except Exception:
                        pass
            return None

        def _read_chat_profile(self, name: str) -> dict | None:
            """Read the agent's profile.json (group chat account name and avatar)"""
            from opensquad.avatar_utils import read_agent_profile_file

            profile = read_agent_profile_file(AGENTS_DIR, name)
            # Empty normalized profile → None so callers keep prior "missing" semantics
            if not profile.get("name") and not profile.get("avatar"):
                return None
            return profile

        def _handle_get_stats(self, name: str):
            """Return the agent's token statistics"""
            stats = self._read_token_stats(name)
            if stats is None:
                return self._send_json({"agent": name, "token_stats": None})
            return self._send_json({"agent": name, "token_stats": stats})

        # ── MCP handlers (Central / Unified) ──

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

        # ── MCP handlers (Per-agent, legacy) ──

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
                return self._send_json(
                    {"ok": True, "message": f"MCP config saved for '{name}'", "restarted": restarted}
                )
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

        # ── Skills handlers ──

        def _handle_list_skills(self):
            """GET /api/skills — Scan the skills/ directory and return the skill list"""
            skills = []
            skill_dirs = _collect_skill_dirs()
            if not skill_dirs:
                return self._send_json({"skills": []})

            for skill_name in sorted(skill_dirs):
                skill_dir = skill_dirs[skill_name]
                skill_json_path = os.path.join(skill_dir, "skill.json")
                skill_md_path = os.path.join(skill_dir, "SKILL.md")

                if os.path.isfile(skill_json_path):
                    try:
                        with open(skill_json_path, encoding="utf-8") as f:
                            meta = json.load(f)
                    except Exception:
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
                    # Fallback: parse SKILL.md frontmatter (--- ... --- block)
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
                    except Exception:
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
            """GET /api/skills/{name}/source — Return file list and SKILL.md content"""
            # Sanitize name
            if not re.match(r"^[a-zA-Z0-9_\-]+$", name):
                return self._send_json({"error": "Invalid skill name"}, 400)
            skill_dir = _find_skill_dir(name)
            if not skill_dir:
                return self._send_json({"error": f"Skill '{name}' not found"}, 404)
            # Collect file list with sizes
            files_info = []
            for fname in sorted(os.listdir(skill_dir)):
                fpath = os.path.join(skill_dir, fname)
                if os.path.isfile(fpath):
                    files_info.append(
                        {
                            "name": fname,
                            "size": os.path.getsize(fpath),
                        }
                    )
            # Read SKILL.md content
            skill_md = ""
            skill_md_path = os.path.join(skill_dir, "SKILL.md")
            if os.path.isfile(skill_md_path):
                try:
                    with open(skill_md_path, encoding="utf-8") as f:
                        skill_md = f.read()
                except Exception:
                    skill_md = "(Failed to read SKILL.md)"
            # Read skill.json if present
            skill_json_data = None
            skill_json_path = os.path.join(skill_dir, "skill.json")
            if os.path.isfile(skill_json_path):
                try:
                    with open(skill_json_path, encoding="utf-8") as f:
                        skill_json_data = json.load(f)
                except Exception:
                    pass
            # Read any .py source files
            py_sources = {}
            # Read other text files (README.md, .txt, .yaml, etc.)
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
                # Skip files already handled separately
                if fi["name"] in ("SKILL.md", "skill.json"):
                    continue
                if ext == ".py":
                    try:
                        with open(fpath, encoding="utf-8") as f:
                            py_sources[fi["name"]] = f.read()
                    except Exception:
                        py_sources[fi["name"]] = "(Failed to read)"
                elif ext in _TEXT_EXTS:
                    try:
                        with open(fpath, encoding="utf-8") as f:
                            other_sources[fi["name"]] = f.read()
                    except Exception:
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

        # ── Role Cards handlers ──

        def _list_cards(self, cards_dir: str):
            """Scan the cards directory, parse SKILL.md-style frontmatter, and return the card list"""
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
                # Parse YAML frontmatter (--- ... --- block)
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
                # Extract title (frontmatter name > first # heading in body > card_name)
                title = fm.get("name", card_name)
                for line in body.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("# "):
                        title = stripped[2:].strip()
                        break
                # Extract tags (comma-separated string → list)
                tags_raw = fm.get("tags", "")
                tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
                # description: prefer frontmatter value; otherwise use first 100 chars of body
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

        def _handle_list_role_cards(self):
            return self._send_json({"cards": self._list_cards(ROLE_CARDS_DIR)})

        def _handle_get_role_card(self, card_name: str):
            fpath = os.path.join(ROLE_CARDS_DIR, f"{card_name}.md")
            if not os.path.isfile(fpath):
                return self._send_json({"error": "Card not found"}, 404)
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            return self._send_json({"name": card_name, "content": content})

        def _handle_put_role_card(self, card_name: str, body: dict):
            os.makedirs(ROLE_CARDS_DIR, exist_ok=True)
            content = body.get("content", "")
            fpath = os.path.join(ROLE_CARDS_DIR, f"{card_name}.md")
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)
            return self._send_json({"ok": True, "name": card_name})

        def _handle_delete_role_card(self, card_name: str):
            fpath = os.path.join(ROLE_CARDS_DIR, f"{card_name}.md")
            if not os.path.isfile(fpath):
                return self._send_json({"error": "Card not found"}, 404)
            os.remove(fpath)
            return self._send_json({"ok": True, "name": card_name})

        # ── Collab Cards handlers ──

        def _handle_list_collab_cards(self):
            return self._send_json({"cards": self._list_cards(COLLAB_CARDS_DIR)})

        def _handle_get_collab_card(self, card_name: str):
            fpath = os.path.join(COLLAB_CARDS_DIR, f"{card_name}.md")
            if not os.path.isfile(fpath):
                return self._send_json({"error": "Card not found"}, 404)
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            return self._send_json({"name": card_name, "content": content})

        def _handle_put_collab_card(self, card_name: str, body: dict):
            os.makedirs(COLLAB_CARDS_DIR, exist_ok=True)
            content = body.get("content", "")
            fpath = os.path.join(COLLAB_CARDS_DIR, f"{card_name}.md")
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)
            return self._send_json({"ok": True, "name": card_name})

        def _handle_delete_collab_card(self, card_name: str):
            fpath = os.path.join(COLLAB_CARDS_DIR, f"{card_name}.md")
            if not os.path.isfile(fpath):
                return self._send_json({"error": "Card not found"}, 404)
            os.remove(fpath)
            return self._send_json({"ok": True, "name": card_name})

        # ── Role/Collab Prompt assignment handlers ──

        def _handle_put_role_prompt(self, name: str, body: dict):
            """Write role card content to agents/{name}/role_prompt.md and update config.json"""
            agent_dir = os.path.join(AGENTS_DIR, name)
            if not os.path.isdir(agent_dir):
                return self._send_json({"error": "Agent not found"}, 404)
            content = body.get("content", "")
            card_name = body.get("card_name", "")
            with open(os.path.join(agent_dir, "role_prompt.md"), "w", encoding="utf-8") as f:
                f.write(content)
            config_path = os.path.join(agent_dir, "config.json")
            cfg = _read_json(config_path)
            cfg.setdefault("prompt", {})["role"] = "role_prompt.md"
            cfg["prompt"]["role_card"] = card_name
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            if name in _processes:
                _processes[name].reload_config()
            return self._send_json({"ok": True})

        def _handle_delete_role_prompt(self, name: str):
            """Unassign role card: restore role.md, delete role_prompt.md"""
            agent_dir = os.path.join(AGENTS_DIR, name)
            if not os.path.isdir(agent_dir):
                return self._send_json({"error": "Agent not found"}, 404)
            config_path = os.path.join(agent_dir, "config.json")
            cfg = _read_json(config_path)
            cfg.setdefault("prompt", {})["role"] = "role.md"
            cfg["prompt"].pop("role_card", None)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            rp = os.path.join(agent_dir, "role_prompt.md")
            if os.path.isfile(rp):
                os.remove(rp)
            if name in _processes:
                _processes[name].reload_config()
            return self._send_json({"ok": True})

        # ── Model Cards handlers ──

        def _handle_list_model_cards(self):
            cards = []
            if not os.path.isdir(MODEL_CARDS_DIR):
                return self._send_json({"cards": cards})
            for fname in sorted(os.listdir(MODEL_CARDS_DIR)):
                if not fname.endswith(".json"):
                    continue
                fpath = os.path.join(MODEL_CARDS_DIR, fname)
                try:
                    with open(fpath, encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
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
                        "render_mode": data.get("render_mode", "strict"),  # full | strict (Default: strict)
                    }
                )
            return self._send_json({"cards": cards})

        def _handle_get_model_card(self, card_name: str):
            fpath = os.path.join(MODEL_CARDS_DIR, f"{card_name}.json")
            if not os.path.isfile(fpath):
                return self._send_json({"error": "Card not found"}, 404)
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
            # Ensure render_mode exists in response
            if "render_mode" not in data:
                data["render_mode"] = "strict"
            return self._send_json({"name": card_name, "card": data})

        def _handle_put_model_card(self, card_name: str, body: dict):
            os.makedirs(MODEL_CARDS_DIR, exist_ok=True)
            # body contains the card fields directly
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
            fpath = os.path.join(MODEL_CARDS_DIR, f"{card_name}.json")
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(card, f, ensure_ascii=False, indent=2)
            return self._send_json({"ok": True, "name": card_name})

        def _handle_delete_model_card(self, card_name: str):
            fpath = os.path.join(MODEL_CARDS_DIR, f"{card_name}.json")
            if not os.path.isfile(fpath):
                return self._send_json({"error": "Card not found"}, 404)
            os.remove(fpath)
            return self._send_json({"ok": True, "name": card_name})

        def _handle_put_model_card_assign(self, name: str, body: dict):
            """Write model card config to the model field of agents/{name}/config.json"""
            agent_dir = os.path.join(AGENTS_DIR, name)
            if not os.path.isdir(agent_dir):
                return self._send_json({"error": "Agent not found"}, 404)
            config_path = os.path.join(agent_dir, "config.json")
            cfg = _read_json(config_path)
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
            if name in _processes:
                _processes[name].reload_config()
            return self._send_json({"ok": True})

        def _handle_delete_model_card_unassign(self, name: str):
            """Unassign model card: clear the config.json model._card field"""
            agent_dir = os.path.join(AGENTS_DIR, name)
            if not os.path.isdir(agent_dir):
                return self._send_json({"error": "Agent not found"}, 404)
            config_path = os.path.join(agent_dir, "config.json")
            cfg = _read_json(config_path)
            cfg.setdefault("model", {}).pop("_card", None)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            if name in _processes:
                _processes[name].reload_config()
            return self._send_json({"ok": True})

        # ── Workspace Management ──────────────────────────────────────

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

    server = _ThreadingHTTPServer(("0.0.0.0", port), ManagementHandler)
    _log.info(f"[Launcher] Management API started on http://0.0.0.0:{port}")
    server.serve_forever()


# ── Task Watch Supervisor ──────────────────────────────────────────────────────
# Runs in a background daemon thread. Checks heartbeats from agent task_watch.
# If a worker goes silent beyond STALL_THRESHOLD, notifies PM via im.send_to_agent.

STALL_THRESHOLD = 300  # seconds before a worker is considered stalled
SUPERVISOR_INTERVAL = 30  # seconds between scan cycles


def _collab_supervisor_loop():
    """Daemon thread: scan task_watch heartbeats, notify PM on worker stalls."""
    while not _shutdown_event.is_set():
        time.sleep(SUPERVISOR_INTERVAL)
        now = time.time()
        for agent_id, hb in list(_task_watch_heartbeats.items()):
            # Only check agents with active (not completed) tasks
            if hb.get("event") == "complete":
                _task_watch_stalled_notified.discard(agent_id)
                continue
            elapsed = now - hb.get("last_update", 0)
            if elapsed > STALL_THRESHOLD and agent_id not in _task_watch_stalled_notified:
                _task_watch_stalled_notified.add(agent_id)
                desc = hb.get("detail") or hb.get("description", "unknown")
                _log.warning(f"[Launcher] ⚠️ Worker {agent_id} stalled ({elapsed:.0f}s since last update): {desc[:80]}")
                # Try to find PM and notify
                try:
                    # Look for an agent whose config has the pm role pattern
                    for ap in _processes.values():
                        cfg = ap.config or {}
                        role = (cfg.get("prompt") or {}).get("role", "")
                        if role and "pm" in role.lower():
                            msg = (
                                f"⚠️ Worker [{agent_id}] 已 {elapsed:.0f} 秒未更新进度\n"
                                f"最后状态: {desc[:200]}\n"
                                f"建议: 1) 查看状态 2) 重试 3) 重新分配"
                            )
                            # Queue a message to PM's input hub (non-blocking try)
                            with contextlib.suppress(Exception):
                                _send_system_message_to_agent(ap.dir_name, msg)
                            break
                except Exception:
                    pass


def _send_system_message_to_agent(agent_dir: str, msg: str):
    """Write a system notification message to the agent's input hub."""
    try:
        import os

        hub_path = os.path.join(AGENTS_DIR, agent_dir, "data", "hub_inbox")
        if os.path.isdir(hub_path):
            fname = f"supervisor_{int(time.time())}.json"
            with open(os.path.join(hub_path, fname), "w", encoding="utf-8") as f:
                import json

                json.dump({"type": "system_notification", "content": msg, "timestamp": time.time()}, f)
    except Exception:
        pass


def _init_workspace():
    """Phase 1: Bootstrap workspace and refresh AGENTS_DIR."""
    from opensquad.workspace_utils import bootstrap_workspace

    try:
        workspace_path = bootstrap_workspace()
        _log.info(f"[Workspace] Active workspace: {workspace_path}\n")
    except Exception as e:
        _log.error(f"[ERROR] Failed to initialize workspace: {e}")
        sys.exit(1)
    global AGENTS_DIR, PLUGINS_DIR, SKILLS_DIR, ROLE_CARDS_DIR, COLLAB_CARDS_DIR, MODEL_CARDS_DIR
    AGENTS_DIR = syscfg.workspace_agents_dir()
    PLUGINS_DIR = syscfg.workspace_plugins_dir()
    SKILLS_DIR = syscfg.workspace_skills_dir()
    ROLE_CARDS_DIR = syscfg.workspace_role_cards_dir()
    COLLAB_CARDS_DIR = syscfg.workspace_collab_cards_dir()
    MODEL_CARDS_DIR = syscfg.workspace_model_cards_dir()
    for d in (PLUGINS_DIR, SKILLS_DIR, ROLE_CARDS_DIR, COLLAB_CARDS_DIR, MODEL_CARDS_DIR):
        os.makedirs(d, exist_ok=True)


def _setup_launcher_logging():
    """Phase 2: Configure launcher log file + console handler + stdout/stderr tee."""
    _launcher_log_dir = syscfg.workspace_logs_dir("gateway")
    os.makedirs(_launcher_log_dir, exist_ok=True)
    _launcher_log_path = os.path.join(_launcher_log_dir, "launcher.log")
    _log.setLevel(logging.DEBUG)
    _log.propagate = False
    from opensquad.safe_rotating_handler import SafeRotatingFileHandler

    _lh = SafeRotatingFileHandler(
        _launcher_log_path,
        maxBytes=syscfg.log_max_size_mb() * 1024 * 1024,
        backupCount=syscfg.log_backup_count(),
        encoding="utf-8",
        delay=True,
    )
    _lh.setFormatter(logging.Formatter(syscfg.log_format(), datefmt=syscfg.log_date_format()))
    _log.handlers.clear()
    _log.addHandler(_lh)
    _ch = logging.StreamHandler()
    _ch.setFormatter(logging.Formatter("%(message)s"))
    _ch.setLevel(logging.INFO)
    _log.addHandler(_ch)

    class _TeeStream:
        """Wraps a stream to also write to a logging.Logger."""

        def __init__(self, original_stream, logger, level=logging.INFO):
            self._original = original_stream
            self._logger = logger
            self._level = level
            self._buf = ""

        def write(self, text):
            self._original.write(text)
            self._buf += text
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                if line.strip():
                    self._logger.log(self._level, line.rstrip("\r"))

        def flush(self):
            self._original.flush()
            if self._buf.strip():
                self._logger.log(self._level, self._buf.rstrip("\r"))
                self._buf = ""

        def __getattr__(self, name):
            return getattr(self._original, name)

    sys.stdout = _TeeStream(sys.stdout, _log)
    sys.stderr = _TeeStream(sys.stderr, _log, level=logging.ERROR)


def _parse_args_and_discover_agents():
    """Phase 3: Parse CLI args, discover agents, return (args, agents_info)."""
    parser = argparse.ArgumentParser(description="Multi-Agent Process Launcher")
    parser.add_argument("--exclude", nargs="+", help="Exclude these agents (directory names)")
    parser.add_argument(
        "--no-auto-start", action="store_true", help="Don't auto-start agents, only open management port"
    )
    parser.add_argument(
        "--no-services",
        action="store_true",
        help="Don't auto-start plugin services (frozen-bundle safe mode; spawns would re-enter the frozen EXE)",
    )
    parser.add_argument(
        "--mgmt-port", type=int, default=MANAGEMENT_PORT, help=f"Management API port (default: {MANAGEMENT_PORT})"
    )
    args = parser.parse_args()
    # Stash parsed args at module level so phase functions (which take no args,
    # e.g. _init_and_start_plugin_services) can read flags like --no-services.
    global _ARGS
    _ARGS = args

    _log.info("=" * 60)
    _log.info("  OpenSquad - Multi-Agent Launcher")
    _log.info("=" * 60)

    discovered_all = discover_agents(AGENTS_DIR, exclude=args.exclude)
    per_agent_auto = [
        info["name"]
        for info in discovered_all
        if bool((info.get("config") or {}).get("ui", {}).get("auto_start_on_boot", False))
    ]
    if per_agent_auto:
        _log.info(f"[Launcher] Using per-agent auto-start flags: {per_agent_auto}")

    agents_info = discovered_all

    if not agents_info:
        _log.info("[Launcher] No agents found in agents/ directory.")
    else:
        _log.info(f"\n[Launcher] Found {len(agents_info)} agent(s):")
        for info in agents_info:
            cfg = info["config"]
            _log.info(f"  - {cfg.get('agent_name', info['name'])} ({cfg.get('agent_id', '?')}) [{info['name']}/]")
        print()

    return args, agents_info


def _register_process_table(agents_info):
    """Phase 4: Register agents into the global process table and cleanup stale registry."""
    for info in agents_info:
        ap = AgentProcess(info["dir"], info["config"])
        _processes[info["name"]] = ap
    _cleanup_runtime_registry(force_kill=False)


def _start_background_services(mgmt_port):
    """Phase 5: Start management server and supervisor threads."""
    mgmt_thread = threading.Thread(target=_start_management_server, args=(mgmt_port,), daemon=True, name="mgmt-server")
    mgmt_thread.start()
    supervisor_thread = threading.Thread(target=_collab_supervisor_loop, daemon=True, name="collab-supervisor")
    supervisor_thread.start()


def _start_node_registration_if_needed(mgmt_port):
    """Phase 6: Multi-node self-registration and WS management tunnel."""
    if syscfg.node_register_to_gateway():
        _start_node_registration_thread(mgmt_port)
        _start_launcher_ws_tunnel(mgmt_port)


def _init_and_start_plugin_services():
    """Phase 7a: Discover/register plugin services; return auto-start id list.

    Does NOT block on per-service pip. Callers should run agents next, then
    ``_auto_start_plugin_services_parallel`` for background parallel starts.
    """
    # Frozen-bundle safe mode: plugin service spawns use `sys.executable` to run
    # the plugin's entry script, but in a PyInstaller bundle sys.executable IS
    # the frozen launcher EXE — spawning it would re-enter the launcher and
    # either crash or fight for ports. --no-services (set by the desktop app)
    # skips AUTO-START only; we still discover services so the Service Manager
    # UI can list them and the user can start them manually when a real Python
    # interpreter is available. Previously --no-services skipped discovery
    # entirely, which made every service return 404 "not found" and hid all
    # plugin-backed UI (Token Analytics, websearch, etc.).
    skip_auto_start = getattr(_ARGS, "no_services", False)
    if skip_auto_start:
        _log.info(
            "[Launcher] --no-services set: skipping plugin service auto-start (frozen-bundle safe mode). "
            "Services are still discovered so they can be started manually from the Service Manager."
        )
    syscfg.ensure_external_api_key()

    # Ensure the Agent Python embed's _pth file is correctly configured
    # (import site + Lib\site-packages). Older setup wizards only added
    # `import site`, which can cause pip-installed packages to not be
    # importable → services crash with ModuleNotFoundError.
    try:
        from opensquad.agent_runtime import ensure_embed_pth_configured

        if ensure_embed_pth_configured():
            _log.info("[Launcher] Fixed Agent Python _pth file (added Lib\\site-packages).")
    except Exception as _e:
        _log.debug(f"[Launcher] _pth check skipped: {_e}")

    _log.info("\n[Launcher] Discovering plugin services...")
    plugin_svc_infos = discover_all_plugin_services()

    _stale_ports = {9700, 9001, 5001}
    for _stale_port in _stale_ports:
        _kill_port_owner(_stale_port)

    if not plugin_svc_infos:
        _log.info("[Launcher] No plugin services found.")
    else:
        _log.info(f"[Launcher] Found {len(plugin_svc_infos)} plugin service(s):")
        for info in plugin_svc_infos:
            auto = info["service_cfg"].get("auto_start", False)
            _log.info(f"  - {info['plugin_id']} (auto_start={auto})")

    _plugin_deps_thread = threading.Thread(
        target=_install_builtin_plugin_deps,
        args=(plugin_svc_infos,),
        daemon=True,
        name="plugin-deps-install",
    )
    _plugin_deps_thread.start()
    _log.info(
        "[Launcher] Plugin dependency installation started in background thread (PID: %s)",
        _plugin_deps_thread.native_id,
    )

    # ── Pass 1: Register ALL services first (fast, no blocking) ──
    # This closes the timing window where the UI lists a service (via
    # /api/services/manage which re-scans plugin.json) but /api/plugin-services/
    # {name}/start returns 404 because _plugin_services dict isn't populated
    # yet. By registering all PSps up-front, any Start click from the UI
    # — even mid-way through Pass 2's auto-start loop — finds the service
    # already in the registry.
    for info in plugin_svc_infos:
        pid = info["plugin_id"]
        psp = PluginServiceProcess(pid, info["plugin_dir"], info["service_cfg"])
        psp.display_name = info.get("display_name", pid)
        psp.plugin_type = info.get("plugin_type", "tool")
        psp.auto_start = info["service_cfg"].get("auto_start", False)
        psp.dependencies = info.get("dependencies", {})
        _plugin_services[pid] = psp

    # ── Pass 2: collect auto-start candidates (actual start is deferred) ──
    # Agents must not wait for plugin pip / playwright / whisper. Parallel
    # start runs after _auto_start_agents via _auto_start_plugin_services_parallel.
    to_start: list[str] = []
    for info in plugin_svc_infos:
        pid = info["plugin_id"]
        psp = _plugin_services[pid]
        if not syscfg.is_service_enabled(pid):
            _log.info(f"[Launcher] Plugin service {pid} disabled via config (services.{pid}.enabled=false), skipping.")
            continue
        if skip_auto_start:
            _log.info(f"[Launcher] Plugin service {pid} discovered but not auto-started (--no-services).")
            continue
        if psp.auto_start:
            to_start.append(pid)
    return to_start


def _auto_start_plugin_services_parallel(plugin_ids: list[str]) -> None:
    """Phase 7b: Auto-start plugin services in a background thread pool."""
    if not plugin_ids:
        return

    def _run():
        from concurrent.futures import ThreadPoolExecutor, as_completed

        workers = min(4, len(plugin_ids))
        _log.info(
            "[Launcher] Auto-starting %d plugin service(s) in parallel (workers=%d): %s",
            len(plugin_ids),
            workers,
            ", ".join(plugin_ids),
        )

        def _start_one(pid: str) -> tuple[str, bool]:
            psp = _plugin_services.get(pid)
            if not psp:
                return pid, False
            try:
                _log.info(f"[Launcher] Auto-starting plugin service: {pid}")
                return pid, bool(psp.start())
            except Exception as e:
                _log.error(f"[Launcher] Auto-start failed for {pid}: {e}")
                return pid, False

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="psp-start") as pool:
            futures = [pool.submit(_start_one, pid) for pid in plugin_ids]
            for fut in as_completed(futures):
                pid, ok = fut.result()
                _log.info("[Launcher] Plugin auto-start %s: %s", pid, "ok" if ok else "failed/skipped")

    threading.Thread(target=_run, daemon=True, name="plugin-autostart-pool").start()


def _auto_start_agents(args, agents_info):
    """Phase 8: Auto-start agents with auto_start_on_boot=true (unless --no-auto-start)."""
    if not args.no_auto_start and agents_info:
        used_ports = [p.actual_port for p in _processes.values() if p.is_alive()]
        for _name, ap in _processes.items():
            auto_flag = bool((ap.config or {}).get("ui", {}).get("auto_start_on_boot", False))
            if not auto_flag:
                continue
            ap.start(allocated_ports=used_ports)
            if ap.actual_port:
                used_ports.append(ap.actual_port)


def _setup_signal_handler():
    """Phase 9: Register graceful shutdown signal handler."""
    global _shutdown_event

    def signal_handler(sig, frame):
        if _shutdown_event.is_set():
            return
        _log.info("\n[Launcher] Received shutdown signal, stopping all agents...")
        _shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


def _monitor_loop():
    """Phase 10: Monitor process health and auto-restart crashed agents/services."""
    try:
        while not _shutdown_event.is_set():
            for _name, ap in list(_processes.items()):
                if not ap.is_alive() and ap.should_run:
                    exit_code = ap.process.returncode if ap.process else -1
                    _log.info(f"[Launcher] {ap.agent_name} exited (code: {exit_code})")
                    if not ap.try_restart():
                        _log.info(f"[Launcher] {ap.agent_name} permanently stopped.")
                elif ap.is_alive() and ap.should_run:
                    if ap.restart_count > 0 and ap._last_stable_time > 0:
                        stable_duration = time.time() - ap._last_stable_time
                        if stable_duration > STABLE_RESET_SECONDS:
                            _log.info(
                                f"[Launcher] {ap.agent_name} stable for {stable_duration:.0f}s, resetting restart_count ({ap.restart_count} -> 0)"
                            )
                            ap.restart_count = 0
            for _pid, psp in list(_plugin_services.items()):
                if not psp.is_alive() and psp.should_run:
                    exit_code = psp.process.returncode if psp.process else -1
                    _log.info(f"[Launcher] Plugin service {psp.plugin_id} exited (code: {exit_code})")
                    if not psp.try_restart():
                        _log.info(f"[Launcher] Plugin service {psp.plugin_id} permanently stopped.")
            _shutdown_event.wait(timeout=2)
    except KeyboardInterrupt:
        pass


def _shutdown_all():
    """Phase 11: Graceful shutdown — stop plugin services, agents, cleanup runtime registry."""
    _original_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        _log.info("\n[Launcher] Shutting down all plugin services...")
        for _pid, psp in list(_plugin_services.items()):
            psp.stop()
        _stale_ports = {9700, 9001, 5001}
        for _stale_port in _stale_ports:
            _kill_port_owner(_stale_port)
        _log.info("\n[Launcher] Shutting down all agents...")
        for _name, ap in list(_processes.items()):
            ap.stop()
        _log.info("[Launcher] All agents stopped. Goodbye.")
    finally:
        signal.signal(signal.SIGINT, _original_sigint)
        try:
            if os.path.isdir(RUNTIME_REGISTRY_DIR):
                for _f in os.listdir(RUNTIME_REGISTRY_DIR):
                    if _f.endswith(".json"):
                        with contextlib.suppress(Exception):
                            os.remove(os.path.join(RUNTIME_REGISTRY_DIR, _f))
        except Exception:
            pass


def main():
    # Phase 1-2: Workspace + logging
    _init_workspace()
    _setup_launcher_logging()

    # Phase 3: Args + agent discovery
    args, agents_info = _parse_args_and_discover_agents()

    # Phase 4: Process table
    _register_process_table(agents_info)

    # Phase 5-6: Background services + node registration
    _start_background_services(args.mgmt_port)
    _start_node_registration_if_needed(args.mgmt_port)

    # Phase 7a: Register plugin services + kick light-dep batch (non-blocking)
    # Phase 8:  Auto-start agents FIRST — must not wait on plugin pip/services
    # Phase 7b: Parallel plugin auto-start in background thread pool
    plugin_autostart_ids = _init_and_start_plugin_services()
    _auto_start_agents(args, agents_info)
    _auto_start_plugin_services_parallel(plugin_autostart_ids)

    # Phase 9-10: Signal handler + monitor loop
    _setup_signal_handler()
    _monitor_loop()

    # Phase 11: Shutdown
    _shutdown_all()


if __name__ == "__main__":
    main()
