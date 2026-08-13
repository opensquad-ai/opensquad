"""opensquad start — Start all OpenSquad services (gateway, registry, frontend, launcher)."""

import atexit
import contextlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

# Default Vite dev server port; can be overridden by system_config.json ports.frontend
_DEFAULT_VITE_PORT = 5173

# Shared shutdown state for signal / console-close / atexit / finally paths.
_SHUTDOWN_LOCK = threading.Lock()
_SHUTDOWN_DONE = False
_ACTIVE_PROCESSES: list[tuple[str, subprocess.Popen]] = []
_ACTIVE_PORTS: tuple[int, ...] = ()
_ACTIVE_JOB = None  # optional Windows kill-on-close job
_LAUNCHER_PORT: int | None = None
_WIN_CONSOLE_HANDLER = None  # keep alive for SetConsoleCtrlHandler


def _get_managed_ports(syscfg):
    """Return tuple of all OpenSquad-managed ports from configuration."""
    _vite_port = syscfg.port("frontend") if hasattr(syscfg, "port") else _DEFAULT_VITE_PORT
    return (
        syscfg.port("gateway"),
        syscfg.port("launcher"),
        syscfg.port("registry"),
        _vite_port,
        syscfg.port("external_adapter"),
    )


# Cache the resolved interpreter for the process lifetime (each probe is a
# subprocess round-trip; start_cmd calls _find_python several times).
_find_python_cache: str | None = None


def _find_python():
    """Find a usable Python interpreter (handles pip console_scripts .exe wrappers on Windows).

    Source/dev mode: ALWAYS return ``sys.executable`` — the interpreter that is
    running this very CLI. Never fall back to a PATH probe: on machines that
    have several Python installs (e.g. uv-tools opensquad + anaconda3 editable
    + Python313) ``shutil.which("python")`` can resolve to a *different*
    environment, silently spawning a second, parallel service stack with
    duplicate gateways/launchers/agents fighting over 9555/9600/9720/8001 and
    the UI stuck in "重连中".
    """
    global _find_python_cache
    if _find_python_cache:
        return _find_python_cache
    exe = sys.executable
    if exe and os.path.isfile(exe):
        _find_python_cache = exe
        return exe
    # sys.executable unavailable (embedded/pip stub). A bare command is the only
    # option, but it must never pick a *different* install on purpose.
    _find_python_cache = "python"
    return "python"


def _find_npm():
    """Find npm executable."""
    import shutil

    path = shutil.which("npm")
    return path or "npm"


def _kill_tree(pid: int) -> None:
    """Kill an entire process tree (cross-platform)."""
    if not pid or pid <= 0:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                check=False,
                timeout=15,
            )
        else:
            try:
                os.killpg(pid, signal.SIGTERM)
            except Exception:
                os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


class _WindowsKillOnCloseJob:
    """Bind child processes so they die when this job handle is closed.

    Closing the console / killing the supervisor closes the job handle, which
    triggers JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE and reaps orphans that would
    otherwise keep gateway/launcher ports alive.
    """

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JobObjectExtendedLimitInformation = 9

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._kernel32 = ctypes.windll.kernel32
        self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self._hjob = self._kernel32.CreateJobObjectW(None, None)
        if not self._hjob:
            raise OSError(f"CreateJobObjectW failed: {ctypes.get_last_error()}")

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):  # noqa: N801
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):  # noqa: N801
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):  # noqa: N801
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = self._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._kernel32.SetInformationJobObject(
            self._hjob,
            self._JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            err = ctypes.get_last_error()
            self.close()
            raise OSError(f"SetInformationJobObject failed: {err}")

    def add(self, proc: subprocess.Popen) -> bool:
        handle = getattr(proc, "_handle", None)
        if not handle:
            return False
        # Nested-job / already-assigned cases are non-fatal; explicit
        # shutdown still cleans via taskkill / ports.
        return bool(self._kernel32.AssignProcessToJobObject(self._hjob, int(handle)))

    def close(self) -> None:
        if getattr(self, "_hjob", None):
            with contextlib.suppress(Exception):
                self._kernel32.CloseHandle(self._hjob)
            self._hjob = None


def _try_graceful_launcher_shutdown(launcher_port: int | None, timeout_s: float = 1.5) -> None:
    """Best-effort POST /api/shutdown so agents/plugins exit before force-kill."""
    if not launcher_port or launcher_port <= 0:
        return
    try:
        import socket
        import urllib.request

        with socket.create_connection(("127.0.0.1", int(launcher_port)), timeout=0.3):
            pass
        req = urllib.request.Request(
            f"http://127.0.0.1:{int(launcher_port)}/api/shutdown",
            method="POST",
            data=json.dumps({"timeout": 2}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=timeout_s)
    except Exception:
        pass


def _shutdown_supervised_services(
    processes: list[tuple[str, subprocess.Popen]] | None = None,
    ports: tuple[int, ...] | None = None,
    *,
    reason: str = "shutdown",
    graceful: bool = True,
) -> None:
    """Idempotent cleanup used by Ctrl+C, console close, atexit, and finally."""
    global _SHUTDOWN_DONE, _ACTIVE_JOB

    with _SHUTDOWN_LOCK:
        if _SHUTDOWN_DONE:
            return
        _SHUTDOWN_DONE = True
        procs = list(processes if processes is not None else _ACTIVE_PROCESSES)
        port_list = tuple(ports if ports is not None else _ACTIVE_PORTS)
        job = _ACTIVE_JOB
        launcher_port = _LAUNCHER_PORT

    print(f"\n[start] Shutting down all services ({reason})...")
    if graceful:
        _try_graceful_launcher_shutdown(launcher_port)

    for name, p in procs:
        pid = getattr(p, "pid", None)
        if pid:
            _kill_tree(pid)
            print(f"[start] {name} (PID {pid}) killed.")

    # Closing the Windows job also kills any still-assigned descendants
    # (uvicorn reload workers, npm→node, launcher agents, etc.).
    if job is not None:
        with contextlib.suppress(Exception):
            job.close()
        with _SHUTDOWN_LOCK:
            if _ACTIVE_JOB is job:
                _ACTIVE_JOB = None

    if port_list:
        _kill_port_owners(*port_list)
        print(f"[start] Cleared managed ports: {', '.join(str(p) for p in port_list)}")


def _install_windows_console_close_handler() -> None:
    """Ensure closing the CMD window still runs process/port cleanup."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        HandlerRoutine = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
        CTRL_C_EVENT = 0
        CTRL_BREAK_EVENT = 1
        CTRL_CLOSE_EVENT = 2
        CTRL_LOGOFF_EVENT = 5
        CTRL_SHUTDOWN_EVENT = 6

        @HandlerRoutine
        def _handler(ctrl_type):
            # Console-close / logoff / shutdown: cleanup must finish quickly
            # (Windows gives ~5s then force-kills the process).
            if ctrl_type in (
                CTRL_C_EVENT,
                CTRL_BREAK_EVENT,
                CTRL_CLOSE_EVENT,
                CTRL_LOGOFF_EVENT,
                CTRL_SHUTDOWN_EVENT,
            ):
                reason = {
                    CTRL_C_EVENT: "Ctrl+C",
                    CTRL_BREAK_EVENT: "Ctrl+Break",
                    CTRL_CLOSE_EVENT: "console close",
                    CTRL_LOGOFF_EVENT: "logoff",
                    CTRL_SHUTDOWN_EVENT: "shutdown",
                }.get(ctrl_type, f"console ctrl {ctrl_type}")
                # Skip HTTP graceful path on hard close — not enough time.
                _shutdown_supervised_services(
                    reason=reason,
                    graceful=ctrl_type in (CTRL_C_EVENT, CTRL_BREAK_EVENT),
                )
                # Console handlers run on a helper thread; leave the process
                # immediately so the monitor loop cannot keep services alive.
                os._exit(0)
            return False

        # Keep a module-level ref so the callback is not GC'd.
        global _WIN_CONSOLE_HANDLER
        _WIN_CONSOLE_HANDLER = _handler
        if not ctypes.windll.kernel32.SetConsoleCtrlHandler(_WIN_CONSOLE_HANDLER, True):
            print("[start] Warning: SetConsoleCtrlHandler failed; console-close cleanup may be incomplete")
    except Exception as exc:
        print(f"[start] Warning: console-close handler unavailable: {exc}")


def _kill_port_owners(*ports: int) -> None:
    """Force-kill any process listening on the given ports.

    Windows: parallel socket probe first — netstat -ano (0.2-1s) only runs
    when a managed port actually has a listener, so a cold start skips it.
    """
    ports = [p for p in ports if isinstance(p, int) and p > 0]
    if not ports:
        return

    # Fast path: parallel probe; nothing listening -> skip netstat entirely.

    active = False
    with ThreadPoolExecutor(max_workers=min(len(ports), 8)) as pool:
        for listening in pool.map(lambda p: _port_listening(p), ports):
            if listening:
                active = True
                break
    if not active:
        return

    if sys.platform == "win32":
        wanted = {str(p) for p in ports}
        pids: set[str] = set()
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                timeout=8,
            )
            for line in result.stdout.splitlines():
                if "LISTENING" not in line.upper():
                    continue
                parts = line.split()
                if len(parts) < 5:
                    continue
                local = parts[1]
                port_str = local.rsplit(":", 1)[-1]
                if port_str not in wanted:
                    continue
                pid = parts[-1]
                if pid.isdigit() and pid != "0":
                    pids.add(pid)
        except Exception:
            return
        for pid in pids:
            with contextlib.suppress(Exception):
                subprocess.run(
                    ["taskkill", "/F", "/PID", pid],
                    capture_output=True,
                    check=False,
                    timeout=10,
                )
        return

    # Unix: lsof / fuser
    for port in ports:
        try:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            for pid in result.stdout.strip().splitlines():
                if pid.strip() and pid.strip().isdigit():
                    subprocess.run(["kill", "-9", pid.strip()], capture_output=True, timeout=10)
        except FileNotFoundError:
            with contextlib.suppress(Exception):
                subprocess.run(
                    ["fuser", "-k", f"{port}/tcp"],
                    capture_output=True,
                    timeout=10,
                )
        except Exception:
            pass


def _port_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.35) -> bool:
    import socket

    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def _wait_ports_ready(
    check_ports: dict[str, int | None],
    *,
    max_wait: float = 30.0,
) -> None:
    """Poll ports in parallel with backoff (no fixed multi-second sleep)."""
    pending = {name: port for name, port in check_ports.items() if port is not None}
    if not pending:
        return
    print("[start] Waiting for services to bind ports...")
    deadline = time.perf_counter() + max_wait
    delay = 0.05
    while pending and time.perf_counter() < deadline:
        for name in list(pending):
            port = pending[name]
            if _port_listening(int(port)):
                print(f"  \u2705 {name}: port {port} ready")
                del pending[name]
        if pending:
            time.sleep(delay)
            delay = min(delay * 1.35, 0.4)
    for name, port in pending.items():
        print(f"  \u274c {name}: port {port} FAILED to start ({max_wait:.0f}s timeout)")


def _workspace_gateway_is_local(gateway_ip: str) -> bool:
    """True when the workspace system_config.json already binds hosts.gateway to gateway_ip."""
    try:
        last_ws_file = os.path.join(os.path.expanduser("~"), ".opensquad", "last_workspace.json")
        if not os.path.isfile(last_ws_file):
            return False
        with open(last_ws_file, encoding="utf-8") as f:
            ws_path = json.load(f).get("last_workspace", "")
        if not ws_path:
            return False
        cfg_path = os.path.join(ws_path, "system_config.json")
        if not os.path.isfile(cfg_path):
            return False
        with open(cfg_path, encoding="utf-8-sig") as f:
            cfg = json.load(f)
        return cfg.get("hosts", {}).get("gateway") == gateway_ip
    except Exception:
        return False


def _setup_local_mode(_root):
    """Apply local-mode config: hosts.gateway = 0.0.0.0 and create .env.local.

    Idempotent: skips config rewrites and the workspace-config subprocess when
    the target local-mode state already exists (saves 0.3-2s per CLI cold start).
    """
    src_dir = os.path.join(_root, "src")
    cfg_path = os.path.join(src_dir, "system_config.json")

    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8-sig") as f:
                cfg = json.load(f)
            if cfg.setdefault("hosts", {}).get("gateway") != "0.0.0.0":
                cfg["hosts"]["gateway"] = "0.0.0.0"
                with open(cfg_path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
                print("[start] Local mode: hosts.gateway = 0.0.0.0")
        except Exception as e:
            print(f"[start] Warning: Failed to update system_config.json: {e}")

    # Skip the update_workspace_config.py subprocess when the workspace config
    # already binds hosts.gateway to the requested address.
    if not _workspace_gateway_is_local("0.0.0.0"):
        with contextlib.suppress(Exception):
            subprocess.run(
                [_find_python(), os.path.join(_root, "scripts", "update_workspace_config.py"), "0.0.0.0"],
                cwd=_root,
                capture_output=True,
                timeout=10,
            )

    frontend_dir = os.path.join(_root, "src", "opensquad", "gateway", "nexuschat-pro")
    env_local = os.path.join(frontend_dir, ".env.local")
    try:
        # Read gateway port from workspace config — NOT hardcoded.
        # The workspace dir is resolved via bootstrap_workspace() elsewhere;
        # as a fallback read the src/system_config.json directly.
        gateway_port = None
        for search_dir in [_root, os.path.join(_root, "src")]:
            candidate = os.path.join(search_dir, "system_config.json")
            if os.path.isfile(candidate):
                try:
                    with open(candidate, encoding="utf-8") as scf:
                        sc = json.load(scf)
                    gateway_port = sc.get("ports", {}).get("gateway")
                    if gateway_port is not None:
                        break
                except Exception:
                    pass
        if gateway_port is None:
            # Try workspace last-workspace JSON (bootstrap_workspace uses this)
            last_ws_file = os.path.join(os.path.expanduser("~"), ".opensquad", "last_workspace.json")
            if os.path.isfile(last_ws_file):
                try:
                    with open(last_ws_file, encoding="utf-8") as lwf:
                        lw = json.load(lwf)
                    ws_path = lw.get("last_workspace")
                    if ws_path:
                        scfp = os.path.join(ws_path, "system_config.json")
                        if os.path.isfile(scfp):
                            with open(scfp, encoding="utf-8") as scf:
                                sc = json.load(scf)
                            gateway_port = sc.get("ports", {}).get("gateway")
                except Exception:
                    pass
        if gateway_port is None:
            gateway_port = 9555  # safe default
        desired_env = f"VITE_BACKEND_HOST=127.0.0.1\nVITE_BACKEND_PORT={gateway_port}\n"
        if os.path.isfile(env_local):
            with open(env_local, encoding="utf-8") as f:
                if f.read() == desired_env:
                    return
        with open(env_local, "w", encoding="utf-8") as f:
            f.write(desired_env)
        print(f"[start] Created .env.local: VITE_BACKEND_HOST=127.0.0.1, VITE_BACKEND_PORT={gateway_port}")
    except Exception as e:
        print(f"[start] Warning: Failed to create .env.local: {e}")


def _popen_opts(args):
    """Build subprocess.Popen kwargs based on verbose flag."""
    if getattr(args, "verbose", False):
        # Show all logs in the same console (inherit stdout/stderr)
        return {"stderr": None, "stdout": None}
    else:
        # Quiet mode: pipe stderr for crash diagnostics, silence stdout
        return {"stderr": subprocess.PIPE, "stdout": subprocess.DEVNULL}


def run_start(args):
    if getattr(args, "detach", False):
        from opensquad.cli.runtime_boot import ensure_services

        ok = ensure_services(quiet=False)
        if ok:
            print("[start] Daemon pre-warmed (optional).")
            print("[start] Daily use is still just:  opensquad code  |  opensquad web")
        sys.exit(0 if ok else 1)

    _t0 = time.perf_counter()
    _root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    python_exe = _find_python()
    popts = _popen_opts(args)

    from opensquad.system_config import syscfg
    from opensquad.workspace_utils import bootstrap_workspace

    # Vite dev server port — read from config (ports.frontend), fallback to 5173
    _VITE_DEV_PORT = syscfg.port("frontend") or _DEFAULT_VITE_PORT

    workspace = bootstrap_workspace()
    print(f"[start] Workspace: {workspace}")

    # CRITICAL: Tell syscfg and all subprocesses to use the workspace config,
    # not src/system_config.json. Without this, Web UI config changes
    # (feishu.enabled, external_api.enabled) are invisible to services.
    syscfg.set_workspace(workspace)
    os.environ["OPENSQUAD_WORKSPACE"] = workspace
    os.environ["OPENSQUAD_USER_DATA"] = workspace
    # Force UTF-8 for every child (gateway/registry/launcher/agents). On
    # Chinese-locale Windows the default GBK console codepage makes children
    # emit GBK bytes; readers that decode utf-8 strictly then crash with
    # UnicodeDecodeError (e.g. opensquad start's launcher stderr pipe), which
    # kills startup threads and can cascade into the whole stack being torn
    # down. PYTHONUTF8=1 inherited by all children fixes the encoding chain.
    os.environ["PYTHONUTF8"] = "1"
    # Also set PYTHONPATH for subprocesses
    python_path = os.path.join(_root, "src")
    if "PYTHONPATH" in os.environ:
        if python_path not in os.environ["PYTHONPATH"]:
            os.environ["PYTHONPATH"] = python_path + os.pathsep + os.environ["PYTHONPATH"]
    else:
        os.environ["PYTHONPATH"] = python_path

    _t_before_local = time.perf_counter()
    _setup_local_mode(_root)
    _t_after_local = time.perf_counter()
    print(f"[start.timing] _setup_local_mode: {(_t_after_local - _t_before_local) * 1000:.0f}ms")

    # 启动前先清理残留端口（防止上次未正常退出导致端口占用）
    _t_before_kill = time.perf_counter()
    _kill_port_owners(*_get_managed_ports(syscfg))
    _t_after_kill = time.perf_counter()
    print(f"[start.timing] _kill_port_owners: {(_t_after_kill - _t_before_kill) * 1000:.0f}ms")

    global _ACTIVE_PROCESSES, _ACTIVE_PORTS, _ACTIVE_JOB, _LAUNCHER_PORT, _SHUTDOWN_DONE
    _SHUTDOWN_DONE = False
    _ACTIVE_PROCESSES = []
    _ACTIVE_PORTS = _get_managed_ports(syscfg)
    _LAUNCHER_PORT = None
    _ACTIVE_JOB = None
    if sys.platform == "win32":
        try:
            _ACTIVE_JOB = _WindowsKillOnCloseJob()
            print("[start] Windows job object enabled (children die when this process exits)")
        except Exception as exc:
            print(f"[start] Warning: Windows job object unavailable: {exc}")

    def _track(name: str, proc: subprocess.Popen) -> None:
        _ACTIVE_PROCESSES.append((name, proc))
        if _ACTIVE_JOB is not None:
            if not _ACTIVE_JOB.add(proc):
                print(f"[start] Warning: could not bind {name} (PID {proc.pid}) to kill-on-close job")

    processes = _ACTIVE_PROCESSES

    gateway_port = args.port or syscfg.port("gateway")
    frontend_dir = os.path.join(_root, "src", "opensquad", "gateway", "nexuschat-pro")
    frontend_dist = os.path.join(frontend_dir, "dist")
    has_frontend_dist = os.path.isfile(os.path.join(frontend_dist, "index.html"))
    force_frontend = bool(getattr(args, "frontend", False))
    npm_exe = None
    if args.no_frontend:
        start_frontend = False
    elif force_frontend:
        start_frontend = True
    elif has_frontend_dist:
        # Prefer static SPA served by Gateway — avoids npm/Vite cold start.
        start_frontend = False
        print("[start] [3/4] Using built frontend dist via Gateway (skip Vite). Use --frontend to force Vite.")
    else:
        start_frontend = True

    # [1/4] Start gateway (FastAPI backend, default port 9555 per system_config)
    if not args.no_gateway:
        gateway_cwd = os.path.join(_root, "src", "opensquad", "gateway", "backend")
        gateway_script = os.path.join(gateway_cwd, "run.py")

        print(f"[start] [1/4] Starting Gateway Backend (port {gateway_port})...")
        # Pass VITE_DEV_PORT so the backend knows where Vite is for reverse proxying.
        # On Windows, httpx may pick up system proxy settings and fail to connect
        # to localhost — NO_PROXY ensures it connects directly.
        # On Windows, httpx (used by the OpenAI SDK) honors HTTP_PROXY /
        # HTTPS_PROXY env vars. LLM calls go to https://api.deepseek.com, so
        # they read HTTPS_PROXY; if it points at a dead local proxy (e.g.
        # 127.0.0.1:17897 when the proxy app is closed), every chat call fails
        # with APIConnectionError. Clear all proxy vars for the gateway and its
        # children so LLM + localhost connections go direct. (WebSocket
        # connections already bypass the proxy via proxy=None in sdk/bridge.)
        _gateway_env = {
            **os.environ,
            "VITE_DEV_PORT": str(_VITE_DEV_PORT),
            "NO_PROXY": "127.0.0.1,localhost",
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
            "ALL_PROXY": "",
        }
        p = subprocess.Popen(
            [python_exe, gateway_script],
            cwd=gateway_cwd,
            env=_gateway_env,
            **popts,
        )
        _track("gateway", p)
        print(f"[start] Gateway started (PID {p.pid})")

    # [2/4] Start plugin registry (port 9720)
    if not args.no_registry:
        registry_cwd = os.path.join(_root, "src", "opensquad", "gateway", "plugin_registry")
        registry_script = os.path.join(registry_cwd, "main.py")

        print(f"[start] [2/4] Starting Plugin Registry (port {syscfg.port('registry')})...")
        p = subprocess.Popen(
            [python_exe, registry_script],
            cwd=registry_cwd,
            **popts,
        )
        _track("registry", p)
        print(f"[start] Plugin Registry started (PID {p.pid})")

    # [3/4] Start frontend dev server (Vite) only when needed
    if start_frontend:
        npm_exe = _find_npm()

        if os.path.isfile(os.path.join(frontend_dir, "package.json")):
            print(f"[start] [3/4] Starting Frontend Dev Server (port {_VITE_DEV_PORT})...")
            try:
                p = subprocess.Popen([npm_exe, "run", "dev"], cwd=frontend_dir)
                _track("frontend", p)
                print(f"[start] Frontend started (PID {p.pid})")
            except FileNotFoundError:
                print("[start] Warning: npm not found. Install Node.js to run the frontend.")
                start_frontend = False
            except Exception as e:
                print(f"[start] Warning: Failed to start frontend: {e}")
                start_frontend = False
        else:
            print("[start] [3/4] Skipping Frontend (package.json not found)")
            start_frontend = False

    # [4/4] Start launcher (agent management, port 9600)
    launcher_port = (args.port + 1) if args.port else syscfg.port("launcher")
    _LAUNCHER_PORT = launcher_port
    if not args.no_launcher:
        launcher_cmd = [python_exe, os.path.join(_root, "src", "opensquad", "launcher_main.py")]
        launcher_cmd.extend(["--mgmt-port", str(launcher_port)])

        print(f"[start] [4/4] Starting Launcher (port {launcher_port})...")
        p = subprocess.Popen(
            launcher_cmd,
            cwd=_root,
            **popts,
        )
        _track("launcher", p)
        print(f"[start] Launcher started (PID {p.pid})")

    if not processes:
        print("[start] Nothing to start. Use selective flags to skip services.")
        return

    print(f"\n{'=' * 50}")
    print("  OpenSquad All-in-One (Local Mode)")
    print(f"{'=' * 50}")
    print(f"  Gateway Backend : http://127.0.0.1:{gateway_port}")
    print(f"  Plugin Registry : http://127.0.0.1:{syscfg.port('registry')}")
    if start_frontend:
        print(f"  Frontend Dev    : http://127.0.0.1:{_VITE_DEV_PORT}")
    elif has_frontend_dist and not args.no_frontend:
        print(f"  Frontend Static : http://127.0.0.1:{gateway_port}/  (dist)")
    else:
        print("  Frontend        : skipped")
    print(f"  Launcher        : http://127.0.0.1:{launcher_port}")
    print(f"{'=' * 50}")
    print(f"\n[start] {len(processes)} service(s) running. Press Ctrl+C to stop.\n")
    print("[start] Closing this window also stops services and frees ports.\n")

    # ── Health check: poll ports with backoff (no fixed multi-second sleep) ──
    _check_ports = {
        "gateway": gateway_port if not args.no_gateway else None,
        "registry": syscfg.port("registry") if not args.no_registry else None,
        "frontend": _VITE_DEV_PORT if start_frontend else None,
        "launcher": launcher_port if not args.no_launcher else None,
    }
    _wait_ports_ready(_check_ports, max_wait=30.0)

    # Watchdog after ports are up — avoids spurious recoveries during bind.
    if not args.no_watchdog:
        watchdog_script = os.path.join(_root, "scripts", "opensquad_watchdog.py")
        if not os.path.isfile(watchdog_script):
            print(f"[start] Warning: watchdog script not found at {watchdog_script}, skipping")
        else:
            print("[start] [5/5] Starting Health-Check Watchdog...")
            wd_cmd = [
                python_exe,
                watchdog_script,
                "--workspace",
                workspace,
                "--interval",
                "15",
            ]
            try:
                wd_p = subprocess.Popen(
                    wd_cmd,
                    cwd=_root,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                _track("watchdog", wd_p)
                print(f"[start] Watchdog started (PID {wd_p.pid})")
            except Exception as e:
                print(f"[start] Warning: Failed to start watchdog: {e}")

    # `opensquad --verbose` opens the browser once services are up, so it
    # behaves like `opensquad web` while still streaming live logs here.
    if getattr(args, "open_browser", False):
        import webbrowser as _webbrowser

        if start_frontend and _port_listening(_VITE_DEV_PORT):
            _web_url = f"http://127.0.0.1:{_VITE_DEV_PORT}"
        else:
            _web_url = f"http://127.0.0.1:{gateway_port}/"
        try:
            _webbrowser.open(_web_url)
            print(f"[start] Opened browser: {_web_url}")
        except Exception as _e:
            print(f"[start] Could not open browser: {_e}\n  Open manually: {_web_url}", file=sys.stderr)

    def _signal_handler(sig, frame):
        _shutdown_supervised_services(reason=f"signal {sig}")
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    # Windows may not deliver SIGTERM, but register anyway for completeness.
    with contextlib.suppress(Exception):
        signal.signal(signal.SIGTERM, _signal_handler)
    _install_windows_console_close_handler()
    atexit.register(lambda: _shutdown_supervised_services(reason="atexit", graceful=False))

    try:
        failures: dict[str, int] = {}
        # Track launch args for auto-restart
        _launch_info: dict[str, tuple] = {}
        for name, p in processes:
            if name == "gateway":
                _launch_info[name] = ([python_exe, gateway_script], gateway_cwd)
            elif name == "registry":
                _launch_info[name] = ([python_exe, registry_script], registry_cwd)
            elif name == "frontend":
                _launch_info[name] = ([npm_exe, "run", "dev"], frontend_dir)
            elif name == "launcher":
                _launch_info[name] = (launcher_cmd, _root)

        while True:
            for name, p in processes:
                rc = p.poll()
                if rc is not None:
                    failures[name] = failures.get(name, 0) + 1
                    # Capture stderr to show the real startup error
                    stderr_text = ""
                    try:
                        if hasattr(p, "stderr") and p.stderr:
                            stderr_text = p.stderr.read().decode("utf-8", errors="replace").strip()
                    except Exception:
                        pass
                    print(f"[start] {name} (PID {p.pid}) exited with code {rc}")
                    if stderr_text:
                        # Show last 5 lines of stderr (the key error is usually at the end)
                        lines = stderr_text.splitlines()
                        tail = lines[-5:] if len(lines) > 5 else lines
                        for line in tail:
                            print(f"  [stderr] {line}")
                    # Detect port conflict (orphaned socket on Windows): wait longer for OS to release
                    port_conflict = stderr_text and (
                        "address already in use" in stderr_text.lower()
                        or "10048" in stderr_text
                        or "WSAEADDRINUSE" in stderr_text.upper()
                    )
                    if failures[name] <= 3:
                        wait = 5 if port_conflict else 1
                        if port_conflict:
                            print(f"[start] Port conflict detected, waiting {wait}s for socket release...")
                        time.sleep(wait)
                        print(f"[start] Restarting {name} (attempt {failures[name]})...")
                        try:
                            cmd, cwd = _launch_info.get(name, (None, None))
                            if cmd:
                                new_p = subprocess.Popen(
                                    cmd,
                                    cwd=cwd,
                                    **popts,
                                )
                                for i, (n, _) in enumerate(processes):
                                    if n == name:
                                        processes[i] = (name, new_p)
                                        if _ACTIVE_JOB is not None and not _ACTIVE_JOB.add(new_p):
                                            print(
                                                f"[start] Warning: could not bind restarted {name} "
                                                f"(PID {new_p.pid}) to kill-on-close job"
                                            )
                                        print(f"[start] {name} restarted (PID {new_p.pid})")
                                        break
                        except Exception as e:
                            print(f"[start] Failed to restart {name}: {e}")
                    elif failures[name] == 5:
                        print(f"[start] {name} failed 5 times, giving up. Run 'opensquad stop' to clean up ports.")
            time.sleep(1)
    except KeyboardInterrupt:
        _shutdown_supervised_services(reason="KeyboardInterrupt")
    finally:
        _shutdown_supervised_services(reason="finally", graceful=False)
