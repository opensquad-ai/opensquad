"""SenseVoice model paths, status, and download (workspace-local, not bundled)."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any

# `_model_downloader` ships helpers (ModelStore, force_remove_file,
# force_remove_status_file) that the uninstall path needs. We import via
# the same absolute / fallback pattern used by the other plugin stores so
# that frozen / non-frozen code paths both work.
try:
    from plugins._model_downloader import (  # type: ignore[no-redef]
        force_remove_file,
        force_remove_status_file,
    )
except ImportError:
    _here = os.path.dirname(os.path.abspath(__file__))
    _plugins_dir = os.path.abspath(os.path.join(_here, "..", ".."))
    if _plugins_dir not in sys.path:
        sys.path.insert(0, _plugins_dir)
    from _model_downloader import (  # type: ignore[no-redef]
        force_remove_file,
        force_remove_status_file,
    )

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

# The official ``iic/SenseVoiceSmall`` repo does NOT ship the quantized ONNX
# file this service loads (``model_quant.onnx``) — it only ships ``model.pt``
# (PyTorch). The ONNX/INT8 export is hosted by sherpa-onnx instead, so we
# fetch the ONNX weights from its HF mirrors and keep the tokenizer / CMVN /
# config files from the official repo.
SHERPA_ONNX_REPO = "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
SHERPA_ONNX_FILENAME = "model.int8.onnx"
SHERPA_ONNX_MIRRORS: tuple[str, ...] = (
    os.environ.get("SENSEVOICE_ONNX_MIRROR", "https://hf-mirror.com"),
    "https://huggingface.co",
)

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
            # A persisted "downloading" state with no live thread means the
            # process (or the download) was interrupted — e.g. the user closed
            # the app mid-download. Reset it so the UI button becomes usable.
            if data.get("state") == "downloading" and not _download_alive():
                data["state"] = "error"
                data["message"] = "Download interrupted — click download to retry"
                data["progress"] = 0.0
                _write_status(data)
            return data
    except (OSError, ValueError):
        pass
    return {"state": "idle", "message": "", "progress": 0.0, "file": ""}


def _download_alive() -> bool:
    global _download_thread
    with _download_lock:
        return bool(_download_thread and _download_thread.is_alive())


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


def _download_file(root: str, name: str, file_index: int, total_files: int) -> None:
    """Fetch one model file, trying each mirror in order. Raises on total failure."""
    dest = os.path.join(root, name)
    if os.path.isfile(dest) and os.path.getsize(dest) > 0:
        return
    if name == "model_quant.onnx":
        # The ONNX weights are NOT in the official repo; pull from sherpa-onnx.
        urls = [
            f"{ep.rstrip('/')}/{SHERPA_ONNX_REPO}/resolve/main/{SHERPA_ONNX_FILENAME}" for ep in SHERPA_ONNX_MIRRORS
        ]
        labels = [ep.replace("https://", "") for ep in SHERPA_ONNX_MIRRORS]
    else:
        urls = [f"{MODELSCOPE_BASE}/{name}"]
        labels = ["modelscope.cn"]
    last: Exception | None = None
    for idx, (url, label) in enumerate(zip(urls, labels, strict=False)):
        try:
            _write_status(
                {
                    "state": "downloading",
                    "message": f"Downloading {name} ({label})…",
                    "file": name,
                    "progress": round((file_index / max(total_files, 1)) * 100, 1),
                }
            )
            _download_one(url, dest, file_index, total_files)
            return
        except Exception as e:  # noqa: BLE001 - try next mirror
            last = e
            logger.warning("[sensevoice] mirror %s failed for %s: %s", label, name, e)
    if last is not None:
        raise last
    raise RuntimeError(f"no mirror produced {name}")


def _run_download() -> None:
    root = model_dir()
    files = list(REQUIRED_FILES) + [f for f in OPTIONAL_FILES if f not in REQUIRED_FILES]
    try:
        _write_status(
            {
                "state": "downloading",
                "message": "Starting SenseVoice model download…",
                "file": "",
                "progress": 0.0,
            }
        )
        total = len(files)
        for i, name in enumerate(files):
            _download_file(root, name, i, total)

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


def uninstall() -> dict[str, Any]:
    """Delete the SenseVoice model files so the model is no longer ready.

    Removes required/optional files from every candidate model directory
    (workspace download target + legacy deploy paths) and clears any persisted
    download status so the UI returns to the idle "not downloaded" state.

    Read-only / locked files (e.g. inside a frozen ``_internal/`` bundle, or
    held open by 360 安全卫士) are tolerated: we clear the read-only bit, then
    if the OS still refuses we rename the file to ``<name>.dead_<rand>`` so
    the model is at least no longer "ready" for the service.
    """
    removed_any = False
    notes: list[str] = []
    for directory in _candidate_model_dirs():
        if not os.path.isdir(directory):
            continue
        for name in (*REQUIRED_FILES, *OPTIONAL_FILES):
            p = os.path.join(directory, name)
            removed, info = force_remove_file(p)
            if removed:
                removed_any = True
                if info and info != "removed":
                    notes.append(f"{name}: {info}")
            elif info and info not in ("not present",):
                notes.append(f"{name}: {info}")
    force_remove_status_file(status_path())
    msg = "Model uninstalled" if removed_any else "No model files to remove"
    if notes:
        msg = f"{msg} ({'; '.join(notes)})"
    return {
        **get_status(),
        "uninstalled": removed_any,
        "notes": notes,
        "message": msg,
    }


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
            # Remove required files so re-download replaces them. Use the
            # shared force-remove helper so read-only files in frozen
            # _internal/ bundles don't block the reinstall.
            for name in REQUIRED_FILES:
                p = os.path.join(model_dir(), name)
                force_remove_file(p)
        t = threading.Thread(target=_run_download, name="sensevoice-download", daemon=True)
        _download_thread = t
        t.start()
    return {**get_status(), "started": True, "message": "Download started"}
