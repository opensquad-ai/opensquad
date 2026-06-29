# -*- coding: utf-8 -*-
"""
OpenSquad Launcher subpackage.

Split from the monolithic launcher.py for maintainability.
"""
from opensquad.launcher.process_manager import (
    AgentProcess,
    PluginServiceProcess,
    _read_json,
    is_port_in_use,
    check_port_conflict,
    find_available_port,
    _ensure_runtime_registry_dir,
    _registry_path,
    _write_runtime_registry,
    _remove_runtime_registry,
    _terminate_pid_tree,
    _kill_port_owner,
    _pid_exists,
    _read_runtime_registry,
    _cleanup_runtime_registry,
    _resolve_discovery_port,
    _ensure_pip_and_install,
    _install_builtin_plugin_deps,
    set_process_tables,
    MAX_RESTART_ATTEMPTS,
    RESTART_COOLDOWN,
    RESTART_BACKOFF_SCHEDULE,
    STABLE_RESET_SECONDS,
    LOG_BUFFER_SIZE,
    MANAGEMENT_PORT,
    RUNTIME_REGISTRY_DIR,
    BOOT_MODULE,
    PROJECT_ROOT,
    _processes,
    _plugin_services,
)

__all__ = [
    "AgentProcess",
    "PluginServiceProcess",
    "set_process_tables",
]
