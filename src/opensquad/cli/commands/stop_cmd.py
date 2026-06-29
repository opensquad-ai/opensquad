# -*- coding: utf-8 -*-
"""opensquad stop — Kill all OpenSQuad processes by port (cross-platform)."""
import json
import os
import signal
import sys
import time
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

def _get_opensquad_ports():
    """Return tuple of all OpenSquad-managed ports from configuration."""
    from opensquad.system_config import syscfg
    return (
        syscfg.port("frontend"),  # Vite dev server port
        syscfg.port("gateway"),
        syscfg.port("launcher"),
        syscfg.port("external_adapter"),
        syscfg.port("registry"),
    )

# Substrings used to detect OpenSQuad-related processes for the tree-kill
# fallback. These are matched against the full command line of each process.
# Note: feishu/external_api subprocesses don't have "opensquad" in their
# cmdline, so we explicitly include "feishu" and "plugins\\" filters.
OPEN_SQUAD_PROCESS_MARKERS = (
    "opensquad",
    "scripts\\opensquad_watchdog",
    "plugins\\feishu",
    "plugins\\external_api",
    "plugins\\websearch",
    "agents_boot",
    "feishu_diag",
)


def _read_runtime_registry_entries() -> list[dict]:
    """Read launcher runtime registry entries so stop can kill known child PIDs."""
    try:
        from opensquad.launcher.process_manager import _read_runtime_registry
        return _read_runtime_registry() or []
    except ImportError:
        return []


def _terminate_registered_processes() -> tuple[int, int, set[int]]:
    """Terminate agent/plugin processes recorded by launcher runtime registry."""
    try:
        from opensquad.launcher.process_manager import _terminate_pid_tree, _pid_exists, _remove_runtime_registry
    except ImportError:
        return 0, 0, set()

    entries = _read_runtime_registry_entries()
    killed = 0
    seen_pids: set[int] = set()
    touched_pids: set[int] = set()
    for entry in entries:
        pid = entry.get("pid")
        kind = entry.get("_kind", "unknown")
        identifier = entry.get("agent_id") or entry.get("plugin_id") or "unknown"
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            continue
        if pid_int <= 0 or pid_int in seen_pids:
            continue
        seen_pids.add(pid_int)
        touched_pids.add(pid_int)
        alive = _pid_exists(pid_int)
        if alive and _terminate_pid_tree(pid_int):
            killed += 1
        try:
            _remove_runtime_registry(kind, identifier)
        except OSError:
            pass
    return killed, len(entries), touched_pids


def _probe_port(port: int, timeout: float = 0.15) -> bool:
    """Check if a port has an active listener (instant on connection refused)."""
    import socket as _socket
    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex(('127.0.0.1', port)) == 0


def _collect_listening_pids_windows(ports: tuple[int, ...]) -> dict[int, list[str]]:
    """Collect listener PIDs for managed ports.

    Performance strategy (ordered by speed):
      1. Parallel socket probe — ~0.15s total (vs 0.3s per-port sequential)
      2. Get-NetTCPConnection — ~1s, precise PID↔port mapping on Win8+
      3. WMIC cmdline heuristic — ~1-2s, indirect (match port in command line)
      4. netstat -ano — last resort, capped at 5s (was 30s)
    """
    port_set = {int(port) for port in ports if isinstance(port, int) and port > 0}
    listeners: dict[int, list[str]] = {}
    if not port_set:
        return listeners

    # Step 1: parallel socket probe (all ports simultaneously, ~0.15s total)
    active_ports: set[int] = set()
    with ThreadPoolExecutor(max_workers=min(len(port_set), 8)) as pool:
        future_to_port = {pool.submit(_probe_port, p): p for p in port_set}
        for future in as_completed(future_to_port):
            if future.result():
                active_ports.add(future_to_port[future])

    if not active_ports:
        return listeners

    # Step 2: Get-NetTCPConnection (modern Windows, ~1s, exact PID↔port)
    try:
        port_csv = ",".join(str(p) for p in sorted(active_ports))
        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                f"Get-NetTCPConnection -State Listen -LocalPort {port_csv} "
                f"| Select-Object LocalPort,OwningProcess | ConvertTo-Json -Compress",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            try:
                data = json.loads(result.stdout)
            except (json.JSONDecodeError, ValueError):
                data = None
            if data is not None:
                if isinstance(data, dict):
                    data = [data]
                for entry in data:
                    port_val = entry.get("LocalPort")
                    pid_val = str(entry.get("OwningProcess", ""))
                    if port_val and pid_val and int(port_val) in active_ports:
                        pid_list = listeners.setdefault(int(port_val), [])
                        if pid_val not in pid_list:
                            pid_list.append(pid_val)
                if listeners:
                    return listeners
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    # Step 3: WMIC cmdline heuristic (~1-2s, indirect match)
    try:
        result = subprocess.run(
            ["wmic", "process", "get", "ProcessId,CommandLine", "/format:csv"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 3 or not parts[-1].isdigit():
                    continue
                try:
                    cmdline = parts[-2].lower()
                    pid = parts[-1]
                except (ValueError, IndexError):
                    continue
                for port in active_ports:
                    if f":{port}" in cmdline or f"port={port}" in cmdline or f"port {port}" in cmdline:
                        pid_list = listeners.setdefault(port, [])
                        if pid not in pid_list:
                            pid_list.append(pid)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Step 4: netstat fallback (last resort, capped at 5s — was 30s)
    if not listeners and active_ports:
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) < 5 or parts[0].upper() != "TCP":
                    continue
                local_addr = parts[1]
                state = parts[3].upper()
                pid = parts[4]
                if state != "LISTENING" or not pid.isdigit() or pid == "0":
                    continue
                host, sep, port_str = local_addr.rpartition(":")
                if not sep:
                    continue
                try:
                    port = int(port_str)
                except ValueError:
                    continue
                if port not in active_ports:
                    continue
                pid_list = listeners.setdefault(port, [])
                if pid not in pid_list:
                    pid_list.append(pid)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    return listeners


def _collect_listening_pids_unix(ports: tuple[int, ...]) -> dict[int, list[str]]:
    """Collect listener PIDs for managed ports in one pass on Unix."""
    port_set = {int(port) for port in ports if isinstance(port, int) and port > 0}
    listeners: dict[int, list[str]] = {}
    if not port_set:
        return listeners

    try:
        result = subprocess.run(
            ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-FpPn"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        current_pid = None
        current_ports: set[int] = set()
        for raw_line in result.stdout.splitlines():
            if not raw_line:
                continue
            tag, value = raw_line[0], raw_line[1:]
            if tag == "p":
                current_pid = value.strip()
                current_ports = set()
            elif tag == "n" and current_pid:
                _, _, tail = value.rpartition(":")
                try:
                    port = int(tail)
                except ValueError:
                    continue
                if port in port_set and port not in current_ports:
                    listeners.setdefault(port, []).append(current_pid)
                    current_ports.add(port)
        return listeners
    except FileNotFoundError:
        pass
    except (OSError, subprocess.TimeoutExpired):
        return listeners

    try:
        result = subprocess.run(
            ["ss", "-tlnp"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return listeners

    import re
    for line in result.stdout.splitlines():
        if "LISTEN" not in line:
            continue
        pid_matches = re.findall(r"pid=(\d+)", line)
        if not pid_matches:
            continue
        for port in port_set:
            if f":{port}" not in line:
                continue
            port_pids = listeners.setdefault(port, [])
            for pid in pid_matches:
                if pid not in port_pids:
                    port_pids.append(pid)
    return listeners


def _kill_port_pids(ports: tuple[int, ...], skip_pids: set[int] | None = None) -> tuple[int, list[tuple[int, list[str]]]]:
    """Kill listener PIDs for managed ports, skipping PIDs already handled earlier."""
    skip = {str(pid) for pid in (skip_pids or set()) if pid and pid > 0}
    listeners = (
        _collect_listening_pids_windows(ports)
        if sys.platform == "win32"
        else _collect_listening_pids_unix(ports)
    )
    total = 0
    details: list[tuple[int, list[str]]] = []
    killed_seen: set[str] = set()

    for port in ports:
        killed_pids: list[str] = []
        for pid in listeners.get(port, []):
            if pid in skip or pid in killed_seen:
                continue
            try:
                if sys.platform == "win32":
                    result = subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", pid],
                        capture_output=True,
                        check=False,
                        timeout=5,
                    )
                else:
                    result = subprocess.run(
                        ["kill", "-9", pid],
                        capture_output=True,
                        check=False,
                        timeout=10,
                    )
            except Exception:
                continue
            if result.returncode == 0:
                killed_seen.add(pid)
                killed_pids.append(pid)
                total += 1
        if killed_pids:
            details.append((port, killed_pids))
    return total, details



def _snapshot_windows_procs() -> dict[int, tuple[int | None, str]]:
    """Snapshot all Windows processes via WMIC in a single subprocess call.

    Returns {pid: (ppid, name_lower)} — typically completes in <2s
    compared to psutil.process_iter which takes 30s+ for 300 processes.

    Falls back to psutil if WMIC is unavailable.
    """
    try:
        result = subprocess.run(
            [
                "wmic", "process",
                "get", "ProcessId,ParentProcessId,Name",
                "/format:csv",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            procs: dict[int, tuple[int | None, str]] = {}
            for line in result.stdout.strip().splitlines():
                # WMIC CSV: alternating data/blank lines
                # Fields: Node,Name,ParentProcessId,ProcessId
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 4 or not parts[-1].isdigit():
                    continue
                try:
                    # parts = [Node, Name, ParentProcessId, ProcessId]
                    name = parts[-3].lower()
                    ppid = int(parts[-2]) if parts[-2].isdigit() else None
                    pid = int(parts[-1])
                    procs[pid] = (ppid, name)
                except (ValueError, IndexError):
                    continue
            if procs:
                return procs
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: psutil (slower but always available)
    try:
        import psutil
        procs = {}
        for p in psutil.process_iter(['pid', 'ppid', 'name']):
            try:
                pid = p.info['pid']
                ppid = p.info.get('ppid')
                name = (p.info.get('name') or '').lower()
                procs[pid] = (ppid, name)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return procs
    except Exception:
        return {}


def _kill_windows_tree_psutil(my_pid: int) -> tuple[int, set[int]]:
    """Kill OpenSQuad processes via process tree walk (Windows).

    Performance: uses WMIC for bulk process snapshot (<2s) instead of
    psutil.process_iter (30s+). Only uses psutil for lazy cmdline
    fetching on the few candidate processes.
    """
    try:
        import psutil
    except ImportError:
        return 0, set()

    # ── Phase 1: bulk snapshot via WMIC (<2s, vs psutil 30s+) ──
    procs_info = _snapshot_windows_procs()
    if not procs_info:
        return 0, set()

    # ── Phase 2: build ancestor set (pure memory work) ──
    skip_pids: set[int] = {my_pid}
    current = my_pid
    for _ in range(32):
        entry = procs_info.get(current)
        if not entry:
            break
        ppid = entry[0]
        if not ppid or ppid <= 0 or ppid in skip_pids:
            break
        skip_pids.add(ppid)
        current = ppid

    # ── Phase 3: build children map ──
    children_of: dict[int, list[int]] = {}
    for pid, (ppid, _) in procs_info.items():
        if ppid:
            children_of.setdefault(ppid, []).append(pid)

    # ── Phase 4: FAST pre-filter by process name, then lazy cmdline check ──
    CANDIDATE_NAMES = {'python.exe', 'pythonw.exe', 'python3.exe', 'node.exe', 'node'}
    candidate_pids: set[int] = set()
    for pid, (ppid, name) in procs_info.items():
        if pid in skip_pids:
            continue
        if name in CANDIDATE_NAMES:
            candidate_pids.add(pid)

    # For candidate PIDs, fetch cmdline only for these few processes (not all 300+)
    to_kill: set[int] = set()
    for pid in candidate_pids:
        cmdline_l = ""
        try:
            proc = psutil.Process(pid)
            cmdline_l = " ".join(proc.cmdline()).lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        for marker in OPEN_SQUAD_PROCESS_MARKERS:
            if marker.lower() in cmdline_l:
                to_kill.add(pid)
                # walk children (pure data walk, no syscall)
                stack = list(children_of.get(pid, []))
                while stack:
                    cpid = stack.pop()
                    if cpid in to_kill or cpid in skip_pids:
                        continue
                    to_kill.add(cpid)
                    stack.extend(children_of.get(cpid, []))
                break

    # ── Phase 5: kill via taskkill /F /T (fastest on Windows) ──
    killed = 0
    for pid in to_kill:
        if pid in skip_pids:
            continue
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, check=False, timeout=5,
            )
            killed += 1
        except Exception:
            pass

    return killed, to_kill


def _kill_unix_tree_psutil(my_pid: int) -> int:
    """Kill OpenSQuad processes via process tree walk (Linux/macOS).

    Single-pass: collect all proc info in one process_iter call,
    then classify and kill using only cached data (no redundant syscalls).
    """
    try:
        import psutil
    except ImportError:
        return 0

    # ── Phase 1: snapshot all process info in one pass ──
    procs_info: list[tuple[int, int | None, str]] = []
    try:
        for p in psutil.process_iter(['pid', 'ppid', 'cmdline']):
            try:
                pid = p.info['pid']
                ppid = p.info.get('ppid')
                cmdline = " ".join(p.info.get('cmdline') or [])
                procs_info.append((pid, ppid, cmdline.lower()))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception:
        pass

    if not procs_info:
        return 0

    # ── Phase 2: build ancestor set ──
    skip_pids: set[int] = {my_pid}
    current = my_pid
    for _ in range(32):
        parent = None
        for pid, ppid, _ in procs_info:
            if pid == current:
                parent = ppid
                break
        if not parent or parent <= 0 or parent in skip_pids:
            break
        skip_pids.add(parent)
        current = parent

    # ── Phase 3: build children map ──
    children_of: dict[int, list[int]] = {}
    for pid, ppid, _ in procs_info:
        if ppid:
            children_of.setdefault(ppid, []).append(pid)

    # ── Phase 4: classify via cached data ──
    to_kill: set[int] = set()
    for pid, ppid, cmdline_l in procs_info:
        if pid in skip_pids:
            continue
        for marker in OPEN_SQUAD_PROCESS_MARKERS:
            if marker.lower() in cmdline_l:
                to_kill.add(pid)
                stack = list(children_of.get(pid, []))
                while stack:
                    cpid = stack.pop()
                    if cpid in to_kill or cpid in skip_pids:
                        continue
                    to_kill.add(cpid)
                    stack.extend(children_of.get(cpid, []))
                break

    # ── Phase 5: kill using psutil ──
    killed = 0
    pid_to_proc: dict[int, psutil.Process] = {}
    for pid, _, _ in procs_info:
        if pid in to_kill and pid not in skip_pids:
            try:
                pid_to_proc[pid] = psutil.Process(pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    for pid, _, _ in procs_info:
        if pid in to_kill and pid in pid_to_proc and pid not in skip_pids:
            try:
                pid_to_proc[pid].kill()
                killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    return killed


def run_stop(args):
    """Kill all OpenSQuad processes — parent first (to prevent auto-restart), then port cleanup."""

    my_pid = os.getpid()
    started_at = time.perf_counter()
    parent_killed = 0
    registry_killed = 0
    registry_entries = 0
    handled_pids: set[int] = set()

    # ── Step 0: Graceful shutdown — notify launcher to stop agents cleanly ──
    syscfg_import_start = time.perf_counter()
    from opensquad.system_config import syscfg
    syscfg_import_elapsed = time.perf_counter() - syscfg_import_start
    graceful_start = time.perf_counter()
    try:
        launcher_port = syscfg.port("launcher")
        print(f"[stop] Sending graceful shutdown to launcher (port {launcher_port})...")
        # Quick socket probe first — if port isn't listening, skip HTTP entirely
        if not _probe_port(launcher_port, timeout=0.5):
            print(f"[stop] Launcher port not listening, skipping graceful shutdown.")
        else:
            import urllib.request, urllib.error
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{launcher_port}/api/shutdown",
                    method="POST",
                    data=json.dumps({"timeout": 5}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=3)
                print(f"[stop] Launcher acknowledged shutdown.")
                # Brief wait for processes to exit gracefully, then verify
                for _ in range(5):
                    time.sleep(0.5)
                    if not _probe_port(launcher_port, timeout=0.3):
                        break
            except urllib.error.URLError:
                print(f"[stop] Launcher not responding, proceeding to force kill.")
                pass
    except Exception:
        pass
    graceful_elapsed = time.perf_counter() - graceful_start

    # ── Step 1: Kill processes recorded by launcher runtime registry ──
    registry_start = time.perf_counter()
    registry_killed, registry_entries, registry_pids = _terminate_registered_processes()
    handled_pids.update(registry_pids)
    registry_elapsed = time.perf_counter() - registry_start
    print(
        f"[stop] Runtime registry: {registry_entries} entrie(s), "
        f"killed {registry_killed} in {registry_elapsed:.2f}s."
    )
    if registry_entries > 0:
        time.sleep(0.5)

    # ── Step 2: Tree-kill all opensquad processes (children included) ──
    tree_start = time.perf_counter()
    if sys.platform == "win32":
        print("[stop] Force-killing OpenSQuad process tree...")
        parent_killed, tree_pids = _kill_windows_tree_psutil(my_pid)
        handled_pids.update(tree_pids)
    else:
        print("[stop] Force-killing OpenSQuad process tree...")
        parent_killed = _kill_unix_tree_psutil(my_pid)
    tree_elapsed = time.perf_counter() - tree_start

    if parent_killed > 0:
        print(f"[stop] Killed {parent_killed} OpenSQuad process(es) (incl. children) in {tree_elapsed:.2f}s")
        time.sleep(0.5)
    else:
        print(f"[stop] Process tree scan finished in {tree_elapsed:.2f}s")

    # ── Step 3: Port-based cleanup for any orphan processes ──
    # Early exit: if no processes were found at all, do a quick port probe.
    # If no ports are active, skip the expensive PID lookup entirely.
    port_start = time.perf_counter()
    opensquad_ports = _get_opensquad_ports()
    need_port_scan = True

    if registry_killed == 0 and parent_killed == 0:
        # No processes found in Steps 1-2; quick-check if any port is still active
        active_any = False
        with ThreadPoolExecutor(max_workers=min(len(opensquad_ports), 8)) as pool:
            futures = {pool.submit(_probe_port, p, 0.15): p for p in opensquad_ports}
            for f in as_completed(futures):
                if f.result():
                    active_any = True
                    break
        if not active_any:
            need_port_scan = False
            print("[stop] No active OpenSQuad ports found, skipping port cleanup.")

    if need_port_scan:
        print("[stop] Cleaning up OpenSQuad ports...")
        total, port_details = _kill_port_pids(opensquad_ports, skip_pids=handled_pids)
        for port, pids in port_details:
            pid_preview = ", ".join(pids[:5])
            suffix = "..." if len(pids) > 5 else ""
            print(f"  Port {port}: killed {len(pids)} unique PID(s) [{pid_preview}{suffix}]")
    else:
        total = 0
        port_details = []

    port_elapsed = time.perf_counter() - port_start

    total_killed = registry_killed + parent_killed + total
    total_elapsed = time.perf_counter() - started_at
    if total_killed == 0:
        print(f"[stop] No OpenSQuad processes found. syscfg_import={syscfg_import_elapsed:.2f}s, graceful={graceful_elapsed:.2f}s, registry={registry_elapsed:.2f}s, tree={tree_elapsed:.2f}s, port={port_elapsed:.2f}s, total={total_elapsed:.2f}s")
    else:
        print(
            f"[stop] Done. registry={registry_killed}, tree={parent_killed}, port={total} "
            f"process(es) killed. syscfg_import={syscfg_import_elapsed:.2f}s, "
            f"graceful={graceful_elapsed:.2f}s, registry={registry_elapsed:.2f}s, "
            f"tree={tree_elapsed:.2f}s, port={port_elapsed:.2f}s, total={total_elapsed:.2f}s"
        )
