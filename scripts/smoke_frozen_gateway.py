"""Frozen-mode smoke test: start gateway, verify /health ready=true.

Simulates Electron spawning run.exe with OPENSQUAD_APP_DATA set to a writable
userData dir (same env vars as main.ts getBackendEnv()).

Usage: uv run python scripts/smoke_frozen_gateway.py
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

BUILD_EXE = os.path.join(os.path.dirname(__file__), "..", "build", "backend-win", "run", "run.exe")
# Electron userData on this project uses package name "nexuschat-pro"
APP_DATA = os.path.join(os.environ.get("APPDATA", ""), "nexuschat-pro")
PORT = 9555


def main() -> None:
    exe = os.path.abspath(BUILD_EXE)
    if not os.path.isfile(exe):
        print(f"FAIL: run.exe not found at {exe}")
        sys.exit(1)

    os.makedirs(APP_DATA, exist_ok=True)
    env = {
        **os.environ,
        "OPENSQUAD_APP_DATA": APP_DATA,
        "OPENSQUAD_USER_DATA": APP_DATA,
        "OPENSQUAD_RELOAD": "0",
        "OPENSQUAD_DISABLE_VITE_PROXY": "1",
    }

    print(f"[smoke-gw] Starting gateway (userData={APP_DATA})...")
    proc = subprocess.Popen(
        [exe],
        cwd=APP_DATA,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    try:
        for i in range(25):
            time.sleep(1)
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                print(f"FAIL: gateway exited early (code {proc.returncode})")
                if out:
                    print(out[-3000:])
                sys.exit(1)
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2) as r:
                    data = json.loads(r.read())
                if data.get("ready") is True:
                    print(f"[smoke-gw] Gateway ready after {i + 1}s: {data}")
                    print("PASS: frozen gateway startup")
                    return
            except Exception:
                pass
        print("FAIL: gateway did not become ready in 25s")
        sys.exit(1)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
