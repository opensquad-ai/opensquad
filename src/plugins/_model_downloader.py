"""Reusable model download helper for plugin services.

Provides a small, dependency-light scaffold for plugin services that need to
fetch large model weights from the internet (e.g. Qwen3-Reranker for websearch,
Whisper .pt for ASR, SenseVoice ONNX for ASR).

Goals
-----
- **Single download path** per plugin: a ``ModelStore`` instance owns the
  target directory, the persisted status file, and a one-at-a-time download
  worker thread.
- **Mirror fallback**: callers pass a list of mirrors (each describes how to
  fetch one model file). On a network/HTTP error (401/403/timeout/connection
  refused/...) we automatically try the next mirror — the user sees a status
  line like ``Trying mirror 2/3: https://hf-mirror.com``.
- **No silent failure**: download errors are written to the status file with
  the underlying exception text, and exposed via ``get_status()`` so the
  frontend can render a retry button.
- **No heavy deps**: status persistence is just JSON. The actual download
  function is injected by the caller (so we can use ``huggingface_hub``,
  ``modelscope``, raw ``urllib``, etc. as appropriate).

Typical usage
-------------
::

    from plugins._model_downloader import ModelStore, FileMirror, huggingface_mirror

    store = ModelStore(
        plugin_name="websearch",
        model_dir=Path("/path/to/data/plugins/websearch/reranker"),
        status_path=Path("/path/to/data/plugins/websearch/model_status.json"),
    )

    mirrors = [
        huggingface_mirror("Qwen/Qwen3-Reranker-0.6B", "e61197...",
                            filenames=["config.json", "model.safetensors", ...]),
        # …more mirrors
    ]

    def worker() -> None:
        with store.job() as job:
            for f in files:
                job.set_file(f)
                fetch_with_mirror(job, f, mirrors, subdir="snapshots/abc")

    store.start_download(worker)
"""

from __future__ import annotations

import json
import logging
import os
import random
import stat
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

logger = logging.getLogger("plugins.model_downloader")


class DownloadCancelled(Exception):  # noqa: N818  (kept for backward-compat references)
    """Raised when a download is cancelled (uninstall or force re-download).

    The worker checks a cancel flag between read chunks and raises this so a
    stuck / slow download thread can be stopped instead of blocking the store
    with a permanent "Download already in progress".
    """


# ── Mirror spec ─────────────────────────────────────────────────────────
# A single file on a single mirror. The downloader tries the same file
# across all mirrors before declaring the file failed.


@dataclass(frozen=True)
class FileMirror:
    """One (file, mirror) pair.

    ``url`` is the full URL to GET. ``headers`` is an optional dict of
    extra HTTP headers. ``name`` is the destination filename inside
    the model dir.
    """

    name: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    label: str = ""  # human-readable label, e.g. "hf-mirror.com"


@dataclass(frozen=True)
class FileSpec:
    """All the mirrors that can provide a single file."""

    name: str
    mirrors: tuple[FileMirror, ...]

    def first_mirror(self) -> FileMirror:
        return self.mirrors[0]


def huggingface_mirror(
    repo_id: str,
    revision: str,
    filenames: Sequence[str],
    endpoint: str,
    label: str = "",
) -> list[FileSpec]:
    """Build a list of FileSpecs for the same files on a single HF endpoint.

    ``endpoint`` is e.g. ``https://hf-mirror.com`` or ``https://huggingface.co``.
    The resulting FileSpec carries N mirrors, one per filename, all from the
    same endpoint — i.e. callers compose a higher-level mirror fallback by
    producing multiple such lists (one per endpoint) and feeding them into
    ``fetch_with_mirrors``.
    """
    label = label or endpoint
    specs: list[FileSpec] = []
    for fn in filenames:
        url = f"{endpoint.rstrip('/')}/{repo_id}/resolve/{revision}/{fn}"
        specs.append(
            FileSpec(
                name=fn,
                mirrors=(FileMirror(name=fn, url=url, label=label),),
            )
        )
    return specs


# ── Status ──────────────────────────────────────────────────────────────


@dataclass
class DownloadStatus:
    state: str = "idle"  # idle | downloading | ready | error
    message: str = ""
    progress: float = 0.0  # 0..100
    file: str = ""
    bytes_done: int = 0
    bytes_total: int = 0
    source: str = ""  # currently active mirror label
    started_at: float = 0.0
    updated_at: float = 0.0
    error: str = ""
    # Per-mirror attempt counter, used for "Trying mirror 2/3 …"
    mirror_index: int = 0
    mirror_total: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "message": self.message,
            "progress": round(self.progress, 1),
            "file": self.file,
            "bytes_done": self.bytes_done,
            "bytes_total": self.bytes_total,
            "source": self.source,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "mirror_index": self.mirror_index,
            "mirror_total": self.mirror_total,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DownloadStatus:
        return cls(
            state=d.get("state", "idle"),
            message=d.get("message", ""),
            progress=float(d.get("progress", 0.0)),
            file=d.get("file", ""),
            bytes_done=int(d.get("bytes_done", 0)),
            bytes_total=int(d.get("bytes_total", 0)),
            source=d.get("source", ""),
            started_at=float(d.get("started_at", 0.0)),
            updated_at=float(d.get("updated_at", 0.0)),
            error=d.get("error", ""),
            mirror_index=int(d.get("mirror_index", 0)),
            mirror_total=int(d.get("mirror_total", 0)),
        )


# ── Persistent status I/O ───────────────────────────────────────────────


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("[model_downloader] status write failed: %s", e)


# ── Model store ─────────────────────────────────────────────────────────


class ModelStore:
    """One model directory + persisted download status.

    A single ``start_download(worker_fn)`` call kicks off a daemon thread
    that runs ``worker_fn(job)`` with a ``JobContext`` that lets the worker
    report progress, swap mirrors, and surface errors. ``start_download`` is
    idempotent: concurrent calls during an in-flight download return
    ``{"started": False, "message": "Download already in progress"}``.

    Parameters
    ----------
    plugin_name
        Used in worker thread names and log lines (no path resolution).
    model_dir
        Directory where model files live. Created on demand.
    status_path
        JSON file where the current DownloadStatus is persisted.
    """

    def __init__(
        self,
        *,
        plugin_name: str,
        model_dir: str | os.PathLike[str],
        status_path: str | os.PathLike[str],
    ) -> None:
        self.plugin_name = plugin_name
        self.model_dir = Path(model_dir)
        self.status_path = Path(status_path)

        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()

    # ── public status / control API ──

    def get_status(self) -> dict[str, Any]:
        payload = _read_json(self.status_path)
        if not payload:
            return DownloadStatus().to_dict()
        st = DownloadStatus.from_dict(payload)
        # A persisted "downloading" state with no live thread means the
        # process (or the download) was interrupted — e.g. the user closed
        # the app mid-download. Reset it so the UI doesn't sit on a forever
        # spinner and the download button becomes actionable again.
        if st.state == "downloading" and not self.is_running():
            st.state = "error"
            st.error = "Previous download was interrupted"
            st.message = "Download interrupted — click download to retry"
            st.progress = 0.0
            self._persist(st)
        return st.to_dict()

    def _persist(self, st: DownloadStatus) -> None:
        st.updated_at = time.time()
        _write_json(self.status_path, st.to_dict())

    def is_running(self) -> bool:
        with self._lock:
            t = self._thread
            return bool(t and t.is_alive())

    def start_download(self, worker: Callable[[JobContext], None], *, force: bool = False) -> dict[str, Any]:
        """Start ``worker(job)`` in a background thread.

        ``worker`` is responsible for actually fetching files and reporting
        progress via ``job``.  Returns the current status plus a
        ``started`` flag and a human-readable ``message``.

        Idempotent unless ``force`` is set: a concurrent call while a download
        is in flight returns ``{"started": False, ...}``.  With ``force=True``
        an in-flight download is cancelled first and a fresh one started, so a
        stuck / slow download can always be superseded.

        NOTE: ``get_status()`` (via ``is_running()``) re-acquires ``self._lock``,
        so we must NEVER call it while holding the lock — that deadlocks a plain
        ``threading.Lock``. We only mutate ``_thread`` under the lock and build
        the response after releasing it.
        """
        with self._lock:
            already_running = bool(self._thread and self._thread.is_alive())
            if already_running and not force:
                pass
            else:
                if already_running:
                    # Signal the old thread to stop; we start a fresh one below.
                    self._cancel.set()
                self._cancel = threading.Event()
                cancel_event = self._cancel
                t = threading.Thread(
                    target=self._run_worker,
                    args=(worker, force, cancel_event),
                    name=f"{self.plugin_name}-model-download",
                    daemon=True,
                )
                self._thread = t
                t.start()
        # Build the response OUTSIDE the lock: get_status() -> is_running()
        # needs the same lock.
        status = self.get_status()
        if already_running and not force:
            status["started"] = False
            status["message"] = "Download already in progress"
        else:
            status["started"] = True
            status["message"] = "Download started"
        return status

    def cancel(self) -> None:
        """Signal any running download to stop at the next progress tick.

        Best-effort: a thread blocked inside a long network read only notices
        the flag once it returns (up to the socket timeout).  The worker then
        raises :class:`DownloadCancelled` and the thread exits.
        """
        with self._lock:
            self._cancel.set()

    def _run_worker(self, worker: Callable[[JobContext], None], force: bool, cancel_event: threading.Event) -> None:
        self.model_dir.mkdir(parents=True, exist_ok=True)
        job = JobContext(self, cancel_event)
        st = job.status
        st.state = "downloading"
        st.started_at = time.time()
        st.updated_at = time.time()
        if force:
            st.message = "Forcing re-download…"
        else:
            st.message = "Starting model download…"
        st.error = ""
        self._persist(st)
        try:
            worker(job)
            # If the worker never flipped state to ready/error, treat as ready
            # (caller may have already done so).
            if job.status.state == "downloading":
                job.status.state = "ready"
                job.status.progress = 100.0
                job.status.message = "Download complete"
            self._persist(job.status)
        except DownloadCancelled:
            # User cancelled (uninstall / force re-download). Return the store
            # to a clean idle state so the button becomes actionable again.
            job.status.state = "idle"
            job.status.message = "Download cancelled"
            job.status.error = ""
            job.status.progress = 0.0
            self._persist(job.status)
        except Exception as e:
            logger.exception("[%s] model download failed", self.plugin_name)
            job.status.state = "error"
            job.status.error = str(e) or e.__class__.__name__
            job.status.message = f"Download failed: {job.status.error[:200]}"
            self._persist(job.status)
        finally:
            with self._lock:
                self._thread = None

    def mark_ready(self, message: str = "Model already present") -> None:
        st = DownloadStatus.from_dict(_read_json(self.status_path))
        if st.state not in ("ready", "downloading"):
            st = DownloadStatus()
        st.state = "ready"
        st.message = message
        st.progress = 100.0
        st.error = ""
        self._persist(st)


# ── JobContext ──────────────────────────────────────────────────────────


class JobContext:
    """Handle passed to download workers.

    Workers call ``set_file(name)`` before each file fetch and
    ``set_progress(done, total, source=...)`` periodically.  The context
    persists the status after every mutation so the UI can poll cheaply.
    """

    def __init__(self, store: ModelStore, cancel_event: threading.Event | None = None) -> None:
        self.store = store
        self._cancel = cancel_event
        existing = _read_json(store.status_path)
        if existing.get("state") == "downloading":
            # Continue the previous status (preserve started_at).
            self.status = DownloadStatus.from_dict(existing)
        else:
            self.status = DownloadStatus()
        self.status.state = "downloading"

    def raise_if_cancelled(self) -> None:
        """Raise :class:`DownloadCancelled` if the download has been cancelled."""
        if self._cancel is not None and self._cancel.is_set():
            raise DownloadCancelled()

    def _save(self) -> None:
        self.store._persist(self.status)

    def set_file(self, name: str) -> None:
        self.status.file = name
        self.status.bytes_done = 0
        self.status.bytes_total = 0
        self._save()

    def set_progress(
        self,
        bytes_done: int,
        bytes_total: int,
        *,
        source: str = "",
        message: str = "",
    ) -> None:
        self.status.bytes_done = int(bytes_done)
        self.status.bytes_total = int(bytes_total)
        if source:
            self.status.source = source
        if message:
            self.status.message = message
        if bytes_total > 0:
            self.status.progress = round(min(bytes_done / bytes_total, 0.999) * 100, 1)
        self._save()

    def set_message(self, message: str) -> None:
        self.status.message = message
        self._save()

    def set_mirror(self, index: int, total: int, label: str) -> None:
        self.status.mirror_index = index
        self.status.mirror_total = total
        self.status.source = label
        self.status.message = f"Trying mirror {index}/{total}: {label}"
        self._save()

    def set_error(self, error: Exception | str) -> None:
        msg = str(error) if isinstance(error, Exception) else str(error)
        self.status.error = msg
        self.status.message = f"Download failed: {msg[:200]}"
        self.status.state = "error"
        self._save()

    def mark_done(self, message: str = "Download complete") -> None:
        self.status.state = "ready"
        self.status.message = message
        self.status.progress = 100.0
        self.status.error = ""
        self._save()


# ── Network helpers ─────────────────────────────────────────────────────

# A loose, conservative set of errors that should trigger mirror fallback.
# We deliberately exclude broad Exception so genuine logic bugs surface.
_NETWORK_ERRORS: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
)


def _is_http_fallback_status(status: int) -> bool:
    """Return True for HTTP statuses that should trigger mirror fallback."""
    return status in (401, 403, 407, 408, 425, 429, 500, 502, 503, 504)


def _http_get(url: str, headers: dict[str, str], timeout: float = 60.0) -> tuple[int, bytes, dict[str, str]]:
    """Plain HTTP GET returning (status, body, response_headers)."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - URL is from config
    except urllib.error.HTTPError as e:
        return e.code, e.read() if e.fp else b"", dict(e.headers or {})
    except _NETWORK_ERRORS as e:
        raise ConnectionError(f"network error: {e!r}") from e
    return resp.status, resp.read(), dict(resp.headers)


def _http_stream_to_file(
    url: str,
    dest: Path,
    headers: dict[str, str],
    *,
    progress_cb: Callable[[int, int], None] | None = None,
    timeout: float = 60.0,
    chunk: int = 1024 * 256,
) -> int:
    """Stream URL to dest. Returns bytes written. Raises on HTTP / network error."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - URL is from config
    except urllib.error.HTTPError as e:
        raise ConnectionError(f"HTTP {e.code} from {url}") from e
    except _NETWORK_ERRORS as e:
        raise ConnectionError(f"network error from {url}: {e!r}") from e

    total = int(resp.headers.get("Content-Length") or 0)
    written = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with tmp.open("wb") as out:
            while True:
                buf = resp.read(chunk)
                if not buf:
                    break
                out.write(buf)
                written += len(buf)
                if progress_cb:
                    try:
                        progress_cb(written, total)
                    except Exception:  # pragma: no cover - progress must never break IO
                        logger.debug("progress callback raised", exc_info=True)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    os.replace(tmp, dest)
    return written


# ── Mirror orchestration ────────────────────────────────────────────────


@dataclass
class MirrorResult:
    file: str
    source: str
    skipped: bool = False
    bytes_written: int = 0


def fetch_with_mirrors(
    job: JobContext,
    file_specs: Iterable[FileSpec],
    *,
    dest_subdir: str = "",
    progress_weight: float = 1.0,
    file_progress_offset: float = 0.0,
) -> list[MirrorResult]:
    """Fetch every file in ``file_specs`` using mirror fallback.

    For each FileSpec, try its mirrors in order. Stop at the first success
    for that file.  If all mirrors for a file fail, raise the last error.

    Parameters
    ----------
    job
        The active JobContext. ``set_file`` / ``set_progress`` / ``set_mirror``
        are called as work proceeds.
    file_specs
        Iterable of FileSpec, each carrying one or more FileMirror entries.
    dest_subdir
        Optional subdirectory inside the model_dir to place files in
        (e.g. ``"snapshots/<revision>"``).
    progress_weight
        How much of the overall progress (0..1) this batch contributes.
    file_progress_offset
        Where in the overall progress this batch starts (0..1).
    """
    specs = list(file_specs)
    if not specs:
        return []

    results: list[MirrorResult] = []
    total_files = len(specs)
    for idx, spec in enumerate(specs):
        job.raise_if_cancelled()
        job.set_file(spec.name)
        dest = job.store.model_dir / dest_subdir / spec.name if dest_subdir else job.store.model_dir / spec.name
        # B10: a file smaller than 1KB is almost certainly a truncated remnant
        # (headers / partial write) — treat it as missing so it is re-downloaded
        # instead of silently serving a corrupt model.
        if dest.is_file() and dest.stat().st_size > 1024:
            results.append(
                MirrorResult(file=spec.name, source="(cached)", skipped=True, bytes_written=dest.stat().st_size)
            )
            # Advance the progress bar to account for this skipped file.
            _ = file_progress_offset + ((idx + 1) / total_files) * progress_weight
            continue

        last_error: Exception | None = None
        for m_idx, mirror in enumerate(spec.mirrors, start=1):
            job.set_mirror(m_idx, len(spec.mirrors), mirror.label or mirror.url)
            try:
                written = _http_stream_to_file(
                    mirror.url,
                    dest,
                    dict(mirror.headers),
                    progress_cb=lambda done, total, _m=mirror.label or mirror.url: _file_progress(
                        job,
                        done,
                        total,
                        _m,
                    ),
                )
                results.append(MirrorResult(file=spec.name, source=mirror.label or mirror.url, bytes_written=written))
                last_error = None
                break
            except Exception as e:
                last_error = e
                logger.warning(
                    "[%s] mirror %s failed for %s: %s",
                    job.store.plugin_name,
                    mirror.label or mirror.url,
                    spec.name,
                    e,
                )
                # Clean up partial file before trying the next mirror.
                try:
                    dest.with_suffix(dest.suffix + ".part").unlink(missing_ok=True)
                except OSError:
                    pass
                continue
        if last_error is not None:
            job.set_error(f"All mirrors failed for {spec.name}: {last_error}")
            raise last_error

    return results


def _file_progress(
    job: JobContext,
    done: int,
    total: int,
    source: str,
) -> None:
    job.raise_if_cancelled()
    _ = done / total if total else 0  # fraction is reserved for future UI hooks
    job.set_progress(done, total, source=source, message=f"Downloading {job.status.file}")


# ── Hugging Face hub helper ─────────────────────────────────────────────


def hf_snapshot_via_hub(
    repo_id: str,
    revision: str,
    dest_dir: str | os.PathLike[str],
    *,
    endpoints: Sequence[str],
    allow_patterns: Sequence[str] | None = None,
) -> None:
    """Download a Hugging Face repo snapshot with mirror fallback.

    ``endpoints`` is a list of HF mirror bases (e.g.
    ``["https://hf-mirror.com", "https://huggingface.co"]``).  The first
    endpoint is tried; on any exception we set ``HF_ENDPOINT`` to the next
    entry and retry.  ``huggingface_hub.snapshot_download`` is the
    underlying primitive.

    Raises the last exception if all endpoints fail.
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    try:
        from huggingface_hub import snapshot_download
    except ImportError as e:
        raise RuntimeError(
            "huggingface_hub is not installed; install openai-whisper / transformers extras "
            "or run `pip install huggingface_hub`."
        ) from e

    last: Exception | None = None
    for endpoint in endpoints:
        os.environ["HF_ENDPOINT"] = endpoint.rstrip("/")
        try:
            snapshot_download(
                repo_id=repo_id,
                revision=revision,
                local_dir=str(dest),
                local_dir_use_symlinks=False,
                allow_patterns=list(allow_patterns) if allow_patterns else None,
            )
            return
        except Exception as e:  # network/HTTP/auth — try next mirror
            last = e
            logger.warning(
                "[hf_snapshot] endpoint %s failed for %s@%s: %s",
                endpoint,
                repo_id,
                revision[:8] if revision else "",
                e,
            )
            continue
    if last is not None:
        raise last
    raise RuntimeError("No HF endpoints provided")


def hf_hub_file_via_hub(
    repo_id: str,
    filename: str,
    dest: str | os.PathLike[str],
    *,
    endpoints: Sequence[str],
) -> None:
    """Download a single HF file with mirror fallback."""
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as e:
        raise RuntimeError("huggingface_hub is not installed") from e

    last: Exception | None = None
    for endpoint in endpoints:
        os.environ["HF_ENDPOINT"] = endpoint.rstrip("/")
        try:
            hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                local_dir=str(Path(dest).parent),
                local_dir_use_symlinks=False,
            )
            return
        except Exception as e:
            last = e
            logger.warning(
                "[hf_hub_file] endpoint %s failed for %s/%s: %s",
                endpoint,
                repo_id,
                filename,
                e,
            )
            continue
    if last is not None:
        raise last
    raise RuntimeError("No HF endpoints provided")


# ── Uninstall helpers ─────────────────────────────────────────────────
# Shared logic for "delete the downloaded weights". Three plugins
# (whisper, sensevoice, websearch reranker) all need to handle:
#
#   * read-only files (often the case in frozen ``_internal/`` bundles)
#   * files currently locked by AV software (e.g. 360 安全卫士)
#   * the user closing the application mid-delete
#
# project_memory hard constraint:
#   "Plugin deletion must handle read-only files and locked files by
#    first clearing read-only attributes and renaming locked files to
#    .dead_xxxx if deletion fails"
#
# The helper tries (in order):
#   1. plain ``os.remove`` / ``shutil.rmtree`` — the happy path
#   2. clear read-only bit, then retry
#   3. if WinError 5 / 32 / 33 (sharing violation / access denied)
#      rename to ``<name>.dead_<rand>`` so the user at least frees the
#      name and the model is no longer "ready" for the service
#   4. otherwise surface the OS error so the caller can show it


def _clear_readonly(path: os.PathLike[str] | str) -> bool:
    """Clear the read-only bit on ``path`` (file or directory tree).

    Returns True if the attribute was cleared (or was never set),
    False if the operation failed (typically WinError 5).
    """
    try:
        path = os.fspath(path)
    except TypeError:
        return False
    if not os.path.exists(path):
        return True
    try:
        if os.path.isfile(path):
            mode = os.stat(path).st_mode
            if mode & stat.S_IWRITE:
                return True
            os.chmod(path, mode | stat.S_IWRITE | stat.S_IREAD)
            return True
        # Walk the directory and clear read-only on every file.
        for root, _dirs, files in os.walk(path):
            for name in files:
                fp = os.path.join(root, name)
                try:
                    mode = os.stat(fp).st_mode
                    if not (mode & stat.S_IWRITE):
                        os.chmod(fp, mode | stat.S_IWRITE | stat.S_IREAD)
                except OSError:
                    # Don't abort the walk; the higher-level try/except
                    # will rename this whole tree if needed.
                    continue
            # also clear the dir's own readonly bit
            try:
                dmode = os.stat(root).st_mode
                if not (dmode & stat.S_IWRITE):
                    os.chmod(root, dmode | stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
        return True
    except OSError as e:
        logger.debug("[model_downloader] clear_readonly %s failed: %s", path, e)
        return False


def _is_locked_error(e: BaseException) -> bool:
    """Return True for OS errors that mean "file is locked / denied"."""
    if not isinstance(e, OSError):
        return False
    # Windows: errno 5 (Access denied), 13 (Permission denied),
    # 32 (sharing violation), 33 (lock violation).
    winno = getattr(e, "winerror", None)
    return (
        winno in (5, 13, 32, 33) or e.errno in (5, 13, 32, 33, 39)  # 39 = directory not empty on Linux
    )


def _dead_name(path: str) -> str:
    """Return a tombstone path for ``path``."""
    rand = f"{int(time.time() * 1000) % 1_000_000:06d}{random.randint(0, 999):03d}"
    return f"{path}.dead_{rand}"


def force_remove_file(path: str) -> tuple[bool, str]:
    """Remove a single file, tolerating read-only + locked states.

    Returns (removed, info). ``info`` is a human-readable status string
    ("removed" / "renamed to ..." / "<error message>"). The caller can
    log it; we never raise.
    """
    if not os.path.exists(path):
        return False, "not present"
    try:
        os.remove(path)
        return True, "removed"
    except OSError as e:
        if not _is_locked_error(e):
            return False, f"{e.__class__.__name__}: {e}"
        # try clearing read-only
        if _clear_readonly(path):
            try:
                os.remove(path)
                return True, "removed after clearing read-only"
            except OSError as e2:
                if not _is_locked_error(e2):
                    return False, f"{e2.__class__.__name__}: {e2}"
        # final fallback: rename out of the way
        try:
            target = _dead_name(path)
            os.rename(path, target)
            logger.warning(
                "[model_downloader] could not delete %s (WinError %s); renamed to %s",
                path,
                getattr(e, "winerror", e.errno),
                target,
            )
            return True, f"renamed to {os.path.basename(target)} (file locked by AV?)"
        except OSError as e3:
            return False, f"{e3.__class__.__name__}: {e3}"


def force_remove_tree(path: str) -> tuple[bool, str]:
    """Remove a directory tree, tolerating read-only + locked states.

    Returns (removed, info) — same contract as :func:`force_remove_file`.
    """
    if not os.path.exists(path):
        return False, "not present"
    try:
        # On Windows, onerror handler lets us recover from read-only
        # files inside the tree.
        def _onerror(func, fpath, exc_info):  # noqa: ANN001
            _clear_readonly(fpath)
            try:
                func(fpath)
            except OSError as ee:
                # If still locked, rename the leaf so the tree
                # truncation can continue. We don't fail the whole
                # operation for one rogue file.
                if _is_locked_error(ee) and os.path.isfile(fpath):
                    try:
                        os.rename(fpath, _dead_name(fpath))
                        logger.warning(
                            "[model_downloader] renamed locked file %s during tree removal",
                            fpath,
                        )
                    except OSError:
                        pass
                else:
                    raise

        import shutil as _shutil

        _shutil.rmtree(path, onerror=_onerror)
        if os.path.isdir(path):
            # rmtree didn't actually remove it (every file was renamed
            # to .dead_xxx). Try to rmdir the empty skeleton.
            try:
                os.rmdir(path)
            except OSError:
                return True, "renamed contents (tree shell left in place)"
        return True, "removed"
    except OSError as e:
        if not _is_locked_error(e):
            return False, f"{e.__class__.__name__}: {e}"
        # try clearing the whole tree's read-only bits and retry
        _clear_readonly(path)
        try:
            import shutil as _shutil

            _shutil.rmtree(path, ignore_errors=False)
            return True, "removed after clearing read-only"
        except OSError as e2:
            if not _is_locked_error(e2):
                return False, f"{e2.__class__.__name__}: {e2}"
            # final fallback: rename the whole tree
            try:
                target = _dead_name(path)
                os.rename(path, target)
                logger.warning(
                    "[model_downloader] could not delete tree %s; renamed to %s",
                    path,
                    target,
                )
                return True, f"renamed to {os.path.basename(target)} (tree locked by AV?)"
            except OSError as e3:
                return False, f"{e3.__class__.__name__}: {e3}"


def force_remove_status_file(path: str) -> None:
    """Best-effort delete for a JSON status file. Never raises.

    Tolerant of read-only / locked files. Used to clear the persisted
    download status after the model itself has been removed.
    """
    if not os.path.isfile(path):
        return
    try:
        os.remove(path)
    except OSError as e:
        if _is_locked_error(e) and _clear_readonly(path):
            try:
                os.remove(path)
            except OSError:
                pass
        # any remaining failure: leave it; next status write will overwrite


__all__ = [
    "DownloadCancelled",
    "DownloadStatus",
    "FileMirror",
    "FileSpec",
    "JobContext",
    "MirrorResult",
    "ModelStore",
    "fetch_with_mirrors",
    "force_remove_file",
    "force_remove_status_file",
    "force_remove_tree",
    "hf_hub_file_via_hub",
    "hf_snapshot_via_hub",
    "huggingface_mirror",
]
