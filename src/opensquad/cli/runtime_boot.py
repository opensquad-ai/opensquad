"""
Bootstrap for `opensquad code` / chat: ensure Gateway + Launcher are up,
auth is valid, and a default agent is ready — then hand off to TUI.

Services stay running after the TUI exits (next `opensquad code` is instant).

Startup strategy (Claude Code–style):
  - ``prepare_code_session_fast`` — instant TUI (local cache only)
  - ``run_tui_preflight`` — background services / auth / agent connect
  - ``prepare_code_session`` — blocking path for ``-m`` / legacy
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import time
from argparse import Namespace
from typing import Any, Callable

import httpx


def _port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _detach_popen_kwargs() -> dict[str, Any]:
    """Spawn children that outlive this CLI process (no extra console windows)."""
    from opensquad.cli.win_process import detach_popen_kwargs

    return detach_popen_kwargs()


def _wait_with_backoff(
    check: Callable[[], bool],
    *,
    timeout: float = 45.0,
    initial: float = 0.05,
    max_delay: float = 0.5,
    name: str = "resource",
) -> bool:
    deadline = time.time() + timeout
    delay = initial
    while time.time() < deadline:
        if check():
            return True
        time.sleep(delay)
        delay = min(delay * 1.4, max_delay)
    print(f"[code] {name} not ready after {timeout:.0f}s", file=sys.stderr)
    return False


def _wait_port(name: str, port: int, timeout: float = 45.0, proc: subprocess.Popen | None = None) -> bool:
    """Wait until *port* is listening. Fail fast if *proc* already exited."""
    deadline = time.time() + timeout
    delay = 0.05
    while time.time() < deadline:
        if _port_open("127.0.0.1", port):
            return True
        if proc is not None and proc.poll() is not None:
            code = proc.returncode or 0
            print(
                f"[code] {name} exited before port {port} opened (code={code:#x}). "
                "Close the OpenSquad desktop app if it is running, then: "
                "opensquad stop && opensquad dev",
                file=sys.stderr,
            )
            return False
        time.sleep(delay)
        delay = min(delay * 1.4, 0.4)
    print(f"[code] {name} port {port} not ready after {timeout:.0f}s", file=sys.stderr)
    return False


def _gateway_url(port: int | None = None) -> str:
    from opensquad.system_config import syscfg

    p = int(port or syscfg.port("gateway") or 9555)
    return f"http://127.0.0.1:{p}"


def _wait_gateway_lite(gateway_url: str, timeout: float = 45.0) -> bool:
    base = gateway_url.rstrip("/")

    def _check() -> bool:
        try:
            r = httpx.get(f"{base}/health/ready-lite", timeout=1.0)
            if r.status_code == 200:
                return bool(r.json().get("ready_lite"))
        except Exception:
            pass
        return False

    return _wait_with_backoff(_check, timeout=timeout, initial=0.05, max_delay=0.35, name="gateway (ready-lite)")


def _wait_gateway_full(gateway_url: str, timeout: float = 30.0) -> bool:
    base = gateway_url.rstrip("/")

    def _check() -> bool:
        try:
            r = httpx.get(f"{base}/health", timeout=1.0)
            if r.status_code == 200:
                return bool(r.json().get("ready"))
        except Exception:
            pass
        return False

    return _wait_with_backoff(_check, timeout=timeout, initial=0.08, max_delay=0.5, name="gateway (full ready)")


def _frozen_backend_exe() -> str | None:
    """PyInstaller gateway/launcher binary when built locally (Phase 3 fast-path)."""
    # OPENSQUAD_SOURCE_MODE=1 forces the source (non-frozen) launch path so
    # backend Python edits take effect without repackaging. Desktop/CI keep
    # this unset to use the packaged binary.
    if os.environ.get("OPENSQUAD_SOURCE_MODE", "0").strip().lower() in ("1", "true", "yes", "on"):
        return None
    root = _repo_root()
    for rel in (
        os.path.join("build", "backend-win", "run", "run.exe"),
        os.path.join("build", "backend-win", "run", "run"),
        os.path.join("build", "backend-linux", "run", "run"),
        os.path.join("build", "backend-mac", "run", "run"),
    ):
        path = os.path.join(root, rel)
        if os.path.isfile(path):
            return path
    return None


def _norm_exe(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _pid_on_port(port: int) -> int | None:
    """PID listening on *port*, or None if unknown / nothing bound."""
    try:
        import psutil

        for conn in psutil.net_connections(kind="inet"):
            if conn.status != psutil.CONN_LISTEN or not conn.laddr or not conn.pid:
                continue
            if int(conn.laddr.port) == int(port):
                return int(conn.pid)
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                timeout=8,
            )
            want = str(int(port))
            for line in result.stdout.splitlines():
                if "LISTENING" not in line.upper():
                    continue
                parts = line.split()
                if len(parts) < 5:
                    continue
                if parts[1].rsplit(":", 1)[-1] != want:
                    continue
                pid = parts[-1]
                if pid.isdigit() and pid != "0":
                    return int(pid)
        except Exception:
            pass
    return None


def _proc_ident(pid: int) -> dict[str, Any] | None:
    try:
        import psutil

        proc = psutil.Process(pid)
        with proc.oneshot():
            return {
                "exe": proc.exe() or "",
                "cmdline": list(proc.cmdline() or []),
                "create_time": float(proc.create_time()),
            }
    except Exception:
        return None


def ident_matches_frozen(
    ident: dict[str, Any],
    frozen_exe: str,
    *,
    bundle_mtime: float | None = None,
) -> bool:
    """True when *ident* is this repo's frozen ``run.exe`` and not older than the file."""
    exe = _norm_exe(str(ident.get("exe") or ""))
    want = _norm_exe(frozen_exe)
    if exe != want:
        return False
    mtime = bundle_mtime
    if mtime is None:
        try:
            mtime = os.path.getmtime(frozen_exe)
        except OSError:
            return True
    return float(ident.get("create_time") or 0) + 2.0 >= float(mtime)


def ident_is_source_script(ident: dict[str, Any], script_name: str) -> bool:
    """True when *ident* is a Python process running *script_name* (not frozen run.exe)."""
    exe = _norm_exe(str(ident.get("exe") or ""))
    base = os.path.basename(exe).lower()
    if base in {"run.exe", "run"}:
        return False
    cmd = " ".join(str(x) for x in (ident.get("cmdline") or [])).replace("\\", "/").lower()
    return script_name.replace("\\", "/").lower() in cmd


def _stack_mismatch_reason(gateway_port: int, launcher_port: int) -> str | None:
    """None when the listening stack matches SOURCE_MODE / latest frozen bundle."""
    frozen = _frozen_backend_exe()
    gw_pid = _pid_on_port(gateway_port)
    la_pid = _pid_on_port(launcher_port)
    gw = _proc_ident(gw_pid) if gw_pid else None
    la = _proc_ident(la_pid) if la_pid else None
    if frozen:
        if not gw or not ident_matches_frozen(gw, frozen):
            return f"gateway is not the latest frozen backend ({frozen})"
        if not la or not ident_matches_frozen(la, frozen):
            return f"launcher is not the latest frozen backend ({frozen})"
        return None
    if not gw or not ident_is_source_script(gw, "run.py"):
        return "gateway is not source run.py (OPENSQUAD_SOURCE_MODE=1)"
    if not la or not ident_is_source_script(la, "launcher_main.py"):
        return "launcher is not source launcher_main.py (OPENSQUAD_SOURCE_MODE=1)"
    return None


def _recycle_running_stack(gateway_port: int, launcher_port: int, registry_port: int, *, quiet: bool) -> None:
    """Stop the current core stack (and registered children) so a new backend can bind."""
    from opensquad.cli.commands.start_cmd import _kill_port_owners, _try_graceful_launcher_shutdown
    from opensquad.cli.commands.stop_cmd import _terminate_registered_processes

    if not quiet:
        print("[code] Stopping current Gateway/Launcher so the desired backend can bind…")
    _try_graceful_launcher_shutdown(launcher_port)
    with contextlib.suppress(Exception):
        _terminate_registered_processes()
    _kill_port_owners(gateway_port, launcher_port, registry_port)
    deadline = time.time() + 8.0
    while time.time() < deadline:
        if not any(_port_open("127.0.0.1", p) for p in (gateway_port, launcher_port)):
            return
        time.sleep(0.15)


def _service_command(component: str, *, launcher_port: int, python_exe: str, root: str) -> list[str]:
    """Prefer frozen ``run.exe --service …`` when available; else Python scripts."""
    frozen = _frozen_backend_exe()
    if frozen:
        if component == "gateway":
            return [frozen, "--service", "gateway"]
        if component == "launcher":
            return [frozen, "--service", "launcher", "--mgmt-port", str(launcher_port)]
    if component == "gateway":
        gateway_cwd = os.path.join(root, "src", "opensquad", "gateway", "backend")
        return [python_exe, os.path.join(gateway_cwd, "run.py")]
    if component == "launcher":
        return [
            python_exe,
            os.path.join(root, "src", "opensquad", "launcher_main.py"),
            "--mgmt-port",
            str(launcher_port),
        ]
    raise ValueError(f"unknown service component: {component}")


def _service_cwd(component: str, root: str) -> str:
    if component == "gateway":
        return os.path.join(root, "src", "opensquad", "gateway", "backend")
    return root


def ensure_services(*, quiet: bool = False, skip_registry: bool = False) -> bool:
    """
    If Gateway/Launcher are down, start them detached.
    Skips frontend (TUI does not need Vite). Returns True if core ports ready.
    """
    from opensquad.cli.commands.start_cmd import _find_python, _setup_local_mode
    from opensquad.system_config import syscfg
    from opensquad.workspace_utils import bootstrap_workspace

    gateway_port = int(syscfg.port("gateway") or 9555)
    launcher_port = int(syscfg.port("launcher") or 9600)
    registry_port = int(syscfg.port("registry") or 9720)

    gw_ok = _port_open("127.0.0.1", gateway_port)
    la_ok = _port_open("127.0.0.1", launcher_port)

    if gw_ok or la_ok:
        mismatch = _stack_mismatch_reason(gateway_port, launcher_port)
        if mismatch:
            if not quiet:
                print(f"[code] Running stack is not the desired backend ({mismatch})")
            _recycle_running_stack(gateway_port, launcher_port, registry_port, quiet=quiet)
            gw_ok = False
            la_ok = False
        elif gw_ok and la_ok:
            frozen = _frozen_backend_exe()
            if not quiet:
                kind = f"frozen {frozen}" if frozen else "source"
                print(f"[code] Services already up (gateway:{gateway_port} launcher:{launcher_port}, {kind})")
            return True

    root = _repo_root()
    if root not in sys.path:
        sys.path.insert(0, root)

    workspace = bootstrap_workspace()
    syscfg.set_workspace(workspace)
    os.environ["OPENSQUAD_WORKSPACE"] = workspace
    os.environ["OPENSQUAD_USER_DATA"] = workspace
    python_path = os.path.join(root, "src")
    if "PYTHONPATH" in os.environ:
        if python_path not in os.environ["PYTHONPATH"]:
            os.environ["PYTHONPATH"] = python_path + os.pathsep + os.environ["PYTHONPATH"]
    else:
        os.environ["PYTHONPATH"] = python_path

    _setup_local_mode(root)
    python_exe = _find_python()
    popts = _detach_popen_kwargs()
    vite_port = int(syscfg.port("frontend") or 5173)

    if not quiet:
        print("[code] Starting OpenSquad services in background…")

    started: list[str] = []
    gw_proc: subprocess.Popen | None = None
    la_proc: subprocess.Popen | None = None

    if not gw_ok:
        gateway_cwd = _service_cwd("gateway", root)
        env = {
            **os.environ,
            "VITE_DEV_PORT": str(vite_port),
            "NO_PROXY": "127.0.0.1,localhost",
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
            "ALL_PROXY": "",
        }
        try:
            gw_proc = subprocess.Popen(
                _service_command("gateway", launcher_port=launcher_port, python_exe=python_exe, root=root),
                cwd=gateway_cwd,
                env=env,
                **popts,
            )
            started.append("gateway")
            time.sleep(0.15)
            if gw_proc.poll() is not None:
                code = gw_proc.returncode or 0
                print(
                    f"[code] Gateway exited immediately (code={code:#x}). Try: opensquad stop && opensquad code",
                    file=sys.stderr,
                )
                return False
        except OSError as e:
            print(f"[code] Failed to start gateway: {e}", file=sys.stderr)
            return False

    if not skip_registry and not _port_open("127.0.0.1", registry_port):
        registry_cwd = os.path.join(root, "src", "opensquad", "gateway", "plugin_registry")
        registry_script = os.path.join(registry_cwd, "main.py")
        if os.path.isfile(registry_script):
            try:
                subprocess.Popen(
                    [python_exe, registry_script],
                    cwd=registry_cwd,
                    **popts,
                )
                started.append("registry")
            except OSError as e:
                print(f"[code] Failed to start registry: {e}", file=sys.stderr)

    if not la_ok:
        try:
            la_proc = subprocess.Popen(
                _service_command("launcher", launcher_port=launcher_port, python_exe=python_exe, root=root),
                cwd=_service_cwd("launcher", root),
                **popts,
            )
            started.append("launcher")
            time.sleep(0.15)
            if la_proc.poll() is not None:
                code = la_proc.returncode or 0
                print(
                    f"[code] Launcher exited immediately (code={code:#x}). Try: opensquad stop && opensquad code",
                    file=sys.stderr,
                )
                return False
        except OSError as e:
            print(f"[code] Failed to start launcher: {e}", file=sys.stderr)
            return False

    if started and not quiet:
        print(f"[code] Launched: {', '.join(started)}")

    ok_gw = _wait_port("gateway", gateway_port, proc=gw_proc)
    ok_la = _wait_port("launcher", launcher_port, proc=la_proc)
    if not (ok_gw and ok_la):
        print(
            "[code] Core services failed to start. Try: opensquad doctor / opensquad stop then opensquad code",
            file=sys.stderr,
        )
        return False

    base = _gateway_url(gateway_port)
    if not _wait_gateway_lite(base):
        # Fallback: full ready (first install may need init_default_data)
        if not _wait_gateway_full(base, timeout=60.0):
            print("[code] Gateway did not become ready-lite in time", file=sys.stderr)
            return False

    if not quiet:
        frozen = _frozen_backend_exe()
        kind = f"frozen {frozen}" if frozen else "source"
        print(f"[code] Gateway + Launcher ready ({kind})")
    return True


def ensure_auth(client: Any, *, interactive: bool = True) -> bool:
    """
    Validate saved JWT; optionally register (first web account) or login via
    env or one-shot prompt. Returns True if client.token is usable.
    """
    from opensquad.cli.api_client import ApiError, load_credentials

    if client.token:
        try:
            client.me()
            return True
        except ApiError as e:
            if e.status not in (401, 403):
                pass
            client.token = ""
        except SystemExit:
            return False
        except Exception:
            pass

    email = (os.environ.get("OPENSQUAD_EMAIL") or "").strip()
    password = os.environ.get("OPENSQUAD_PASSWORD") or ""
    creds = load_credentials()
    if not email:
        email = (creds.get("email") or "").strip()

    # Registration required when no web account exists yet (Web parity).
    registration_required = False
    try:
        status = client.registration_status()
        registration_required = bool(status.get("registration_required"))
    except Exception:
        pass

    def _default_name() -> str:
        return (os.environ.get("OPENSQUAD_NAME") or "").strip() or (email or "user").split("@")[0]

    if email and password:
        try:
            if registration_required:
                client.register(_default_name(), email, password, language="zh")
            else:
                client.login(email, password, language="zh")
            if interactive:
                print(f"[code] Logged in as {email}")
            return True
        except Exception as e:
            print(f"[code] Auto-login failed: {e}", file=sys.stderr)

    if not interactive:
        return False

    if registration_required:
        print("[code] First run: no web account yet — registering (Web parity)")
        try:
            name = input("Name: ").strip() or _default_name()
            if not email:
                email = input("Email: ").strip()
            if not email:
                print("[code] Email required", file=sys.stderr)
                return False
            import getpass

            if not password:
                password = getpass.getpass("Password: ")
            client.register(name, email, password, language="zh")
            print(f"[code] Registered — logged in as {email}")
            return True
        except (EOFError, KeyboardInterrupt):
            print("\n[code] Registration cancelled", file=sys.stderr)
            return False
        except Exception as e:
            print(f"[code] Registration failed: {e}", file=sys.stderr)
            return False

    print("[code] Login required (saved after first success)")
    try:
        if not email:
            email = input("Email: ").strip()
        if not email:
            print("[code] Email required", file=sys.stderr)
            return False
        import getpass

        if not password:
            password = getpass.getpass("Password: ")
        client.login(email, password, language="zh")
        print(f"[code] Logged in as {email}")
        return True
    except (EOFError, KeyboardInterrupt):
        print("\n[code] Login cancelled", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[code] Login failed: {e}", file=sys.stderr)
        return False


def pick_agent_name(client: Any, preferred: str | None = None) -> str | None:
    """Pick agent name without blocking on start/ready (network list only).

    Prefer explicit CLI flag, else the Web/CLI auto-start agent. No silent
    fallback to an arbitrary agent.
    """
    from opensquad.cli.api_client import pick_default_agent

    chosen = (preferred or "").strip() or None
    if chosen:
        return chosen
    if client.token:
        return pick_default_agent(client)
    return None


def ensure_agent(client: Any, preferred: str | None = None, *, timeout: float = 60.0) -> str | None:
    """Pick (or start) an agent and wait until ready. Returns dir_name/agent_id."""
    from opensquad.cli.api_client import ApiError

    def _list() -> list[dict[str, Any]]:
        try:
            data = client.admin_get("agents")
            return list(data.get("agents") or [])
        except Exception:
            return []

    agents = _list()
    if not agents:
        print("[code] No agents found. Create one in Web UI or via launcher.", file=sys.stderr)
        return None

    def _match(name: str) -> dict[str, Any] | None:
        return next(
            (
                a
                for a in agents
                if a.get("dir_name") == name or a.get("agent_id") == name or a.get("agent_name") == name
            ),
            None,
        )

    target = preferred
    if not target:
        from opensquad.cli.api_client import pick_default_agent

        target = pick_default_agent(client)
    if not target:
        print(
            "[code] No auto-start agent configured.\n"
            "  Start one:  /start <dir>   or   opensquad agent start <dir>\n"
            "  Set default: opensquad agent autostart <dir>   (same as Web 「设为默认启动」)",
            file=sys.stderr,
        )
        return None

    hit = _match(target)
    if not hit:
        print(f"[code] Agent not found: {target}", file=sys.stderr)
        return None

    assert target
    if hit.get("ready"):
        return target

    try:
        if hit.get("process_status") == "running" and not hit.get("ready"):
            print(f"[code] {target} running but registry offline — restarting…")
            client.admin_post(f"agents/{target}/restart")
        else:
            print(f"[code] Starting agent {target}…")
            client.admin_post(f"agents/{target}/start")
    except ApiError as e:
        print(f"[code] agent start: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[code] agent start failed: {e}", file=sys.stderr)

    delay = 0.08
    deadline = time.time() + timeout
    started = time.time()
    last_print = 0.0
    while time.time() < deadline:
        agents = _list()
        hit = next(
            (a for a in agents if a.get("dir_name") == target or a.get("agent_id") == target),
            None,
        )
        if hit and hit.get("ready"):
            print(f"[code] Agent ready: {target}")
            return target
        now = time.time()
        if now - last_print >= 1.0:
            elapsed = int(now - started)
            proc = (hit or {}).get("process_status") or "?"
            reg = (hit or {}).get("registry_status") or "offline"
            print(f"[code] Waiting for {target}… {elapsed}s ({proc}/{reg})", flush=True)
            last_print = now
        time.sleep(delay)
        delay = min(delay * 1.35, 0.6)

    print(f"[code] Agent {target} not ready yet — TUI will retry on connect", file=sys.stderr)
    return target


def prepare_code_session_fast(
    *,
    gateway: str | None = None,
    agent: str | None = None,
) -> tuple[Any, str | None]:
    """Instant path: GatewayClient + optional agent name (no network blocking).

    Without an explicit ``--agent``, leave agent unset until preflight resolves
    the auto-start agent from config (do not use stale last_agent as boot default).
    """
    from opensquad.cli.api_client import GatewayClient

    client = GatewayClient(gateway_url=gateway)
    chosen = (agent or "").strip() or None
    return client, chosen


def prepare_code_session(
    *,
    gateway: str | None = None,
    agent: str | None = None,
    no_start: bool = False,
    skip_login: bool = False,
) -> tuple[Any, str | None]:
    """
    Blocking preflight for ``-m`` / legacy shell.
    Returns (GatewayClient, agent_name_or_None).
    """
    from opensquad.cli.api_client import GatewayClient, remember_agent

    if not no_start:
        if not ensure_services():
            raise SystemExit(1)

    client = GatewayClient(gateway_url=gateway)

    try:
        with httpx.Client(timeout=5.0) as h:
            h.get(f"{client.gateway_url}/api/auth/me")
    except httpx.ConnectError:
        print(
            f"[code] Cannot reach Gateway at {client.gateway_url}\n"
            f"  Hint: opensquad doctor   or   opensquad stop then opensquad code",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if not skip_login:
        if not ensure_auth(client, interactive=True):
            print("[code] Continuing without auth — use /login inside TUI")

    chosen = pick_agent_name(client, agent)
    if client.token and chosen:
        chosen = ensure_agent(client, preferred=chosen)
        if chosen:
            remember_agent(chosen)
    elif client.token and not chosen:
        print(
            "[code] No auto-start agent — open TUI and /start <name>, or: opensquad agent autostart <name>",
            file=sys.stderr,
        )
    return client, chosen


def run_tui_preflight(
    client: Any,
    agent: str | None,
    *,
    no_start: bool = False,
) -> tuple[bool, str | None, bool]:
    """
    Background preflight for Instant TUI (services + auth + agent name only).

    Returns ``(ok, agent_name, needs_new_session)``.
    Agent start/connect happens once in the TUI worker via ``_ensure_agent_connected``.
    """
    if not no_start:
        if not ensure_services(quiet=True, skip_registry=True):
            return False, agent, False

    try:
        with httpx.Client(timeout=3.0) as h:
            h.get(f"{client.gateway_url}/api/auth/me")
    except httpx.ConnectError:
        return False, agent, False

    if client.token:
        ensure_auth(client, interactive=False)

    chosen = pick_agent_name(client, agent)
    needs_session = bool(client.token and chosen)
    return True, chosen, needs_session


def run_code(args: Namespace) -> None:
    """Entry for `opensquad code` — start services, wait agent ready, then TUI.

    Users only need ``opensquad code``. Gateway/Launcher are started automatically.
    Agent is brought online in the terminal (with progress) before the TUI opens,
    so the UI is not entered while still offline. Pass ``--no-start`` only for
    advanced use.
    """
    gateway = getattr(args, "gateway", None)
    agent = getattr(args, "agent", None)
    no_start = bool(getattr(args, "no_start", False))
    legacy = bool(getattr(args, "legacy", False))
    message = getattr(args, "message", None)

    if not legacy:
        try:
            from opensquad.cli.tui.app import _quiet_tui_loggers

            _quiet_tui_loggers()
        except Exception:
            pass

    # Bring up Gateway + Launcher before any HTTP / TUI (unless --no-start).
    if not no_start:
        from opensquad.system_config import syscfg

        gw_port = int(syscfg.port("gateway") or 9555)
        la_port = int(syscfg.port("launcher") or 9600)
        already = _port_open("127.0.0.1", gw_port) and _port_open("127.0.0.1", la_port)
        if not ensure_services(quiet=already):
            print(
                "[code] Could not start Gateway/Launcher. Try: opensquad doctor",
                file=sys.stderr,
            )
            raise SystemExit(1)

    # Blocking: auth + pick agent + wait ready (progress printed to terminal).
    client, agent = prepare_code_session(
        gateway=gateway,
        agent=agent,
        no_start=True,  # services already ensured (or user passed --no-start)
    )

    if message:
        if not client.token or not agent:
            print("[code] login + agent required for -m", file=sys.stderr)
            raise SystemExit(1)
        from opensquad.cli.commands.chat_cmd import _oneshot

        _oneshot(client, agent, message)
        return

    if legacy:
        from opensquad.cli.commands.chat_cmd import InteractiveShell

        try:
            InteractiveShell(client, agent).run()
        except KeyboardInterrupt:
            print("\nbye")
        return

    from opensquad.cli.tui import run_tui

    # Agent should already be ready; TUI only opens WS + new_session.
    print("[code] Opening TUI…", flush=True)
    run_tui(gateway=client.gateway_url, agent=agent, no_start=True)
