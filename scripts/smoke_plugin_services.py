"""Frozen-mode smoke test: verify plugin services / plugins / skills / MCP config
are all discoverable when launcher runs with --no-services (the flag Electron
passes to the desktop launcher).

This is the regression test for the bug where --no-services used to skip service
*discovery* entirely, making every /api/plugin-services/{id}/start call return
404 "Plugin service 'websearch' not found" and hiding plugin-backed UI panels
(Token Analytics dashboard, etc.).

What this verifies (all against a --no-services launcher on port 9600):
  1. /api/plugin-services          → non-empty list, websearch present
  2. /api/services/manage          → non-empty list (Service Manager UI source)
  3. /api/plugins                  → non-empty list, token_analytics present,
                                     contributes.views non-empty (dashboard panel)
  4. /api/plugins/token_analytics/data → reachable (not 404 for plugin missing)
  5. /api/skills                   → non-empty list (skills discoverable)
  6. /api/mcp/config               → mcpServers non-empty (playwright present)
  7. POST /api/plugin-services/websearch/start → service actually starts and
                                     becomes healthy (catches the class of bugs
                                     where deps are installed to the wrong
                                     Python interpreter and the service crashes
                                     with ModuleNotFoundError on import)

Usage: uv run python scripts/smoke_plugin_services.py
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

BUILD_EXE = os.path.join(os.path.dirname(__file__), "..", "build", "backend-win", "run", "run.exe")
APP_DATA = os.path.join(os.environ.get("APPDATA", ""), "nexuschat-pro")


def api(port, path, method="GET", data=None, timeout=10):
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url, method=method)
    if data:
        req.data = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
        except Exception:
            body = {"error": str(e)}
        return e.code, body
    except Exception as e:
        return 0, {"error": str(e)}


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
    try:
        subprocess.run(["taskkill", "/f", "/im", "run.exe"], capture_output=True, timeout=5)
    except Exception:
        pass


def main():
    exe = os.path.abspath(BUILD_EXE)
    if not os.path.isfile(exe):
        print(f"FAIL: run.exe not found at {exe}")
        print("      Run scripts\\build_backend.bat first.")
        sys.exit(1)

    env = {
        **os.environ,
        "OPENSQUAD_APP_DATA": APP_DATA,
        "OPENSQUAD_USER_DATA": APP_DATA,
        "OPENSQUAD_RELOAD": "0",
        "OPENSQUAD_DISABLE_VITE_PROXY": "1",
    }
    # Use the installed agent runtime if available (same as smoke_frozen_agent.py)
    runtime_python = os.path.join(os.environ.get("LOCALAPPDATA", ""), "OpenSquad", "runtime", "python311", "python.exe")
    if os.path.isfile(runtime_python):
        env["OPENSQUAD_PYTHON"] = runtime_python
        env["OPENSQUAD_AGENT_RUNTIME"] = runtime_python
        print(f"[smoke] Using agent runtime: {runtime_python}")

    # Start launcher with --no-services (exactly what Electron does)
    print("[smoke] Starting launcher with --no-services (simulating Electron desktop launch)...")
    launcher = subprocess.Popen(
        [exe, "--service", "launcher", "--mgmt-port", "9600", "--no-auto-start", "--no-services"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    procs = [launcher]

    failures: list[str] = []

    try:
        # Wait for port 9600
        for i in range(20):
            time.sleep(1)
            status, r = api(9600, "/api/agents")
            if status == 200 and "error" not in r:
                print(f"[smoke] Launcher up after {i + 1}s")
                break
        else:
            print("FAIL: Launcher did not start in 20s")
            _dump_launcher_stdout(launcher)
            sys.exit(1)

        # Wait for plugin service discovery to complete (it runs in a thread
        # after the management API starts, so /api/agents being up does not
        # guarantee /api/plugin-services is populated yet).
        for i in range(15):
            status, r = api(9600, "/api/plugin-services")
            if status == 200 and r.get("plugin_services"):
                print(f"[smoke] Plugin service discovery complete after {i + 1}s")
                break
            time.sleep(1)
        else:
            print("[smoke] WARNING: plugin service discovery not complete after 15s")

        # ── Check 1: /api/plugin-services ──
        print("\n[smoke] Check 1: GET /api/plugin-services (service discovery under --no-services)")
        status, r = api(9600, "/api/plugin-services")
        if status != 200:
            failures.append(f"check1: /api/plugin-services returned HTTP {status}")
            print(f"  FAIL: HTTP {status} {r}")
        else:
            services = r.get("plugin_services", [])
            svc_ids = [s.get("plugin_id") for s in services]
            print(f"  OK: {len(services)} services discovered: {svc_ids}")
            if "websearch" not in svc_ids:
                failures.append(f"check1: websearch not in discovered services {svc_ids}")
                print("  FAIL: websearch missing — this is the original bug")
            else:
                print("  OK: websearch present (the original 404 bug is fixed)")

        # ── Check 2: /api/services/manage (Service Manager UI source) ──
        print("\n[smoke] Check 2: GET /api/services/manage (Service Manager UI source)")
        status, r = api(9600, "/api/services/manage")
        if status != 200:
            failures.append(f"check2: /api/services/manage returned HTTP {status}")
            print(f"  FAIL: HTTP {status} {r}")
        else:
            services = r.get("services", [])
            print(f"  OK: {len(services)} services in manage list")
            if not services:
                failures.append("check2: services list empty (UI would show no services)")

        # ── Check 3: /api/plugins (frontend plugin list + contributes) ──
        print("\n[smoke] Check 3: GET /api/plugins (frontend list + contributes.views for dashboards)")
        status, r = api(9600, "/api/plugins")
        if status != 200:
            failures.append(f"check3: /api/plugins returned HTTP {status}")
            print(f"  FAIL: HTTP {status} {r}")
        else:
            plugins = r.get("plugins", [])
            by_name = {p.get("name"): p for p in plugins}
            print(f"  OK: {len(plugins)} plugins listed")
            ta = by_name.get("token_analytics")
            if not ta:
                failures.append("check3: token_analytics plugin missing from /api/plugins")
                print("  FAIL: token_analytics missing (dashboard panel would not render)")
            else:
                views = (ta.get("contributes") or {}).get("views") or []
                print(f"  OK: token_analytics present, contributes.views={len(views)} item(s)")
                if not views:
                    failures.append("check3: token_analytics contributes.views empty")
                    print("  FAIL: dashboard view missing (Token Analytics panel would not show)")
                else:
                    print(f"  OK: dashboard view present: {views[0].get('name')}")

        # ── Check 4: /api/plugins/token_analytics/data (dashboard data endpoint) ──
        print("\n[smoke] Check 4: GET /api/plugins/token_analytics/data (dashboard data endpoint)")
        status, r = api(9600, "/api/plugins/token_analytics/data")
        # 200 = OK; 404 with "Plugin ... not found" = the discovery bug; 500 with
        # query error = OK (plugin found, query failed because no token data yet).
        if status == 404 and "not found" in str(r).lower():
            failures.append(f"check4: token_analytics data endpoint 404 'not found': {r}")
            print("  FAIL: 404 'not found' — plugin discovery broken")
        elif status == 200:
            print("  OK: data returned (status 200)")
        else:
            print(f"  OK: HTTP {status} (acceptable — query may fail with empty data): {r}")

        # ── Check 5: /api/skills ──
        print("\n[smoke] Check 5: GET /api/skills (skills discoverable)")
        status, r = api(9600, "/api/skills")
        if status != 200:
            failures.append(f"check5: /api/skills returned HTTP {status}")
            print(f"  FAIL: HTTP {status} {r}")
        else:
            skills = r.get("skills", [])
            print(f"  OK: {len(skills)} skills discovered")
            if not skills:
                failures.append("check5: skills list empty")

        # ── Check 6: /api/mcp/config (MCP central config) ──
        print("\n[smoke] Check 6: GET /api/mcp/config (MCP central config + playwright entry)")
        status, r = api(9600, "/api/mcp/config")
        if status != 200:
            failures.append(f"check6: /api/mcp/config returned HTTP {status}")
            print(f"  FAIL: HTTP {status} {r}")
        else:
            servers = (r.get("config") or r).get("mcpServers", {})
            print(f"  OK: {len(servers)} MCP servers configured: {list(servers.keys())}")
            if "playwright" not in servers:
                failures.append(f"check6: playwright not in mcpServers {list(servers.keys())}")
                print("  FAIL: playwright MCP missing")
            else:
                pw = servers["playwright"]
                print(f"  OK: playwright present, enabled={pw.get('enabled')}, command={pw.get('command')}")

        # ── Check 7: POST /api/plugin-services/websearch/start (actual start) ──
        # This is the regression test for the "deps installed to wrong Python"
        # bug: the launcher's _ensure_pip_and_install used sys.executable (frozen
        # run.exe) instead of the Agent Python, so ModuleNotFoundError crashed
        # the service on import. Discovery (checks 1-6) passed but the service
        # was unstartable.
        print("\n[smoke] Check 7: POST /api/plugin-services/websearch/start (actually start it)")
        # Use a long timeout: the start endpoint waits for the background
        # _install_builtin_plugin_deps thread to finish (up to 300s on a cold
        # cache — pip bootstrap via get-pip.py + ~13 deps), then does a
        # per-service dep check, then spawns the service. 360s gives margin.
        status, r = api(9600, "/api/plugin-services/websearch/start", method="POST", timeout=360)
        if status not in (200, 409):
            failures.append(f"check7: start returned HTTP {status}: {r}")
            print(f"  FAIL: HTTP {status} {r}")
        else:
            print(f"  OK: start returned HTTP {status} (200=started, 409=already running)")
            # Poll health endpoint on the service port (websearch default = 9001)
            svc_port = 9001
            healthy = False
            # First launch needs to bootstrap pip in the Agent Python embed via
            # get-pip.py (no ensurepip in embed builds), then install ~13 deps.
            # That can take 2-3 min on a cold cache. 120s + status prints.
            for i in range(120):
                status_h, r_h = api(svc_port, "/health")
                if status_h == 200:
                    healthy = True
                    break
                if i % 10 == 0 and i > 0:
                    print(f"  ...waiting for service health ({i}s elapsed)")
                time.sleep(1)
            if healthy:
                print(f"  OK: websearch service healthy on port {svc_port} after {i + 1}s")
            else:
                failures.append(
                    "check7: websearch service did not become healthy within 120s "
                    "(likely pip bootstrap in Agent Python embed still in progress, "
                    "or ModuleNotFoundError — deps installed to wrong Python)"
                )
                print(f"  FAIL: websearch not healthy on port {svc_port} within 120s")
                print("        Possible causes:")
                print("        1. Agent Python embed has no pip — get-pip.py bootstrap slow/failed")
                print("        2. Deps installed to wrong Python (frozen run.exe instead of Agent Python)")
            # Stop the service so the smoke test doesn't leave it running
            api(9600, "/api/plugin-services/websearch/stop", method="POST")

        # ── Summary ──
        print("\n" + "=" * 70)
        if failures:
            print(f"FAIL: {len(failures)} check(s) failed:")
            for f in failures:
                print(f"  - {f}")
            _dump_launcher_stdout(launcher)
            sys.exit(1)
        else:
            print("PASS: All checks passed — plugin services / plugins / skills / MCP")
            print("      are all discoverable under --no-services (the desktop launch flag).")
            sys.exit(0)
    finally:
        _cleanup(procs)


def _dump_launcher_stdout(launcher) -> None:
    print("\n[smoke] Launcher stdout (last 40 lines):")
    try:
        launcher.stdout.flush()
        for _ in range(40):
            line = launcher.stdout.readline()
            if not line:
                break
            print(f"  {line.rstrip()}")
    except Exception as e:
        print(f"  (cannot read launcher stdout: {e})")


if __name__ == "__main__":
    main()
