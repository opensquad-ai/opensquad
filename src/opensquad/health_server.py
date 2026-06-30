"""Lightweight HTTP health-check server for Agent processes (P0-2).

Runs on a dedicated localhost port (auto-assigned) and exposes a minimal
/health endpoint that the Launcher polls to detect hang/deadlock states.

Why not reuse the WebSocket or Web Server?
- WebSocket may itself be the component that hangs
- Web Server may be slow to start or disabled
- This server is intentionally tiny (stdlib only) and starts instantly
"""

import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

logger = logging.getLogger(__name__)

# ── Global state ──
_health_server: HTTPServer | None = None
_health_thread: threading.Thread | None = None
_health_port: int = 0
_start_time: float = 0.0


class _HealthHandler(BaseHTTPRequestHandler):
    """Minimal handler: GET /health → JSON status."""

    def log_message(self, fmt, *args):
        # Suppress default access logs (too noisy)
        pass

    def do_GET(self):
        if self.path == "/health":
            uptime = time.time() - _start_time
            payload = {
                "status": "ok",
                "uptime_seconds": round(uptime, 2),
                "pid": os.getpid(),
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)


def start_health_server(preferred_port: int = 0) -> int:
    """Start the health-check HTTP server on a free localhost port.

    Args:
        preferred_port: If > 0, try this port first; fall back to auto-assigned.

    Returns:
        The actual port number the server is listening on.
    """
    global _health_server, _health_thread, _health_port, _start_time

    if _health_server is not None:
        return _health_port

    port = preferred_port
    while True:
        try:
            _health_server = HTTPServer(("127.0.0.1", port), _HealthHandler)
            break
        except OSError:
            if port == 0:
                raise  # Should not happen with port 0
            port = 0  # Fall back to OS-assigned

    _health_port = _health_server.server_address[1]
    _start_time = time.time()

    _health_thread = threading.Thread(
        target=_health_server.serve_forever,
        daemon=True,
        name="health-server",
    )
    _health_thread.start()
    logger.info(f"[HealthServer] Started on 127.0.0.1:{_health_port}")
    return _health_port


def stop_health_server(timeout: float = 2.0):
    """Gracefully stop the health-check server."""
    global _health_server, _health_thread, _health_port
    if _health_server is None:
        return
    try:
        _health_server.shutdown()
    except Exception as e:
        logger.warning(f"[HealthServer] Shutdown error: {e}")
    if _health_thread and _health_thread.is_alive():
        _health_thread.join(timeout=timeout)
    _health_server = None
    _health_thread = None
    _health_port = 0
    logger.info("[HealthServer] Stopped")


def get_health_port() -> int:
    """Return the current health server port (0 if not running)."""
    return _health_port
