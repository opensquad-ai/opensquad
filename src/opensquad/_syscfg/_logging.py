"""
_syscfg/_logging.py -- Logging configuration.

Extracted from system_config.py.
"""

from __future__ import annotations

import os

from ._config import get, get_int


def log_dir() -> str:
    """Log directory path. env > system_config logging.log_dir > workspace data/logs."""
    val = os.environ.get("LOG_DIR")
    if val:
        return val
    cfg_val = get("logging", "log_dir", "")
    if cfg_val:
        return cfg_val
    from ._paths import workspace_logs_dir

    return workspace_logs_dir()


def log_max_size_mb() -> int:
    """Max log file size in MB before rotation."""
    val = os.environ.get("LOG_MAX_SIZE_MB")
    if val:
        return int(val)
    return get_int("logging", "max_size_mb", 3)


def log_backup_count() -> int:
    """Number of rotated log backup files to keep."""
    val = os.environ.get("LOG_BACKUP_COUNT")
    if val:
        return int(val)
    return get_int("logging", "backup_count", 5)


def log_level() -> str:
    """Default log level."""
    val = os.environ.get("LOG_LEVEL")
    if val:
        return val.upper()
    return get("logging", "log_level", "INFO").upper()


def log_format() -> str:
    """Log format string."""
    return get("logging", "log_format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s")


def log_date_format() -> str:
    """Log date format string."""
    return get("logging", "log_date_format", "%Y-%m-%d %H:%M:%S")


def tool_call_debug() -> bool:
    """Whether tool call debug logging is enabled."""
    val = os.environ.get("TOOL_CALL_DEBUG")
    if val is not None:
        return val.lower() in ("1", "true", "yes")
    return bool(get("logging", "tool_call_debug", False))


def tool_call_debug_max_size_mb() -> int:
    """Max size of tool_call_debug.log before rotation."""
    return get_int("logging", "tool_call_debug_max_size_mb", 5)


def tool_call_debug_backup_count() -> int:
    """Number of rotated tool_call_debug backup files."""
    return get_int("logging", "tool_call_debug_backup_count", 3)
