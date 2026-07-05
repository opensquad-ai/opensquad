"""
Backend service startup script
Supports both normal Python execution and PyInstaller frozen bundles.

Single binary, multiple services: the desktop app spawns this same executable
once per service via ``--service <name>``. Without the flag (or with
``--service gateway``) it runs the FastAPI gateway as before, so all existing
launch paths (``python run.py``, ``run.exe``) keep working unchanged.
"""

import contextlib
import importlib.util
import json
import os
import sys

import uvicorn

# ── PyInstaller compatibility: detect whether running in a frozen bundle ──────────────────────────
IS_FROZEN = getattr(sys, "frozen", False)

if IS_FROZEN:
    # Frozen: executable directory is the root for all resources
    BACKEND_DIR = os.path.dirname(sys.executable)
    # Make app.* modules importable (backend dir is the root)
    if BACKEND_DIR not in sys.path:
        sys.path.insert(0, BACKEND_DIR)
    PROJECT_ROOT = BACKEND_DIR  # opensquad package is embedded in the bundle; no extra path needed
else:
    # Normal run: set sys.path according to the source directory structure
    # Ensure backend dir is in sys.path so uvicorn can import "app.main:app"
    BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
    if BACKEND_DIR not in sys.path:
        sys.path.insert(0, BACKEND_DIR)

    # Ensure project root is in sys.path so "system_config" can be imported
    PROJECT_ROOT = os.path.dirname(os.path.dirname(BACKEND_DIR))
    if PROJECT_ROOT not in sys.path:
        sys.path.insert(0, PROJECT_ROOT)

# Propagate sys.path into PYTHONPATH so uvicorn's reloader subprocess inherits it.
# Without this, the watchfiles reloader spawns a new python process that doesn't
# inherit sys.path modifications done here, causing "ModuleNotFoundError: opensquad".
# (Only relevant in non-frozen mode; reloader is disabled when frozen.)
if not IS_FROZEN:
    _existing_pythonpath = os.environ.get("PYTHONPATH", "")
    _extra_paths = [p for p in [BACKEND_DIR, PROJECT_ROOT] if p not in _existing_pythonpath]
    if _extra_paths:
        os.environ["PYTHONPATH"] = os.pathsep.join(
            _extra_paths + ([_existing_pythonpath] if _existing_pythonpath else [])
        )


def load_config():
    """Load config from gateway/config.json"""
    if IS_FROZEN:
        # PyInstaller 6.x: COLLECT puts datas under <exe-dir>/_internal/,
        # not directly next to the exe. The spec bundles config.json as
        # `datas += [(config.json, ".")]`, so it lives at _internal/config.json.
        frozen_root = os.path.join(BACKEND_DIR, "_internal")
        config_path = os.path.join(frozen_root, "config.json")
        if not os.path.exists(config_path):
            # Fallback to legacy location (PyInstaller 5.x layout).
            config_path = os.path.join(BACKEND_DIR, "config.json")
    else:
        root_dir = os.path.dirname(BACKEND_DIR)
        config_path = os.path.join(root_dir, "config.json")

    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


def _parse_service_arg():
    """Read ``--service <name>`` from argv.

    Returns ``(service, argv_without_service)``. ``service`` defaults to
    ``"gateway"`` when the flag is absent so plain ``run.exe`` keeps launching
    the gateway exactly as before. The returned argv has the ``--service`` pair
    removed so the remaining flags can be forwarded to the chosen service
    (e.g. the launcher reads ``--mgmt-port`` / ``--no-auto-start`` off argv).
    """
    argv = list(sys.argv)
    service = "gateway"
    if "--service" in argv:
        i = argv.index("--service")
        if i + 1 < len(argv):
            service = argv[i + 1]
            del argv[i : i + 2]  # drop "--service" and its value
        else:
            del argv[i]  # trailing "--service" with no value
    return service, argv


def run_launcher():
    """Run the agent launcher (management API on port 9600).

    The launcher lives in ``opensquad/launcher.py`` — a single 3k-line module
    whose ``main()`` is the entry point. It is **shadowed** by the
    ``opensquad/launcher/`` package (which only re-exports process_manager),
    so ``import opensquad.launcher`` resolves to the package and never reaches
    ``main()``. We therefore load launcher.py directly by file path with
    importlib, which executes the module body (it is safe to import — no
    sockets/subprocesses fire at import time) and call its ``main()``.

    Frozen: launcher.py is bundled as a data file at
    ``<exe-dir>/_internal/opensquad/_launcher_main/launcher.py`` (see
    opensquad_backend.spec). It must NOT live at ``opensquad/launcher.py`` in
    the bundle — that path shadows the ``opensquad.launcher`` package.
    Non-frozen: it sits next to the opensquad package at
    ``<project-root>/opensquad/launcher.py``.

    ``main()`` parses ``sys.argv`` itself, so we strip the ``--service`` pair
    (already done by _parse_service_arg) and let the rest through. The desktop
    app passes ``--no-auto-start --no-services`` so the launcher only opens its
    management port without spawning child processes (a frozen EXE cannot
    ``sys.executable -m`` an agent or run a plugin's adapter.py).
    """
    if IS_FROZEN:
        launcher_path = os.path.join(BACKEND_DIR, "_internal", "opensquad", "_launcher_main", "launcher_main.py")
    else:
        # Non-frozen: PROJECT_ROOT is the opensquad package dir (src/opensquad),
        # so launcher_main.py is right inside it.
        launcher_path = os.path.join(PROJECT_ROOT, "launcher_main.py")

    if not os.path.isfile(launcher_path):
        print(f"[run] Launcher module not found at {launcher_path}", file=sys.stderr)
        sys.exit(1)

    print(f"[run] Loading launcher from {launcher_path}")
    # Load under a unique name so it never collides with the opensquad.launcher
    # package (which would shadow the real module if imported normally).
    spec = importlib.util.spec_from_file_location(
        "_opensquad_launcher_entry",
        launcher_path,
    )
    if spec is None or spec.loader is None:
        print(f"[run] Failed to create import spec for {launcher_path}", file=sys.stderr)
        sys.exit(1)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    if not hasattr(mod, "main"):
        print(f"[run] {launcher_path} has no main() — not a valid launcher entry", file=sys.stderr)
        sys.exit(1)

    # Re-export the module so code that does `from opensquad import launcher`-
    # style lookups (none currently, but keeps the runtime sane) can find it.
    sys.modules["_opensquad_launcher_entry"] = mod

    print("==========================================")
    print("   OpenSquad Launcher Starting...")
    print(f"   Frozen: {IS_FROZEN}")
    print("==========================================")
    mod.main()


def run_agent():
    """Run an agent boot process (frozen-mode agent spawn target).

    The launcher spawns ``run.exe --service agent --agent-dir <dir> --port <n>``
    instead of ``<external-python> -m opensquad.agents_boot ...`` because an
    external Python (embeddable or system) cannot ``import opensquad`` —
    PyInstaller compiles ``.py`` into the PYZ archive, not onto disk.

    The frozen ``run.exe`` has the full ``opensquad`` package in its PYZ, so
    it can run ``agents_boot.main()`` directly.  ``--service agent`` is already
    stripped from argv by ``_parse_service_arg``, leaving ``--agent-dir`` and
    ``--port`` for agents_boot's own argparse.
    """
    import argparse
    import asyncio
    import time
    import traceback

    from opensquad.agents_boot import main as agent_main

    parser = argparse.ArgumentParser(description="Boot an AI agent from config")
    parser.add_argument("--agent-dir", required=True, help="Path to agent directory containing config.json")
    parser.add_argument("--port", type=int, help="Override web server port")
    args = parser.parse_args()

    try:
        asyncio.run(agent_main(args.agent_dir, override_port=args.port))
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception:
        # Agent boot crashed before its logger was wired up. The launcher's
        # _forward_logs captures stdout/stderr, but on a fresh CI runner the
        # process can exit so fast that the log thread hasn't drained the pipe.
        # Write the traceback to BOTH stderr (best effort, picked up by the
        # launcher log buffer) and a workspace crash file so smoke tests and
        # post-mortem debugging can see why the agent died.
        tb = traceback.format_exc()
        print(f"[run_agent] FATAL: agent boot crashed\n{tb}", file=sys.stderr, flush=True)
        try:
            from opensquad.system_config import syscfg as _syscfg_for_crash

            crash_dir = _syscfg_for_crash.workspace_logs_dir("agent_crash")
            os.makedirs(crash_dir, exist_ok=True)
            crash_file = os.path.join(
                crash_dir,
                f"crash_{os.path.basename(args.agent_dir)}_{int(time.time())}.log",
            )
            with open(crash_file, "w", encoding="utf-8") as f:
                f.write(f"agent_dir={args.agent_dir}\nport={args.port}\n\n{tb}")
            print(f"[run_agent] crash log written to {crash_file}", file=sys.stderr, flush=True)
        except Exception:
            pass
        raise


def run_playwright_install(argv: list[str] | None = None):
    """Install Playwright browser binary using the bundled playwright driver.

    Called via ``run.exe --service playwright-install [chromium]``.

    When no system Python 3.11 is available, plugin services fall back to
    running on the frozen ``run.exe``, which uses the bundled playwright
    package.  ``_ensure_playwright_browser()`` cannot use ``run.exe -m
    playwright install`` because run.exe's entry point does not support
    ``-m`` — it would start the gateway instead.  This service mode
    invokes playwright's internal Node driver directly so the browser
    revision matches the bundled playwright version.
    """
    import subprocess

    browsers = (argv or ["chromium"]) if argv else ["chromium"]
    # Filter out empty strings
    browsers = [b for b in browsers if b]

    try:
        from playwright._impl._driver import compute_driver_executable
    except ImportError as e:
        print(f"[playwright-install] playwright not bundled: {e}", file=sys.stderr)
        sys.exit(1)

    driver = compute_driver_executable()
    print(f"[playwright-install] driver: {driver}")
    print(f"[playwright-install] installing: {browsers}")

    r = subprocess.run([str(driver), "install"] + browsers)
    if r.returncode != 0:
        print(f"[playwright-install] failed (exit {r.returncode})", file=sys.stderr)
    else:
        print("[playwright-install] success")
    sys.exit(r.returncode)


if __name__ == "__main__":
    # ── Force UTF-8 for stdout/stderr on Windows before any output ──
    # Without this, uvicorn / Python error messages containing Chinese
    # (e.g. OS error descriptions) are printed as garbled GBK bytes in a
    # UTF-8 terminal.
    #
    # NOTE: We intentionally do NOT set PYTHONUTF8 / PYTHONIOENCODING as
    # environment variables here.  uvicorn's watchfiles reloader spawns a
    # child Python process that inherits the environment.  On machines where
    # site-packages contain a GBK-encoded .pth file (common with some Chinese
    # Python installs), PYTHONUTF8=1 causes site.py to crash with
    # UnicodeDecodeError before the child can start.  Direct stream
    # reconfigure is sufficient for the parent process without poisoning
    # child processes.
    if sys.platform == "win32":
        for _s in (sys.stdout, sys.stderr):
            if hasattr(_s, "reconfigure"):
                with contextlib.suppress(Exception):
                    _s.reconfigure(encoding="utf-8", errors="replace")

    # ── Dispatch on --service ──────────────────────────────────────────────
    # Single frozen binary serves multiple processes: the desktop app spawns
    # `run.exe --service launcher ...` alongside the plain `run.exe` gateway.
    # Strip the --service pair from argv so the chosen service sees only its
    # own flags, then hand off. Anything below this block is gateway-only.
    # Electron sets OPENSQUAD_USER_DATA / OPENSQUAD_APP_DATA before spawn.
    # Mirror into OPENSQUAD_WORKSPACE so _syscfg/_workspace.py resolves a
    # writable path before any opensquad import creates data/ under _internal/.
    if IS_FROZEN:
        _ws = os.environ.get("OPENSQUAD_USER_DATA", "").strip() or os.environ.get("OPENSQUAD_APP_DATA", "").strip()
        if _ws:
            os.environ.setdefault("OPENSQUAD_WORKSPACE", os.path.abspath(_ws))

    service, _forward_argv = _parse_service_arg()
    # Make sure the launcher's argparse (which reads sys.argv) doesn't see the
    # --service flag we already consumed.
    sys.argv = _forward_argv
    if service == "launcher":
        run_launcher()
        sys.exit(0)
    if service == "agent":
        run_agent()
        sys.exit(0)
    if service == "playwright-install":
        run_playwright_install(_forward_argv)
        sys.exit(0)
    # service == "gateway" (or anything else) → fall through to gateway startup

    try:
        config = load_config()
        backend_config = config.get("backend", {})
        print("[Config] Loaded config from gateway/config.json")
    except Exception as e:
        print(f"[Config] Failed to load config: {e}")
        backend_config = {}

    # Frozen mode: Electron passes port and data directory via environment variables
    if IS_FROZEN:
        port = int(os.environ.get("OPENSQUAD_PORT", backend_config.get("port", 9555)))
        host = os.environ.get("OPENSQUAD_HOST", backend_config.get("host", "0.0.0.0"))
        # Write userData directory to env so app/main.py can read it
        user_data = os.environ.get("OPENSQUAD_USER_DATA", BACKEND_DIR)
        os.environ.setdefault("OPENSQUAD_USER_DATA", user_data)
    else:
        port = backend_config.get("port", 9555)
        host = backend_config.get("host", "0.0.0.0")

    print("==========================================")
    print("   OpenSquad Backend Starting...")
    print(f"   Host: {host}")
    print(f"   Port: {port}")
    print(f"   Frozen: {IS_FROZEN}")
    print("==========================================")

    # Disable uvicorn hot-reload in frozen mode (reloader forks subprocesses; PyInstaller doesn't support that)
    # Electron can also force-disable reload via OPENSQUAD_RELOAD=0
    enable_reload = (
        not IS_FROZEN and os.environ.get("OPENSQUAD_RELOAD", "1") != "0" and backend_config.get("reload", True)
    )

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=enable_reload,
        reload_dirs=[BACKEND_DIR] if enable_reload else None,
        log_level=backend_config.get("log_level", "warning"),
        access_log=False,
    )
