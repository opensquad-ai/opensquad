"""opensquad web — ensure services + open the NexusChat Pro web UI."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import webbrowser
from argparse import Namespace


def _port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))


def _wait_port(port: int, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    delay = 0.08
    while time.time() < deadline:
        if _port_open("127.0.0.1", port):
            return True
        time.sleep(delay)
        delay = min(delay * 1.35, 0.5)
    return False


def _ensure_frontend(vite_port: int) -> bool:
    """Start Vite dev server if down. Returns True when port is listening."""
    if _port_open("127.0.0.1", vite_port):
        return True

    from opensquad.cli.commands.start_cmd import _find_npm
    from opensquad.cli.win_process import detach_popen_kwargs

    root = _repo_root()
    frontend_dir = os.path.join(root, "src", "opensquad", "gateway", "nexuschat-pro")
    if not os.path.isfile(os.path.join(frontend_dir, "package.json")):
        print("[web] Frontend package.json not found — will try Gateway static UI", file=sys.stderr)
        return False

    npm_exe = _find_npm()
    print(f"[web] Starting frontend (Vite :{vite_port})…")
    try:
        subprocess.Popen(
            [npm_exe, "run", "dev"],
            cwd=frontend_dir,
            **detach_popen_kwargs(),
        )
    except FileNotFoundError:
        print("[web] npm not found. Install Node.js, or use a built frontend via Gateway.", file=sys.stderr)
        return False
    except OSError as e:
        print(f"[web] Failed to start frontend: {e}", file=sys.stderr)
        return False

    if not _wait_port(vite_port, timeout=90.0):
        print(f"[web] Frontend port {vite_port} not ready", file=sys.stderr)
        return False
    return True


def _gateway_static_available(gateway_url: str) -> bool:
    """True when Gateway serves the SPA (built dist / frozen desktop)."""
    import httpx

    try:
        r = httpx.get(f"{gateway_url.rstrip('/')}/", timeout=3.0, follow_redirects=True)
        # Vite SPA index or gateway static — avoid bare 404 JSON APIs
        ct = (r.headers.get("content-type") or "").lower()
        return r.status_code == 200 and ("text/html" in ct or "<!doctype" in (r.text or "")[:200].lower())
    except Exception:
        return False


def run_web(args: Namespace) -> None:
    """Ensure daemon stack + frontend, then open the browser."""
    from opensquad.cli.api_client import resolve_gateway_url
    from opensquad.cli.runtime_boot import ensure_services
    from opensquad.system_config import syscfg

    no_start = bool(getattr(args, "no_start", False))
    no_browser = bool(getattr(args, "no_browser", False))
    vite_port = int(syscfg.port("frontend") or 5173)
    gateway_url = resolve_gateway_url(getattr(args, "gateway", None))

    if not no_start:
        print("[web] Ensuring Gateway + Launcher…")
        if not ensure_services(quiet=False):
            print(
                "[web] Core services failed. Try: opensquad doctor  (or opensquad stop then opensquad web)",
                file=sys.stderr,
            )
            raise SystemExit(1)

    url: str | None = None

    # Prefer Vite only for development (--dev) or when the built frontend is
    # missing; otherwise serve the built static UI directly from Gateway to
    # avoid the 5-15s Vite cold start on every `opensquad web`.
    dev_mode = bool(getattr(args, "dev", False))
    vite_up = _port_open("127.0.0.1", vite_port)
    dist_index = os.path.join(_repo_root(), "src", "opensquad", "gateway", "nexuschat-pro", "dist", "index.html")
    use_vite = dev_mode or not os.path.isfile(dist_index)

    if use_vite and (vite_up or (not no_start and _ensure_frontend(vite_port))):
        url = f"http://127.0.0.1:{vite_port}"
    elif _gateway_static_available(gateway_url):
        url = gateway_url.rstrip("/") + "/"
        print(f"[web] Using Gateway static UI at {url}")
    else:
        print(
            "[web] No web UI available.\n"
            "  Dev: install Node.js + npm, then retry `opensquad web`\n"
            "  Or:  opensquad start   (foreground, includes Vite)\n"
            f"  Gateway: {gateway_url}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    print(f"[web] OpenSquad Web → {url}")
    if no_browser:
        return
    try:
        webbrowser.open(url)
    except Exception as e:
        print(f"[web] Could not open browser: {e}\n  Open manually: {url}", file=sys.stderr)
