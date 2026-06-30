"""opensquad logs — Tail and filter service logs."""

import os
import sys


def run_logs(args):
    from opensquad.system_config import syscfg

    ws = syscfg.get_workspace()
    log_dir = os.path.join(ws, "data", "logs")

    if not os.path.isdir(log_dir):
        print(f"[logs] No log directory found: {log_dir}")
        print("[logs] Start services first with: opensquad start")
        sys.exit(1)

    # Build service -> log file mapping
    log_map = {
        "gateway": os.path.join(log_dir, "gateway", "websocket.log"),
        "api": os.path.join(log_dir, "gateway", "api.log"),
        "auth": os.path.join(log_dir, "gateway", "auth.log"),
        "database": os.path.join(log_dir, "gateway", "database.log"),
        "backend": os.path.join(log_dir, "gateway", "backend.log"),
        "startup": os.path.join(log_dir, "gateway", "backend_startup.log"),
        "watchdog": os.path.join(log_dir, "watchdog.log"),
        "agent": os.path.join(ws, "data", "logs", "agent_run.log"),
    }

    # List mode
    if getattr(args, "list_services", False):
        print("[logs] Available log sources:")
        for name, path in sorted(log_map.items()):
            exists = " \u2705" if os.path.isfile(path) else " \u274c"
            size = ""
            if os.path.isfile(path):
                sz = os.path.getsize(path)
                if sz > 1024 * 1024:
                    size = f" ({sz // (1024 * 1024)}MB)"
                elif sz > 1024:
                    size = f" ({sz // 1024}KB)"
            print(f"  {name:<15} {path}{exists}{size}")
        return

    # Pick service
    service = getattr(args, "service", "gateway")
    if service not in log_map:
        print(f"[logs] Unknown service: {service}", file=sys.stderr)
        print("[logs] Use --list to see available services")
        sys.exit(1)

    log_file = log_map[service]
    if not os.path.isfile(log_file):
        print(f"[logs] Log file not found: {log_file}", file=sys.stderr)
        sys.exit(1)

    # Read and filter
    try:
        with open(log_file, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        print(f"[logs] Error reading {log_file}: {e}", file=sys.stderr)
        sys.exit(1)

    # Filter
    level = getattr(args, "level", "").upper()
    grep = getattr(args, "grep", "")
    tail = getattr(args, "tail", 30)

    if grep:
        lines = [l for l in lines if grep.lower() in l.lower()]
    if level:
        lines = [l for l in lines if f" {level} " in l or f"[{level}]" in l]

    # Tail
    if tail and tail > 0:
        lines = lines[-tail:]
    elif tail == 0:
        lines = []  # show nothing, only header

    # Output
    header = f"{service} log"
    if grep:
        header += f" (grep: {grep})"
    if level:
        header += f" (level: {level})"
    print(f"[logs] {header} — {len(lines)} lines")
    for line in lines:
        print(line.rstrip())
