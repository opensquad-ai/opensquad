"""Qwen3-Reranker-0.6B model store for the websearch plugin.

The weights (~1.2GB) are excluded from git. The store lets the launcher
expose a download UI (mirrored on the existing SenseVoice panel) and also
re-uses the same logic for first-run auto-download (see
``service/reranker_sidecar.py``).

Mirrors (in order; first one that works wins)
--------------------------------------------
1. **hf-mirror.com** — community HF mirror, fast in mainland China
2. **huggingface.co** — official HF, default outside CN
3. **modelscope.cn** — Alibaba's mirror (the Qwen repo is also published
   on ModelScope under ``Qwen/Qwen3-Reranker-0.6B``)

The first two use ``huggingface_hub.snapshot_download``; ModelScope uses
its own SDK.  We try each in turn and surface the failure reason in the
persisted status file.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from typing import Any

# Allow direct import when the file is loaded as a script (e.g. by the
# service process which is a frozen Agent Python without the `plugins`
# package context).
if __package__ in (None, ""):
    _here = os.path.dirname(os.path.abspath(__file__))
    _plugins_dir = os.path.abspath(os.path.join(_here, "..", ".."))
    if _plugins_dir not in sys.path:
        sys.path.insert(0, _plugins_dir)
    from plugins._model_downloader import (  # type: ignore[no-redef]
        ModelStore,
        hf_snapshot_via_hub,
    )
else:
    from plugins._model_downloader import (
        ModelStore,
        hf_snapshot_via_hub,
    )

logger = logging.getLogger("plugins.websearch.reranker_model")

# ── Constants ──────────────────────────────────────────────────────────

REPO_ID = "Qwen/Qwen3-Reranker-0.6B"
REVISION = "e61197ed45024b0ed8a2d74b80b4d909f1255473"
# Same snapshot subdir as the old manual deploy path so service code
# that reads from `service/reranker/models/.../snapshots/<revision>` keeps
# working.
SNAPSHOT_REV = REVISION

# Files the model needs to be considered "ready".
# We deliberately keep this small: missing shards is the most common
# incomplete-download failure mode and is easy to detect.
# NOTE: ``special_tokens_map.json`` is intentionally NOT required — HF's
# LFS/xet placeholder mechanism (`./.no_exist/`) may keep it out of the
# ``snapshots/<rev>`` dir even on a legitimate deploy, and AutoTokenizer
# reads the same special-token definitions from ``tokenizer_config.json``.
REQUIRED_FILES = (
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "merges.txt",
    "vocab.json",
    "chat_template.jinja",
)
# At least one of these weight files must be present.
WEIGHT_GLOBS = (".safetensors",)


# ── Paths ──────────────────────────────────────────────────────────────


def _plugin_data_dir() -> str:
    try:
        from plugins._service_runtime import workspace_data_dir
    except ImportError:
        from _service_runtime import workspace_data_dir  # type: ignore[no-redef]

    return workspace_data_dir("plugins", "websearch")


def model_dir() -> str:
    """Return the directory where reranker weights are stored.

    Layout::

        {workspace}/data/plugins/websearch/reranker/
            snapshots/{revision}/config.json
            snapshots/{revision}/model*.safetensors
            ...

    This matches the path the original manual deploy uses
    (``service/reranker/models/models--Qwen--Qwen3-Reranker-0.6B/snapshots/...``)
    so existing service code keeps working with no changes.
    """
    return os.path.join(_plugin_data_dir(), "reranker")


def _status_path() -> str:
    return os.path.join(_plugin_data_dir(), "reranker_model_status.json")


# ── Store singleton ───────────────────────────────────────────────────

_store: ModelStore | None = None


def _get_store() -> ModelStore:
    global _store
    if _store is None:
        _store = ModelStore(
            plugin_name="websearch-reranker",
            model_dir=model_dir(),
            status_path=_status_path(),
        )
    return _store


# ── Status ─────────────────────────────────────────────────────────────


def _snapshot_dir() -> str:
    return os.path.join(model_dir(), "snapshots", SNAPSHOT_REV)


def _legacy_snapshot_dir() -> str:
    """Path used by the pre-UI manual deploy (``service/reranker/models``).

    The reranker sidecar (``service/reranker/deploy.py``) loads weights from
    ``service/reranker/models/models--Qwen--Qwen3-Reranker-0.6B/snapshots/<rev>``,
    which is also what ships inside the frozen bundle.  ``ModelStore`` downloads
    into the writable workspace path instead, so treat either location as ready.
    """
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "service",
        "reranker",
        "models",
        f"models--{REPO_ID.replace('/', '--')}",
        "snapshots",
        SNAPSHOT_REV,
    )


def _snapshot_flat_complete(snap: str) -> bool:
    """True when a snapshot dir has all required files plus a weight file."""
    if not os.path.isdir(snap):
        return False
    for name in REQUIRED_FILES:
        if not os.path.isfile(os.path.join(snap, name)):
            return False
    return any(any(f.endswith(ext) for ext in WEIGHT_GLOBS) for f in os.listdir(snap))


def _active_snapshot_dir() -> str:
    """Return the snapshot dir that actually holds the weights (workspace first)."""
    if _snapshot_flat_complete(_snapshot_dir()):
        return _snapshot_dir()
    if _snapshot_flat_complete(_legacy_snapshot_dir()):
        return _legacy_snapshot_dir()
    return _snapshot_dir()


def is_complete() -> bool:
    """Check that the model is present in the workspace path or the legacy
    ``service/reranker/models`` deploy path."""
    return _snapshot_flat_complete(_snapshot_dir()) or _snapshot_flat_complete(_legacy_snapshot_dir())


def file_sizes(snap: str | None = None) -> dict[str, int]:
    snap = snap or _snapshot_dir()
    if not os.path.isdir(snap):
        return {}
    out: dict[str, int] = {}
    for name in os.listdir(snap):
        path = os.path.join(snap, name)
        if os.path.isfile(path):
            try:
                out[name] = os.path.getsize(path)
            except OSError:
                out[name] = 0
    return out


def missing_files(snap: str | None = None) -> list[str]:
    snap = snap or _snapshot_dir()
    if not os.path.isdir(snap):
        return list(REQUIRED_FILES)
    return [n for n in REQUIRED_FILES if not os.path.isfile(os.path.join(snap, n))]


def get_status() -> dict[str, Any]:
    active = _active_snapshot_dir()
    return {
        "ready": is_complete(),
        "model_dir": model_dir(),
        "snapshot_dir": active,
        "legacy_snapshot_dir": _legacy_snapshot_dir(),
        "files": file_sizes(active),
        "missing": missing_files(active),
        "repo_id": REPO_ID,
        "revision": SNAPSHOT_REV,
        "download": _get_store().get_status(),
    }


# ── Download worker ───────────────────────────────────────────────────


# Endpoints to try, in order. The first one that succeeds wins.
# ``HF_ENDPOINT`` and ``MODELSCOPE_ENDPOINT`` are respected by the
# respective SDKs.  We override per attempt so a single bad mirror doesn't
# poison subsequent retries.
HF_MIRRORS = (
    os.environ.get("WEBSEARCH_RERANKER_HF_MIRROR", "https://hf-mirror.com"),
    "https://huggingface.co",
)

# ModelScope SDK uses the ``MODELSCOPE_ENDPOINT`` env var when set.
_MODELSCOPE_ENDPOINT = "https://www.modelscope.cn"


def _try_modelscope(job) -> bool:
    """Try downloading via ModelScope SDK. Returns True on success."""
    try:
        from modelscope.hub.snapshot_download import snapshot_download
    except ImportError:
        logger.info("[websearch.reranker] modelscope SDK unavailable, skipping")
        return False

    job.set_message("Trying ModelScope mirror…")
    job.set_mirror(3, 3, "modelscope.cn")
    snap_dir = _snapshot_dir()
    try:
        # Force the ModelScope endpoint env var (the SDK reads it).
        os.environ["MODELSCOPE_ENDPOINT"] = _MODELSCOPE_ENDPOINT
        snapshot_download(
            REPO_ID,
            local_dir=os.path.join(model_dir(), "modelscope"),
        )
        # Move files from the modelscope path into the snapshot path the
        # service code actually reads.
        ms_dir = os.path.join(model_dir(), "modelscope")
        if os.path.isdir(ms_dir):
            os.makedirs(snap_dir, exist_ok=True)
            for entry in os.listdir(ms_dir):
                src = os.path.join(ms_dir, entry)
                dst = os.path.join(snap_dir, entry)
                if os.path.isfile(src):
                    shutil.move(src, dst)
            try:
                shutil.rmtree(ms_dir, ignore_errors=True)
            except OSError:
                pass
        return is_complete()
    except Exception as e:
        logger.warning("[websearch.reranker] modelscope failed: %s", e)
        job.set_message(f"ModelScope failed: {e}")
        return False


def _try_hf(job) -> bool:
    """Try downloading via huggingface_hub with mirror fallback."""
    job.set_message("Trying Hugging Face mirrors…")
    snap_dir = _snapshot_dir()

    def _progress_cb() -> None:
        # huggingface_hub lacks a streaming progress hook we can wire up
        # without forking the SDK; the UI just sees "Downloading" until
        # the call returns. We bump the message periodically instead.
        job.set_message("Hugging Face download in progress…")

    for idx, endpoint in enumerate(HF_MIRRORS, start=1):
        label = endpoint.replace("https://", "")
        job.set_mirror(idx, len(HF_MIRRORS), label)
        try:
            hf_snapshot_via_hub(
                REPO_ID,
                REVISION,
                snap_dir,
                endpoints=[endpoint],
            )
            if is_complete():
                return True
            # Incomplete download — try the next mirror.
            logger.warning(
                "[websearch.reranker] HF mirror %s returned incomplete files",
                endpoint,
            )
        except Exception as e:
            logger.warning(
                "[websearch.reranker] HF mirror %s failed: %s",
                endpoint,
                e,
            )
            job.set_message(f"Mirror {idx}/{len(HF_MIRRORS)} ({label}) failed: {e}")
        _progress_cb()
    return False


def _download_worker(job) -> None:
    if is_complete():
        job.mark_done("Reranker model already present")
        return
    # Try the mirror chain. First success wins.
    if _try_hf(job):
        job.mark_done("Qwen3-Reranker model downloaded")
        return
    if _try_modelscope(job):
        job.mark_done("Qwen3-Reranker model downloaded (ModelScope)")
        return
    raise RuntimeError(
        "All reranker mirrors failed. "
        "Check the network (some endpoints require login from outside CN) "
        "or set WEBSEARCH_RERANKER_HF_MIRROR to a known-good endpoint."
    )


# ── Public API ─────────────────────────────────────────────────────────


def start_download(*, force: bool = False) -> dict[str, Any]:
    """Start a background download. Idempotent while one is running."""
    store = _get_store()
    if is_complete() and not force:
        store.mark_ready("Reranker model already present")
        return {**get_status(), "started": False, "message": "Model already present"}
    if force:
        # Wipe snapshot so the worker re-downloads.
        snap = _snapshot_dir()
        try:
            if os.path.isdir(snap):
                shutil.rmtree(snap, ignore_errors=True)
        except OSError:
            pass
    return store.start_download(_download_worker, force=force)


def reset_for_tests() -> None:
    """Clear cached singleton so tests can re-init with different paths."""
    global _store
    _store = None
