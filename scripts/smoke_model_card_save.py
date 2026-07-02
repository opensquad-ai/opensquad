"""Smoke test: launcher saves model cards to writable workspace (not _internal/).

Usage: uv run python scripts/smoke_model_card_save.py
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

BUILD_EXE = os.path.join(os.path.dirname(__file__), "..", "build", "backend-win", "run", "run.exe")
APP_DATA = os.path.join(os.environ.get("APPDATA", ""), "nexuschat-pro")
CARD_NAME = "_smoke_test_card"


def api(port, path, method="GET", data=None):
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url, method=method)
    if data is not None:
        req.data = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def main() -> None:
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

    proc = subprocess.Popen(
        [exe, "--service", "launcher", "--mgmt-port", "9600", "--no-auto-start", "--no-services"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    try:
        for i in range(15):
            time.sleep(1)
            if proc.poll() is not None:
                err = proc.stderr.read() if proc.stderr else ""
                print(f"FAIL: launcher exited ({proc.returncode})\n{err[-2000:]}")
                sys.exit(1)
            try:
                api(9600, "/api/agents")
                break
            except Exception:
                pass
        else:
            print("FAIL: launcher did not start")
            sys.exit(1)

        body = {
            "title": "Smoke Test",
            "api_protocol": "openai_compat",
            "provider": "test",
            "api_key": "sk-test",
            "base_url": "https://example.com/v1",
            "model_name": "test-model",
            "token_max": 128000,
        }
        result = api(9600, f"/api/model-cards/{CARD_NAME}", method="PUT", data=body)
        if not result.get("ok"):
            print(f"FAIL: save returned {result}")
            sys.exit(1)

        expected = os.path.join(APP_DATA, "model_cards", f"{CARD_NAME}.json")
        if not os.path.isfile(expected):
            print(f"FAIL: file not written to workspace: {expected}")
            sys.exit(1)

        internal = os.path.join(os.path.dirname(exe), "_internal", "model_cards", f"{CARD_NAME}.json")
        if os.path.isfile(internal):
            print(f"FAIL: card incorrectly written to read-only bundle: {internal}")
            sys.exit(1)

        print(f"PASS: model card saved to {expected}")
    finally:
        try:
            api(9600, f"/api/model-cards/{CARD_NAME}", method="DELETE")
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
