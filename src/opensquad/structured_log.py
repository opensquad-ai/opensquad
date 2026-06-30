"""
Structured logging with trace IDs for OpenSquad.

Goals:
  1. Every log line can be parsed as JSON (for log aggregation)
  2. Every request/turn gets a trace_id that propagates across components
  3. Backward compatible: plain-text format still works if JSON is disabled

Usage:
    from opensquad.structured_log import get_logger, TraceContext

    with TraceContext(trace_id="abc123"):
        logger = get_logger("runner")
        logger.info("turn started", extra={"turn": 3, "agent_id": "a1"})
        # Output (JSON mode):
        # {"ts":"2024-06-09T12:00:00Z","level":"INFO","logger":"runner",
        #  "trace_id":"abc123","msg":"turn started","turn":3,"agent_id":"a1"}

Performance logging helpers:
    from opensquad.structured_log import perf_event, PerfTimer

    # Structured event with elapsed time
    perf_event("boot", "phase_complete", phase="plugins", elapsed_ms=142, agent_id="a1")

    # Context manager for auto-elapsed tracking
    with PerfTimer("turn", trace_id="abc") as pt:
        # ... do work ...
        pt.update(turn_id=3, tool_count=5)
    # Logs: {"level":"INFO","msg":"turn","elapsed_ms":12,"turn_id":3,"tool_count":5}
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from opensquad.time_utils import utc_now_iso

# ---------------------------------------------------------------------------
# Trace context propagation (thread-safe + asyncio-safe via contextvars)
# ---------------------------------------------------------------------------

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def get_trace_id() -> str | None:
    """Return the current trace ID (or None if not set)."""
    return _trace_id.get()


def set_trace_id(tid: str | None) -> None:
    """Set the trace ID for the current execution context."""
    _trace_id.set(tid)


def new_trace_id() -> str:
    """Generate a new unique trace ID."""
    return uuid.uuid4().hex[:16]


@contextmanager
def TraceContext(trace_id: str | None = None):
    """Context manager that sets a trace ID for the enclosed block."""
    tid = trace_id or new_trace_id()
    token = _trace_id.set(tid)
    try:
        yield tid
    finally:
        _trace_id.reset(token)


# ---------------------------------------------------------------------------
# JSON formatter
# ---------------------------------------------------------------------------


class JSONFormatter(logging.Formatter):
    """Format log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        obj: dict[str, Any] = {
            "ts": utc_now_iso(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Add trace_id if present
        tid = getattr(record, "trace_id", None) or _trace_id.get()
        if tid:
            obj["trace_id"] = tid

        # Add any extra fields from the record
        for key, value in record.__dict__.items():
            if key in (
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "trace_id",
                "message",
                "asctime",
            ):
                continue
            if value is not None:
                try:
                    json.dumps(value)  # validate JSON-serializable
                    obj[key] = value
                except (TypeError, ValueError):
                    obj[key] = str(value)

        # Exception info
        if record.exc_info:
            obj["exception"] = self.formatException(record.exc_info)

        return json.dumps(obj, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Plain-text formatter (with trace_id support)
# ---------------------------------------------------------------------------


class TraceFormatter(logging.Formatter):
    """Standard text format but appends [trace_id=...] when available."""

    def format(self, record: logging.LogRecord) -> str:
        tid = getattr(record, "trace_id", None) or _trace_id.get()
        if tid:
            record.trace_id_str = f" [trace_id={tid}]"
        else:
            record.trace_id_str = ""
        return super().format(record)


# ---------------------------------------------------------------------------
# Logger adapter that injects trace_id into every record
# ---------------------------------------------------------------------------


class TraceLoggerAdapter(logging.LoggerAdapter):
    """Logger adapter that automatically includes trace_id in every log record."""

    def process(self, msg, kwargs):
        extra = kwargs.get("extra", {})
        tid = extra.get("trace_id") or _trace_id.get()
        if tid:
            extra["trace_id"] = tid
            kwargs["extra"] = extra
        return msg, kwargs


# ---------------------------------------------------------------------------
# Performance profiling helpers
# ---------------------------------------------------------------------------

_perf_logger: logging.Logger | None = None


def _get_perf_logger() -> logging.Logger:
    global _perf_logger
    if _perf_logger is None:
        _perf_logger = logging.getLogger("perf")
        _perf_logger.setLevel(logging.INFO)
        if not _perf_logger.handlers:
            _perf_logger.addHandler(logging.NullHandler())
    return _perf_logger


def perf_event(
    domain: str,
    event: str,
    agent_id: str = "",
    trace_id: str | None = None,
    **fields: Any,
) -> None:
    """Emit a structured performance event.

    All keyword args become JSON fields. A field named ``elapsed_ms`` is
    emitted as a raw integer milliseconds value.
    """
    tid = trace_id or get_trace_id()
    payload: dict[str, Any] = {
        "domain": domain,
        "event": event,
        "ts": utc_now_iso(),
    }
    if tid:
        payload["trace_id"] = tid
    if agent_id:
        payload["agent_id"] = agent_id
    for k, v in fields.items():
        payload[k] = v
    _get_perf_logger().info(
        "[perf] %s|%s %s",
        domain,
        event,
        " ".join(f"{k}={v}" for k, v in payload.items() if k not in ("domain", "event", "ts", "trace_id", "agent_id")),
        extra=payload,
    )


class PerfTimer:
    """Context manager that auto-reports elapsed time on exit via perf_event."""

    def __init__(
        self,
        domain: str,
        event: str = "complete",
        agent_id: str = "",
        trace_id: str | None = None,
        log_on_start: bool = False,
    ):
        self.domain = domain
        self.event = event
        self.agent_id = agent_id
        self.trace_id = trace_id
        self.log_on_start = log_on_start
        self._t0: float = 0.0
        self._extra: dict[str, Any] = {}

    def update(self, **fields: Any) -> PerfTimer:
        """Attach arbitrary fields captured mid-block."""
        self._extra.update(fields)
        return self

    def __enter__(self) -> PerfTimer:
        self._t0 = time.perf_counter()
        if self.log_on_start:
            tid = self.trace_id or get_trace_id()
            perf_event(self.domain, "start", agent_id=self.agent_id, trace_id=tid)
        return self

    def __exit__(self, *_: Any) -> None:
        elapsed_ms = int((time.perf_counter() - self._t0) * 1000)
        tid = self.trace_id or get_trace_id()
        fields = dict(self._extra)
        fields["elapsed_ms"] = elapsed_ms
        perf_event(self.domain, self.event, agent_id=self.agent_id, trace_id=tid, **fields)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_JSON_MODE = False  # toggled by configure_structured_logging


def get_logger(name: str) -> TraceLoggerAdapter:
    """Get a logger wrapped with trace-context support."""
    return TraceLoggerAdapter(logging.getLogger(name), {})


def configure_structured_logging(
    *,
    json_mode: bool = False,
    level: str = "INFO",
    log_dir: str | None = None,
):
    """Configure root logger with structured formatting.

    Args:
        json_mode: If True, use JSONFormatter; otherwise use TraceFormatter.
        level:     Log level string (DEBUG, INFO, WARNING, ERROR).
        log_dir:   If provided, also write to a structured log file.
    """
    global _JSON_MODE
    _JSON_MODE = json_mode

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplication
    for h in list(root.handlers):
        root.removeHandler(h)

    if json_mode:
        fmt = JSONFormatter()
    else:
        fmt = TraceFormatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s%(trace_id_str)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # Optional file handler
    if log_dir:
        import os

        from opensquad.safe_rotating_handler import SafeRotatingFileHandler

        os.makedirs(log_dir, exist_ok=True)
        fh = SafeRotatingFileHandler(
            os.path.join(log_dir, "structured.log"),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
            delay=True,
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
