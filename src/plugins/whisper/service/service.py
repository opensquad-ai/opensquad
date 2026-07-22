"""
Whisper Speech-to-Text Service
Model is kept in memory to avoid reloading on every call (saves ~4 minutes of startup time).

Migrated from services/whisper/service.py; corrected sys.path depth and added dynamic port resolution.

Start the service:
    python service.py

API endpoints:
    POST /transcribe                 - Upload audio file for transcription
    POST /transcribe/url             - Transcribe by local file path
    POST /v1/audio/transcriptions    - OpenAI-compatible transcriptions (model cards)
    GET  /health                     - Health check
    GET  /status                     - Service status
"""

import logging
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request
from flask_cors import CORS

# ── Windows compat patch for openai-whisper ──
# whisper.py tries ctypes.CDLL(find_library('c')) which returns None on Windows,
# causing TypeError: argument of type 'NoneType' is not iterable.
# Monkey-patch ctypes.util.find_library to return a safe fallback for 'c' / 'libc'.
if sys.platform == "win32":
    import ctypes.util

    _orig_find_library = ctypes.util.find_library

    def _patched_find_library(name):
        if name in ("c", "libc"):
            return "msvcrt"
        return _orig_find_library(name)

    ctypes.util.find_library = _patched_find_library

try:
    import whisper
except Exception as _import_err:
    # If whisper still fails to import (e.g. patch didn't cover the case),
    # don't crash the entire service — log and run in degraded mode.
    print(f"[Whisper] FATAL: Failed to import whisper: {_import_err}")
    whisper = None

# ── sys.path setup ─────────────────────────────────────────
# _here: plugins/whisper/service/
# _project_root: project root (opensquad/), 3 levels up — needed so the
# Agent Python (which has no opensquad package) can import
# plugins._service_runtime.
#
# In frozen mode, _project_root is the read-only _internal/ dir. APPEND it
# (not insert(0)) so the Agent Python's site-packages wins for third-party
# packages (flask, flask-cors, ...). Otherwise `import flask` would load
# the loose copy under _internal/flask/ but its transitive deps (click,
# jinja2, etc.) live only in the PYZ archive and crash with
# ModuleNotFoundError. See websearch/service/main.py for full rationale.
_here = os.path.dirname(os.path.abspath(__file__))
# _plugins_dir: plugins/ directory — contains _service_runtime.py
_plugins_dir = os.path.abspath(os.path.join(_here, "..", ".."))
if _here not in sys.path:
    sys.path.insert(0, _here)  # current dir (reserved for future cross-file imports)
if _plugins_dir not in sys.path:
    sys.path.insert(0, _plugins_dir)  # Allow `import _service_runtime` (direct)

# Self-contained runtime helper — does NOT import opensquad (which is not
# available to the Agent Python that runs plugin services in frozen mode).
try:
    from plugins._service_runtime import port as _runtime_port
    from plugins._service_runtime import workspace_data_dir as _runtime_workspace_data_dir
except ImportError:
    from _service_runtime import port as _runtime_port
    from _service_runtime import workspace_data_dir as _runtime_workspace_data_dir

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("whisper_service")

app = Flask(__name__)
CORS(app)  # Allow cross-origin requests

# Global variables: model and statistics
MODEL = None
# Model options: base (fast, 74MB), small (461MB), medium (1.5GB), large-v3 (3GB, most accurate)
MODEL_NAME = os.environ.get("WHISPER_MODEL", "base")  # Default: base model
STATS = {
    "model_loaded": False,
    "model_load_time": 0,
    "total_requests": 0,
    "successful_requests": 0,
    "failed_requests": 0,
    "startup_time": datetime.now().isoformat(),
}


def _resolve_service_port() -> int:
    """
    Port resolution priority (high to low):
    1. data/plugins/whisper/config.json → port (Web UI runtime config)
    2. system_config.json ports.whisper (deployment-level override)
    3. Default value 5001
    """
    config_path = _runtime_workspace_data_dir("plugins", "whisper", "config.json")
    if os.path.isfile(config_path):
        try:
            import json as _json

            with open(config_path, encoding="utf-8") as _f:
                _cfg = _json.load(_f)
            if "port" in _cfg:
                return int(_cfg["port"])
        except Exception:
            pass
    try:
        return _runtime_port("whisper")
    except Exception:
        pass
    return 5001


def load_model():
    """Load the Whisper model into memory."""
    global MODEL, STATS
    if whisper is None:
        logger.error("Cannot load model: whisper module not available (import failed at startup)")
        STATS["model_loaded"] = False
        return False
    logger.info(f"Loading Whisper model: {MODEL_NAME}")
    start_time = time.time()

    try:
        MODEL = whisper.load_model(MODEL_NAME)
        load_time = time.time() - start_time
        STATS["model_loaded"] = True
        STATS["model_load_time"] = round(load_time, 2)
        logger.info(f"Model loaded successfully! Time taken: {load_time:.2f} seconds")
        logger.info(f"Model location: {Path.home() / '.cache' / 'whisper' / f'{MODEL_NAME}.pt'}")
        return True
    except Exception as e:
        logger.error(f"Model load failed: {e}")
        STATS["model_loaded"] = False
        return False


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint"""
    return jsonify(
        {
            "status": "healthy" if STATS["model_loaded"] else "unhealthy",
            "model_loaded": STATS["model_loaded"],
            "uptime": (datetime.now() - datetime.fromisoformat(STATS["startup_time"])).total_seconds(),
        }
    )


@app.route("/status", methods=["GET"])
def status():
    """Service status endpoint"""
    return jsonify(STATS)


@app.route("/transcribe", methods=["POST"])
def transcribe():
    """
    Audio transcription endpoint.

    Request parameters:
        - file: Audio file (multipart/form-data)
        - language: Language code (optional, e.g. 'zh', 'en')
        - task: Task type (optional, 'transcribe' or 'translate')

    Returns:
        {
            "success": true,
            "text": "transcription result",
            "language": "zh",
            "duration": 1.23
        }
    """
    global MODEL, STATS
    STATS["total_requests"] += 1

    # Check if model is loaded
    if not STATS["model_loaded"] or MODEL is None:
        STATS["failed_requests"] += 1
        return jsonify({"success": False, "error": "Model not loaded, please try again later"}), 503

    # Check if a file was provided
    if "file" not in request.files:
        STATS["failed_requests"] += 1
        return jsonify({"success": False, "error": "No audio file found, please upload using the 'file' field"}), 400

    audio_file = request.files["file"]
    if audio_file.filename == "":
        STATS["failed_requests"] += 1
        return jsonify({"success": False, "error": "Filename is empty"}), 400

    # Get optional parameters
    language = request.form.get("language", None)  # auto-detect
    task = request.form.get("task", "transcribe")  # transcribe or translate

    # Save temp file
    temp_file = None
    try:
        # Create a temp file
        suffix = os.path.splitext(audio_file.filename)[1] or ".wav"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            audio_file.save(tmp.name)
            temp_file = tmp.name

        logger.info(f"Starting transcription: {audio_file.filename} (language={language}, task={task})")
        start_time = time.time()

        # Perform transcription
        result = MODEL.transcribe(
            temp_file,
            language=language,
            task=task,
            fp16=False,  # May need to be False on Windows
        )

        duration = time.time() - start_time
        STATS["successful_requests"] += 1

        logger.info(f"Transcription complete! Time taken: {duration:.2f} seconds")

        return jsonify(
            {
                "success": True,
                "text": result["text"].strip(),
                "language": result.get("language", "unknown"),
                "duration": round(duration, 2),
                "segments": len(result.get("segments", [])),
            }
        )

    except Exception as e:
        STATS["failed_requests"] += 1
        logger.error(f"Transcription failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

    finally:
        # Clean up temp file
        if temp_file and os.path.exists(temp_file):
            try:
                os.unlink(temp_file)
            except Exception as e:
                logger.warning(f"Failed to clean up temp file: {e}")


@app.route("/transcribe/url", methods=["POST"])
def transcribe_url():
    """
    Transcribe via local file path (suitable for same-machine deployment).

    Request body (JSON):
        {
            "path": "C:/path/to/audio.wav",
            "language": "zh",  // optional
            "task": "transcribe"  // optional
        }
    """
    global MODEL, STATS
    STATS["total_requests"] += 1

    if not STATS["model_loaded"] or MODEL is None:
        STATS["failed_requests"] += 1
        return jsonify({"success": False, "error": "Model not loaded"}), 503

    data = request.get_json()
    if not data or "path" not in data:
        STATS["failed_requests"] += 1
        return jsonify({"success": False, "error": "Please provide the 'path' parameter"}), 400

    audio_path = data["path"]
    if not os.path.exists(audio_path):
        STATS["failed_requests"] += 1
        return jsonify({"success": False, "error": f"File not found: {audio_path}"}), 404

    language = data.get("language", None)
    task = data.get("task", "transcribe")

    try:
        logger.info(f"Starting transcription: {audio_path}")
        start_time = time.time()

        result = MODEL.transcribe(audio_path, language=language, task=task, fp16=False)

        duration = time.time() - start_time
        STATS["successful_requests"] += 1

        logger.info(f"Transcription complete! Time taken: {duration:.2f} seconds")

        return jsonify(
            {
                "success": True,
                "text": result["text"].strip(),
                "language": result.get("language", "unknown"),
                "duration": round(duration, 2),
            }
        )

    except Exception as e:
        STATS["failed_requests"] += 1
        logger.error(f"Transcription failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/v1/audio/transcriptions", methods=["POST"])
def openai_transcriptions():
    """OpenAI-compatible audio transcriptions endpoint for model-card ASR.

    Accepts multipart form fields:
      - file: audio file (required)
      - model: model name (ignored for local whisper; accepted for compatibility)
      - language: optional language code (e.g. zh, en)
      - response_format: optional (json / text); default json
    """
    global MODEL, STATS
    STATS["total_requests"] += 1

    if not STATS["model_loaded"] or MODEL is None:
        STATS["failed_requests"] += 1
        return jsonify(
            {
                "success": False,
                "error": "Whisper model not loaded. Start the Whisper service and retry.",
            }
        ), 503

    if "file" not in request.files:
        STATS["failed_requests"] += 1
        return jsonify({"success": False, "error": "No audio file found; upload using the 'file' field"}), 400

    audio_file = request.files["file"]
    if not audio_file or audio_file.filename == "":
        STATS["failed_requests"] += 1
        return jsonify({"success": False, "error": "Filename is empty"}), 400

    language = request.form.get("language") or None
    if language in ("", "auto", "null", "none"):
        language = None
    task = request.form.get("task") or "transcribe"
    response_format = (request.form.get("response_format") or "json").strip().lower()

    temp_file = None
    try:
        suffix = os.path.splitext(audio_file.filename)[1] or ".wav"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            audio_file.save(tmp.name)
            temp_file = tmp.name

        logger.info(
            "OpenAI transcriptions: %s (language=%s, task=%s)",
            audio_file.filename,
            language,
            task,
        )
        start_time = time.time()
        result = MODEL.transcribe(
            temp_file,
            language=language,
            task=task,
            fp16=False,
        )
        duration = time.time() - start_time
        STATS["successful_requests"] += 1
        text = (result.get("text") or "").strip()
        logger.info("OpenAI transcriptions complete in %.2fs", duration)

        if response_format == "text":
            return text, 200, {"Content-Type": "text/plain; charset=utf-8"}

        return jsonify(
            {
                "text": text,
                "language": result.get("language", "unknown"),
                "duration": round(duration, 2),
                "success": True,
            }
        )
    except Exception as e:
        STATS["failed_requests"] += 1
        logger.error("OpenAI transcriptions failed: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if temp_file and os.path.exists(temp_file):
            try:
                os.unlink(temp_file)
            except Exception as e:
                logger.warning("Failed to clean up temp file: %s", e)


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("Whisper Speech-to-Text Service starting...")
    logger.info("=" * 60)

    # Suppress werkzeug access logs for /health endpoint
    class _HealthCheckFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return "/health" not in record.getMessage()

    logging.getLogger("werkzeug").addFilter(_HealthCheckFilter())

    # Load model on startup
    if not load_model():
        logger.error("Model load failed, service will run in degraded mode")

    # Start Flask service
    port = _resolve_service_port()
    logger.info(f"Service address: http://localhost:{port}")
    logger.info("API endpoints:")
    logger.info("  - POST /transcribe                  Upload audio file for transcription")
    logger.info("  - POST /transcribe/url              Transcribe by file path")
    logger.info("  - POST /v1/audio/transcriptions     OpenAI-compatible transcriptions")
    logger.info("  - GET  /health                      Health check")
    logger.info("  - GET  /status                      Service status")
    logger.info("=" * 60)

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,  # Set to False for production
        threaded=True,  # Support concurrent requests
    )
