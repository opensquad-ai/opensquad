"""
Process management for OpenSquad Launcher.

Contains AgentProcess, PluginServiceProcess, and process lifecycle utilities.
Extracted from launcher.py to improve maintainability.
"""

import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime

_log = logging.getLogger("launcher.process_manager")

_PYINSTALLER_PATH_MARKERS = (
    os.path.join("backend-win", "run"),
    os.path.join("backend-mac", "run"),
    os.path.join("backend-linux", "run"),
    "_internal",
)


def _is_pyinstaller_internal_path(path: str) -> bool:
    if not path:
        return False
    norm = os.path.normcase(os.path.normpath(path))
    return any(marker in norm for marker in _PYINSTALLER_PATH_MARKERS)


def _sanitize_path_for_child(path_value: str) -> str:
    parts = [p for p in path_value.split(os.pathsep) if p and not _is_pyinstaller_internal_path(p)]
    return os.pathsep.join(parts)


def _resolve_packaged_python_executable() -> str | None:
    """Pick Python for agent/plugin child processes in frozen desktop builds."""
    from opensquad.agent_runtime import resolve_bundled_agent_python

    bundled = resolve_bundled_agent_python()
    if bundled:
        return bundled

    override = os.environ.get("OPENSQUAD_PYTHON") or os.environ.get("OPENSQUAD_AGENT_PYTHON")
    if override:
        override = os.path.abspath(override)
        if os.path.isfile(override):
            return override

    # Prefer 3.11 via py launcher — the PyInstaller bundle is 3.11-compiled,
    # so only 3.11 is safe. 3.10/3.12/3.13+ will crash with
    # "Module use of python311.dll conflicts with this version of Python"
    # if they fall back to importing _internal/ loose copies.
    if sys.platform == "win32":
        py_launcher = shutil.which("py")
        if py_launcher:
            for ver in ("3.11",):
                try:
                    proc = subprocess.run(
                        [py_launcher, f"-{ver}", "-c", "import sys; print(sys.executable)"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                except Exception:
                    continue
                if proc.returncode == 0:
                    exe = proc.stdout.strip()
                    if exe and os.path.isfile(exe):
                        return exe

    for name in ("python3.11", "python311"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _child_python_executable() -> str | None:
    """Python interpreter for agent/plugin child processes.

    In a PyInstaller bundle ``sys.executable`` is ``run.exe`` and cannot run
    ``python -m opensquad.agents_boot`` or plugin ``service/main.py`` scripts.
    Spawning ``run.exe`` without ``--service`` would start another gateway on
    port 9555, which looks like a backend crash loop and breaks agent startup.
    Prefer a system Python when packaged.
    """
    if not getattr(sys, "frozen", False):
        return sys.executable
    return _resolve_packaged_python_executable()


def _build_child_process_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for agent/plugin subprocesses.

    Strip PyInstaller ``_internal`` dirs from PATH on Windows so a system Python
    (e.g. 3.13) does not load ``python311.dll`` from the bundled backend and crash
    with ``Module use of python311.dll conflicts with this version of Python``.
    """
    child_env = os.environ.copy()
    if getattr(sys, "frozen", False):
        # Another ``run.exe`` child (--service agent/launcher).  Do NOT set
        # PYTHONUTF8/PYTHONIOENCODING here — run.py reconfigures streams directly
        # and documents that these env vars can crash PyInstaller boot (site.py).
        child_env.pop("PYTHONUTF8", None)
        child_env.pop("PYTHONIOENCODING", None)
    else:
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["PYTHONUTF8"] = "1"
    if sys.platform == "win32":
        if child_env.get("PATH"):
            child_env["PATH"] = _sanitize_path_for_child(child_env["PATH"])
        child_env.pop("PYTHONHOME", None)

    install_dir = syscfg.get_builtin_root()
    if getattr(sys, "frozen", False):
        # Do NOT put _internal/ on PYTHONPATH. The Agent Python's _pth file
        # ignores PYTHONPATH anyway, and a system-Python fallback would pick
        # up the loose uvicorn/fastapi copies under _internal/ WITHOUT their
        # transitive deps (click, annotated_doc, ...), crashing the service
        # with ModuleNotFoundError. Service entry scripts manage sys.path
        # themselves (append _project_root in frozen mode so site-packages
        # wins). Frozen run.exe children (agent processes) get their imports
        # from the PYZ archive, not from PYTHONPATH.
        child_env["PYTHONPATH"] = ""
        ws = (
            os.environ.get("OPENSQUAD_WORKSPACE", "").strip()
            or os.environ.get("OPENSQUAD_USER_DATA", "").strip()
            or os.environ.get("OPENSQUAD_APP_DATA", "").strip()
        )
        if ws:
            ws_abs = os.path.abspath(ws)
            child_env.setdefault("OPENSQUAD_WORKSPACE", ws_abs)
            child_env.setdefault("OPENSQUAD_USER_DATA", ws_abs)
            app_data = os.environ.get("OPENSQUAD_APP_DATA", "").strip()
            if app_data:
                child_env.setdefault("OPENSQUAD_APP_DATA", os.path.abspath(app_data))
    else:
        existing_pp = child_env.get("PYTHONPATH", "")
        child_env["PYTHONPATH"] = (install_dir + os.pathsep + existing_pp) if existing_pp else install_dir

    if extra:
        child_env.update(extra)
    return child_env


# Backward-compatible alias for plugin service spawns.
def _plugin_python_executable() -> str:
    return _child_python_executable() or sys.executable


from opensquad._storage.json_io import read_json as _read_json
from opensquad.system_config import syscfg

# ── Constants ──
MAX_RESTART_ATTEMPTS = 5
RESTART_COOLDOWN = 3  # seconds (base; actual uses exponential backoff)
RESTART_BACKOFF_SCHEDULE = [3, 6, 12, 30, 60]
STABLE_RESET_SECONDS = 300

# Circuit breaker constants
CIRCUIT_BREAKER_MAX_FAILS = 5  # consecutive failures before opening
CIRCUIT_BREAKER_COOLDOWN = 60  # seconds to stay open before half-open retry
CIRCUIT_BREAKER_HALF_OPEN_MAX = 1  # max half-open retries before staying open
CIRCUIT_BREAKER_RESET_SECONDS = 300  # stable uptime before fully resetting circuit

# Permanent failure indicators (retrying these is pointless)
PERMANENT_FAILURE_SIGNALS = {
    "EADDRINUSE",
    "EACCES",
    "ENOENT",
    "ECONNREFUSED",
    "Address already in use",
    "Permission denied",
    "No such file or directory",
    "Cannot assign requested address",
}
LOG_BUFFER_SIZE = 500
MANAGEMENT_PORT = syscfg.port("launcher")
RUNTIME_REGISTRY_DIR = syscfg.workspace_metadata_dir("runtime")

# Project root (same as launcher.py)
import contextlib

import opensquad

BOOT_SCRIPT_DIR = os.path.dirname(os.path.abspath(opensquad.__file__))
BOOT_MODULE = "opensquad.agents_boot"
PROJECT_ROOT = syscfg.project_root()

# ── Global process tables (shared with launcher.py) ──
# These are populated by launcher.py main() and read by ManagementHandler
_processes: dict[str, "AgentProcess"] = {}
_plugin_services: dict[str, "PluginServiceProcess"] = {}

# Global reentrant lock protecting _processes, _plugin_services, and all
# launcher shared state (task heartbeats, stalled set).  Acquired by
# launcher.py and _launcher_api.py for the same dicts.
_launcher_state_lock = threading.RLock()


def get_launcher_state_lock() -> threading.RLock:
    return _launcher_state_lock


def set_process_tables(procs: dict, plugin_svcs: dict):
    """Inject the process tables from launcher.py (called once at startup)."""
    global _processes, _plugin_services
    _processes = procs
    _plugin_services = plugin_svcs


# NOTE: _read_json is now imported from opensquad._storage.json_io

MAX_RESTART_ATTEMPTS = 5
RESTART_COOLDOWN = 3  # seconds (base; actual uses exponential backoff)
RESTART_BACKOFF_SCHEDULE = [3, 6, 12, 30, 60]  # Exponential backoff seconds per restart attempt
STABLE_RESET_SECONDS = 300  # Reset restart_count after 5 minutes of stable running
LOG_BUFFER_SIZE = 500  # keep last N lines per agent
MANAGEMENT_PORT = syscfg.port("launcher")
RUNTIME_REGISTRY_DIR = syscfg.workspace_metadata_dir("runtime")

# Workspace migration background task status table (shared across requests)
_workspace_migration_tasks: dict = {}

# Event signaled when the background _install_builtin_plugin_deps thread finishes
# (success or failure). PluginServiceProcess.start() waits on this so a service
# is not spawned before its dependencies are ready — otherwise the service
# crashes with ModuleNotFoundError on import while pip is still bootstrapping.
_plugin_deps_ready = threading.Event()


def is_port_in_use(port: int) -> bool:
    """Check whether a local port is in use"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def check_port_conflict(config: dict) -> str:
    """
    Check whether config.web_server.port conflicts with a running agent or system process.
    Returns an error message string; returns empty string if no conflict.
    Only checks when port is explicitly specified; if port is omitted, the system auto-assigns one.
    """
    port = config.get("web_server", {}).get("port")
    if not port:
        return ""

    # Check whether it is occupied by another alive agent
    with _launcher_state_lock:
        for agent_name, ap in _processes.items():
            if ap.is_alive() and ap.actual_port == port:
                return (
                    f"Port {port} is already used by Agent '{agent_name}'. "
                    "Please change web_server.port to another value, or remove the port field to let the system auto-assign a free port."
                )

    # Check whether it is occupied by another system process
    if is_port_in_use(port):
        return (
            f"Port {port} is already used by another system process. "
            "Please change web_server.port to another value, or remove the port field to let the system auto-assign a free port."
        )

    return ""


def find_available_port(start_port: int, exclude_ports: list[int] | None = None) -> int:
    """Find the first available free port starting from start_port"""
    port = start_port
    exclude = set(exclude_ports or [])
    while port < 65535:
        if port not in exclude and not is_port_in_use(port):
            return port
        port += 1
    return start_port


def _ensure_runtime_registry_dir():
    with contextlib.suppress(Exception):
        os.makedirs(RUNTIME_REGISTRY_DIR, exist_ok=True)


def _registry_path(kind: str, identifier: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", identifier)
    return os.path.join(RUNTIME_REGISTRY_DIR, f"{kind}_{safe}.json")


def _write_runtime_registry(kind: str, identifier: str, payload: dict):
    _ensure_runtime_registry_dir()
    p = _registry_path(kind, identifier)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _log.error(f"[Launcher] Failed to write runtime registry {p}: {e}")


def _remove_runtime_registry(kind: str, identifier: str):
    p = _registry_path(kind, identifier)
    try:
        if os.path.exists(p):
            os.remove(p)
    except Exception as e:
        _log.error(f"[Launcher] Failed to remove runtime registry {p}: {e}")


def _terminate_pid_tree(pid: int) -> bool:
    """Terminate only the target process tree, never wildcard-kill python/node."""
    if not pid or pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)
            return True
        try:
            os.killpg(pid, signal.SIGTERM)
            return True
        except Exception:
            try:
                os.kill(pid, signal.SIGTERM)
                return True
            except Exception:
                return False
    except Exception:
        return False


def _kill_port_owner(port: int) -> bool:
    """Kill any process holding the given port. Returns True if a process was killed."""
    if not port or port <= 0:
        return False
    try:
        if sys.platform == "win32":
            result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, check=False)
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    pid_str = parts[-1]
                    try:
                        pid = int(pid_str)
                        _log.warning(f"[Launcher] Found stale port {port} held by PID {pid}, killing...")
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)
                        import time

                        time.sleep(1)
                        return True
                    except ValueError:
                        pass
        else:
            result = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True, check=False)
            if result.stdout.strip():
                for pid_str in result.stdout.strip().splitlines():
                    try:
                        pid = int(pid_str)
                        _log.warning(f"[Launcher] Found stale port {port} held by PID {pid}, killing...")
                        os.kill(pid, signal.SIGKILL)
                        return True
                    except Exception:
                        pass
    except Exception:
        pass
    return False


def _pid_exists(pid: int) -> bool:
    """Check if a process with the given PID exists.
    Performance: uses psutil (in-memory) instead of spawning a tasklist subprocess."""
    if not pid or pid <= 0:
        return False
    try:
        import psutil

        return psutil.pid_exists(pid)
    except ImportError:
        pass
    # Fallback for non-Windows or if psutil unavailable
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_runtime_registry() -> list[dict]:
    _ensure_runtime_registry_dir()
    items: list[dict] = []
    try:
        for name in os.listdir(RUNTIME_REGISTRY_DIR):
            if not name.endswith(".json"):
                continue
            path = os.path.join(RUNTIME_REGISTRY_DIR, name)
            try:
                with open(path, encoding="utf-8") as f:
                    payload = json.load(f)
                payload["_registry_file"] = path
                payload["_kind"] = (
                    "agent" if name.startswith("agent_") else "plugin" if name.startswith("plugin_") else "unknown"
                )
                items.append(payload)
            except Exception:
                continue
    except Exception:
        pass
    return items


def _cleanup_runtime_registry(force_kill: bool = False) -> dict:
    cleaned = 0
    killed = 0
    remaining = []
    entries = _read_runtime_registry()
    for entry in entries:
        pid = entry.get("pid")
        kind = entry.get("_kind", "unknown")
        identifier = entry.get("agent_id") or entry.get("plugin_id") or "unknown"
        alive = _pid_exists(int(pid)) if pid else False
        managed = False
        if kind == "agent":
            with _launcher_state_lock:
                managed = any(ap.agent_id == identifier for ap in _processes.values())
        elif kind == "plugin":
            managed = identifier in _plugin_services

        if not alive:
            _remove_runtime_registry(kind, identifier)
            cleaned += 1
            continue

        # PID is alive but NOT managed by current launcher instance — this is a
        # stale process from a previous launcher run that wasn't cleaned up on exit
        # (e.g. Launcher was force-killed, crashed, or user closed terminal).
        # Kill it and clean up the registry to avoid port conflicts.
        if alive and not managed:
            _log.info(
                f"[Launcher] Found stale {kind} process (identifier={identifier}, pid={pid}) from previous run, terminating..."
            )
            if _terminate_pid_tree(int(pid)):
                _remove_runtime_registry(kind, identifier)
                cleaned += 1
                killed += 1
                _log.info(f"[Launcher] Stale {kind} process (pid={pid}) terminated and cleaned up.")
            else:
                _log.warning(
                    f"[Launcher] WARNING: Could not terminate stale {kind} process (pid={pid}), may cause port conflicts."
                )
                entry["alive"] = True
                entry["managed"] = False
                remaining.append(entry)
            continue

        if force_kill and not managed and _terminate_pid_tree(int(pid)):
            killed += 1
            _remove_runtime_registry(kind, identifier)
            cleaned += 1
            continue

        entry["alive"] = alive
        entry["managed"] = managed
        remaining.append(entry)

    return {
        "cleaned": cleaned,
        "killed": killed,
        "remaining": remaining,
    }


class AgentProcess:
    """Manages a single Agent child process"""

    # P0-2: Health check config
    HEALTH_CHECK_INTERVAL = 10  # seconds between probes
    HEALTH_CHECK_TIMEOUT = 5  # seconds before probe is considered failed
    HEALTH_CHECK_FAIL_THRESHOLD = 3  # consecutive failures before restart
    HEALTH_CHECK_INITIAL_DELAY = 20  # let health server register before first probe

    def __init__(self, agent_dir: str, config: dict):
        self.agent_dir = agent_dir
        self.dir_name = os.path.basename(agent_dir)
        self.config = config
        self.agent_id = config.get("agent_id", self.dir_name)
        self.agent_name = config.get("agent_name", self.agent_id)
        self.process: subprocess.Popen | None = None
        self.restart_count = 0
        self.should_run = False  # Changed to False; explicitly set by start()
        self._log_thread: threading.Thread | None = None
        self.log_buffer: deque = deque(maxlen=LOG_BUFFER_SIZE)
        self.started_at: str | None = None
        self.actual_port = config.get("web_server", {}).get("port")
        self._last_stable_time: float = 0.0  # Timestamp when agent last became stable (for restart_count reset)

        # P0-2: Health check state
        self._health_port: int | None = None
        self._health_thread: threading.Thread | None = None
        self._stop_health = threading.Event()
        self._health_fail_count = 0
        self._last_health_ok: bool | None = None
        self._last_health_time: float | None = None

    def start(self, allocated_ports: list[int] | None = None):
        """Start the child process"""
        if self.process and self.process.poll() is None:
            _log.warning(f"[Launcher] {self.agent_name} already running (PID: {self.process.pid})")
            return False

        # Dynamic port assignment: use configured port if available; auto-find a free port if occupied or not configured
        target_port = self.config.get("web_server", {}).get("port")
        if not target_port or is_port_in_use(target_port):
            if target_port and is_port_in_use(target_port):
                _log.warning(f"[Launcher] Port {target_port} in use, auto-assigning for {self.agent_name}")
            new_port = find_available_port(8001, exclude_ports=allocated_ports)
            target_port = new_port

        self.actual_port = target_port

        python_exe = _child_python_executable()
        if python_exe is None and not getattr(sys, "frozen", False):
            _log.error(
                "[Launcher] Cannot start %s: install the Agent Python runtime via the "
                "desktop setup wizard (%%LOCALAPPDATA%%\\OpenSquad\\runtime) or set "
                "OPENSQUAD_PYTHON to python.exe.",
                self.agent_name,
            )
            return False

        # Frozen mode: use the bundled run.exe itself to run the agent (it has
        # the full opensquad package in its PYZ).  An external Python cannot
        # import opensquad because PyInstaller puts .py into the PYZ archive,
        # not onto disk.  Non-frozen: use the venv/system Python with -m.
        if getattr(sys, "frozen", False):
            cmd = [
                sys.executable,
                "--service",
                "agent",
                "--agent-dir",
                self.agent_dir,
                "--port",
                str(target_port),
            ]
        else:
            cmd = [
                python_exe,
                "-m",
                BOOT_MODULE,
                "--agent-dir",
                self.agent_dir,
                "--port",
                str(target_port),
            ]

        child_env = _build_child_process_env(
            {
                "OPENSQUAD_AGENT_ID": self.agent_id,
                "OPENSQUAD_AGENT_DIR": self.agent_dir,
                "OPENSQUAD_LAUNCHER_PORT": str(MANAGEMENT_PORT),  # for task_watch heartbeat
            }
        )
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=PROJECT_ROOT,
            env=child_env,
            bufsize=1,  # line-buffered
            creationflags=creationflags,
        )

        self.should_run = True
        self.restart_count = 0
        self.started_at = datetime.now().isoformat()

        _write_runtime_registry(
            "agent",
            self.agent_id,
            {
                "agent_id": self.agent_id,
                "agent_name": self.agent_name,
                "pid": self.process.pid,
                "port": target_port,
                "agent_dir": self.agent_dir,
                "started_at": self.started_at,
            },
        )

        _log.info(f"[Launcher] Started {self.agent_name} on Port {target_port} (PID: {self.process.pid})")

        # Start log forwarding thread
        self._log_thread = threading.Thread(target=self._forward_logs, daemon=True, name=f"log-{self.agent_id}")
        self._log_thread.start()

        # P0-2: Start health-check monitor after a brief delay (let Agent boot its health server)
        self._stop_health.clear()
        self._health_fail_count = 0
        self._health_port = None
        if self._health_thread and self._health_thread.is_alive():
            self._health_thread.join(timeout=1)
        self._health_thread = threading.Thread(
            target=self._health_monitor_loop,
            daemon=True,
            name=f"health-agent-{self.agent_id}",
        )
        self._health_thread.start()
        return True

    def stop(self):
        """Stop the child process (safe: target PID tree only)."""
        self.should_run = False
        # P0-2: Stop health monitor first
        self._stop_health.set()
        if self._health_thread and self._health_thread.is_alive():
            self._health_thread.join(timeout=2)
            self._health_thread = None
        if self.process and self.process.poll() is None:
            pid = self.process.pid
            _log.info(f"[Launcher] Stopping {self.agent_name} (PID: {pid})...")
            _terminate_pid_tree(pid)
            try:
                self.process.wait(timeout=8)
            except Exception:
                with contextlib.suppress(Exception):
                    self.process.kill()
                self.process.wait()
            _remove_runtime_registry("agent", self.agent_id)
            _log.info(f"[Launcher] {self.agent_name} stopped.")
            return True
        _remove_runtime_registry("agent", self.agent_id)
        return False

    def is_alive(self) -> bool:
        """Check whether the process is alive"""
        return self.process is not None and self.process.poll() is None

    def try_restart(self) -> bool:
        """Attempt to restart with exponential backoff."""
        if not self.should_run:
            return False
        if self.restart_count >= MAX_RESTART_ATTEMPTS:
            _log.error(f"[Launcher] {self.agent_name} exceeded max restarts ({MAX_RESTART_ATTEMPTS}), giving up.")
            return False

        self.restart_count += 1
        # Exponential backoff: use schedule or cap at last value
        backoff_idx = min(self.restart_count - 1, len(RESTART_BACKOFF_SCHEDULE) - 1)
        wait_seconds = RESTART_BACKOFF_SCHEDULE[backoff_idx]
        _log.info(
            f"[Launcher] Restarting {self.agent_name} (attempt {self.restart_count}/{MAX_RESTART_ATTEMPTS}, backoff {wait_seconds}s)..."
        )
        time.sleep(wait_seconds)
        # Get all currently allocated dynamic ports to avoid conflicts on restart
        with _launcher_state_lock:
            used_ports = [ap.actual_port for ap in _processes.values() if ap.is_alive()]
        self.start(allocated_ports=used_ports)
        self._last_stable_time = time.time()  # Reset stable timer on restart
        return True

    def get_status(self) -> dict:
        """Return process status information"""
        return {
            "dir_name": self.dir_name,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "alive": self.is_alive(),
            "pid": self.process.pid if self.process and self.process.poll() is None else None,
            "port": self.actual_port,  # Return actual port
            "should_run": self.should_run,
            "restart_count": self.restart_count,
            "started_at": self.started_at,
            "config": self.config,
            "health_ok": self._last_health_ok,
            "health_port": self._health_port,
        }

    def get_logs(self, lines: int = 200) -> list[str]:
        """Return the last N lines of logs"""
        buf = list(self.log_buffer)
        return buf[-lines:]

    def reload_config(self):
        """Re-read config.json from disk"""
        config_path = os.path.join(self.agent_dir, "config.json")
        try:
            self.config = _read_json(config_path)
            self.agent_id = self.config.get("agent_id", self.dir_name)
            self.agent_name = self.config.get("agent_name", self.agent_id)
        except Exception as e:
            _log.info(f"[Launcher] Failed to reload config for {self.dir_name}: {e}")

    def restart(self) -> bool:
        """Clean restart: kill current process and launch a fresh one (not crash-recovery)."""
        if self.process and self.process.poll() is None:
            try:
                self.process.kill()
                self.process.wait(timeout=5)
            except Exception:
                pass
        self.restart_count = 0
        self.should_run = True
        self.start()
        return self.is_alive()

    def _forward_logs(self):
        """Forward child process output to the main console (with prefix) + store in log buffer"""
        prefix = f"[{self.agent_id}]"
        try:
            for line in self.process.stdout:
                line = line.rstrip("\n\r")
                if line:
                    ts = datetime.now().strftime("%H:%M:%S")
                    log_line = f"[{ts}] {line}"
                    self.log_buffer.append(log_line)
                    _log.info(f"{prefix} {line}")
        except (ValueError, OSError):
            # Process already closed
            pass

    # ── P0-2: Health check for Agent processes ──

    def _discover_health_port(self) -> int | None:
        """Discover the Agent's health-check port from its stdout or registry."""
        # Strategy 1: Check runtime registry for health_port field (written by Agent)
        registry = _read_runtime_registry()
        for entry in registry:
            if entry.get("agent_id") == self.agent_id and "health_port" in entry:
                return int(entry["health_port"])
        # Strategy 2: Scan stdout log buffer for '[Boot] Health server on port X'
        for line in self.log_buffer:
            match = re.search(r"Health server on port (\d+)", line)
            if match:
                return int(match.group(1))
        return None

    def _check_agent_health(self) -> bool:
        """Probe the Agent's /health endpoint. Returns True if healthy."""
        if self._health_port is None:
            self._health_port = self._discover_health_port()
        if not self._health_port:
            return False  # Not discovered yet
        try:
            import urllib.request

            url = f"http://127.0.0.1:{self._health_port}/health"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self.HEALTH_CHECK_TIMEOUT) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _health_monitor_loop(self):
        """Periodically probe Agent health; restart on consecutive failures."""
        # Initial delay: let Agent boot and start its health server
        self._stop_health.wait(self.HEALTH_CHECK_INITIAL_DELAY)
        while not self._stop_health.is_set():
            alive = self.is_alive()
            if not alive:
                # Process already dead — the existing restart logic in launcher.py will handle it
                break

            healthy = self._check_agent_health()
            self._last_health_ok = healthy
            self._last_health_time = time.time()

            if healthy:
                if self._health_fail_count > 0:
                    _log.info(
                        f"[Launcher] {self.agent_name} health recovered after {self._health_fail_count} failure(s)"
                    )
                self._health_fail_count = 0
            else:
                self._health_fail_count += 1
                _log.warning(
                    f"[Launcher] {self.agent_name} health check failed ({self._health_fail_count}/{self.HEALTH_CHECK_FAIL_THRESHOLD})"
                )
                if self._health_fail_count >= self.HEALTH_CHECK_FAIL_THRESHOLD:
                    _log.error(
                        f"[Launcher] {self.agent_name} health check FAILED {self.HEALTH_CHECK_FAIL_THRESHOLD} times — triggering restart"
                    )
                    # Trigger restart on the main thread (avoid blocking this monitor)
                    threading.Thread(
                        target=self._trigger_restart,
                        daemon=True,
                        name=f"restart-{self.agent_id}",
                    ).start()
                    break

            self._stop_health.wait(self.HEALTH_CHECK_INTERVAL)

    def _trigger_restart(self):
        """Safely trigger try_restart from a background thread."""
        try:
            self.try_restart()
        except Exception as e:
            _log.error(f"[Launcher] {self.agent_name} restart failed: {e}")


class PluginServiceProcess:
    """Manages a single plugin HTTP service child process.

    Enhanced with:
      - Health check monitoring (configurable interval & endpoint)
      - Auto-restart with exponential backoff
      - Running time tracking
      - Log file persistence to disk
      - Rich status reporting (health_status, uptime, etc.)
    """

    def __init__(self, plugin_id: str, plugin_dir: str, service_cfg: dict):
        self.plugin_id = plugin_id
        self.plugin_dir = plugin_dir
        self.service_cfg = service_cfg
        self.process: subprocess.Popen | None = None
        self.restart_count = 0
        self.should_run = False
        self._log_thread: threading.Thread | None = None
        self.log_buffer: deque = deque(maxlen=LOG_BUFFER_SIZE)
        self.started_at: str | None = None
        self.port = self._resolve_port()

        # Health check
        self.health_endpoint = service_cfg.get("health_endpoint", "/health")
        self.health_check_interval = service_cfg.get("health_check_interval", 30)
        self._health_thread: threading.Thread | None = None
        self._stop_health = threading.Event()
        self._last_health_ok: bool | None = None
        self._last_health_time: float | None = None

        # Auto-restart backoff
        self._restart_backoff = service_cfg.get("restart_policy", {}).get("backoff", [3, 6, 12, 30, 60])
        self._max_restarts = service_cfg.get("restart_policy", {}).get("max_retries", MAX_RESTART_ATTEMPTS)

        # Log file persistence
        self._log_file_path: str | None = None
        self._log_file_handle: object | None = None

        # Plugin metadata (populated after discover)
        self.display_name: str | None = None
        self.plugin_type: str | None = None
        self.auto_start: bool = service_cfg.get("auto_start", False)
        self.dependencies: dict = {}  # Populated by main() from discover_plugin_services()

        # ── Circuit breaker state ──
        self._circuit_state: str = "closed"  # closed / open / half-open
        self._circuit_fail_count: int = 0  # consecutive failures
        self._circuit_open_until: float | None = None  # time.time threshold
        self._circuit_half_open_retries: int = 0  # half-open retry count

        # Per-instance lock serializing start()/stop() to prevent the
        # TOCTOU race where two threads both pass the "already running?"
        # check and spawn duplicate child processes on the same port.
        self._start_lock = threading.Lock()
        self._circuit_last_failure_time: float | None = None
        self._circuit_last_failure_reason: str = ""
        self._circuit_permanent: bool = False  # True = permanent failure, never retry
        self._circuit_was_alive: bool = False  # track alive state transitions for stable timer

    def _resolve_port(self) -> int:
        """Port priority: data/plugins/{id}/config.json > system_config ports > plugin.json default_port"""
        # 1. data/plugins/{id}/config.json → port
        config_path = syscfg.workspace_data_dir("plugins", self.plugin_id, "config.json")
        if os.path.isfile(config_path):
            try:
                cfg = _read_json(config_path)
                if "port" in cfg:
                    return int(cfg["port"])
            except Exception:
                pass
        # 2. system_config.json ports.{port_key}
        port_key = self.service_cfg.get("port_key", "")
        if port_key:
            try:
                return syscfg.port(port_key)
            except Exception:
                pass
        # 3. plugin.json service.default_port — if unset, service has no port (client adapter)
        return self.service_cfg.get("default_port", 0)

    def _resolve_host(self) -> str:
        """Host priority: data/plugins/{id}/config.json → plugin.json service.host → 0.0.0.0"""
        config_path = syscfg.workspace_data_dir("plugins", self.plugin_id, "config.json")
        if os.path.isfile(config_path):
            try:
                cfg = _read_json(config_path)
                if "host" in cfg:
                    return str(cfg["host"])
            except Exception:
                pass
        return self.service_cfg.get("host", "0.0.0.0")

    def _resolve_auto_start(self) -> bool:
        """Auto-start priority: system_config services.X.enabled > plugin.json service.auto_start > True"""
        return syscfg.is_service_enabled(self.plugin_id)

    def _install_dependencies(self) -> bool:
        """Install pip dependencies declared in plugin.json before launching the service.

        Reads plugin.json fresh each time (not cached), so updated deps
        take effect on next start without requiring a Launcher restart.

        Returns True if all declared deps are importable in the plugin Python
        (either already installed or just installed). Returns False if pip
        install failed or deps are still missing — the caller should NOT
        spawn the service process in that case (it would crash with
        ModuleNotFoundError on import).
        """
        # Read fresh from plugin.json
        plugin_json_path = os.path.join(self.plugin_dir, "plugin.json")
        pip_deps = []
        if os.path.isfile(plugin_json_path):
            try:
                with open(plugin_json_path, encoding="utf-8") as f:
                    meta = json.load(f)
                pip_deps = meta.get("dependencies", {}).get("pip", [])
            except Exception:
                pass
        if not pip_deps:
            return True

        pkg_import_map = {
            "beautifulsoup4": "bs4",
            "opencv-python": "cv2",
            "PyMuPDF": "fitz",
            "python-dotenv": "dotenv",
            "scikit-learn": "sklearn",
            "flask-cors": "flask_cors",
            "lark-oapi": "lark_oapi",
            "playwright-stealth": "playwright_stealth",
        }

        def _check_all() -> list[str]:
            """Return list of deps still missing (not importable in plugin Python)."""
            still_missing = []
            for dep in pip_deps:
                pkg = dep.split("[")[0].split("==")[0].split(">=")[0].split("<=")[0].strip()
                import_name = pkg_import_map.get(pkg, pkg.replace("-", "_"))
                if not _plugin_python_has_module(import_name):
                    still_missing.append(dep)
            return still_missing

        try:
            missing = _check_all()
            if missing:
                _log.info(f"[Launcher] Installing dependencies for {self.plugin_id}: {missing}")
                _ensure_pip_and_install(missing, label=self.plugin_id)
                # Verify: pip may have failed silently or partially installed
                still_missing = _check_all()
                if still_missing:
                    _log.error(
                        f"[Launcher] {self.plugin_id}: dependencies still missing after install: {still_missing}"
                    )
                    self._circuit_last_failure_reason = f"Dependencies not installed: {still_missing}"
                    return False
            return True
        except Exception as e:
            _log.warning(f"[Launcher] Warning: Failed to install dependencies for {self.plugin_id}: {e}")
            self._circuit_last_failure_reason = f"pip install exception: {e}"
            return False

    def start(self) -> bool:
        """Start the plugin service child process.

        Serialized by ``_start_lock`` to prevent the TOCTOU race where two
        threads both pass the "already running?" check and spawn duplicate
        child processes on the same port (observed in beta.2: two websearch
        processes started 2s apart, second one EADDRINUSE → crash loop).
        """
        with self._start_lock:
            return self._start_impl()

    def _start_impl(self) -> bool:
        """Actual start logic — caller holds ``_start_lock``."""
        if self.process and self.process.poll() is None:
            _log.warning(f"[Launcher] Plugin service {self.plugin_id} already running (PID: {self.process.pid})")
            return False

        # ── Port pre-check: detect EADDRINUSE before spawning the child ──
        if self.port > 0 and is_port_in_use(self.port):
            _log.warning(f"[Launcher] Port {self.port} already in use for {self.plugin_id}, attempting to free it...")
            _kill_port_owner(self.port)
            time.sleep(1)
            if is_port_in_use(self.port):
                _log.error(
                    f"[Launcher] Port {self.port} still in use after kill for {self.plugin_id}. Circuit breaker: permanent failure."
                )
                self._circuit_permanent = True
                self._circuit_last_failure_reason = f"Port {self.port} in use and could not be freed"
                return False

        cmd_str = self.service_cfg.get("cmd", "")
        entry = self.service_cfg.get("entry", "")

        if cmd_str:
            # Shell command mode: supports npx / node / any executable command
            popen_args = cmd_str
            popen_kwargs = {"shell": True}
        elif entry:
            abs_entry = os.path.join(self.plugin_dir, entry)
            if not os.path.isfile(abs_entry):
                _log.info(f"[Launcher] Plugin service entry not found: {abs_entry}")
                return False
            popen_args = [_plugin_python_executable(), abs_entry]
            popen_kwargs = {}
        else:
            _log.info(f"[Launcher] Plugin service {self.plugin_id}: neither 'cmd' nor 'entry' defined")
            return False

        # Wait for the background _install_builtin_plugin_deps thread to finish
        # before doing per-service dep check. Without this, a user clicking
        # "Start" in the Service Manager UI right after launcher boot would
        # spawn the service while pip is still bootstrapping in the Agent
        # Python embed (get-pip.py + ~13 deps = 2-3 min on cold cache), and
        # the service crashes with ModuleNotFoundError on import.
        if not _plugin_deps_ready.is_set():
            _log.info(f"[Launcher] {self.plugin_id}: waiting for background plugin dependency install to finish...")
            _plugin_deps_ready.wait(timeout=300)  # 5 min max
            if not _plugin_deps_ready.is_set():
                _log.error(
                    f"[Launcher] {self.plugin_id}: background dependency install did not finish in 300s, aborting start"
                )
                self._circuit_last_failure_reason = "Background dep install timeout (300s)"
                return False

        # Install pip dependencies before launching (safety net for hot-installed plugins).
        # Returns False if deps are still missing after install attempt — do NOT
        # spawn the service in that case (it would crash immediately with
        # ModuleNotFoundError and trigger the circuit breaker restart loop).
        if not self._install_dependencies():
            _log.error(
                f"[Launcher] {self.plugin_id}: dependencies not available, refusing to start "
                "(would crash with ModuleNotFoundError). Check Agent Python pip install logs."
            )
            self._circuit_trip("dependencies not installed", permanent=False)
            return False

        child_env = _build_child_process_env(
            {
                "OPENSQUAD_WORKSPACE": syscfg.get_workspace(),
                "PORT": str(self.port),
                **self.service_cfg.get("env", {}),
            }
        )
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

        # Open log file for persistence
        self._open_log_file()

        self.process = subprocess.Popen(
            popen_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=PROJECT_ROOT,
            env=child_env,
            bufsize=1,
            creationflags=creationflags,
            **popen_kwargs,
        )
        self.should_run = True
        self.restart_count = 0
        self.started_at = datetime.now().isoformat()
        _write_runtime_registry(
            "plugin",
            self.plugin_id,
            {
                "plugin_id": self.plugin_id,
                "pid": self.process.pid,
                "port": self.port,
                "plugin_dir": self.plugin_dir,
                "started_at": self.started_at,
            },
        )
        _log.info(f"[Launcher] Started plugin service {self.plugin_id} on port {self.port} (PID: {self.process.pid})")
        self._log_thread = threading.Thread(target=self._forward_logs, daemon=True, name=f"log-svc-{self.plugin_id}")
        self._log_thread.start()

        # Start health check monitor (only for services that have a port)
        if self.port > 0:
            self._start_health_monitor()

        return True

    def stop(self) -> bool:
        """Stop the plugin service child process (safe: target PID tree only)."""
        self.should_run = False
        self._stop_health.set()
        if self._health_thread and self._health_thread.is_alive():
            self._health_thread.join(timeout=2)
            self._health_thread = None
        if self.process and self.process.poll() is None:
            pid = self.process.pid
            _log.info(f"[Launcher] Stopping plugin service {self.plugin_id} (PID: {pid})...")
            _terminate_pid_tree(pid)
            try:
                self.process.wait(timeout=8)
            except Exception:
                with contextlib.suppress(Exception):
                    self.process.kill()
                self.process.wait()
            # Safety net: kill the port owner in case the process escaped the tree
            if self.port:
                _kill_port_owner(self.port)
            _remove_runtime_registry("plugin", self.plugin_id)
            self._close_log_file()
            _log.info(f"[Launcher] Plugin service {self.plugin_id} stopped.")
            return True
        _remove_runtime_registry("plugin", self.plugin_id)
        self._close_log_file()
        return False

    def is_alive(self) -> bool:
        """Check whether the process is alive"""
        return self.process is not None and self.process.poll() is None

    # ── Circuit breaker helpers ──

    def _circuit_trip(self, reason: str, permanent: bool = False):
        """Open the circuit breaker — stop all restart attempts."""
        self._circuit_state = "open"
        self._circuit_fail_count += 1
        self._circuit_last_failure_time = time.time()
        self._circuit_last_failure_reason = reason
        self._circuit_permanent = permanent
        self._circuit_half_open_retries = 0
        if permanent:
            self._circuit_open_until = None  # Never auto-recover
            _log.error(f"[Launcher] Circuit OPEN (permanent) for {self.plugin_id}: {reason}")
        else:
            cooldown = CIRCUIT_BREAKER_COOLDOWN * min(self._circuit_fail_count, 5)
            self._circuit_open_until = time.time() + cooldown
            _log.warning(f"[Launcher] Circuit OPEN for {self.plugin_id} ({cooldown}s): {reason}")

    def _circuit_allow_retry(self) -> bool:
        """Check whether the circuit allows a retry attempt."""
        if self._circuit_permanent:
            return False
        if self._circuit_state == "closed":
            return True
        if self._circuit_state == "open":
            if self._circuit_open_until and time.time() >= self._circuit_open_until:
                self._circuit_state = "half-open"
                _log.info(f"[Launcher] Circuit HALF-OPEN for {self.plugin_id}, allowing trial retry")
                return True
            remaining = 0
            if self._circuit_open_until:
                remaining = int(self._circuit_open_until - time.time())
            _log.info(f"[Launcher] Circuit OPEN for {self.plugin_id}, skipping restart ({remaining}s remaining)")
            return False
        if self._circuit_state == "half-open":
            if self._circuit_half_open_retries >= CIRCUIT_BREAKER_HALF_OPEN_MAX:
                _log.warning(f"[Launcher] Circuit half-open retries exhausted for {self.plugin_id}, reopening")
                self._circuit_state = "open"
                self._circuit_open_until = time.time() + CIRCUIT_BREAKER_COOLDOWN
                return False
            self._circuit_half_open_retries += 1
            return True
        return False

    def _circuit_report_success(self):
        """Report a successful start — reset circuit breaker."""
        self._circuit_state = "closed"
        self._circuit_fail_count = 0
        self._circuit_half_open_retries = 0
        self._circuit_open_until = None
        self._circuit_permanent = False
        self._circuit_last_failure_reason = ""
        self._circuit_last_failure_time = None
        _log.info(f"[Launcher] Circuit CLOSED for {self.plugin_id}: healthy")

    def _circuit_check_permanent_failure(self, error_text: str) -> bool:
        """Check if an error indicates a permanent failure."""
        return any(signal.lower() in error_text.lower() for signal in PERMANENT_FAILURE_SIGNALS)

    def try_restart(self) -> bool:
        """Attempt to restart (with circuit breaker)."""
        if not self.should_run:
            return False
        # Check circuit breaker
        if not self._circuit_allow_retry():
            return False
        # Check max retries
        if self.restart_count >= MAX_RESTART_ATTEMPTS:
            _log.info(
                f"[Launcher] Plugin service {self.plugin_id} exceeded max restarts ({MAX_RESTART_ATTEMPTS}), giving up."
            )
            return False
        self.restart_count += 1
        _log.info(
            f"[Launcher] Restarting plugin service {self.plugin_id} (attempt {self.restart_count}/{MAX_RESTART_ATTEMPTS})..."
        )
        time.sleep(RESTART_COOLDOWN)
        success = self.start()
        if not success:
            self._circuit_trip("start() returned False")
        else:
            self._circuit_report_success()
        return success

    def get_status(self) -> dict:
        """Return enriched process status information."""
        alive = self.is_alive()
        uptime_seconds = None
        if alive and self.started_at:
            try:
                started = datetime.fromisoformat(self.started_at)
                uptime_seconds = (datetime.now() - started).total_seconds()
            except Exception:
                pass

        status = {
            "plugin_id": self.plugin_id,
            "display_name": self.display_name or self.plugin_id,
            "plugin_type": self.plugin_type or "",
            "alive": alive,
            "pid": self.process.pid if alive else None,
            "port": self.port,
            "host": self._resolve_host(),
            "auto_start": self._resolve_auto_start(),
            "should_run": self.should_run,
            "restart_count": self.restart_count,
            "max_restarts": self._max_restarts,
            "started_at": self.started_at,
            "uptime_seconds": int(uptime_seconds) if uptime_seconds else None,
            "health_endpoint": self.health_endpoint,
            "health_ok": self._last_health_ok,
            "service_cfg": self.service_cfg,
            # Circuit breaker state
            "circuit_state": self._circuit_state,
            "circuit_fail_count": self._circuit_fail_count,
            "circuit_permanent": self._circuit_permanent,
            "circuit_last_failure_reason": self._circuit_last_failure_reason,
        }
        # ── Plugin-supplied status (P1.4) ──
        try:
            status_path = syscfg.workspace_data_dir("plugins", self.plugin_id, "status.json")
            if os.path.isfile(status_path):
                with open(status_path, encoding="utf-8") as f:
                    extra = json.load(f)
                if isinstance(extra, dict):
                    status["plugin_status"] = extra
        except Exception:
            pass
        return status

    def get_logs(self, lines: int = 200) -> list[str]:
        """Return the last N lines of logs"""
        buf = list(self.log_buffer)
        return buf[-lines:]

    def _forward_logs(self):
        """Forward child process output to console + store in log buffer + persist to file"""
        prefix = f"[svc:{self.plugin_id}]"
        try:
            for line in self.process.stdout:
                line = line.rstrip("\n\r")
                if line:
                    ts = datetime.now().strftime("%H:%M:%S")
                    log_line = f"[{ts}] {line}"
                    self.log_buffer.append(log_line)
                    _log.info(f"{prefix} {line}")
                    # Persist to log file
                    if self._log_file_handle:
                        try:
                            self._log_file_handle.write(log_line + "\n")
                            self._log_file_handle.flush()
                        except Exception:
                            pass
        except (ValueError, OSError):
            pass

    # ── Health check ──

    def _check_health(self) -> bool:
        """Check whether the service is healthy via its /health endpoint."""
        try:
            import urllib.request

            url = f"http://127.0.0.1:{self.port}{self.health_endpoint}"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _start_health_monitor(self):
        """Start background health-check thread."""
        self._stop_health.clear()
        if self._health_thread and self._health_thread.is_alive():
            return
        self._health_thread = threading.Thread(
            target=self._health_monitor_loop,
            daemon=True,
            name=f"health-{self.plugin_id}",
        )
        self._health_thread.start()

    def _health_monitor_loop(self):
        """Periodically check process liveness + health endpoint.
        Restarts only when the process dies unexpectedly.
        Health endpoint status is for UI reporting only.
        Circuit breaker resets when the process runs stably."""
        # Initial delay to let the service start up
        self._stop_health.wait(5)
        _stable_since = time.time()
        while not self._stop_health.is_set():
            # Check process liveness
            alive = self.is_alive()
            if not alive and self.should_run:
                _log.info(f"[Launcher] Plugin service {self.plugin_id} process died, attempting restart...")
                self._attempt_auto_restart()
                break

            if alive:
                # Check health endpoint (for UI status only, not restart trigger)
                # Skip for client adapters without a port
                if self.port > 0:
                    self._last_health_ok = self._check_health()
                else:
                    self._last_health_ok = None  # N/A for client adapters

                # Circuit breaker auto-reset: if running stably for CIRCUIT_BREAKER_RESET_SECONDS
                if self._circuit_state != "closed" and (time.time() - _stable_since) >= CIRCUIT_BREAKER_RESET_SECONDS:
                    _log.info(
                        f"[Launcher] Plugin service {self.plugin_id} stable for {CIRCUIT_BREAKER_RESET_SECONDS}s, resetting circuit breaker"
                    )
                    self._circuit_report_success()
                    self.restart_count = 0
                # Update stable timer only when the process first comes up or was previously dead
                if not getattr(self, "_circuit_was_alive", False):
                    _stable_since = time.time()
                self._circuit_was_alive = True
            else:
                self._circuit_was_alive = False

            self._last_health_time = time.time()
            self._stop_health.wait(self.health_check_interval)

    def _attempt_auto_restart(self):
        """Attempt auto-restart with exponential backoff and circuit breaker."""
        if not self.should_run:
            return
        # Check circuit breaker before attempting
        if not self._circuit_allow_retry():
            return
        if self.restart_count >= self._max_restarts:
            _log.info(
                f"[Launcher] Plugin service {self.plugin_id} exceeded max restarts ({self._max_restarts}), giving up."
            )
            self.should_run = False
            self._circuit_trip(f"exceeded {self._max_restarts} restarts", permanent=True)
            return
        self.restart_count += 1
        idx = min(self.restart_count - 1, len(self._restart_backoff) - 1)
        delay = self._restart_backoff[idx]
        _log.info(
            f"[Launcher] Restarting plugin service {self.plugin_id} (attempt {self.restart_count}/{self._max_restarts}) in {delay}s..."
        )
        time.sleep(delay)
        # Stop existing process
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                with contextlib.suppress(Exception):
                    self.process.kill()
        self._close_log_file()
        # Retry
        success = self.start()
        if not success:
            # Check if the failure is permanent (port conflict, missing file, etc.)
            if self._circuit_permanent:
                _log.error(f"[Launcher] Permanent failure for {self.plugin_id}, giving up.")
                self.should_run = False
            else:
                self._circuit_trip("auto-restart start() failed")
        else:
            self._circuit_report_success()

    # ── Log file persistence ──

    def _open_log_file(self):
        """Open log file for persistent storage."""
        try:
            log_dir = syscfg.workspace_data_dir("logs")
            os.makedirs(log_dir, exist_ok=True)
            self._log_file_path = os.path.join(log_dir, f"{self.plugin_id}_service.log")
            self._log_file_handle = open(self._log_file_path, "a", encoding="utf-8")
        except Exception as e:
            _log.warning(f"[Launcher] Warning: Could not open log file for {self.plugin_id}: {e}")

    def _close_log_file(self):
        """Close the log file handle."""
        if self._log_file_handle:
            with contextlib.suppress(Exception):
                self._log_file_handle.close()
            self._log_file_handle = None


def _resolve_discovery_port(info: dict) -> int:
    """Resolve port for a discovered but not-yet-registered service."""
    plugin_id = info.get("plugin_id", "")
    service_cfg = info.get("service_cfg", {})

    # 1. data/plugins/{id}/config.json → port
    config_path = syscfg.workspace_data_dir("plugins", plugin_id, "config.json")
    if os.path.isfile(config_path):
        try:
            cfg = _read_json(config_path)
            if "port" in cfg:
                return int(cfg["port"])
        except Exception:
            pass

    # 2. system_config.json ports.{port_key}
    port_key = service_cfg.get("port_key", "")
    if port_key:
        try:
            return syscfg.port(port_key)
        except Exception:
            pass

    # 3. plugin.json service.default_port — if unset, service has no port (client adapter)
    return service_cfg.get("default_port", 0)


def _resolve_uv_executable() -> str | None:
    """Detect the ``uv`` binary on PATH.

    ``uv`` is a Rust-based pip replacement that is 10-100x faster and,
    crucially, does NOT require pip to be bootstrapped in the target
    Python — ``uv pip install --python <target>`` writes directly into
    the target's site-packages. This sidesteps the entire ensurepip /
    get-pip.py fallback chain that embed Python forces us into.
    """
    return shutil.which("uv")


def _ensure_pip_and_install(packages: list, label: str = "") -> bool:
    """Install pip packages, bootstrapping pip itself if needed. Returns True on success.

    Targets the *plugin service Python* (``_plugin_python_executable()``), NOT
    ``sys.executable``. In a PyInstaller bundle ``sys.executable`` is the frozen
    ``run.exe`` and pip would install into the read-only ``_internal/`` tree
    (or silently no-op because importlib already sees the bundled copies).
    Plugin services are spawned with the Agent Python runtime, so dependencies
    must land in *that* interpreter's site-packages.
    """
    if not packages:
        return True

    label_prefix = f"[{label}] " if label else ""
    target_python = _plugin_python_executable()

    # If the target Python differs from sys.executable (frozen-bundle mode),
    # log it so the operator can see where deps are going.
    if os.path.normcase(target_python) != os.path.normcase(sys.executable):
        _log.info(f"[Launcher] {label_prefix}Installing to plugin Python: {target_python}")

    # Build the clean env once — used by both uv and pip paths below.
    # Sanitizes PYTHONHOME/PYTHONPATH so the launcher's frozen-bundle env
    # does not leak into the Agent Python embed.
    clean_env = _build_child_process_env()

    # ── Prefer uv when available ──────────────────────────────────────────
    # uv doesn't need pip to be bootstrapped in the target Python, so it
    # skips the entire ensurepip / get-pip.py fallback chain (the source
    # of beta.2's "circuit breaker permanently open" bug on embed Python).
    # If uv fails for any reason, fall through to the pip path below.
    uv_exe = _resolve_uv_executable()
    if uv_exe:
        _log.info(f"[Launcher] {label_prefix}Using uv to install {len(packages)} package(s) into {target_python}")
        try:
            r = subprocess.run(
                [uv_exe, "pip", "install", "--python", target_python, *packages],
                capture_output=True,
                check=False,
                timeout=180,
                env=clean_env,
            )
            if r.returncode == 0:
                _log.info(f"[Launcher] {label_prefix}uv install succeeded")
                return True
            stderr = r.stderr.decode(errors="replace").strip() if r.stderr else ""
            _log.warning(
                f"[Launcher] {label_prefix}uv pip install failed (exit {r.returncode}): {stderr} — falling back to pip"
            )
        except subprocess.TimeoutExpired:
            _log.warning(f"[Launcher] {label_prefix}uv pip install timed out (180s) — falling back to pip")
        except Exception as e:
            _log.warning(f"[Launcher] {label_prefix}uv pip install exception: {e} — falling back to pip")
        # Fall through to pip path

    # Check if pip is available; if not, bootstrap it.
    # Use _build_child_process_env() (same as PluginServiceProcess.start())
    # to sanitize PYTHONHOME/PYTHONPATH — without this the launcher's
    # frozen-bundle env can leak into the Agent Python embed and cause
    # pip detection to fail even though pip is installed.
    pip_available = False
    try:
        r = subprocess.run(
            [target_python, "-m", "pip", "--version"],
            capture_output=True,
            check=False,
            timeout=15,
            env=clean_env,
        )
        pip_available = r.returncode == 0
        if not pip_available:
            stderr = r.stderr.decode(errors="replace")[:200] if r.stderr else ""
            _log.info(f"[Launcher] {label_prefix}pip --version exit={r.returncode}, stderr={stderr!r}")
    except subprocess.TimeoutExpired:
        _log.warning(f"[Launcher] {label_prefix}pip --version timed out (15s)")
        pip_available = False
    except Exception as e:
        _log.warning(f"[Launcher] {label_prefix}pip --version exception: {e}")
        pip_available = False

    if not pip_available:
        _log.info(f"[Launcher] {label_prefix}pip not found, bootstrapping via ensurepip...")
        try:
            # ensurepip on embed Python (no ensurepip module) hangs — use
            # short timeout so we fail fast and fall back to get-pip.py.
            r = subprocess.run(
                [target_python, "-m", "ensurepip", "--default-pip"],
                capture_output=True,
                check=False,
                timeout=15,
                env=clean_env,
            )
            if r.returncode != 0:
                _log.info(
                    f"[Launcher] {label_prefix}ensurepip failed (exit {r.returncode}), trying get-pip.py fallback..."
                )
                # Fallback: download and run get-pip.py
                import tempfile
                import urllib.request

                get_pip_url = "https://bootstrap.pypa.io/get-pip.py"
                with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as tmp:
                    tmp_path = tmp.name
                try:
                    urllib.request.urlretrieve(get_pip_url, tmp_path)
                    r2 = subprocess.run(
                        [target_python, tmp_path],
                        capture_output=True,
                        check=False,
                        timeout=120,
                        env=clean_env,
                    )
                    if r2.returncode != 0:
                        stderr2 = r2.stderr.decode(errors="replace")[:200] if r2.stderr else ""
                        _log.info(f"[Launcher] {label_prefix}get-pip.py also failed (exit {r2.returncode}): {stderr2}")
                        return False
                finally:
                    with contextlib.suppress(OSError):
                        os.unlink(tmp_path)
            # Verify pip works now
            r3 = subprocess.run(
                [target_python, "-m", "pip", "--version"],
                capture_output=True,
                check=False,
                timeout=15,
                env=clean_env,
            )
            if r3.returncode != 0:
                _log.info(f"[Launcher] {label_prefix}pip still not available after bootstrapping")
                return False
            _log.info(f"[Launcher] {label_prefix}pip bootstrapped successfully")
        except subprocess.TimeoutExpired:
            _log.warning(
                f"[Launcher] {label_prefix}ensurepip timed out (15s) — embed Python likely has no ensurepip module"
            )
            # Last resort: try get-pip.py directly
            try:
                import tempfile
                import urllib.request

                get_pip_url = "https://bootstrap.pypa.io/get-pip.py"
                with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as tmp:
                    tmp_path = tmp.name
                try:
                    urllib.request.urlretrieve(get_pip_url, tmp_path)
                    r2 = subprocess.run(
                        [target_python, tmp_path],
                        capture_output=True,
                        check=False,
                        timeout=120,
                        env=clean_env,
                    )
                    if r2.returncode != 0:
                        _log.info(
                            f"[Launcher] {label_prefix}get-pip.py failed after ensurepip timeout (exit {r2.returncode})"
                        )
                        return False
                finally:
                    with contextlib.suppress(OSError):
                        os.unlink(tmp_path)
            except Exception as e2:
                _log.info(f"[Launcher] {label_prefix}get-pip.py fallback also failed: {e2}")
                return False
        except Exception as e:
            _log.info(f"[Launcher] {label_prefix}Failed to bootstrap pip: {e}")
            return False

    try:
        r = subprocess.run(
            [target_python, "-m", "pip", "install", "--quiet", *packages],
            capture_output=True,
            check=False,
            timeout=300,
            env=clean_env,
        )
        if r.returncode != 0:
            stderr = r.stderr.decode(errors="replace").strip() if r.stderr else ""
            _log.info(f"[Launcher] {label_prefix}pip install failed (exit {r.returncode}): {stderr}")
            return False
        return True
    except Exception as e:
        _log.info(f"[Launcher] {label_prefix}pip install failed: {e}")
        return False


def _plugin_python_has_module(import_name: str) -> bool:
    """Check if a module is importable in the *plugin service Python*.

    Uses a subprocess instead of in-process ``importlib.import_module`` because
    the launcher process (frozen ``run.exe``) has its own bundled copy of
    fastapi/uvicorn/click/etc. in ``_internal/``; importing them in-process
    would always succeed and hide the fact that the Agent Python runtime is
    missing them, leading to ``ModuleNotFoundError`` when the service actually
    starts.

    Uses ``_build_child_process_env()`` for the subprocess env (same as
    ``PluginServiceProcess.start()``) to ensure PYTHONHOME/PYTHONPATH are
    sanitized — without this the launcher's frozen-bundle env can leak into
    the Agent Python embed and cause false negatives (module appears missing
    even though it's installed in site-packages).
    """
    target_python = _plugin_python_executable()
    try:
        r = subprocess.run(
            [target_python, "-c", f"import {import_name}"],
            capture_output=True,
            check=False,
            timeout=15,
            env=_build_child_process_env(),
        )
        if r.returncode != 0:
            stderr = r.stderr.decode(errors="replace")[:200] if r.stderr else ""
            _log.debug(
                f"[Launcher] _plugin_python_has_module('{import_name}') -> False "
                f"(exit={r.returncode}, stderr={stderr!r})"
            )
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        _log.warning(f"[Launcher] _plugin_python_has_module('{import_name}') -> timeout (15s)")
        return False
    except Exception as e:
        _log.warning(f"[Launcher] _plugin_python_has_module('{import_name}') -> exception: {e}")
        return False


def _install_builtin_plugin_deps(svc_infos: list[dict]):
    """Install all built-in plugin pip dependencies in one batch at startup.

    Scans all discovered plugin services, collects their dependencies.pip entries,
    deduplicates, checks which are already installed, and installs missing ones.

    This is the primary install path for built-in plugins.
    Per-service _install_dependencies() in PluginServiceProcess serves as a
    safety net for hot-installed plugins (installed while launcher is running).

    Signals ``_plugin_deps_ready`` when done (success or failure) so that
    ``PluginServiceProcess.start()`` can stop waiting and proceed.
    """
    try:
        all_deps: set = set()
        for info in svc_infos:
            deps = info.get("dependencies", {}).get("pip", [])
            for d in deps:
                all_deps.add(d)

        if not all_deps:
            _plugin_deps_ready.set()
            return

        # Heavy packages that pull in huge transitive deps (torch ~2GB for
        # whisper, etc.). These are skipped in the batch install so they don't
        # block ALL service starts — the per-service _install_dependencies()
        # safety net handles them when the specific service is actually started.
        # Without this, `pip install whisper` (which pulls torch) can take 10+
        # minutes and every PluginServiceProcess.start() waits on
        # _plugin_deps_ready, making websearch/external_api unstartable.
        _HEAVY_PACKAGES = {"whisper", "torch", "torchvision", "torchaudio"}

        # NOTE: Dependency presence is checked via ``_plugin_python_has_module()``
        # (subprocess against the Agent Python), NOT in-process ``importlib``.
        # The launcher process (frozen ``run.exe``) bundles its own copies of
        # fastapi/uvicorn/click/etc. in ``_internal/``; importing them in-process
        # would always succeed and hide missing deps in the Agent Python runtime,
        # causing ``ModuleNotFoundError`` when the service actually starts.
        pkg_import_map = {
            "beautifulsoup4": "bs4",
            "PyMuPDF": "fitz",
            "python-dotenv": "dotenv",
            "scikit-learn": "sklearn",
            "flask-cors": "flask_cors",
            "lark-oapi": "lark_oapi",
            "playwright-stealth": "playwright_stealth",
        }

        missing = []
        skipped_heavy = []
        for dep in sorted(all_deps):
            pkg = dep.split("[")[0].split("==")[0].split(">=")[0].split("<=")[0].strip()
            if pkg in _HEAVY_PACKAGES:
                skipped_heavy.append(dep)
                continue
            import_name = pkg_import_map.get(pkg, pkg.replace("-", "_"))
            if not _plugin_python_has_module(import_name):
                missing.append(dep)

        if skipped_heavy:
            _log.info(
                f"[Launcher] Skipping heavy packages in batch install (will install per-service on start): {skipped_heavy}"
            )

        if missing:
            _log.info(f"[Launcher] Installing built-in plugin dependencies ({len(missing)} package(s)): {missing}")
            if _ensure_pip_and_install(missing, label="builtin"):
                _log.info("[Launcher] All plugin dependencies installed.")
            else:
                _log.warning("[Launcher] Warning: Batch dependency install failed.")
        else:
            _log.info(f"[Launcher] All built-in plugin dependencies already installed ({len(all_deps)} packages).")
    finally:
        # Always signal — even on failure — so PluginServiceProcess.start()
        # doesn't block forever. Per-service _install_dependencies() will do
        # a retry and report the actual missing deps.
        _plugin_deps_ready.set()
