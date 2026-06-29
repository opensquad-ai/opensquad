"""
Backend service startup script
Supports both normal Python execution and PyInstaller frozen bundles.
"""
import json
import os
import sys
import uvicorn

# ── PyInstaller compatibility: detect whether running in a frozen bundle ──────────────────────────
IS_FROZEN = getattr(sys, 'frozen', False)

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
        os.environ["PYTHONPATH"] = os.pathsep.join(_extra_paths + ([_existing_pythonpath] if _existing_pythonpath else []))


def load_config():
    """Load config from gateway/config.json"""
    if IS_FROZEN:
        # Frozen: config.json is next to the executable
        config_path = os.path.join(BACKEND_DIR, "config.json")
    else:
        root_dir = os.path.dirname(BACKEND_DIR)
        config_path = os.path.join(root_dir, "config.json")

    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


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
                try:
                    _s.reconfigure(encoding="utf-8", errors="replace")
                except Exception:
                    pass

    try:
        config = load_config()
        backend_config = config.get("backend", {})
        print(f"[Config] Loaded config from gateway/config.json")
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

    print(f"==========================================")
    print(f"   NexusChat Backend Starting...")
    print(f"   Host: {host}")
    print(f"   Port: {port}")
    print(f"   Frozen: {IS_FROZEN}")
    print(f"==========================================")

    # Disable uvicorn hot-reload in frozen mode (reloader forks subprocesses; PyInstaller doesn't support that)
    # Electron can also force-disable reload via OPENSQUAD_RELOAD=0
    enable_reload = (
        not IS_FROZEN
        and os.environ.get("OPENSQUAD_RELOAD", "1") != "0"
        and backend_config.get("reload", True)
    )

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=enable_reload,
        reload_dirs=[BACKEND_DIR] if enable_reload else None,
        log_level=backend_config.get("log_level", "warning"),
        access_log=False
    )
