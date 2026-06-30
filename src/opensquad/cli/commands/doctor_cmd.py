"""opensquad doctor — Diagnostic report for troubleshooting."""

import json
import os
import socket
import sys
from datetime import datetime


def _ok(msg):
    return f"  \u2705 {msg}"


def _warn(msg):
    return f"  \u26a0 {msg}"


def _err(msg):
    return f"  \u274c {msg}"


def _info(msg):
    return f"  \u2139 {msg}"


def _port_listening(host, port, timeout=1.0):
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except (TimeoutError, ConnectionRefusedError, OSError):
        return False


def run_doctor(args):
    print(f"\n{'=' * 60}")
    print(f"  OpenSquad Doctor — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 60}\n")

    from opensquad.system_config import syscfg

    issues = 0

    # ── 1. Python Environment ──
    print(
        "\u2501\u2501 Python Environment \u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501"
    )
    py_ver = sys.version.split()[0]
    print(_ok(f"Python {py_ver} ({sys.executable})"))
    for pkg in ("fastapi", "uvicorn", "httpx", "openai", "lark_oapi", "psutil"):
        try:
            mod = __import__(pkg)
            ver = getattr(mod, "__version__", "?")
            print(_ok(f"{pkg}=={ver}"))
        except ImportError:
            print(_err(f"{pkg} NOT INSTALLED"))
            issues += 1

    # ── 2. Workspace ──
    print(
        "\n\u2501\u2501 Workspace \u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501"
    )
    ws = syscfg.get_workspace()
    print(_info(f"Workspace: {ws}"))
    cfg_path = os.path.join(ws, "system_config.json")
    if not os.path.isfile(cfg_path):
        print(_err(f"Config missing: {cfg_path}"))
        issues += 1
    else:
        print(_ok(f"Config: {cfg_path}"))
        try:
            with open(cfg_path, encoding="utf-8-sig") as f:
                cfg = json.load(f)
            # Check key sections
            for section in ("hosts", "ports", "auth"):
                if section not in cfg:
                    print(_warn(f"Missing section '{section}' in config"))
                    issues += 1
            # Auto-discover all plugin services and their toggle status
            from opensquad.cli.commands.service_scan import discover_plugin_status

            plugins = discover_plugin_status()
            if plugins:
                for p in plugins:
                    if p["enabled"]:
                        print(_ok(f"Plugin service '{p['name']}': {p['display']} (enabled)"))
                    else:
                        print(_info(f"Plugin service '{p['name']}': {p['display']} (disabled)"))
        except json.JSONDecodeError as e:
            print(_err(f"Invalid JSON in config: {e}"))
            issues += 1

    # ── 3. All Services (port scan, auto-discovered from plugins/) ──
    print(
        "\n\u2501\u2501 Services (Port Scan) \u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501"
    )
    from opensquad.cli.commands.service_scan import discover_all_services

    all_services = discover_all_services()
    port_issues = 0
    for name, port in all_services:
        if _port_listening("127.0.0.1", port):
            print(_ok(f"{name} (:{port})"))
        else:
            print(_err(f"{name} (:{port}) — DOWN"))
            port_issues += 1
            issues += 1
    if port_issues == 0:
        print(_ok(f"All {len(all_services)} services running"))
    else:
        print(_warn(f"{port_issues}/{len(all_services)} service(s) not running. Try: opensquad start"))

    # ── 4. Watchdog ──
    print(
        "\n\u2501\u2501 Watchdog \u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501"
    )
    try:
        import psutil

        found = False
        for proc in psutil.process_iter(["name", "cmdline"]):
            try:
                cmd = " ".join(proc.info.get("cmdline") or [])
                if "opensquad_watchdog" in cmd:
                    print(_ok(f"Watchdog running (PID {proc.pid})"))
                    found = True
                    # Show last few log lines
                    log_path = os.path.join(ws, "data", "logs", "watchdog.log")
                    if os.path.isfile(log_path):
                        with open(log_path, encoding="utf-8") as f:
                            lines = [l.strip() for l in f.readlines() if l.strip()]
                        if lines:
                            print(_info(f"Last log: {lines[-1][:100]}"))
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if not found:
            print(_warn("Watchdog NOT RUNNING. Restart with: opensquad start"))
    except ImportError:
        print(_warn("psutil not installed, can't check watchdog"))

    # ── 5. Path Sanity (install dir vs workspace) ──
    print(
        "\n\u2501\u2501 Path Sanity \u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501"
    )
    install_root = syscfg.get_builtin_root()
    # Check: data should NOT be written to install dir
    install_data = os.path.join(install_root, "data")
    if os.path.isdir(install_data):
        for root, _dirs, files in os.walk(install_data):
            dbs = [f for f in files if f.endswith(".db") or f.endswith(".sqlite")]
            logs = [f for f in files if f.endswith(".log")]
            if dbs or logs:
                rel = os.path.relpath(root, install_root)
                print(_warn(f"Data leaked to install dir: {rel}/ ({len(dbs)} db, {len(logs)} logs)"))
                print(_info(f"  Move to workspace: {os.path.join(ws, rel)}"))
                issues += 1
                break  # one warning per tree
    else:
        print(_ok("No data in install directory"))

    # ── 6. Disk ──
    print(
        "\n\u2501\u2501 Disk \u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501"
    )
    try:
        import shutil

        usage = shutil.disk_usage(ws)
        gb_free = usage.free / (1024**3)
        gb_total = usage.total / (1024**3)
        if gb_free < 1:
            print(_err(f"Low disk space: {gb_free:.1f} GB free / {gb_total:.1f} GB total"))
            issues += 1
        else:
            print(_ok(f"Disk: {gb_free:.1f} GB free / {gb_total:.1f} GB total"))
    except Exception:
        print(_warn("Could not check disk"))

    # ── 6. Recent Error Logs (tail 5 lines from gateway websocket.log) ──
    print(
        "\n\u2501\u2501 Recent Errors (Gateway) \u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501"
    )
    ws_log = os.path.join(ws, "data", "logs", "gateway", "websocket.log")
    if os.path.isfile(ws_log):
        try:
            with open(ws_log, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            error_lines = [l.strip() for l in lines if " ERROR " in l or " WARNING " in l][-5:]
            if error_lines:
                print(_info("Last 5 errors/warnings:"))
                for line in error_lines:
                    print(f"    {line[:150]}")
            else:
                print(_ok("No recent errors"))
        except Exception:
            print(_warn("Could not read websocket log"))
    else:
        print(_info("No gateway log found (service may not have started yet)"))

    # ── Summary ──
    print(f"\n{'=' * 60}")
    if issues == 0:
        print("  \u2705 All checks passed. System is healthy.")
    else:
        print(f"  \u26a0 {issues} issue(s) found. Review the items marked \u274c above.")
    print(f"{'=' * 60}\n")
