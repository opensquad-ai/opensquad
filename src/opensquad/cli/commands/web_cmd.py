"""opensquad web — ensure services + open the NexusChat Pro web UI."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
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


def _package_dir() -> str:
    """Directory holding the installed `opensquad` package.

    Anchored on the package rather than "up four levels from this file": the
    latter resolves relative to a repo checkout and points outside
    site-packages for a pip install.
    """
    import opensquad

    return os.path.dirname(os.path.abspath(opensquad.__file__))


def _resolve_npm() -> str:
    """Absolute path to npm, or ``''`` when it is not installed.

    :func:`start_cmd._find_npm` falls back to the literal ``"npm"``, which Windows
    ``CreateProcess`` cannot run on its own (it appends ``.exe``, never ``.cmd``),
    so a machine without npm fails later, as an odd ``FileNotFoundError``.
    Resolving here puts both failure modes in one answer.
    """
    import shutil

    return shutil.which("npm") or ""


def _npm_can_run(npm_exe: str) -> bool:
    """True when that npm can find a node to execute it.

    npm's own ``.cmd`` shim runs ``<npm folder>\\node.exe`` if it sits next to the
    shim and falls back to bare ``node`` from PATH otherwise. The fallback is what
    breaks an install whose Node folder was moved or never added to PATH: the shim
    exits immediately, and a detached child has no console left to say so in —
    which is exactly how a missing ``node`` turned into "Frontend port 5173 not
    ready" after a full 90-second wait.
    """
    import shutil

    side_by_side = os.path.join(os.path.dirname(os.path.abspath(npm_exe)), "node.exe")
    if os.path.isfile(side_by_side):
        return True
    return bool(shutil.which("node"))


def _node_exe_names() -> tuple[str, ...]:
    """What ``node`` is called on this platform."""
    return ("node.exe",) if os.name == "nt" else ("node",)


def _registered_path_dirs() -> list[str]:
    """Windows' *stored* PATH, for a terminal that predates the Node install.

    A shell opened before Node was installed keeps the old PATH for its whole
    lifetime: it still has npm's ``%APPDATA%\\npm`` shim from an earlier install
    but not the folder holding ``node.exe``. The registry value is the source
    that is up to date, so it is the one worth re-reading.
    """
    if sys.platform != "win32":
        return []

    import winreg

    out: list[str] = []
    for root, sub in (
        (winreg.HKEY_CURRENT_USER, r"Environment"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
    ):
        try:
            with winreg.OpenKey(root, sub) as key:
                stored = winreg.QueryValueEx(key, "Path")[0]
        except OSError:
            continue
        out.extend(part for part in str(stored).split(";") if part.strip())
    return out


def _node_hint_dirs() -> list[str]:
    """Folders that may hold the ``node`` this process's PATH forgot about."""
    dirs: list[str] = []

    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "APPDATA"):
        base = (os.environ.get(var) or "").strip()
        if not base:
            continue
        dirs.append(os.path.join(base, "nodejs"))
        dirs.append(os.path.join(base, "Programs", "nodejs"))
        dirs.append(os.path.join(base, "nvm"))

    symlink = (os.environ.get("NVM_SYMLINK") or "").strip()
    if symlink:
        dirs.append(symlink)
    nvm_home = (os.environ.get("NVM_HOME") or "").strip()
    if nvm_home:
        dirs.append(nvm_home)
        if os.path.isdir(nvm_home):
            try:
                dirs.extend(os.path.join(nvm_home, name) for name in os.listdir(nvm_home))
            except OSError:
                pass

    dirs.extend(_registered_path_dirs())

    seen: set[str] = set()
    unique: list[str] = []
    for raw in dirs:
        path = os.path.expandvars((raw or "").strip()).strip('"')
        if not path:
            continue
        key = os.path.normcase(os.path.normpath(path))
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _locate_node(npm_exe: str = "") -> str:
    """Absolute path to a runnable ``node``, or ``''`` when there is none.

    npm's order, widened by one step: beside the shim, then this process's PATH,
    then the folders an installed-but-uninherited Node lives in.
    """
    import shutil

    names = _node_exe_names()
    if npm_exe:
        beside = os.path.dirname(os.path.abspath(npm_exe))
        for name in names:
            candidate = os.path.join(beside, name)
            if os.path.isfile(candidate):
                return candidate
    found = shutil.which("node")
    if found:
        return found
    for folder in _node_hint_dirs():
        for name in names:
            candidate = os.path.join(folder, name)
            if os.path.isfile(candidate):
                return candidate
    return ""


def _frontend_log_path() -> str:
    """Where the dev server's output goes; ``''`` when nothing is writable.

    The child is spawned without a console, so a log file is the only channel its
    failure has. Falls back to the temp dir when the workspace has none yet — a
    missing ``node`` is worth reporting even on a machine that never started the
    stack.
    """
    from opensquad.system_config import syscfg

    try:
        ws = syscfg.get_workspace() or ""
    except Exception:
        ws = ""
    candidates = [os.path.join(ws, "data", "logs", "frontend.log")] if ws else []
    candidates.append(os.path.join(tempfile.gettempdir(), "opensquad-frontend.log"))
    for path in candidates:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8"):
                pass
        except OSError:
            continue
        return path
    return ""


def _wait_port_ready(proc: subprocess.Popen, port: int) -> bool:
    """Wait for Vite, reusing the daemon stack's fail-fast poll.

    ``runtime_boot._wait_port`` already reports "exited before the port opened"
    with the exit code and the desktop-app-conflict hint, which is the message
    this flow was missing; only the log pointer is added on top of it.
    """
    from opensquad.cli.runtime_boot import _wait_port

    return _wait_port("frontend", port, timeout=90.0, proc=proc)


def _ensure_frontend(vite_port: int) -> bool:
    """Start Vite dev server if down. Returns True when port is listening."""
    if _port_open("127.0.0.1", vite_port):
        return True

    from opensquad.cli.win_process import detach_popen_kwargs

    root = _package_dir()
    frontend_dir = os.path.join(root, "gateway", "nexuschat-pro")
    if not os.path.isfile(os.path.join(frontend_dir, "package.json")):
        print("[web] Frontend package.json not found — will try Gateway static UI", file=sys.stderr)
        return False

    npm_exe = _resolve_npm()
    if not npm_exe:
        print("[web] npm not found. Install Node.js, or use a built frontend via Gateway.", file=sys.stderr)
        return False
    child_path_prefix = ""
    if not _npm_can_run(npm_exe):
        # _npm_can_run only sees *this* process's PATH. A terminal opened before
        # Node was installed keeps a stale one: npm still resolves to a shim
        # whose bare `node` fallback finds nothing, even though Node is
        # installed and still on the registered PATH. Look past the inherited
        # environment before reporting a machine-wide absence.
        node_exe = _locate_node(npm_exe)
        if not node_exe:
            print(
                "[web] node is not on PATH, so npm cannot run the dev server. "
                "Install Node.js or add its folder to PATH, then retry.",
                file=sys.stderr,
            )
            print(f"[web]   npm resolved to: {npm_exe}", file=sys.stderr)
            print(
                "[web]   node was not found on PATH, beside npm, or in the usual install folders",
                file=sys.stderr,
            )
            return False
        node_dir = os.path.dirname(node_exe)
        sibling_npm = os.path.join(node_dir, "npm.cmd" if os.name == "nt" else "npm")
        if os.path.isfile(sibling_npm):
            # A Node install ships its own npm, which prefers the node beside it
            # — no PATH surgery needed at all.
            print(f"[web] npm's shim cannot see node; using {sibling_npm}", file=sys.stderr)
            npm_exe = sibling_npm
        else:
            print(
                f"[web] npm's shim cannot see node; adding {node_dir} to the dev server's PATH",
                file=sys.stderr,
            )
            child_path_prefix = node_dir

    print(f"[web] Starting frontend (Vite :{vite_port})…")
    log_path = _frontend_log_path()
    log_fh = None
    if log_path:
        try:
            log_fh = open(log_path, "a", encoding="utf-8")  # noqa: SIM115 - handed to the child
        except OSError:
            log_path = ""

    popen_kw = detach_popen_kwargs()
    if child_path_prefix:
        # The shim runs a bare `node`; put the folder holding it back on PATH.
        env = dict(os.environ)
        env["PATH"] = child_path_prefix + os.pathsep + env.get("PATH", "")
        popen_kw["env"] = env
    if log_fh is not None:
        # detach_popen_kwargs() points stdout/stderr at DEVNULL: without a console
        # that is where every real error about to happen would go.
        log_fh.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} opensquad web: vite dev -> :{vite_port} ===\n")
        log_fh.flush()
        popen_kw["stdout"] = log_fh
        popen_kw["stderr"] = subprocess.STDOUT

    try:
        proc = subprocess.Popen([npm_exe, "run", "dev"], cwd=frontend_dir, **popen_kw)
    except OSError as e:
        print(f"[web] Failed to start frontend: {e}", file=sys.stderr)
        return False
    finally:
        if log_fh is not None:
            log_fh.close()

    if _wait_port_ready(proc, vite_port):
        return True
    if log_path:
        print(f"[web] Dev-server output: {log_path}", file=sys.stderr)
    else:
        print("[web] Dev-server output was discarded (no writable log directory)", file=sys.stderr)
    return False


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
    dist_index = os.path.join(_package_dir(), "gateway", "nexuschat-pro", "dist", "index.html")
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
