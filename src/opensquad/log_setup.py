"""
Unified logging setup for OpenSquad.

Replaces the three duplicate setup_logging() functions that were scattered
across opensquad/tool.py, opensquad/tools/tool.py, and opensquad/tools/websearch.py.

Uses RotatingFileHandler with configurable max size and backup count.
All settings are read from system_config.json via system_config.py.

Usage:
    from opensquad.log_setup import setup_logging

    logger = logging.getLogger(__name__)
    setup_logging(logger, "agent_run.log")
"""

import contextlib
import logging
import os
import sys
import warnings

from opensquad.safe_rotating_handler import SafeRotatingFileHandler

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Import syscfg - handle different import contexts
try:
    from opensquad.system_config import syscfg
except ImportError:
    syscfg = None


def _ensure_utf8_console() -> None:
    """Force UTF-8 encoding for stdout/stderr on Windows to prevent Chinese garbling.

    Windows defaults to cp936 (GBK) for the console, causing any UTF-8 string
    written via print() or logging.StreamHandler to appear as garbage.  Calling
    reconfigure() here fixes the current process; child processes inherit the
    PYTHONUTF8 / PYTHONIOENCODING env vars set by launcher.py / run.py.
    """
    if sys.platform != "win32":
        return
    for attr in ("stdout", "stderr"):
        stream = getattr(sys, attr, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            with contextlib.suppress(Exception):
                stream.reconfigure(encoding="utf-8", errors="replace")


_ensure_utf8_console()

# Track which loggers have already been configured to avoid duplicate setup
_configured_loggers = set()


def setup_logging(
    logger: logging.Logger,
    log_filename: str = "agent_run.log",
    *,
    level: str | None = None,
    console: bool = True,
    force: bool = False,
    log_dir: str | None = None,  # Optional: specify log directory
):
    """
    Configure a logger with console + rotating file handler.

    Args:
        logger:       The logger instance to configure.
        log_filename: Log file name (placed inside the configured log_dir).
        level:        Override log level (default: from system_config).
        console:      Whether to add a console (StreamHandler) handler.
        force:        Re-configure even if already set up.
        log_dir:      Override log directory (default: from system_config).
    """
    logger_id = id(logger)
    if not force and logger_id in _configured_loggers:
        return
    _configured_loggers.add(logger_id)

    # Read config from syscfg (falls back to sensible defaults)
    if syscfg is not None:
        default_log_dir = syscfg.log_dir()
        max_bytes = syscfg.log_max_size_mb() * 1024 * 1024
        backup_count = syscfg.log_backup_count()
        cfg_level = syscfg.log_level()
        fmt = syscfg.log_format()
        datefmt = syscfg.log_date_format()
        project_root = syscfg.project_root()
    else:
        default_log_dir = "data/logs"
        max_bytes = 10 * 1024 * 1024
        backup_count = 5
        cfg_level = "INFO"
        fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        datefmt = "%Y-%m-%d %H:%M:%S"
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Use provided log_dir if given, otherwise use default
    actual_log_dir = log_dir if log_dir else default_log_dir

    # Resolve log_dir to absolute path (relative to project root)
    if not os.path.isabs(actual_log_dir):
        actual_log_dir = os.path.join(project_root, actual_log_dir)

    # Use override level if provided, otherwise config level
    log_level_str = (level or cfg_level).upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    formatter = logging.Formatter(fmt, datefmt=datefmt)

    # Clear existing handlers to avoid duplicate logs
    if logger.hasHandlers():
        logger.handlers.clear()

    # Console handler
    if console:
        ch = logging.StreamHandler()
        ch.setFormatter(formatter)
        logger.addHandler(ch)

    # Rotating file handler (use SafeRotatingFileHandler to avoid Windows multi-process file lock issues)
    try:
        os.makedirs(actual_log_dir, exist_ok=True)
        log_path = os.path.join(actual_log_dir, log_filename)
        fh = SafeRotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=True,  # defer file open
        )
        fh.setFormatter(formatter)
        logger.addHandler(fh)
    except Exception as e:
        # If file handler fails, at least console still works
        logger.error(f"Failed to configure file logging to {actual_log_dir}/{log_filename}: {e}")

    logger.setLevel(log_level)


# -- Tool-call debug logger (singleton) --

_tc_debug_logger: logging.Logger = None


def get_tool_call_debug_logger() -> logging.Logger:
    """
    Return a dedicated logger for tool-call debug tracing.

    - Writes to a separate file: tool_call_debug.log (with rotation)
    - Only active when system_config.json > logging > tool_call_debug is true
    - If disabled, returns a logger with NullHandler (no-op)

    Usage:
        from opensquad.log_setup import get_tool_call_debug_logger
        tc_log = get_tool_call_debug_logger()
        tc_log.debug("some message")  # only written when enabled
    """
    global _tc_debug_logger
    if _tc_debug_logger is not None:
        return _tc_debug_logger

    _tc_debug_logger = logging.getLogger("tool_call_debug")
    _tc_debug_logger.propagate = False  # don't bubble up to root logger

    enabled = False
    if syscfg is not None:
        enabled = syscfg.tool_call_debug()

    if not enabled:
        _tc_debug_logger.addHandler(logging.NullHandler())
        _tc_debug_logger.setLevel(logging.CRITICAL + 1)  # effectively silent
        return _tc_debug_logger

    # Read rotation settings for tool-call debug log
    if syscfg is not None:
        log_dir = syscfg.log_dir()
        max_bytes = syscfg.tool_call_debug_max_size_mb() * 1024 * 1024
        backup_count = syscfg.tool_call_debug_backup_count()
        fmt = syscfg.log_format()
        datefmt = syscfg.log_date_format()
        project_root = syscfg.project_root()
    else:
        log_dir = "data/logs"
        max_bytes = 5 * 1024 * 1024
        backup_count = 3
        fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        datefmt = "%Y-%m-%d %H:%M:%S"
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if not os.path.isabs(log_dir):
        log_dir = os.path.join(project_root, log_dir)

    formatter = logging.Formatter(fmt, datefmt=datefmt)

    try:
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "tool_call_debug.log")
        fh = SafeRotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=True,
        )
        fh.setFormatter(formatter)
        _tc_debug_logger.addHandler(fh)
    except Exception as e:
        _tc_debug_logger.addHandler(logging.NullHandler())
        logging.getLogger(__name__).error(f"Failed to create tool_call_debug log: {e}")

    _tc_debug_logger.setLevel(logging.DEBUG)
    return _tc_debug_logger
