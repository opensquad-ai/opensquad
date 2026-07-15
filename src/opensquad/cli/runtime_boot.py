"""
Bootstrap for `opensquad code` / chat: ensure Gateway + Launcher are up,
auth is valid, and a default agent is ready — then hand off to TUI.

Services stay running after the TUI exits (next `opensquad code` is instant).
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from argparse import Namespace
from typing import Any

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
    kw: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        # CREATE_NO_WINDOW hides the black python.exe console flash on Windows.
        # CREATE_NEW_PROCESS_GROUP lets the child outlive this CLI process.
        # Do NOT use DETACHED_PROCESS here — it still allocates a visible console.
        create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        create_new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        kw["creationflags"] = create_no_window | create_new_group
        kw["close_fds"] = True
    else:
        kw["start_new_session"] = True
    return kw


def _wait_port(name: str, port: int, timeout: float = 45.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_open("127.0.0.1", port):
            return True
        time.sleep(0.35)
    print(f"[code] {name} port {port} not ready after {timeout:.0f}s", file=sys.stderr)
    return False


def ensure_services(*, quiet: bool = False) -> bool:
    """
    If Gateway/Launcher (and Registry) are down, start them detached.
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

    if gw_ok and la_ok:
        if not quiet:
            print(f"[code] Services already up (gateway:{gateway_port} launcher:{launcher_port})")
        return True

    root = _repo_root()
    if root not in sys.path:
        sys.path.insert(0, root)

    workspace = bootstrap_workspace()
    syscfg.set_workspace(workspace)
    os.environ["OPENSQUAD_WORKSPACE"] = workspace
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

    if not gw_ok:
        gateway_cwd = os.path.join(root, "src", "opensquad", "gateway", "backend")
        gateway_script = os.path.join(gateway_cwd, "run.py")
        env = {
            **os.environ,
            "VITE_DEV_PORT": str(vite_port),
            "NO_PROXY": "127.0.0.1,localhost",
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
            "ALL_PROXY": "",
        }
        subprocess.Popen(
            [python_exe, gateway_script],
            cwd=gateway_cwd,
            env=env,
            **popts,
        )
        started.append("gateway")

    if not _port_open("127.0.0.1", registry_port):
        registry_cwd = os.path.join(root, "src", "opensquad", "gateway", "plugin_registry")
        registry_script = os.path.join(registry_cwd, "main.py")
        if os.path.isfile(registry_script):
            subprocess.Popen(
                [python_exe, registry_script],
                cwd=registry_cwd,
                **popts,
            )
            started.append("registry")

    if not la_ok:
        launcher_cmd = [
            python_exe,
            os.path.join(root, "src", "opensquad", "launcher_main.py"),
            "--mgmt-port",
            str(launcher_port),
        ]
        subprocess.Popen(launcher_cmd, cwd=root, **popts)
        started.append("launcher")

    if started and not quiet:
        print(f"[code] Launched: {', '.join(started)}")

    ok_gw = _wait_port("gateway", gateway_port)
    ok_la = _wait_port("launcher", launcher_port)
    if not (ok_gw and ok_la):
        print(
            "[code] Core services failed to start. Try: opensquad doctor / opensquad start",
            file=sys.stderr,
        )
        return False

    # Brief settle so auth routes are bound
    time.sleep(0.6)
    if not quiet:
        print("[code] Gateway + Launcher ready")
    return True


def ensure_auth(client: Any, *, interactive: bool = True) -> bool:
    """
    Validate saved JWT; optionally login via env or one-shot prompt.
    Returns True if client.token is usable.
    """
    from opensquad.cli.api_client import ApiError, load_credentials

    if client.token:
        try:
            client.me()
            return True
        except ApiError as e:
            if e.status not in (401, 403):
                # Gateway up but other error — still try proceed / re-login
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

    if email and password:
        try:
            client.login(email, password, language="zh")
            print(f"[code] Logged in as {email}")
            return True
        except Exception as e:
            print(f"[code] Auto-login failed: {e}", file=sys.stderr)

    if not interactive:
        print("[code] Not logged in. Run: opensquad login", file=sys.stderr)
        return False

    # One prompt — then remember for next time
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
    if target:
        hit = _match(target)
        if not hit:
            print(f"[code] Agent not found: {target}", file=sys.stderr)
            return None
    else:
        ready = [a for a in agents if a.get("ready")]
        pool = ready or agents
        hit = pool[0]
        target = hit.get("dir_name") or hit.get("agent_id")

    assert target
    hit = _match(target) or hit
    if hit and hit.get("ready"):
        return target

    # running but not in Gateway registry → restart so it re-registers
    try:
        if hit and hit.get("process_status") == "running" and not hit.get("ready"):
            print(f"[code] {target} running but registry offline — restarting…")
            client.admin_post(f"agents/{target}/restart")
        else:
            print(f"[code] Starting agent {target}…")
            client.admin_post(f"agents/{target}/start")
    except ApiError as e:
        print(f"[code] agent start: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[code] agent start failed: {e}", file=sys.stderr)

    deadline = time.time() + timeout
    while time.time() < deadline:
        agents = _list()
        hit = next(
            (a for a in agents if a.get("dir_name") == target or a.get("agent_id") == target),
            None,
        )
        if hit and hit.get("ready"):
            print(f"[code] Agent ready: {target}")
            return target
        time.sleep(0.8)

    print(f"[code] Agent {target} not ready yet — TUI will retry on connect", file=sys.stderr)
    return target


def prepare_code_session(
    *,
    gateway: str | None = None,
    agent: str | None = None,
    no_start: bool = False,
    skip_login: bool = False,
) -> tuple[Any, str | None]:
    """
    Full preflight for `opensquad code`.
    Returns (GatewayClient, agent_name_or_None).
    """
    from opensquad.cli.api_client import GatewayClient

    if not no_start:
        if not ensure_services():
            raise SystemExit(1)

    client = GatewayClient(gateway_url=gateway)

    # Smoke: gateway accepts TCP/HTTP
    try:
        with httpx.Client(timeout=5.0) as h:
            # Any response (even 401/404) means HTTP is up
            h.get(f"{client.gateway_url}/api/auth/me")
    except httpx.ConnectError:
        print(
            f"[code] Cannot reach Gateway at {client.gateway_url}\n  Hint: opensquad start   or   opensquad doctor",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if not skip_login:
        if not ensure_auth(client, interactive=True):
            # Still open TUI so user can /login — but warn
            print("[code] Continuing without auth — use /login inside TUI")

    chosen = agent
    if client.token:
        if not chosen:
            from opensquad.cli.api_client import pick_default_agent

            chosen = pick_default_agent(client)
        chosen = ensure_agent(client, preferred=chosen)
        if chosen:
            from opensquad.cli.api_client import remember_agent

            remember_agent(chosen)
    return client, chosen


def run_code(args: Namespace) -> None:
    """Entry for `opensquad code` — bootstrap then TUI (or legacy)."""
    gateway = getattr(args, "gateway", None)
    agent = getattr(args, "agent", None)
    no_start = bool(getattr(args, "no_start", False))
    legacy = bool(getattr(args, "legacy", False))
    message = getattr(args, "message", None)

    # Quiet HTTP client logs before any admin API traffic (avoids tty flicker in TUI)
    if not legacy:
        try:
            from opensquad.cli.tui.app import _quiet_tui_loggers

            _quiet_tui_loggers()
        except Exception:
            pass

    client, agent = prepare_code_session(
        gateway=gateway,
        agent=agent,
        no_start=no_start,
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

    run_tui(gateway=client.gateway_url, agent=agent)
