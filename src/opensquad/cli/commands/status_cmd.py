# -*- coding: utf-8 -*-
"""opensquad status — Show agent and service status."""
import socket
import sys

try:
    import httpx
except ImportError:
    httpx = None


def _check_port(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False


def run_status(args):
    if httpx is None:
        print("[status] Error: httpx is required. Install with: pip install httpx", file=sys.stderr)
        sys.exit(1)

    from opensquad.system_config import syscfg
    from opensquad.cli.commands.service_scan import discover_all_services

    # ── All services (core + plugin, auto-discovered) ──
    launcher_port = args.port or syscfg.port("launcher")
    all_services = discover_all_services()

    # Split into core vs plugin for cleaner output
    core_names = {"Launcher", "Gateway", "Registry", "Frontend", "External API"}
    core_services = [(n, p) for n, p in all_services if n in core_names]
    plugin_services = [(n, p) for n, p in all_services if n not in core_names]

    print(f"{'Core Service':<25} {'Port':<8} {'Status'}")
    print("-" * 45)
    launcher_alive = False
    for name, port in core_services:
        alive = _check_port("127.0.0.1", port)
        icon = "\u2705 running" if alive else "\u274C DOWN"
        print(f"{name:<25} {port:<8} {icon}")
        if name == "Launcher" and alive:
            launcher_alive = True

    # ── Agents (via launcher API) ──
    if launcher_alive:
        base = f"http://127.0.0.1:{launcher_port}"
        try:
            resp = httpx.get(f"{base}/api/agents", timeout=5)
            resp.raise_for_status()
            agents = resp.json().get("agents", [])

            print(f"\n{'Agent':<25} {'Status':<12} {'PID':<8} {'Port':<8} {'Restarts'}")
            print("-" * 75)
            for agent in agents:
                name = (agent.get("agent_name") or agent.get("dir_name") or "?")[:24]
                alive = agent.get("alive", False)
                pid = str(agent.get("pid") or "-")
                port = str(agent.get("port") or "-")
                restarts = agent.get("restart_count", 0)
                icon = "[RUN]" if alive else "[STOP]"
                print(f"{name:<25} {icon:<12} {pid:<8} {port:<8} {restarts}")

            running = sum(1 for a in agents if a.get("alive"))
            print(f"\n[status] {running}/{len(agents)} agent(s) running.")
        except Exception as e:
            print(f"\n[status] Launcher up but API error: {e}", file=sys.stderr)
    else:
        print(f"\n[status] Launcher not running \u2014 agent list unavailable.")
        print(f"[status] Start it with: opensquad start")

    # ── Plugin services ──
    if plugin_services:
        print(f"\n{'Plugin Service':<25} {'Port':<8} {'Status'}")
        print("-" * 45)
        for name, port in plugin_services:
            alive = _check_port("127.0.0.1", port)
            icon = "\u2705 running" if alive else "\u274C DOWN"
            print(f"{name:<25} {port:<8} {icon}")

    # ── Watchdog ──
    try:
        import psutil
        wd_running = False
        for proc in psutil.process_iter(["name", "cmdline"]):
            try:
                cmdline = " ".join(proc.info.get("cmdline") or [])
                if "opensquad_watchdog" in cmdline:
                    wd_running = True
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        icon = "\u2705 running" if wd_running else "\u274C NOT RUNNING"
        print(f"\n{'Watchdog':<15} {'-':<8} {icon}")
    except ImportError:
        print(f"\n{'Watchdog':<15} {'-':<8} \u26A0 psutil not installed")
