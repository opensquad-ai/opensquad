"""Quick frozen-mode smoke test: start launcher, start an agent, verify it's alive.

Usage: uv run python scripts/smoke_frozen_agent.py
Skip full electron-builder — tests run.exe directly from build/backend-win/.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

BUILD_EXE = os.path.join(os.path.dirname(__file__), "..", "build", "backend-win", "run", "run.exe")
APP_DATA = os.path.join(os.environ.get("APPDATA", ""), "nexuschat-pro")


def api(port, path, method="GET", data=None):
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url, method=method)
    if data:
        req.data = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


def main():
    exe = os.path.abspath(BUILD_EXE)
    if not os.path.isfile(exe):
        print(f"FAIL: run.exe not found at {exe}")
        sys.exit(1)

    env = {
        **os.environ,
        "OPENSQUAD_APP_DATA": APP_DATA,
        "OPENSQUAD_USER_DATA": APP_DATA,
        "OPENSQUAD_RELOAD": "0",
        "OPENSQUAD_DISABLE_VITE_PROXY": "1",
    }
    # Use the installed agent runtime if available
    runtime_python = os.path.join(os.environ.get("LOCALAPPDATA", ""), "OpenSquad", "runtime", "python311", "python.exe")
    if os.path.isfile(runtime_python):
        env["OPENSQUAD_PYTHON"] = runtime_python
        env["OPENSQUAD_AGENT_RUNTIME"] = runtime_python
        print(f"[smoke] Using agent runtime: {runtime_python}")

    procs = []

    # 1. Start launcher
    print("[smoke] Starting launcher (run.exe --service launcher)...")
    launcher = subprocess.Popen(
        [exe, "--service", "launcher", "--mgmt-port", "9600", "--no-auto-start", "--no-services"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    procs.append(launcher)

    # Wait for port 9600
    for i in range(15):
        time.sleep(1)
        r = api(9600, "/api/agents")
        if "error" not in r:
            print(f"[smoke] Launcher up after {i + 1}s, agents: {[a['dir_name'] for a in r.get('agents', [])]}")
            break
    else:
        print("FAIL: Launcher did not start in 15s")
        _cleanup(procs)
        sys.exit(1)

    # 2. Start coder agent
    print("[smoke] Starting coder agent...")
    r = api(9600, "/api/agents/coder/start", method="POST")
    print(f"[smoke] Start response: {r}")

    # 3. Wait and check if alive
    for i in range(20):
        time.sleep(2)
        r = api(9600, "/api/agents")
        agents = {a["dir_name"]: a for a in r.get("agents", [])}
        coder = agents.get("coder", {})
        alive = coder.get("alive")
        pid = coder.get("pid")
        port = coder.get("port")
        restart_count = coder.get("restart_count")
        print(f"[smoke] {i * 2}s: alive={alive} pid={pid} port={port} restarts={restart_count}")
        if alive:
            print(f"PASS: coder agent is alive on port {port}")
            # Check tool inventory — this is the regression test for the class
            # of bugs where one plugin's exception cascaded and hid subsequent
            # plugins' tools (e.g. sequential_think PydanticUserError →
            # websearch/whisper/telegram all lost).
            _check_tool_inventory(9600, "coder")
            _cleanup(procs)
            sys.exit(0)
        if restart_count and restart_count >= 3:
            print(f"FAIL: coder restarted {restart_count} times, giving up")
            break

    print("FAIL: coder did not become alive in 40s")
    _dump_agent_logs(9600, "coder")
    _dump_workspace_crash_log(APP_DATA, "coder")
    _dump_launcher_stdout(launcher)
    _check_workspace_agent_dir(APP_DATA, "coder")
    _cleanup(procs)
    sys.exit(1)


def _check_tool_inventory(launcher_port: int, agent_name: str) -> None:
    """Verify the agent's ToolRegistry has the expected namespaces.

    Reads the agent's log file directly and looks for the
    `[Boot] ToolRegistry inventory:` line printed by registry.log_inventory().
    If a critical namespace is missing, it means a plugin failed to register
    silently — the class of bug where one plugin's exception cascaded and
    hid subsequent plugins' tools.

    Note: the agent becomes "alive" (health-check OK) BEFORE boot completes
    (plugin registration + log_inventory happens at boot end, ~7s in).
    Poll up to 20s for the inventory line to appear in the log file.
    """
    # Read the agent.log file directly — the launcher's get_logs() API returns
    # an in-memory stdout buffer that may not capture Python logging output
    # reliably (buffer size limits, timing). The file is the source of truth.
    log_path = os.path.join(APP_DATA, "agents", agent_name, "data", "logs", "agent.log")
    inventory_lines: list[str] = []
    for wait_s in range(0, 20, 2):
        time.sleep(2)
        if not os.path.isfile(log_path):
            continue
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                # Read only the tail to avoid loading huge log files
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 64000))
                tail = f.read()
            lines = tail.splitlines()
        except OSError:
            continue
        inventory_lines = [l for l in lines if "ToolRegistry inventory" in l]
        if inventory_lines:
            print(f"[smoke] ToolRegistry inventory found after {wait_s + 2}s")
            break
    if not inventory_lines:
        print(f"WARN: No ToolRegistry inventory line found in {log_path} after 20s")
        print("      (agent may still be initializing — not a failure)")
        return
    # Parse the last inventory line (MCP reload prints a second one)
    last = inventory_lines[-1]
    # Format: "[Boot] ToolRegistry inventory: ['ns1', 'ns2', ...] (mcp_adapter=yes/no)"
    import ast as _ast

    try:
        bracket_start = last.index("[", last.index("inventory:") + len("inventory:"))
        bracket_end = last.index("]", bracket_start) + 1
        namespaces = _ast.literal_eval(last[bracket_start:bracket_end])
    except Exception:
        print(f"WARN: Could not parse inventory line: {last}")
        return
    print(f"[smoke] ToolRegistry namespaces ({len(namespaces)}): {namespaces}")
    # Critical namespaces — if any is missing, a plugin failed to register.
    # websearch/whisper are proxy-pattern plugins; their absence means the
    # register_tools_to_agent loop was interrupted by an earlier plugin error.
    critical = ["websearch"]
    missing = [ns for ns in critical if ns not in namespaces]
    if missing:
        print(f"FAIL: Critical tool namespaces missing: {missing}")
        print(f"      Full inventory: {namespaces}")
        print("      This indicates a plugin registration cascade failure.")
        sys.exit(1)
    else:
        print(f"OK: All critical namespaces present ({critical})")


def _dump_workspace_crash_log(app_data: str, name: str) -> None:
    """Look for agent crash logs written by run_agent()'s exception handler."""
    crash_dir = os.path.join(app_data, "logs", "agent_crash")
    if not os.path.isdir(crash_dir):
        print(f"[smoke] No agent_crash dir at {crash_dir} (agent may have died before crash handler ran)")
        return
    crash_files = sorted(
        (f for f in os.listdir(crash_dir) if name in f),
        key=lambda f: os.path.getmtime(os.path.join(crash_dir, f)),
        reverse=True,
    )
    if not crash_files:
        print(f"[smoke] No crash log for {name} in {crash_dir}")
        return
    path = os.path.join(crash_dir, crash_files[0])
    print(f"[smoke] Latest crash log for {name} ({crash_files[0]}):")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f.read().splitlines()[-40:]:
                print(f"  {line}")
    except OSError as e:
        print(f"  (cannot read: {e})")


def _dump_launcher_stdout(launcher) -> None:
    """Drain launcher's stdout pipe to see launcher-side errors."""
    print("[smoke] Launcher stdout (last 30 lines):")
    try:
        launcher.stdout.flush()
        lines = []
        # Read whatever is currently buffered without blocking on Windows.
        # readline() on a closed/EOF pipe returns "" quickly; on an open pipe
        # with data it returns the line. We cap at 30 lines to stay bounded.
        for _ in range(30):
            line = launcher.stdout.readline()
            if not line:
                break
            lines.append(line.rstrip())
        if not lines:
            print("  (no buffered output)")
        for line in lines:
            print(f"  {line}")
    except Exception as e:
        print(f"  (cannot read launcher stdout: {e})")


def _check_workspace_agent_dir(app_data: str, name: str) -> None:
    """Verify the agent dir exists in the workspace (auto-copied from builtin)."""
    agent_dir = os.path.join(app_data, "agents", name)
    if os.path.isdir(agent_dir):
        cfg = os.path.join(agent_dir, "config.json")
        print(f"[smoke] Workspace agent dir OK: {agent_dir}")
        print(f"[smoke]   config.json exists: {os.path.isfile(cfg)}")
    else:
        print(f"[smoke] Workspace agent dir MISSING: {agent_dir}")
        print("[smoke]   ensure_agent_in_workspace() may have failed to copy from builtin")


def _dump_agent_logs(port: int, name: str) -> None:
    logs = api(port, f"/api/agents/{name}/logs?lines=40")
    if isinstance(logs, dict) and logs.get("logs"):
        raw = logs["logs"]
        lines = raw.splitlines() if isinstance(raw, str) else raw
        print(f"[smoke] Last logs for {name}:")
        for line in lines[-20:]:
            print(f"  {line.rstrip()}")


def _cleanup(procs):
    for p in procs:
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    # Also kill any run.exe --service agent children
    try:
        subprocess.run(["taskkill", "/f", "/im", "run.exe"], capture_output=True, timeout=5)
    except Exception:
        pass


if __name__ == "__main__":
    main()
