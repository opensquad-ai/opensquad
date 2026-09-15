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
import contextlib
import json
import logging
import os
import signal
import sys
import tempfile
import threading
import time

_log = logging.getLogger("launcher")


def _share_launcher_main_module(mod=None) -> None:
    """Point ``opensquad.launcher_main`` at the module that actually runs ``main()``.

    Three production entry points load this file under a *different* ``sys.modules``
    key than ``opensquad.launcher_main``:

    * ``python src/opensquad/launcher_main.py`` → ``__main__``
    * ``python -m opensquad.launcher_main`` → ``__main__``
    * ``run.py --service launcher`` (importlib by path) → ``_opensquad_launcher_entry``

    After the management API split, mixins do
    ``from opensquad.launcher_main import _plugin_services``. Without this alias
    that import executes a second copy whose registries stay empty, so Service
    Manager can list plugins (it re-scans plugin.json) but Start returns
    404 ``Plugin service 'websearch' not found``.
    """
    me = mod if mod is not None else sys.modules.get(__name__)
    if me is None:
        return
    if sys.modules.get("opensquad.launcher_main") is not me:
        sys.modules["opensquad.launcher_main"] = me


# Must run during module exec, before any later ``import opensquad.launcher_main``.
_share_launcher_main_module()

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


def _ensure_frozen_utils() -> None:
    """Register opensquad.utils.* from disk when the frozen PYZ omitted them."""
    import importlib
    import importlib.util

    roots: list[str] = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        roots.append(os.path.join(meipass, "opensquad", "utils"))
    roots.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "utils"))
    root = next((p for p in roots if os.path.isdir(p)), "")
    if not root:
        return
    try:
        importlib.import_module("opensquad.utils")
    except Exception:
        pass
    for fname in os.listdir(root):
        if not fname.endswith(".py") or fname.startswith("_"):
            continue
        modname = f"opensquad.utils.{fname[:-3]}"
        if modname in sys.modules:
            continue
        try:
            importlib.import_module(modname)
            continue
        except ModuleNotFoundError:
            pass
        path = os.path.join(root, fname)
        spec = importlib.util.spec_from_file_location(modname, path)
        if spec is None or spec.loader is None:
            continue
        mod = importlib.util.module_from_spec(spec)
        sys.modules[modname] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception:
            sys.modules.pop(modname, None)


_ensure_frozen_utils()

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


def _refresh_builtin_plugins() -> dict:
    """Reload builtin_plugins.json from the live builtin dir (frozen-safe)."""
    global _BUILTIN_PLUGINS, BUILTIN_PLUGINS_DIR
    BUILTIN_PLUGINS_DIR = syscfg.builtin_resources_dir("plugins")
    path = os.path.join(BUILTIN_PLUGINS_DIR, "builtin_plugins.json")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f).get("plugins") or {}
            if data:
                _BUILTIN_PLUGINS = data
        except Exception:
            pass
    return _BUILTIN_PLUGINS


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
            try:
                from opensquad._syscfg._workspace import is_locally_uninstalled

                if is_locally_uninstalled("plugins", pid):
                    continue
            except Exception:
                pass
            seen.add(pid)
            result.append(info)
    return result


def _register_plugin_service_from_info(info: dict) -> "PluginServiceProcess":
    """Create or reuse the process wrapper for one discovered plugin service."""
    pid = info["plugin_id"]
    existing = _plugin_services.get(pid)
    if existing is not None:
        return existing
    psp = PluginServiceProcess(pid, info["plugin_dir"], info["service_cfg"])
    psp.display_name = info.get("display_name", pid)
    psp.plugin_type = info.get("plugin_type", "tool")
    psp.auto_start = info["service_cfg"].get("auto_start", False)
    psp.dependencies = info.get("dependencies", {})
    _plugin_services[pid] = psp
    return psp


def ensure_plugin_service_registered(plugin_id: str) -> "PluginServiceProcess | None":
    """Return the process wrapper for ``plugin_id``, discovering it if needed.

    The Service Manager lists services by re-scanning plugin.json, so a plugin
    installed after boot (or missed at boot) can appear in the UI while still
    missing from ``_plugin_services``. Start/stop/restart/logs must register
    from that same discovery result instead of 404-ing.
    """
    existing = _plugin_services.get(plugin_id)
    if existing is not None:
        return existing
    for info in discover_all_plugin_services():
        if info["plugin_id"] == plugin_id:
            return _register_plugin_service_from_info(info)
    return None


def _collect_plugin_dirs() -> dict[str, str]:
    """Map dir_name -> plugin_dir; workspace entries override builtin.

    Use live syscfg paths (not import-time constants) and accept frozen
    layouts where ``plugin.py`` lives in the PYZ archive.
    """
    from plugins.plugin_manager import collect_plugin_dirs

    return collect_plugin_dirs()


def _abspath_under(path: str, root: str) -> bool:
    if not path or not root:
        return False
    abs_path = os.path.abspath(path)
    abs_root = os.path.abspath(root)
    return abs_path == abs_root or abs_path.startswith(abs_root + os.sep)


def _same_abs(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return os.path.abspath(a) == os.path.abspath(b)


def _is_bundled_resource(path: str, workspace_root: str, builtin_root: str) -> bool:
    """True when ``path`` is the shipped seed, not a user workspace overlay."""
    if not path:
        return False
    if _same_abs(workspace_root, builtin_root):
        return _abspath_under(path, builtin_root)
    return _abspath_under(path, builtin_root) and not _abspath_under(path, workspace_root)


def _workspace_overlay_dir(workspace_root: str, builtin_root: str, dir_name: str) -> str | None:
    """Return a workspace directory that is safe to rmtree, or None."""
    if not dir_name or _same_abs(workspace_root, builtin_root):
        return None
    candidate = os.path.join(workspace_root, dir_name)
    if not os.path.isdir(candidate) or not _abspath_under(candidate, workspace_root):
        return None
    builtin_copy = os.path.join(builtin_root, dir_name)
    if os.path.isdir(builtin_copy) and _same_abs(candidate, builtin_copy):
        return None
    return candidate


def _resolve_plugin_dir(name: str) -> tuple[str | None, str | None]:
    """Resolve plugin.json name or directory name to (plugin_dir, dir_name).

    Uses an unfiltered directory scan so uninstall still resolves after the
    plugin has already been hidden in this workspace.
    """
    from opensquad.resource_uninstall import _iter_plugin_dirs, resolve_plugin_dir_name

    collected = _iter_plugin_dirs()
    for cand_name, plugin_dir in _collect_plugin_dirs().items():
        collected.setdefault(cand_name, plugin_dir)
    dir_name = resolve_plugin_dir_name(name)
    if dir_name and dir_name in collected:
        return collected[dir_name], dir_name
    if name in collected:
        return collected[name], name
    for cand_name, plugin_dir in collected.items():
        manifest = os.path.join(plugin_dir, "plugin.json") if plugin_dir else ""
        if not os.path.isfile(manifest):
            continue
        try:
            with open(manifest, encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("name") == name:
                return plugin_dir, cand_name
        except Exception:
            pass
    if dir_name:
        for root in _plugin_search_dirs():
            plugin_dir = os.path.join(root, dir_name)
            if os.path.isdir(plugin_dir):
                return plugin_dir, dir_name
        return None, dir_name
    return None, None


def _skill_declared_name(skill_dir: str, fallback: str) -> str:
    skill_json = os.path.join(skill_dir, "skill.json")
    if os.path.isfile(skill_json):
        try:
            with open(skill_json, encoding="utf-8") as f:
                meta = json.load(f)
            declared = str(meta.get("name") or "").strip()
            if declared:
                return declared
        except Exception:
            pass
    return fallback


def _find_skill_dir(name: str) -> str | None:
    dirs = _collect_skill_dirs()
    if name in dirs:
        return dirs[name]
    for dir_name, skill_dir in dirs.items():
        if _skill_declared_name(skill_dir, dir_name) == name:
            return skill_dir
    for root in _skill_search_dirs():
        skill_dir = os.path.join(root, name)
        if os.path.isdir(skill_dir):
            from opensquad._syscfg._workspace import is_locally_uninstalled

            if is_locally_uninstalled("skills", os.path.basename(os.path.abspath(skill_dir))):
                continue
            return skill_dir
    return None


def plan_resource_uninstall(resource_type: str, name: str) -> tuple[str | None, str | None, int, str]:
    """Resolve an uninstall request to an optional workspace overlay + dir_name.

    Bundled seeds under src/plugins or src/skills are never rmtree'd. The
    caller should ``mark_locally_uninstalled`` so they disappear from this
    workspace. System plugins listed in builtin_plugins.json cannot be removed.
    """
    if resource_type == "plugins":
        _plugin_dir, dir_name = _resolve_plugin_dir(name)
        if not dir_name:
            from opensquad.resource_uninstall import resolve_plugin_dir_name

            dir_name = resolve_plugin_dir_name(name)
        if not dir_name:
            return None, None, 404, f"Plugin '{name}' not found"
        protected = set(_BUILTIN_PLUGINS.keys())
        if dir_name in protected or name in protected:
            return None, None, 400, (f"Plugin '{name}' is a system plugin and cannot be uninstalled.")
        overlay = _workspace_overlay_dir(PLUGINS_DIR, BUILTIN_PLUGINS_DIR, dir_name)
        return overlay, dir_name, 200, ""
    if resource_type == "skills":
        skill_dir = _find_skill_dir(name)
        if not skill_dir:
            return None, None, 404, f"Skill '{name}' not found"
        dir_name = os.path.basename(os.path.abspath(skill_dir))
        overlay = _workspace_overlay_dir(SKILLS_DIR, BUILTIN_SKILLS_DIR, dir_name)
        return overlay, dir_name, 200, ""
    return None, None, 400, "Invalid resource type"


def resolve_workspace_delete(resource_type: str, name: str) -> tuple[str | None, int, str]:
    """Backward-compatible helper used by tests.

    Overlay-only deletes still return the workspace path. Bundled-only
    uninstalls return status 200 with ``target_dir is None`` (tombstone).
    """
    overlay, _dir_name, status, err = plan_resource_uninstall(resource_type, name)
    if status != 200:
        return None, status, err
    return overlay, 200, ""


def _collect_skill_dirs() -> dict[str, str]:
    """Map skill dir_name -> path; workspace overrides builtin."""
    from opensquad._syscfg._workspace import is_locally_uninstalled

    out: dict[str, str] = {}
    for root in (BUILTIN_SKILLS_DIR, SKILLS_DIR):
        if not os.path.isdir(root):
            continue
        for entry in os.listdir(root):
            if is_locally_uninstalled("skills", entry):
                continue
            skill_dir = os.path.join(root, entry)
            if os.path.isdir(skill_dir):
                out[entry] = skill_dir
    return out


# BOOT_SCRIPT is now inside the package
import opensquad

BOOT_SCRIPT_DIR = os.path.dirname(os.path.abspath(opensquad.__file__))
BOOT_MODULE = "opensquad.agents_boot"

# ── Process management (extracted to opensquad.launcher.process_manager) ──
_RUNTIME_LIST_TTL_S = 5.0
_AGENTS_LIST_TTL_S = 5.0
from opensquad.launcher.process_manager import (
    MANAGEMENT_PORT,
    RUNTIME_REGISTRY_DIR,
    STABLE_RESET_SECONDS,
    AgentProcess,
    PluginServiceProcess,
    _cleanup_runtime_registry,
    _install_builtin_plugin_deps,
    _kill_port_owner,
    set_process_tables,
)

# Workspace migration background task status table (shared across requests)
_workspace_migration_tasks: dict = {}


import contextlib


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
    from opensquad.utils.local_http import open_local

    node_id = syscfg.node_id()
    node_label = syscfg.node_label()
    gateway = syscfg.gateway_http()
    token = syscfg.auth("gateway_token")

    # If system_config.json has node.launcher_url configured, use it directly; otherwise build from local IP
    launcher_url = syscfg.launcher_url()

    def _post(path: str, payload: dict):
        data = json.dumps(payload).encode("utf-8")
        # open_local: gateway_http() maps the default 0.0.0.0 host to 127.0.0.1,
        # and an ambient HTTP_PROXY would swallow this node-registration call
        # (same reason the gateway httpx clients set trust_env=False).
        with open_local(
            f"{gateway}/api/ai-web{path}",
            timeout=8,
            method="POST",
            data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        ) as resp:
            return json.loads(resp.read())

    def _put(path: str, payload: dict):
        data = json.dumps(payload).encode("utf-8")
        with open_local(
            f"{gateway}/api/ai-web{path}",
            timeout=8,
            method="PUT",
            data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        ) as resp:
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

    from opensquad.utils.local_http import open_local

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
                        # P0: admin requests used to be relayed serially inside
                        # this read loop with a synchronous urlopen() call, so
                        # ONE slow request (cold fs/tree scan, plugin-data DB
                        # query) blocked EVERY other request routed through the
                        # tunnel (Web refresh bursts queue up for seconds).
                        # Each admin_request is now dispatched to its own task
                        # and the blocking HTTP relay runs in a worker thread,
                        # so slow requests no longer serialize the tunnel.
                        # websockets forbids concurrent send() on one
                        # connection, so all sends share a lock.
                        _send_lock = asyncio.Lock()
                        _pending_tasks: set[asyncio.Task] = set()

                        async def _relay_admin_request(_ws, _send_lock, msg):
                            req_id = msg.get("req_id", "")
                            method = msg.get("method", "GET").upper()
                            path = msg.get("path", "/")
                            body = msg.get("body")

                            # Relay to local HTTP management server.
                            # Plugin data queries (token_analytics) can take
                            # several seconds on large DBs — keep above Gateway's
                            # 60s plugin-data proxy timeout.
                            _admin_timeout = 60
                            if path == "/api/system/pick-directory":
                                # Native folder dialog can stay open for minutes.
                                _admin_timeout = 600
                            elif "/api/plugins/" in path and path.rstrip("/").endswith("/data"):
                                _admin_timeout = 90

                            def _http_relay():
                                data = json.dumps(body).encode("utf-8") if body else None
                                headers = {"Content-Type": "application/json"} if data else {}
                                # open_local: the relay target is the local
                                # management server (127.0.0.1) -- an ambient
                                # HTTP_PROXY must not intercept it.
                                with open_local(
                                    f"{local_base}{path}",
                                    timeout=_admin_timeout,
                                    method=method,
                                    data=data,
                                    headers=headers,
                                ) as resp:
                                    return json.loads(resp.read())

                            try:
                                resp_body = await asyncio.to_thread(_http_relay)
                                payload = {
                                    "type": "admin_response",
                                    "req_id": req_id,
                                    "status": 200,
                                    "body": resp_body,
                                }
                            except _uerr.HTTPError as e:
                                err_body = {}
                                with contextlib.suppress(Exception):
                                    err_body = json.loads(e.read())
                                payload = {
                                    "type": "admin_response",
                                    "req_id": req_id,
                                    "status": e.code,
                                    "body": err_body,
                                }
                            except Exception as e:
                                payload = {
                                    "type": "admin_response",
                                    "req_id": req_id,
                                    "status": 502,
                                    "body": {"error": str(e)},
                                }
                            try:
                                async with _send_lock:
                                    await _ws.send(json.dumps(payload))
                            except Exception:
                                pass

                        async for raw in ws:
                            try:
                                msg = json.loads(raw)
                            except Exception:
                                continue

                            if msg.get("type") == "keepalive":
                                continue

                            if msg.get("type") != "admin_request":
                                continue

                            task = asyncio.create_task(_relay_admin_request(ws, _send_lock, msg))
                            _pending_tasks.add(task)
                            task.add_done_callback(_pending_tasks.discard)
                    finally:
                        for _t in list(_pending_tasks):
                            _t.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await asyncio.gather(*list(_pending_tasks), return_exceptions=True)
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
    """Start the HTTP management server in a dedicated thread.

    The request handler (``ManagementHandler``) and the exclusive-bind server
    live in :mod:`opensquad.launcher.management_api`, where the handler is split
    by route domain. This function keeps only the bind / exit-on-conflict policy
    so launcher_main stays in charge of process exit. Route-surface parity after
    the split was verified by diffing the dispatch tables method by method.
    """
    # The handler and the exclusive-bind server live in
    # opensquad.launcher.management_api, split by route domain (see that
    # package's docstring). Imported here rather than at module level because
    # management_api imports the launcher registries *from this module*, so a
    # module-level import would be circular.
    _share_launcher_main_module()
    from opensquad.launcher.management_api import ExclusiveHTTPServer, ManagementHandler

    _log.info(f"[Launcher] Binding management port {port} (exclusive={os.name != 'nt'})")
    try:
        server = ExclusiveHTTPServer(("0.0.0.0", port), ManagementHandler)
    except OSError as e:
        # Port already owned (a concurrent launcher won the bind race after
        # both passed the _ensure_single_launcher probe): step aside entirely.
        # Staying alive would register agents and manage the runtime registry
        # in parallel with the owner, causing stale-kill loops.
        _log.error(f"[Launcher] Cannot bind management port {port} ({e}); exiting.")
        os._exit(1)
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
    global BUILTIN_PLUGINS_DIR, BUILTIN_SKILLS_DIR, _BUILTIN_PLUGINS
    AGENTS_DIR = syscfg.workspace_agents_dir()
    PLUGINS_DIR = syscfg.workspace_plugins_dir()
    SKILLS_DIR = syscfg.workspace_skills_dir()
    ROLE_CARDS_DIR = syscfg.workspace_role_cards_dir()
    COLLAB_CARDS_DIR = syscfg.workspace_collab_cards_dir()
    MODEL_CARDS_DIR = syscfg.workspace_model_cards_dir()
    BUILTIN_PLUGINS_DIR = syscfg.builtin_resources_dir("plugins")
    BUILTIN_SKILLS_DIR = syscfg.builtin_resources_dir("skills")
    _builtin_plugins_path = os.path.join(BUILTIN_PLUGINS_DIR, "builtin_plugins.json")
    if os.path.isfile(_builtin_plugins_path):
        try:
            with open(_builtin_plugins_path, encoding="utf-8") as _bf:
                _BUILTIN_PLUGINS = json.load(_bf).get("plugins", {}) or _BUILTIN_PLUGINS
        except Exception:
            pass
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
        _register_plugin_service_from_info(info)

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
    """Phase 8: Auto-start agents with auto_start_on_boot=true (unless --no-auto-start).

    Only the per-agent UI flag is honored. Do **not** warm-start the last CLI
    agent from ``~/.opensquad/cli_credentials.json`` — that used to start
    agents even when 「设为默认启动」 was off, which surprised desktop users.
    ``opensquad code`` still boots the last agent on demand when the CLI runs.
    """
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


def _launcher_lock_path() -> str:
    """Path of the single-launcher PID lock, shared across Python installs."""
    try:
        ws = syscfg.get_workspace()
    except Exception:
        ws = None
    if ws:
        reg_dir = os.path.join(ws, "registry")
        try:
            os.makedirs(reg_dir, exist_ok=True)
        except OSError:
            pass
        return os.path.join(reg_dir, "launcher.pid")
    return os.path.join(tempfile.gettempdir(), "opensquad_launcher.pid")


def _pid_alive(pid: int) -> bool:
    """True only if *pid* is a running process (Windows: still-active, not a zombie)."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            # 5 = ERROR_ACCESS_DENIED → process exists but we cannot query it.
            return k32.GetLastError() == 5
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return int(code.value) == STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return False


def _launcher_api_healthy(port: int) -> bool:
    """True when something on *port* answers the launcher workspace API."""
    from opensquad.utils.local_http import open_local

    try:
        # open_local: an ambient HTTP_PROXY must not intercept this probe. If it
        # does, a running launcher looks unhealthy, _ensure_single_launcher()
        # concludes no launcher owns the port and a SECOND instance starts --
        # and dual launchers kill each other's agents as "stale" in a 60s
        # kill/restart loop.
        with open_local(f"http://127.0.0.1:{int(port)}/api/workspace", timeout=2.0) as r:
            return r.status == 200 and "workspace" in r.read().decode("utf-8", "ignore")
    except Exception:
        return False


def _ensure_single_launcher(port: int, _attempt: int = 0) -> bool:
    """Exit when another healthy launcher already owns the management port.

    Dual-launcher instances (e.g. uv-tools opensquad and anaconda editable
    install both spawning launcher_main.py) share the runtime registry and
    kill each other's agents as "stale" — a 60s kill/restart loop. The second
    instance steps aside instead of fighting for it.

    Port probing alone has a TOCTOU race: two launchers started in the same
    second both see the port free and both proceed, then one fails to bind
    while the other wins — each may already have spawned agents. An atomic
    O_EXCL PID lockfile (shared workspace → shared across Python installs)
    serializes ownership so only ONE launcher manages a workspace.

    A live PID with a dead management port (desktop frozen launcher shutting
    down, crash before bind, stale lock) is treated as stale so ``opensquad
    dev`` can take over instead of waiting 45s on a port that never opens.
    """
    if _attempt > 3:
        _log.error("[Launcher] Could not acquire launcher lock after retries; exiting.")
        return False
    lock_path = _launcher_lock_path()
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        # We hold the lockfile — we are the owner. Still verify the port in
        # case a pre-existing (lock-less) launcher already serves it.
        if _launcher_api_healthy(port):
            _log.warning(
                f"[Launcher] Port {port} already served by another launcher; "
                "exiting (avoid dual-instance agent management)."
            )
            # Do not leave our (soon-dead) pid in the lock — that blocks the
            # next ``opensquad dev`` after the lock-less owner later exits.
            try:
                os.remove(lock_path)
            except OSError:
                pass
            return False
        return True
    except FileExistsError:
        owner = 0
        try:
            with open(lock_path, encoding="utf-8") as f:
                owner = int(f.read().strip() or "0")
        except Exception:
            owner = 0
        if owner and _pid_alive(owner) and _launcher_api_healthy(port):
            _log.warning(
                f"[Launcher] Another launcher (pid {owner}) owns {lock_path}; exiting "
                "(avoid dual-instance agent management)."
            )
            return False
        _log.info(f"[Launcher] Stale launcher lock (pid {owner or '?'}, api down); taking over {lock_path}")
        try:
            os.remove(lock_path)
        except OSError:
            pass
        return _ensure_single_launcher(port, _attempt + 1)


def main():
    _share_launcher_main_module()
    # Phase 1-2: Workspace + logging
    _init_workspace()
    _setup_launcher_logging()

    # Phase 3: Args + agent discovery
    args, agents_info = _parse_args_and_discover_agents()

    # Single-instance guard: a second launcher (different python install)
    # must not register agents or start management on an owned port.
    if not _ensure_single_launcher(args.mgmt_port):
        return

    # Phase 4: Process table
    _register_process_table(agents_info)
    # Inject our process tables into process_manager so its cleanup/stale
    # logic sees the agents we manage. This was never wired up: without it,
    # process_manager._processes stays empty and _cleanup_runtime_registry
    # treats every live agent/plugin as stale and kills it (~60s loop).
    set_process_tables(_processes, _plugin_services)

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
