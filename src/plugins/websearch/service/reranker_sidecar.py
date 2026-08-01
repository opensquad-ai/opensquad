"""Spawn / stop the Qwen3-Reranker sidecar alongside the websearch service."""

from __future__ import annotations

import atexit
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

_reranker_proc: subprocess.Popen | None = None


def _env_truthy(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in {"0", "false", "no", "off"}


def _reranker_port() -> int:
    raw = os.environ.get("WEBSEARCH_RERANKER_PORT", "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    url = os.environ.get("WEBSEARCH_RERANKER_URL", "http://127.0.0.1:8111").rstrip("/")
    try:
        # http://host:port[/...]
        hostport = url.split("://", 1)[-1].split("/", 1)[0]
        if ":" in hostport:
            return int(hostport.rsplit(":", 1)[-1])
    except ValueError:
        pass
    return 8111


def _reranker_base_url() -> str:
    return os.environ.get("WEBSEARCH_RERANKER_URL", f"http://127.0.0.1:{_reranker_port()}").rstrip("/")


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def _health_ok(timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"{_reranker_base_url()}/health", timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


def _model_path() -> str:
    override = os.environ.get("WEBSEARCH_RERANKER_MODEL_PATH", "").strip()
    if override:
        return override
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(
        here,
        "reranker",
        "models",
        "models--Qwen--Qwen3-Reranker-0.6B",
        "snapshots",
        "e61197ed45024b0ed8a2d74b80b4d909f1255473",
    )


def start_reranker_sidecar() -> None:
    """Best-effort start of the local :8111 reranker. Never blocks websearch boot."""
    global _reranker_proc

    if not _env_truthy("WEBSEARCH_RERANKER_ENABLED", "1"):
        print("[WebSearch] Reranker sidecar disabled (WEBSEARCH_RERANKER_ENABLED=0)")
        return

    port = _reranker_port()
    os.environ.setdefault("WEBSEARCH_RERANKER_URL", f"http://127.0.0.1:{port}")
    os.environ.setdefault("WEBSEARCH_RERANKER_PORT", str(port))

    if _health_ok():
        print(f"[WebSearch] Reranker already healthy at {_reranker_base_url()}")
        _start_guardian()
        return

    if _port_open(port):
        print(
            f"[WebSearch] Port {port} in use but /health failed; "
            "not spawning another reranker (websearch will fall back to Bing order)"
        )
        _start_guardian()
        return

    model = _model_path()
    if not os.path.isdir(model):
        print(
            f"[WebSearch] Reranker model missing at {model}; "
            "skipping sidecar (set WEBSEARCH_RERANKER_MODEL_PATH to override)"
        )
        return

    for mod in ("torch", "transformers"):
        try:
            __import__(mod)
        except ImportError:
            print(
                f"[WebSearch] Reranker deps missing ({mod}); "
                "skipping sidecar until plugin pip deps install (torch, transformers)"
            )
            return

    deploy_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reranker", "deploy.py")
    if not os.path.isfile(deploy_py):
        print(f"[WebSearch] Reranker entry missing: {deploy_py}")
        return

    env = os.environ.copy()
    env["WEBSEARCH_RERANKER_MODEL_PATH"] = model
    env["WEBSEARCH_RERANKER_PORT"] = str(port)
    env.setdefault("WEBSEARCH_RERANKER_HOST", "127.0.0.1")
    # Ensure UTF-8 logs on Windows consoles.
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")

    try:
        _reranker_proc = subprocess.Popen(
            [sys.executable, deploy_py],
            cwd=os.path.dirname(deploy_py),
            env=env,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    except Exception as e:
        print(f"[WebSearch] Failed to spawn reranker sidecar: {e}")
        _reranker_proc = None
        return

    atexit.register(stop_reranker_sidecar)
    print(f"[WebSearch] Reranker sidecar started (pid={_reranker_proc.pid}, port={port})")

    # Model load can take tens of seconds; wait briefly then continue either way.
    deadline = time.time() + float(os.environ.get("WEBSEARCH_RERANKER_WAIT_S", "90") or "90")
    while time.time() < deadline:
        if _reranker_proc.poll() is not None:
            print(
                f"[WebSearch] Reranker sidecar exited early (code={_reranker_proc.returncode}); "
                "search will keep Bing order"
            )
            _reranker_proc = None
            break
        if _health_ok(timeout=2.0):
            print(f"[WebSearch] Reranker ready at {_reranker_base_url()}")
            _start_guardian()
            return
        time.sleep(1.0)
    print(
        "[WebSearch] Reranker still loading after wait; websearch continues "
        "(rerank will activate once /health succeeds)"
    )
    _start_guardian()


# ── Guardian: auto-restart the reranker if it dies mid-session ─────────
_guardian_started = False
_guardian_lock = threading.Lock()
_guardian_interval = float(os.environ.get("WEBSEARCH_RERANKER_GUARD_S", "30") or "30")
_guardian_debounce = float(os.environ.get("WEBSEARCH_RERANKER_GUARD_DEBOUNCE_S", "120") or "120")
_last_guard_restart = 0.0


def _guardian_loop() -> None:
    global _last_guard_restart
    while True:
        time.sleep(_guardian_interval)
        if not _env_truthy("WEBSEARCH_RERANKER_ENABLED", "1"):
            return
        # If the port is serving and healthy, nothing to do.
        if _health_ok(timeout=1.5):
            continue
        # Port open but /health failed (loading / wedged) — wait it out.
        if _port_open(_reranker_port()):
            continue
        # Port closed: our sidecar died. Restart unless we just did (debounce).
        now = time.time()
        if now - _last_guard_restart < _guardian_debounce:
            continue
        print("[WebSearch] Reranker guardian: process gone, respawning…")
        _last_guard_restart = now
        start_reranker_sidecar()


def _start_guardian() -> None:
    global _guardian_started
    with _guardian_lock:
        if _guardian_started:
            return
        try:
            t = threading.Thread(target=_guardian_loop, name="reranker-guardian", daemon=True)
            t.start()
            _guardian_started = True
            print("[WebSearch] Reranker guardian started")
        except Exception as e:
            print(f"[WebSearch] Reranker guardian start failed: {e}")


def stop_reranker_sidecar() -> None:
    """Terminate the sidecar we spawned (no-op if we attached to an existing one)."""
    global _reranker_proc
    proc = _reranker_proc
    _reranker_proc = None
    if proc is None or proc.poll() is not None:
        return
    print(f"[WebSearch] Stopping reranker sidecar (pid={proc.pid})")
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
        else:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
    except Exception as e:
        print(f"[WebSearch] Reranker stop error: {e}")
