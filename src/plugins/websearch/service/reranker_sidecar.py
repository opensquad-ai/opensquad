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


# ── Model auto-download ────────────────────────────────────────────────
# The 1.2GB weights are excluded from git. On first run (or when a deployment
# lacks them) we fetch them in the background so the reranker sidecar can
# start once the download completes. Download never blocks the websearch
# service boot; search keeps Bing order until weights exist.
#
# The actual download flow is owned by ``websearch.reranker_model_store``,
# which is the same module the admin UI uses for its "Download model"
# button — this means a user-initiated download and a first-boot
# auto-download are mutually consistent (only one runs at a time, both
# write to the same status file).
_MODEL_REPO_ID = "Qwen/Qwen3-Reranker-0.6B"
_MODEL_REVISION = "e61197ed45024b0ed8a2d74b80b4d909f1255473"


# Imported lazily so this module still loads on frozen Agent Python
# builds that don't include the model store (defensive).
def _model_store():
    try:
        from plugins.websearch.reranker_model_store import is_complete, start_download

        return is_complete, start_download
    except ImportError:
        try:
            from reranker_model_store import is_complete, start_download  # type: ignore[no-redef]

            return is_complete, start_download
        except ImportError:
            return None, None


_download_started = False
_download_lock = threading.Lock()


def _model_is_complete(model_dir: str) -> bool:
    """Minimal completeness check: the safetensors weight must be present."""
    if not os.path.isdir(model_dir):
        return False
    for name in ("config.json", "tokenizer.json"):
        if not os.path.isfile(os.path.join(model_dir, name)):
            return False
    # weights file may be split into shards
    return any(f.endswith(".safetensors") for f in os.listdir(model_dir))


def _reranker_deps_available() -> bool:
    for mod in ("torch", "transformers"):
        try:
            __import__(mod)
        except ImportError:
            return False
    return True


def _auto_download_model(model_dir: str) -> bool:
    """Return True if a download was (or already is) running / completed.

    Spawns a daemon thread so it never blocks the service; the guardian or the
    next start will pick up the model once it is on disk.

    Prefers the shared ``reranker_model_store`` (which the admin UI also uses
    and which provides multi-mirror fallback).  Falls back to a direct
    ``huggingface_hub`` call against ``hf-mirror.com`` if the model store
    module is unavailable (e.g. frozen Agent Python embed that doesn't
    bundle it).
    """
    global _download_started

    is_complete, store_start = _model_store()
    if is_complete is not None and is_complete():
        return True

    with _download_lock:
        if _download_started:
            return True
        _download_started = True

    if not _env_truthy("WEBSEARCH_RERANKER_AUTO_DOWNLOAD", "1"):
        print("[WebSearch] Reranker auto-download disabled (WEBSEARCH_RERANKER_AUTO_DOWNLOAD=0)")
        return False

    def _do_download() -> None:
        # Preferred: route through the model store (mirror chain,
        # status persistence, idempotent).  This shares state with the
        # admin UI's "Download" button.
        if store_start is not None:
            try:
                result = store_start(force=False)
                # The model_store reports its own progress; once it
                # returns, the weights are on disk and the sidecar can
                # spawn — if not, it already wrote a status file with
                # the failure reason.
                if result.get("ready") is True or result.get("started"):
                    # Re-trigger the sidecar now that weights exist.
                    try:
                        start_reranker_sidecar()
                    except Exception as e:  # pragma: no cover
                        print(f"[WebSearch] Reranker re-spawn after download failed: {e}")
                return
            except Exception as e:
                print(f"[WebSearch] Reranker model_store download failed, falling back to direct HF call: {e}")
                # fall through to legacy path

        # Legacy fallback: best-effort single-mirror download. Kept
        # around for frozen builds that don't bundle the model store.
        try:
            import huggingface_hub
        except ImportError:
            print("[WebSearch] huggingface_hub not available for model auto-download")
            return

        def _legacy() -> None:
            try:
                os.makedirs(model_dir, exist_ok=True)
                print(f"[WebSearch] Downloading {_MODEL_REPO_ID}@{_MODEL_REVISION[:8]} → {model_dir} (1.2GB)…")
                os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
                huggingface_hub.snapshot_download(
                    repo_id=_MODEL_REPO_ID,
                    revision=_MODEL_REVISION,
                    local_dir=model_dir,
                    local_dir_use_symlinks=False,
                )
                if _model_is_complete(model_dir):
                    print("[WebSearch] Reranker model downloaded; starting sidecar")
                    start_reranker_sidecar()
                else:
                    print("[WebSearch] Reranker model download incomplete; retry on next start")
            except Exception as e:
                print(f"[WebSearch] Reranker model download failed (non-fatal): {e}")

        t = threading.Thread(target=_legacy, name="reranker-model-download-legacy", daemon=True)
        t.start()
        print("[WebSearch] Reranker model download started in background (legacy fallback)")
        return True

    # Run on a tiny background thread so the calling websearch boot
    # isn't blocked even if the model_store import is slow.
    t = threading.Thread(target=_do_download, name="reranker-model-bootstrap", daemon=True)
    t.start()
    print("[WebSearch] Reranker model download scheduled")
    return True


def start_reranker_sidecar() -> None:
    """Spawn the local reranker and return without waiting for model load.

    Websearch HTTP must become ready independently of the reranker. The
    guardian polls /health and owns late readiness / restart handling.
    """
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
        print(f"[WebSearch] Reranker model missing at {model}; auto-downloading…")
        if not _auto_download_model(model):
            print(
                f"[WebSearch] Auto-download unavailable/failed for {model}. "
                "Search keeps Bing order until the weights exist. "
                'Manual deploy: python -c "from huggingface_hub import snapshot_download; '
                "snapshot_download('Qwen/Qwen3-Reranker-0.6B', local_dir=r'{model}')\" "
                "or copy the weights from a machine that has them."
            )
        return

    if not _reranker_deps_available():
        print("[WebSearch] Reranker deps missing (torch/transformers); skipping sidecar until plugin pip deps install")
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

    if _reranker_proc.poll() is not None:
        print(
            f"[WebSearch] Reranker sidecar exited early (code={_reranker_proc.returncode}); search will keep Bing order"
        )
        _reranker_proc = None
    _start_guardian()
    print("[WebSearch] Reranker loading in background; websearch stays available immediately")


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
        # Also avoid respawn if the process we spawned is still alive (it may be
        # mid-load and the port check raced/failed).
        if _reranker_proc is not None and _reranker_proc.poll() is None:
            continue
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
