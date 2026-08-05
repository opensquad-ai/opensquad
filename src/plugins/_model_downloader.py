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
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

logger = logging.getLogger("plugins.model_downloader")


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

    # ── public status / control API ──

    def get_status(self) -> dict[str, Any]:
        payload = _read_json(self.status_path)
        if not payload:
            return DownloadStatus().to_dict()
        return DownloadStatus.from_dict(payload).to_dict()

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
        """
        with self._lock:
            if self._thread and self._thread.is_alive():
                return {**self.get_status(), "started": False, "message": "Download already in progress"}
            t = threading.Thread(
                target=self._run_worker,
                args=(worker, force),
                name=f"{self.plugin_name}-model-download",
                daemon=True,
            )
            self._thread = t
            t.start()
        return {**self.get_status(), "started": True, "message": "Download started"}

    def _run_worker(self, worker: Callable[[JobContext], None], force: bool) -> None:
        self.model_dir.mkdir(parents=True, exist_ok=True)
        job = JobContext(self)
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

    def __init__(self, store: ModelStore) -> None:
        self.store = store
        existing = _read_json(store.status_path)
        if existing.get("state") == "downloading":
            # Continue the previous status (preserve started_at).
            self.status = DownloadStatus.from_dict(existing)
        else:
            self.status = DownloadStatus()
        self.status.state = "downloading"

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
        job.set_file(spec.name)
        dest = job.store.model_dir / dest_subdir / spec.name if dest_subdir else job.store.model_dir / spec.name
        if dest.is_file() and dest.stat().st_size > 0:
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


__all__ = [
    "DownloadStatus",
    "FileMirror",
    "FileSpec",
    "JobContext",
    "MirrorResult",
    "ModelStore",
    "fetch_with_mirrors",
    "hf_hub_file_via_hub",
    "hf_snapshot_via_hub",
    "huggingface_mirror",
]
