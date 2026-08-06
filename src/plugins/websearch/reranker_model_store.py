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
import sys
from typing import Any

# Always import ``_model_downloader`` as a top-level module so the launcher's
# two load paths (data endpoint via import_module, action endpoint via
# spec_from_file_location) share ONE module instance — and therefore one
# ``ModelStore`` singleton.  A shared singleton is what lets the UI see a
# running download thread across requests.  Put the plugins root (where
# ``_model_downloader.py`` lives) on sys.path so it resolves everywhere.
_here = os.path.dirname(os.path.abspath(__file__))
_plugins_root = os.path.abspath(os.path.join(_here, ".."))
for _p in (_plugins_root, _here):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _model_downloader import (  # noqa: E402
    FileMirror,
    FileSpec,
    JobContext,
    ModelStore,
    fetch_with_mirrors,
    force_remove_status_file,
    force_remove_tree,
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


# Mirror endpoints. We use plain HTTP (no huggingface_hub / modelscope SDK
# required) so the download works in any environment, including the frozen
# launcher bundle where those SDKs are often absent. ``hf-mirror.com`` is
# tried first (CN-friendly), then ``huggingface.co``.
HF_MIRRORS = (
    os.environ.get("WEBSEARCH_RERANKER_HF_MIRROR", "https://hf-mirror.com"),
    "https://huggingface.co",
)

# Files the deployment needs. The weights are a single ``model.safetensors``
# (~1.2 GB) plus the tokenizer / config files.
DOWNLOAD_FILES = (
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "merges.txt",
    "vocab.json",
    "chat_template.jinja",
    "model.safetensors",
)


def _build_file_specs() -> list[FileSpec]:
    """Build (file, mirror) pairs for every file across all HF endpoints."""
    specs: list[FileSpec] = []
    for fn in DOWNLOAD_FILES:
        mirrors = tuple(
            FileMirror(
                name=fn,
                url=f"{ep.rstrip('/')}/{REPO_ID}/resolve/{REVISION}/{fn}",
                label=ep.replace("https://", ""),
            )
            for ep in HF_MIRRORS
        )
        specs.append(FileSpec(name=fn, mirrors=mirrors))
    return specs


def _download_worker(job: JobContext) -> None:
    if is_complete():
        job.mark_done("Reranker model already present")
        return
    # Stream every file with mirror fallback into the snapshot dir. Progress
    # is reported per-file so the UI shows a real progress bar.
    fetch_with_mirrors(
        job,
        _build_file_specs(),
        dest_subdir=os.path.join("snapshots", SNAPSHOT_REV),
    )
    if not is_complete():
        raise RuntimeError(
            "Reranker download finished but files are still missing. "
            "Download may be incomplete or the mirror served stale files."
        )
    job.mark_done("Qwen3-Reranker model downloaded")


# ── Public API ─────────────────────────────────────────────────────────


def start_download(*, force: bool = False) -> dict[str, Any]:
    """Start a background download. Idempotent while one is running."""
    store = _get_store()
    if is_complete() and not force:
        store.mark_ready("Reranker model already present")
        return {**get_status(), "started": False, "message": "Model already present"}
    if force:
        # Wipe snapshot so the worker re-downloads. Use the shared
        # force-remove helper so read-only / locked files don't abort
        # the reinstall (a common case in frozen _internal/ bundles).
        for snap in (_snapshot_dir(), _legacy_snapshot_dir()):
            force_remove_tree(snap)
    return store.start_download(_download_worker, force=force)


def cancel_download() -> dict[str, Any]:
    """Cancel any in-flight reranker download so the button becomes actionable.

    Returns the current status; the running thread exits at its next progress
    tick and the store returns to the idle state.
    """
    store = _get_store()
    store.cancel()
    return get_status()


def uninstall() -> dict[str, Any]:
    """Delete the downloaded reranker weights so the model is no longer ready.

    Removes both the writable workspace snapshot (ModelStore download target)
    and the legacy ``service/reranker/models`` deploy path.  In a frozen bundle
    the legacy path lives under read-only ``_internal/``, so removal is
    attempted but tolerated if the OS refuses — the workspace copy is the one
    the download UI owns.  Also clears any persisted download status so the UI
    returns to the idle "not downloaded" state.

    Any in-flight download is cancelled first so a stuck / slow thread cannot
    leave the store wedged in "Download already in progress".

    Read-only / locked files (e.g. inside a frozen ``_internal/`` bundle, or
    held open by 360 安全卫士) are tolerated: we clear the read-only bit, then
    if the OS still refuses we rename the file to ``<name>.dead_<rand>`` so
    the model is at least no longer "ready" for the service.
    """
    store = _get_store()
    store.cancel()
    removed_any = False
    notes: list[str] = []
    for snap in (_snapshot_dir(), _legacy_snapshot_dir()):
        if not os.path.isdir(snap):
            continue
        removed, info = force_remove_tree(snap)
        if removed:
            removed_any = True
            if info and info not in ("removed",):
                notes.append(f"{os.path.basename(snap)}: {info}")
        elif info and info not in ("not present",):
            notes.append(f"{os.path.basename(snap)}: {info}")
    force_remove_status_file(_status_path())
    msg = "Model uninstalled" if removed_any else "No model files to remove"
    if notes:
        msg = f"{msg} ({'; '.join(notes)})"
    return {
        **get_status(),
        "uninstalled": removed_any,
        "notes": notes,
        "message": msg,
    }


def reset_for_tests() -> None:
    """Clear cached singleton so tests can re-init with different paths."""
    global _store
    _store = None
