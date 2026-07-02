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
        print(f"[smoke] Last logs for {name}:")
        for line in logs["logs"][-20:]:
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
