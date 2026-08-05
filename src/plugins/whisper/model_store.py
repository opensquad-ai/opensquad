"""Whisper model store for the whisper_transcribe plugin.

The whisper Python library downloads ``{model}.pt`` files to
``~/.cache/whisper/`` on first use. That cache is not shared between
"system Python" and the frozen "Agent Python" embed, and it is also
invisible to the launcher UI.

This module:

- tracks which Whisper model size is currently selected (``WHISPER_MODEL``
  env, default ``base``)
- exposes a download UI that fetches the ``.pt`` weights from the
  openai-whisper Azure CDN with mirror fallback (Azure CDN primary, then
  HF mirror / HF official as backups for users in mainland China).  HF
  mirrors ship the model as ``pytorch_model.bin`` (transformers format) —
  we download the corresponding ``config.json`` and ``pytorch_model.bin``
  then re-pack them into the openai-whisper-compatible ``{name}.pt``
  (with ``dims`` and ``model_state_dict``).
- copies the downloaded ``.pt`` into the standard ``~/.cache/whisper/``
  location so the existing ``whisper.load_model()`` call Just Works
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from typing import Any

# _model_downloader lives at plugins/_model_downloader.py, not
# plugins/whisper/_model_downloader.py — so we always import via
# the absolute path.  When loaded as part of the ``plugins.whisper``
# package, ``plugins/`` is already importable.
try:
    from plugins._model_downloader import ModelStore  # type: ignore[no-redef]
except ImportError:
    # Loading as a standalone script (e.g. the Agent Python embed which
    # has no ``plugins`` package) — add the parent dir to sys.path.
    _here = os.path.dirname(os.path.abspath(__file__))
    _plugins_dir = os.path.abspath(os.path.join(_here, "..", ".."))
    if _plugins_dir not in sys.path:
        sys.path.insert(0, _plugins_dir)
    from _model_downloader import ModelStore  # type: ignore[no-redef]

logger = logging.getLogger("plugins.whisper.model_store")

# ── Constants ──────────────────────────────────────────────────────────

# Per-model mapping. Each entry has:
#   * ``azure_sha256``: SHA256 fragment in the Azure CDN URL
#   * ``azure_filename``: filename part of the Azure CDN URL
#   * ``hf_repo``: HuggingFace repo carrying the transformers-format weights
#   * ``hf_pytorch_filename``: file in the HF repo containing the state dict
#   * ``hf_config_filename``: file in the HF repo containing ``config.json``
#   * ``hf_short``: short name used to construct HF mirror repo paths
#     (some aliases like "large" map to "large-v3" on HF).
#
# These come from the public openai/whisper release table:
# https://github.com/openai/whisper/blob/main/whisper/__init__.py
MODELS: dict[str, dict[str, str]] = {
    "tiny": {
        "azure_sha256": "65147644a518d12f04e32d6f3b26facc3f8dd46e5390956a9424a650c0ce22b9",
        "azure_filename": "tiny.pt",
        "hf_repo": "openai/whisper-tiny",
        "hf_pytorch_filename": "pytorch_model.bin",
        "hf_config_filename": "config.json",
        "hf_short": "tiny",
    },
    "base": {
        "azure_sha256": "ed3a0b6b1c0edf879ad9b11b1af5a0e6ab5db9205f891f668f8b0e6c6326e34e",
        "azure_filename": "base.pt",
        "hf_repo": "openai/whisper-base",
        "hf_pytorch_filename": "pytorch_model.bin",
        "hf_config_filename": "config.json",
        "hf_short": "base",
    },
    "small": {
        "azure_sha256": "9ecf779972d90ba49c06d968637d720dd632c55bbf19d441fb42bf17a411e794",
        "azure_filename": "small.pt",
        "hf_repo": "openai/whisper-small",
        "hf_pytorch_filename": "pytorch_model.bin",
        "hf_config_filename": "config.json",
        "hf_short": "small",
    },
    "medium": {
        "azure_sha256": "345ae4da62f9b3d59415adc60127b97c714f32e89e936602e85993674d08dcb1",
        "azure_filename": "medium.pt",
        "hf_repo": "openai/whisper-medium",
        "hf_pytorch_filename": "pytorch_model.bin",
        "hf_config_filename": "config.json",
        "hf_short": "medium",
    },
    # "large" is an alias for large-v3 (most recent large model).
    "large": {
        "azure_sha256": "e5b1a55b89c1367dacf97e3e19bfd829a01529dbfdeefa8caeb59b3f1b81dadb",
        "azure_filename": "large-v3.pt",
        "hf_repo": "openai/whisper-large-v3",
        "hf_pytorch_filename": "model.safetensors",
        "hf_config_filename": "config.json",
        "hf_short": "large-v3",
    },
    "large-v2": {
        "azure_sha256": "81f7c96c852ee8fc832187b0132e569d6c3065a3252ed18e56effd0b6a73e524",
        "azure_filename": "large-v2.pt",
        "hf_repo": "openai/whisper-large-v2",
        "hf_pytorch_filename": "pytorch_model.bin",
        "hf_config_filename": "config.json",
        "hf_short": "large-v2",
    },
    "large-v3": {
        "azure_sha256": "e5b1a55b89c1367dacf97e3e19bfd829a01529dbfdeefa8caeb59b3f1b81dadb",
        "azure_filename": "large-v3.pt",
        "hf_repo": "openai/whisper-large-v3",
        "hf_pytorch_filename": "model.safetensors",
        "hf_config_filename": "config.json",
        "hf_short": "large-v3",
    },
}

# Approximate on-disk sizes, used only for UI hints.
APPROX_SIZE_MB: dict[str, int] = {
    "tiny": 75,
    "base": 142,
    "small": 466,
    "medium": 1500,
    "large": 3100,
    "large-v2": 3100,
    "large-v3": 3100,
}

DEFAULT_MODEL = "base"

# HF endpoints to try, in order. The first endpoint is the CN-friendly
# hf-mirror.com; the second is the official Hugging Face endpoint.
HF_MIRRORS: tuple[str, ...] = (
    os.environ.get("WHISPER_HF_MIRROR", "https://hf-mirror.com"),
    "https://huggingface.co",
)

# Azure CDN base — the canonical openai-whisper download location.
AZURE_CDN_BASE = "https://openaipublic.azureedge.net/main/whisper/models"


def azure_url(model: str) -> str | None:
    """Return the Azure CDN URL for the given model's .pt file, or None."""
    info = MODELS.get(model)
    if not info:
        return None
    return f"{AZURE_CDN_BASE}/{info['azure_sha256']}/{info['azure_filename']}"


def hf_mirror_urls(model: str, filename: str) -> list[tuple[str, str]]:
    """Return ``(url, label)`` for ``filename`` in the HF mirror for ``model``."""
    info = MODELS.get(model)
    if not info:
        return []
    out: list[tuple[str, str]] = []
    for endpoint in HF_MIRRORS:
        label = endpoint.replace("https://", "")
        url = f"{endpoint.rstrip('/')}/{info['hf_repo']}/resolve/main/{filename}"
        out.append((url, label))
    return out


# ── Paths ──────────────────────────────────────────────────────────────


def _plugin_data_dir() -> str:
    try:
        from plugins._service_runtime import workspace_data_dir
    except ImportError:
        from _service_runtime import workspace_data_dir  # type: ignore[no-redef]

    return workspace_data_dir("plugins", "whisper")


def model_dir() -> str:
    """Where downloaded .pt files live (under workspace data)."""
    return os.path.join(_plugin_data_dir(), "models")


def _status_path() -> str:
    return os.path.join(_plugin_data_dir(), "model_status.json")


def _legacy_cache_dir() -> str:
    """Where openai-whisper looks for models by default."""
    return os.path.join(os.path.expanduser("~"), ".cache", "whisper")


def _read_selected_model() -> str:
    """Read the model name the service would load."""
    env = os.environ.get("WHISPER_MODEL", "").strip().lower()
    if env and env in MODELS:
        return env
    # Fall back to workspace config if present.
    try:
        cfg_path = os.path.join(_plugin_data_dir(), "config.json")
        if os.path.isfile(cfg_path):
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f) or {}
            m = str(cfg.get("model", "")).strip().lower()
            if m and m in MODELS:
                return m
    except (OSError, ValueError):
        pass
    return DEFAULT_MODEL


# ── Store singleton ───────────────────────────────────────────────────

_store: ModelStore | None = None


def _get_store() -> ModelStore:
    global _store
    if _store is None:
        _store = ModelStore(
            plugin_name="whisper",
            model_dir=model_dir(),
            status_path=_status_path(),
        )
    return _store


# ── Status ─────────────────────────────────────────────────────────────


def _dest_filename(model: str) -> str:
    return f"{model}.pt"


def is_model_ready(model: str | None = None) -> bool:
    m = (model or _read_selected_model()).lower()
    if m not in MODELS:
        return False
    fname = _dest_filename(m)
    # Either in our workspace store, or in the legacy cache.
    p1 = os.path.join(model_dir(), fname)
    p2 = os.path.join(_legacy_cache_dir(), fname)
    return any(os.path.isfile(p) and os.path.getsize(p) > 0 for p in (p1, p2))


def list_available_models() -> list[dict[str, Any]]:
    """List every supported model size with ready/not-ready status."""
    out: list[dict[str, Any]] = []
    selected = _read_selected_model()
    for name in MODELS:
        out.append(
            {
                "name": name,
                "size_mb": APPROX_SIZE_MB.get(name, 0),
                "ready": is_model_ready(name),
                "selected": (name == selected),
            }
        )
    return out


def model_file_size(model: str) -> int:
    fname = _dest_filename(model)
    for p in (
        os.path.join(model_dir(), fname),
        os.path.join(_legacy_cache_dir(), fname),
    ):
        try:
            if os.path.isfile(p):
                return os.path.getsize(p)
        except OSError:
            pass
    return 0


def get_status() -> dict[str, Any]:
    selected = _read_selected_model()
    return {
        "ready": is_model_ready(selected),
        "model": selected,
        "model_dir": model_dir(),
        "legacy_cache_dir": _legacy_cache_dir(),
        "file_size": model_file_size(selected),
        "available_models": list_available_models(),
        "download": _get_store().get_status(),
    }


# ── Helpers ───────────────────────────────────────────────────────────


def _publish_to_legacy_cache(filename: str) -> None:
    """Mirror the .pt into the standard whisper cache directory."""
    src = os.path.join(model_dir(), filename)
    if not os.path.isfile(src):
        return
    dest_dir = _legacy_cache_dir()
    try:
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, filename)
        if os.path.abspath(src) == os.path.abspath(dest):
            return
        # Don't overwrite a working copy with a corrupt one.
        if os.path.isfile(dest) and os.path.getsize(dest) > 0:
            return
        shutil.copy2(src, dest)
    except OSError as e:
        # Non-fatal: the service may still load from model_dir.
        logger.warning("[whisper] mirror to legacy cache failed: %s", e)


def _verify_azure_sha256(path: str, expected_sha256: str) -> None:
    """Verify SHA256 against the Azure URL's hash fragment; raise on mismatch."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
    except OSError as e:
        raise RuntimeError(f"could not read downloaded .pt for hashing: {e}") from e
    actual = h.hexdigest()
    if actual != expected_sha256:
        try:
            os.remove(path)
        except OSError:
            pass
        raise RuntimeError(
            f"Whisper .pt SHA256 mismatch: expected {expected_sha256}, got {actual}. "
            "The download may be corrupt; please retry."
        )


# ── Download worker ───────────────────────────────────────────────────


# Network errors that should trigger mirror fallback.
_NETWORK_ERRORS: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
)


def _http_get_bytes(url: str, *, headers: dict[str, str] | None = None, timeout: float = 60.0) -> bytes:
    """GET ``url`` and return the body as bytes. Raise on HTTP / network errors."""
    headers = dict(headers or {})
    headers.setdefault("User-Agent", "OpenSquad-Whisper/1.0")
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - URL from config
            return resp.read()
    except urllib.error.HTTPError as e:
        raise ConnectionError(f"HTTP {e.code} from {url}") from e
    except _NETWORK_ERRORS as e:
        raise ConnectionError(f"network error from {url}: {e!r}") from e


def _download_with_progress(
    job,
    url: str,
    dest_path: str,
    *,
    label: str,
    timeout: float = 60.0,
) -> None:
    """Stream ``url`` to ``dest_path`` and feed progress to ``job``."""
    headers = {"User-Agent": "OpenSquad-Whisper/1.0"}
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - URL from config
    except urllib.error.HTTPError as e:
        raise ConnectionError(f"HTTP {e.code} from {url}") from e
    except _NETWORK_ERRORS as e:
        raise ConnectionError(f"network error from {url}: {e!r}") from e

    total = int(resp.headers.get("Content-Length") or 0)
    written = 0
    tmp = dest_path + ".part"
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    last_persist = time.time()
    try:
        with open(tmp, "wb") as out:
            while True:
                buf = resp.read(1024 * 256)
                if not buf:
                    break
                out.write(buf)
                written += len(buf)
                # Throttle status writes to at most ~2/sec.
                now = time.time()
                if now - last_persist > 0.5:
                    last_persist = now
                    if total > 0:
                        frac = min(written / total, 0.999)
                    else:
                        frac = 0.0
                    job.set_progress(
                        written,
                        total,
                        source=label,
                        message=f"Downloading {os.path.basename(dest_path)}",
                    )
                    job.status.progress = round(frac * 100, 1)
                    job._save()
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, dest_path)


def _download_azure_pt(job, model: str, dest_path: str, *, force: bool = False) -> bool:
    """Try downloading the .pt from Azure CDN. Returns True on success."""
    info = MODELS.get(model)
    if not info:
        return False
    url = azure_url(model)
    if not url:
        return False
    label = "openaipublic.azureedge.net"
    job.set_mirror(1, 3, label)
    try:
        _download_with_progress(job, url, dest_path, label=label, timeout=120)
    except Exception as e:
        logger.warning("[whisper] Azure CDN failed: %s", e)
        job.set_message(f"Azure CDN failed: {e}")
        return False
    # Verify SHA256 (Azure URL embeds it).
    try:
        _verify_azure_sha256(dest_path, info["azure_sha256"])
    except Exception as e:
        logger.warning("[whisper] Azure CDN SHA256 verify failed: %s", e)
        job.set_message(f"Azure CDN SHA256 verify failed: {e}")
        return False
    return True


def _hf_config_dims(hf_config_bytes: bytes) -> dict[str, int]:
    """Translate an HF whisper config.json into the openai-whisper dims dict.

    Returns a dict with keys: n_mels, n_audio_ctx, n_audio_state,
    n_text_ctx, n_text_state, n_head, n_layer.
    """
    cfg = json.loads(hf_config_bytes.decode("utf-8"))
    return {
        "n_mels": int(cfg["num_mel_bins"]),
        "n_audio_ctx": int(cfg["max_source_positions"]),
        "n_audio_state": int(cfg["d_model"]),
        "n_text_ctx": int(cfg["max_target_positions"]),
        "n_text_state": int(cfg["d_model"]),
        "n_head": int(cfg["decoder_attention_heads"]),
        "n_layer": int(cfg["decoder_layers"]),
    }


def _download_hf_pytorch_and_repack(job, model: str, dest_path: str, *, mirror_index_start: int) -> bool:
    """Try downloading ``pytorch_model.bin`` (or ``model.safetensors``) +
    ``config.json`` from HF mirrors, repack as openai-whisper .pt.
    """
    info = MODELS.get(model)
    if not info:
        return False
    repo = info["hf_repo"]
    config_fn = info["hf_config_filename"]
    weights_fn = info["hf_pytorch_filename"]

    # Per-mirror plan: we have 2 mirrors, so try mirror 1 then mirror 2
    # for the entire pair. If both fail, give up.
    n_mirrors = len(HF_MIRRORS)
    for m_idx, endpoint in enumerate(HF_MIRRORS, start=1):
        label = endpoint.replace("https://", "")
        job.set_mirror(mirror_index_start + m_idx - 1, mirror_index_start + n_mirrors - 1, label)
        job.set_message(f"HF mirror {m_idx}/{n_mirrors} ({label}): fetching config + weights…")
        try:
            config_url = f"{endpoint.rstrip('/')}/{repo}/resolve/main/{config_fn}"
            weights_url = f"{endpoint.rstrip('/')}/{repo}/resolve/main/{weights_fn}"
            # We don't stream big files via _download_with_progress for
            # the re-pack path because we need both files in memory to
            # stitch them.  Files up to ~3GB fit in RAM on a developer
            # machine; for a packaged build users will mostly use small
            # models.  We still feed coarse progress to the UI.
            job.set_message(f"HF {label}: downloading config.json…")
            config_bytes = _http_get_bytes(config_url, timeout=60)
            job.set_message(f"HF {label}: downloading {weights_fn} (~{APPROX_SIZE_MB.get(model, 0)} MB)…")
            weights_bytes = _http_get_bytes(weights_url, timeout=600)
        except Exception as e:
            logger.warning("[whisper] HF mirror %s failed: %s", endpoint, e)
            job.set_message(f"HF mirror {m_idx}/{n_mirrors} ({label}) failed: {e}")
            continue

        # Re-pack into openai-whisper .pt format.  This requires torch
        # only on the *fallback* path; we import lazily so the common
        # case (Azure CDN succeeds) never pays this cost.
        try:
            import torch
        except ImportError as e:
            raise RuntimeError(
                "torch is required to repack HF model weights into the "
                "openai-whisper .pt format. Install openai-whisper's deps."
            ) from e

        try:
            dims = _hf_config_dims(config_bytes)
            job.set_message(f"HF {label}: loading state dict into memory…")
            if weights_fn.endswith(".safetensors"):
                try:
                    from safetensors.torch import load as _safe_load
                except ImportError as e:
                    raise RuntimeError(
                        "safetensors is required to load "
                        f"{weights_fn} from HF mirror {label}. "
                        "Install via `pip install safetensors`."
                    ) from e
                state = _safe_load(io.BytesIO(weights_bytes))
            else:
                state = torch.load(io.BytesIO(weights_bytes), map_location="cpu", weights_only=True)
            job.set_message(
                f"HF {label}: repacking .pt (dims={dims['n_audio_state']}/{dims['n_head']}/{dims['n_layer']})…"
            )
            checkpoint = {"dims": dims, "model_state_dict": state}
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            tmp = dest_path + ".part"
            with open(tmp, "wb") as f:
                torch.save(checkpoint, f)
            os.replace(tmp, dest_path)
        except Exception as e:
            logger.warning("[whisper] HF repack failed on mirror %s: %s", endpoint, e)
            job.set_message(f"HF repack failed: {e}")
            continue
        return True
    return False


def _download_worker(job) -> None:
    model = _read_selected_model()
    info = MODELS.get(model)
    if not info:
        raise RuntimeError(f"Unknown whisper model: {model!r}")

    dest_filename = _dest_filename(model)
    dest_path = os.path.join(model_dir(), dest_filename)
    if os.path.isfile(dest_path) and os.path.getsize(dest_path) > 0:
        job.mark_done(f"Whisper '{model}' already present")
        return

    job.set_file(dest_filename)
    job.set_message(f"Downloading Whisper '{model}' (~{APPROX_SIZE_MB.get(model, 0)} MB)…")

    # Total mirror count for the UI: 1 (Azure) + len(HF_MIRRORS).
    total_mirrors = 1 + len(HF_MIRRORS)

    # 1) Try Azure CDN (canonical openai-whisper source).
    job.set_mirror(1, total_mirrors, "openaipublic.azureedge.net")
    job.set_message("Trying Azure CDN (openaipublic.azureedge.net)…")
    if _download_azure_pt(job, model, dest_path):
        _publish_to_legacy_cache(dest_filename)
        job.mark_done(f"Whisper '{model}' downloaded (Azure CDN)")
        return

    # 2) Fall back to HuggingFace mirrors (transformers-format re-pack).
    job.set_message("Azure CDN unreachable; falling back to HuggingFace mirrors…")
    if _download_hf_pytorch_and_repack(job, model, dest_path, mirror_index_start=2):
        _publish_to_legacy_cache(dest_filename)
        job.mark_done(f"Whisper '{model}' downloaded (HF mirror repack)")
        return

    raise RuntimeError(
        f"All mirrors failed for Whisper '{model}'. "
        f"Tried: Azure CDN ({AZURE_CDN_BASE}), "
        f"and HF mirrors: {', '.join(HF_MIRRORS)}. "
        "Check your network or pre-place the .pt into ~/.cache/whisper/."
    )


# ── Public API ─────────────────────────────────────────────────────────


def start_download(*, model: str | None = None, force: bool = False) -> dict[str, Any]:
    """Start a background download for the given (or configured) Whisper model.

    Parameters
    ----------
    model
        If supplied and different from the currently selected model, the
        workspace config is updated and the service will load it on the
        next start.
    force
        Re-download even if the file already exists.
    """
    if model:
        model = model.strip().lower()
        if model not in MODELS:
            return {"ok": False, "error": f"Unknown whisper model: {model!r}"}
        # Persist the new selection so the service picks it up.
        _write_selected_model(model)

    store = _get_store()
    target = _read_selected_model()
    if is_model_ready(target) and not force:
        store.mark_ready(f"Whisper '{target}' already present")
        return {**get_status(), "started": False, "message": "Model already present"}

    if force:
        # Wipe both locations so the worker re-downloads.
        for path in (
            os.path.join(model_dir(), _dest_filename(target)),
            os.path.join(_legacy_cache_dir(), _dest_filename(target)),
        ):
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass

    return store.start_download(_download_worker, force=force)


def _write_selected_model(model: str) -> None:
    """Persist the selected model so the service reads it on next start."""
    cfg_path = os.path.join(_plugin_data_dir(), "config.json")
    os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
    payload: dict[str, Any] = {}
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8") as f:
                payload = json.load(f) or {}
        except (OSError, ValueError):
            payload = {}
    payload["model"] = model
    payload.setdefault("port", 5001)
    payload.setdefault("host", "0.0.0.0")
    tmp = cfg_path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, cfg_path)
    except OSError as e:
        logger.warning("[whisper] failed to persist model selection: %s", e)


def reset_for_tests() -> None:
    global _store
    _store = None
