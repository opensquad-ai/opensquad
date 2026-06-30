"""
OpenSquad Launcher subpackage.

Split from the monolithic launcher.py for maintainability.
"""

from opensquad.launcher.process_manager import (
    BOOT_MODULE,
    LOG_BUFFER_SIZE,
    MANAGEMENT_PORT,
    MAX_RESTART_ATTEMPTS,
    PROJECT_ROOT,
    RESTART_BACKOFF_SCHEDULE,
    RESTART_COOLDOWN,
    RUNTIME_REGISTRY_DIR,
    STABLE_RESET_SECONDS,
    AgentProcess,
    PluginServiceProcess,
    _cleanup_runtime_registry,
    _ensure_pip_and_install,
    _ensure_runtime_registry_dir,
    _install_builtin_plugin_deps,
    _kill_port_owner,
    _pid_exists,
    _plugin_services,
    _processes,
    _read_json,
    _read_runtime_registry,
    _registry_path,
    _remove_runtime_registry,
    _resolve_discovery_port,
    _terminate_pid_tree,
    _write_runtime_registry,
    check_port_conflict,
    find_available_port,
    is_port_in_use,
    set_process_tables,
)

__all__ = [
    "AgentProcess",
    "PluginServiceProcess",
    "set_process_tables",
]
