"""SenseVoice model paths, status, and download (workspace-local, not bundled)."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger("plugins.sensevoice.model_store")

# Core files required for ONNX INT8 inference (SenseVoice-Small).
REQUIRED_FILES = (
    "model_quant.onnx",
    "tokens.json",
    "am.mvn",
    "config.yaml",
)

OPTIONAL_FILES = ("configuration.json",)

# ModelScope raw resolve URLs (master branch of iic/SenseVoiceSmall).
MODELSCOPE_BASE = "https://www.modelscope.cn/models/iic/SenseVoiceSmall/resolve/master"

_download_lock = threading.Lock()
_download_thread: threading.Thread | None = None


def _workspace_root() -> str:
    """Resolve active workspace without importing opensquad (service-safe)."""
    try:
        from plugins._service_runtime import get_workspace

        return get_workspace()
    except Exception:
        pass
    try:
        from opensquad.system_config import syscfg

        return syscfg.get_workspace()
    except Exception:
        pass
    for key in ("OPENSQUAD_WORKSPACE", "OPENSQUAD_USER_DATA", "OPENSQUAD_APP_DATA"):
        raw = os.environ.get(key, "").strip()
        if raw:
            return os.path.abspath(raw)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _candidate_model_dirs() -> list[str]:
    """Preferred then legacy locations for SenseVoice model files."""
    root = _workspace_root()
    return [
        os.path.join(root, "data", "plugins", "sensevoice", "model"),
        # Pre-plugin / manual deploy path used during SenseVoice bring-up docs
        os.path.join(root, "workspace", "sensevoice", "model"),
        os.path.join(root, "sensevoice", "model"),
    ]


def model_dir() -> str:
    """Return the SenseVoice model directory (created on demand).

    Preference:
      1. ``{workspace}/data/plugins/sensevoice/model`` (plugin download target)
      2. Existing ready dirs under ``workspace/sensevoice/model`` etc. (legacy)
    """
    for path in _candidate_model_dirs():
        if model_ready(path):
            return path
    primary = _candidate_model_dirs()[0]
    os.makedirs(primary, exist_ok=True)
    return primary


def status_path() -> str:
    return os.path.join(_workspace_root(), "data", "plugins", "sensevoice", "download_status.json")


def _write_status(payload: dict[str, Any]) -> None:
    path = status_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {**payload, "updated_at": time.time()}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def read_status() -> dict[str, Any]:
    path = status_path()
    if not os.path.isfile(path):
        return {"state": "idle", "message": "", "progress": 0.0, "file": ""}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {"state": "idle", "message": "", "progress": 0.0, "file": ""}


def model_ready(directory: str | None = None) -> bool:
    root = directory or model_dir()
    return all(os.path.isfile(os.path.join(root, name)) for name in REQUIRED_FILES)


def model_file_sizes(directory: str | None = None) -> dict[str, int]:
    root = directory or model_dir()
    out: dict[str, int] = {}
    for name in (*REQUIRED_FILES, *OPTIONAL_FILES):
        p = os.path.join(root, name)
        if os.path.isfile(p):
            try:
                out[name] = os.path.getsize(p)
            except OSError:
                out[name] = 0
    return out


def get_status() -> dict[str, Any]:
    ready = model_ready()
    dl = read_status()
    return {
        "ready": ready,
        "model_dir": model_dir(),
        "files": model_file_sizes(),
        "required_files": list(REQUIRED_FILES),
        "download": dl,
        "missing": [n for n in REQUIRED_FILES if not os.path.isfile(os.path.join(model_dir(), n))],
    }


def _download_one(url: str, dest: str, file_index: int, total_files: int) -> None:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "OpenSquad-SenseVoice/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        chunk = 1024 * 256
        with open(tmp, "wb") as out:
            while True:
                buf = resp.read(chunk)
                if not buf:
                    break
                out.write(buf)
                done += len(buf)
                file_frac = (done / total) if total else 0.0
                overall = (file_index + file_frac) / max(total_files, 1)
                _write_status(
                    {
                        "state": "downloading",
                        "message": f"Downloading {os.path.basename(dest)}",
                        "file": os.path.basename(dest),
                        "progress": round(min(overall, 0.99) * 100, 1),
                        "bytes_done": done,
                        "bytes_total": total,
                    }
                )
    os.replace(tmp, dest)


def _run_download() -> None:
    root = model_dir()
    files = list(REQUIRED_FILES) + [f for f in OPTIONAL_FILES if f not in REQUIRED_FILES]
    try:
        _write_status(
            {
                "state": "downloading",
                "message": "Starting SenseVoice model download from ModelScope…",
                "file": "",
                "progress": 0.0,
            }
        )
        # Prefer modelscope SDK when available (handles LFS / auth better).
        try:
            from modelscope.hub.snapshot_download import snapshot_download

            _write_status(
                {
                    "state": "downloading",
                    "message": "Downloading via ModelScope SDK…",
                    "file": "",
                    "progress": 5.0,
                }
            )
            snapshot_download("iic/SenseVoiceSmall", local_dir=root)
            if model_ready(root):
                _write_status(
                    {
                        "state": "ready",
                        "message": "Model downloaded successfully",
                        "file": "",
                        "progress": 100.0,
                    }
                )
                return
            logger.warning("[sensevoice] modelscope download incomplete; falling back to HTTP")
        except Exception as e:
            logger.info("[sensevoice] modelscope SDK unavailable or failed (%s); using HTTP", e)

        total = len(files)
        for i, name in enumerate(files):
            dest = os.path.join(root, name)
            if os.path.isfile(dest) and os.path.getsize(dest) > 0:
                continue
            url = f"{MODELSCOPE_BASE}/{name}"
            _download_one(url, dest, i, total)

        if not model_ready(root):
            missing = [n for n in REQUIRED_FILES if not os.path.isfile(os.path.join(root, n))]
            _write_status(
                {
                    "state": "error",
                    "message": f"Download incomplete, missing: {', '.join(missing)}",
                    "file": "",
                    "progress": 0.0,
                }
            )
            return

        _write_status(
            {
                "state": "ready",
                "message": "Model downloaded successfully",
                "file": "",
                "progress": 100.0,
            }
        )
    except Exception as e:
        logger.exception("[sensevoice] download failed")
        _write_status(
            {
                "state": "error",
                "message": str(e),
                "file": "",
                "progress": 0.0,
            }
        )
    finally:
        global _download_thread
        with _download_lock:
            _download_thread = None


def start_download(*, force: bool = False) -> dict[str, Any]:
    """Start background model download. Idempotent while a download is running."""
    global _download_thread
    if model_ready() and not force:
        _write_status(
            {
                "state": "ready",
                "message": "Model already present",
                "file": "",
                "progress": 100.0,
            }
        )
        return get_status()

    with _download_lock:
        if _download_thread and _download_thread.is_alive():
            return {**get_status(), "started": False, "message": "Download already in progress"}
        if force:
            # Remove required files so re-download replaces them.
            for name in REQUIRED_FILES:
                p = os.path.join(model_dir(), name)
                try:
                    if os.path.isfile(p):
                        os.remove(p)
                except OSError:
                    pass
        t = threading.Thread(target=_run_download, name="sensevoice-download", daemon=True)
        _download_thread = t
        t.start()
    return {**get_status(), "started": True, "message": "Download started"}
