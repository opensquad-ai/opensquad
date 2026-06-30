"""opensquad start — Start all OpenSquad services (gateway, registry, frontend, launcher)."""

import contextlib
import json
import os
import signal
import subprocess
import sys
import time

# Default Vite dev server port; can be overridden by system_config.json ports.frontend
_DEFAULT_VITE_PORT = 5173


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


def _find_python():
    """Find a usable Python interpreter (handles pip console_scripts .exe wrappers on Windows)."""
    import shutil

    # Prefer sys.executable — it's the actual python that's running right now
    exe = sys.executable
    if exe and os.path.isfile(exe):
        # Verify it's a real Python, not a pip stub
        try:
            result = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and "Python" in result.stdout:
                return exe
        except Exception:
            pass
    # Fallback: search PATH for python/python3
    for name in ("python3", "python"):
        path = shutil.which(name)
        if path and path != sys.executable:
            try:
                result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
                if result.returncode == 0 and "Python" in result.stdout:
                    return path
            except Exception:
                pass
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
            )
        else:
            try:
                os.killpg(pid, signal.SIGTERM)
            except Exception:
                os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def _kill_port_owners(*ports: int) -> None:
    """Force-kill any process listening on the given ports.
    Uses PowerShell Get-NetTCPConnection on Windows (accurate kernel-level PID),
    falls back to netstat and lsof/fuser on Unix."""
    all_ports = " ".join(str(p) for p in ports if isinstance(p, int) and p > 0)
    if not all_ports.strip():
        return

    if sys.platform == "win32":
        # 1st: PowerShell Get-NetTCPConnection — reads kernel socket table directly, PID is accurate
        ps_cmd = (
            f"Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue "
            f"| Where-Object {{ $_.LocalPort -in @({','.join(str(p) for p in ports)}) }} "
            f"| ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}"
        )
        with contextlib.suppress(Exception):
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True,
                timeout=15,
            )

        # 2nd: netstat fallback (for older Windows without Get-NetTCPConnection)
        for port in ports:
            try:
                result = subprocess.run(
                    f'netstat -ano | findstr ":{port} " | findstr "LISTENING"',
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                for line in result.stdout.strip().splitlines():
                    parts = line.split()
                    if len(parts) >= 5:
                        pid = parts[-1]
                        # Only kill if PID looks valid (digits only, not "0")
                        if pid.isdigit() and pid != "0":
                            subprocess.run(
                                ["taskkill", "/F", "/PID", pid],
                                capture_output=True,
                                check=False,
                                timeout=10,
                            )
            except Exception:
                pass
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
            # Fallback to fuser
            with contextlib.suppress(Exception):
                subprocess.run(
                    ["fuser", "-k", f"{port}/tcp"],
                    capture_output=True,
                    timeout=10,
                )
        except Exception:
            pass


def _setup_local_mode(_root):
    """Apply local-mode config: hosts.gateway = 0.0.0.0 and create .env.local."""
    src_dir = os.path.join(_root, "src")
    cfg_path = os.path.join(src_dir, "system_config.json")

    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8-sig") as f:
                cfg = json.load(f)
            cfg.setdefault("hosts", {})["gateway"] = "0.0.0.0"
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            print("[start] Local mode: hosts.gateway = 0.0.0.0")
        except Exception as e:
            print(f"[start] Warning: Failed to update system_config.json: {e}")

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
        with open(env_local, "w", encoding="utf-8") as f:
            f.write("VITE_BACKEND_HOST=127.0.0.1\n")
            f.write(f"VITE_BACKEND_PORT={gateway_port}\n")
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

    processes = []

    # [1/4] Start gateway (FastAPI backend, default port 9555 per system_config)
    if not args.no_gateway:
        gateway_port = args.port or syscfg.port("gateway")
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
        processes.append(("gateway", p))
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
        processes.append(("registry", p))
        print(f"[start] Plugin Registry started (PID {p.pid})")

    # [3/4] Start frontend dev server (Vite, port 5173)
    if not args.no_frontend:
        frontend_dir = os.path.join(_root, "src", "opensquad", "gateway", "nexuschat-pro")
        npm_exe = _find_npm()

        if os.path.isfile(os.path.join(frontend_dir, "package.json")):
            print(f"[start] [3/4] Starting Frontend Dev Server (port {_VITE_DEV_PORT})...")
            try:
                p = subprocess.Popen([npm_exe, "run", "dev"], cwd=frontend_dir)
                processes.append(("frontend", p))
                print(f"[start] Frontend started (PID {p.pid})")
            except FileNotFoundError:
                print("[start] Warning: npm not found. Install Node.js to run the frontend.")
            except Exception as e:
                print(f"[start] Warning: Failed to start frontend: {e}")
        else:
            print("[start] [3/4] Skipping Frontend (package.json not found)")

    # [4/4] Start launcher (agent management, port 9600)
    if not args.no_launcher:
        # Compute launcher port: defaults to (gateway port + 1) or 9600
        launcher_port = (args.port + 1) if args.port else syscfg.port("launcher")
        launcher_cmd = [python_exe, os.path.join(_root, "src", "opensquad", "launcher.py")]
        launcher_cmd.extend(["--mgmt-port", str(launcher_port)])

        print(f"[start] [4/4] Starting Launcher (port {launcher_port})...")
        p = subprocess.Popen(
            launcher_cmd,
            cwd=_root,
            **popts,
        )
        processes.append(("launcher", p))
        print(f"[start] Launcher started (PID {p.pid})")

    # [5/5] Start watchdog (health check) — last so it has something to monitor
    # NOTE: Must start AFTER Gateway/Registry/Frontend/Launcher are up, otherwise
    # the watchdog's first check will see the services as "not yet started" and
    # might trigger spurious recoveries.
    if not args.no_watchdog:
        watchdog_script = os.path.join(_root, "scripts", "opensquad_watchdog.py")
        if not os.path.isfile(watchdog_script):
            print(f"[start] Warning: watchdog script not found at {watchdog_script}, skipping")
        else:
            # Give the other services a few seconds to bind their ports before
            # the watchdog starts its first check.
            time.sleep(2)
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
                    stderr=subprocess.PIPE,  # capture stderr for crash diagnostics
                    text=True,
                )
                processes.append(("watchdog", wd_p))
                print(f"[start] Watchdog started (PID {wd_p.pid})")
            except Exception as e:
                print(f"[start] Warning: Failed to start watchdog: {e}")

    if not processes:
        print("[start] Nothing to start. Use selective flags to skip services.")
        return

    print(f"\n{'=' * 50}")
    print("  OpenSquad All-in-One (Local Mode)")
    print(f"{'=' * 50}")
    print(f"  Gateway Backend : http://127.0.0.1:{args.port or syscfg.port('gateway')}")
    print(f"  Plugin Registry : http://127.0.0.1:{syscfg.port('registry')}")
    print(f"  Frontend Dev    : http://127.0.0.1:{_VITE_DEV_PORT}")
    print(f"  Launcher        : http://127.0.0.1:{launcher_port}")
    print("  Watchdog        : health-checking (15s interval)")
    print(f"{'=' * 50}")
    print(f"\n[start] {len(processes)} service(s) running. Press Ctrl+C to stop.\n")

    # ── Health check: wait briefly then verify ports ──
    _check_ports = {
        "gateway": gateway_port,
        "registry": syscfg.port("registry"),
        "frontend": _VITE_DEV_PORT,
        "launcher": launcher_port,
        "external_api": syscfg.port("external_adapter"),
    }
    print("[start] Waiting for services to bind ports...")
    import socket as _socket
    import time as _time

    max_wait = 30
    _time.sleep(3)
    for name, port in _check_ports.items():
        ok = False
        for attempt in range(max_wait):
            try:
                s = _socket.create_connection(("127.0.0.1", port), timeout=1)
                s.close()
                ok = True
                break
            except Exception:
                if attempt < max_wait - 1:
                    _time.sleep(1)
        if ok:
            print(f"  \u2705 {name}: port {port} ready")
        else:
            print(f"  \u274c {name}: port {port} FAILED to start ({max_wait}s timeout)")

    # ── Shutdown state ──
    _shutting_down = False

    def _signal_handler(sig, frame):
        nonlocal _shutting_down
        if _shutting_down:
            return
        _shutting_down = True
        print("\n[start] Ctrl+C received, shutting down all services...")
        for name, p in processes:
            _kill_tree(p.pid)
            print(f"[start] {name} (PID {p.pid}) killed.")
        # 兜底：按端口强制清理孤儿进程
        _kill_port_owners(*_get_managed_ports(syscfg))
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, _signal_handler)

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
                                        print(f"[start] {name} restarted (PID {new_p.pid})")
                                        break
                        except Exception as e:
                            print(f"[start] Failed to restart {name}: {e}")
                    elif failures[name] == 5:
                        print(f"[start] {name} failed 5 times, giving up. Run 'opensquad stop' to clean up ports.")
            time.sleep(1)
    except KeyboardInterrupt:
        if not _shutting_down:
            print("\n[start] Ctrl+C received, shutting down all services...")
            for name, p in processes:
                _kill_tree(p.pid)
                print(f"[start] {name} (PID {p.pid}) killed.")
            # 兜底：按端口强制清理孤儿进程
            _kill_port_owners(*_get_managed_ports(syscfg))
