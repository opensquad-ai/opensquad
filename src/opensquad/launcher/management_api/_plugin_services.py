"""Plugin service processes, runtime discovery and shutdown for the Launcher management API.

Extracted verbatim from ``launcher_main._start_management_server`` -- method
bodies are byte-identical to the original, only re-indented, so this is a pure
move.  See ``management_api/__init__.py`` for the composed handler.

Shared launcher registries are imported **by value** from ``launcher_main``.
That is safe because launcher_main only ever mutates these containers in place
(``dict[...] = ...`` / ``.append`` / ``.add``); the single rebind of
``_BUILTIN_PLUGINS`` happens at launcher_main module load, i.e. before this
module can be imported -- ``launcher_main`` imports this package lazily, from
inside the function that starts the server.
"""

from __future__ import annotations

import json
import subprocess
import time

from opensquad.launcher.process_manager import MAX_RESTART_ATTEMPTS, _cleanup_runtime_registry, _resolve_discovery_port
from opensquad.launcher_main import (
    _RUNTIME_LIST_TTL_S,
    _log,
    _plugin_services,
    _processes,
    discover_all_plugin_services,
    ensure_plugin_service_registered,
)
from opensquad.system_config import syscfg

# Must live in THIS module: ``global`` in the mixin binds here, not launcher_main.
_runtime_list_cache_at: float = 0.0
_runtime_list_cache_result: dict | None = None


class PluginServicesMixin:
    """Plugin service processes, runtime discovery and shutdown."""

    def _handle_list_plugin_services(self):
        """GET /api/plugin-services — List all plugin services and their status"""
        result = [psp.get_status() for psp in _plugin_services.values()]
        return self._send_json({"plugin_services": result})

    def _handle_services_manage(self):
        """GET /api/services/manage — Enriched service list for the Service Manager UI.
        Returns ALL discovered services (from plugin.json) merged with runtime status.
        This endpoint is used by the new standalone Service Management page."""
        # Function-local import to keep this mixin's module graph shallow, same as
        # the gateway does before calling into the uninstall helpers.
        from opensquad.resource_uninstall import is_protected_plugin

        # 1. Discover all plugin services from plugin.json
        discovered = discover_all_plugin_services()

        # 2. Build result merging discovery info with runtime status
        services = []
        for info in discovered:
            pid = info["plugin_id"]
            if pid in _plugin_services:
                psp = _plugin_services[pid]
                status = psp.get_status()
            else:
                # Not yet registered in _plugin_services (e.g., service was never started)
                status = {
                    "plugin_id": pid,
                    "display_name": info.get("display_name", pid),
                    "plugin_type": info.get("plugin_type", "tool"),
                    "alive": False,
                    "pid": None,
                    "port": _resolve_discovery_port(info),
                    "host": info.get("service_cfg", {}).get("host", "0.0.0.0"),
                    "auto_start": syscfg.is_service_enabled(pid),
                    "should_run": False,
                    "restart_count": 0,
                    "max_restarts": MAX_RESTART_ATTEMPTS,
                    "started_at": None,
                    "uptime_seconds": None,
                    "health_endpoint": info["service_cfg"].get("health_endpoint", "/health"),
                    "health_ok": None,
                    "service_cfg": info["service_cfg"],
                }
            # A service is owned by the plugin that declares it, so "uninstall
            # this service" is really "uninstall that plugin". Resolve the same
            # protection flag the plugin page reads from ``/api/plugins`` here,
            # otherwise the Service Manager offers a button whose only possible
            # outcome is the HTTP 400 ``prepare_plugin_uninstall`` raises for
            # ``builtin_plugins.json`` entries.
            status["builtin"] = is_protected_plugin(pid)
            services.append(status)

        return self._send_json({"services": services})

    def _handle_runtime_list(self):
        """GET /api/runtime/list — list runtime registry and managed process states.

        ``_cleanup_runtime_registry`` scans processes and can take ~1.5s on
        Windows; the frontend polls this endpoint, so the cleanup+response
        is cached for 5s (cleanup is idempotent — it only reaps stale pids
        that are already dead or unmanaged).
        """
        global _runtime_list_cache_at, _runtime_list_cache_result
        now = time.monotonic()
        if _runtime_list_cache_result is not None and now - _runtime_list_cache_at < _RUNTIME_LIST_TTL_S:
            return self._send_json(_runtime_list_cache_result)

        cleanup = _cleanup_runtime_registry(force_kill=False)
        managed_agents = []
        for ap in _processes.values():
            managed_agents.append(
                {
                    "agent_id": ap.agent_id,
                    "agent_name": ap.agent_name,
                    "pid": ap.process.pid if ap.process and ap.process.poll() is None else None,
                    "port": ap.actual_port,
                    "alive": ap.is_alive(),
                    "should_run": ap.should_run,
                }
            )
        managed_plugins = []
        for psp in _plugin_services.values():
            managed_plugins.append(
                {
                    "plugin_id": psp.plugin_id,
                    "pid": psp.process.pid if psp.process and psp.process.poll() is None else None,
                    "port": psp.port,
                    "alive": psp.is_alive(),
                    "should_run": psp.should_run,
                }
            )
        result = {
            "runtime_registry": cleanup.get("remaining", []),
            "cleanup": {
                "cleaned": cleanup.get("cleaned", 0),
                "killed": cleanup.get("killed", 0),
            },
            "managed": {
                "agents": managed_agents,
                "plugins": managed_plugins,
            },
        }
        _runtime_list_cache_at = now
        _runtime_list_cache_result = result
        return self._send_json(result)

    def _handle_plugin_service_start(self, plugin_id: str):
        """POST /api/plugin-services/{id}/start — Start a plugin service"""
        psp = ensure_plugin_service_registered(plugin_id)
        if psp is None:
            return self._send_json({"error": f"Plugin service '{plugin_id}' not found"}, 404)
        # Idempotent: if already running or in `starting` (deps installing),
        # return 200 with already_running=true so the UI doesn't alert an
        # error when the user clicks Start on a service that auto-started
        # in the background. Previously this returned HTTP 400 which made
        # the front-end pop an "Start failed: ... already running" dialog.
        if psp.is_alive() or psp.state == "starting":
            return self._send_json(
                {
                    "message": f"{plugin_id} already running",
                    "already_running": True,
                    "state": psp.state,
                    "pid": psp.process.pid if psp.process else None,
                    "port": psp.port,
                }
            )
        # Sync services.X.enabled = true so the service can read its own config
        # (otherwise the service may exit immediately if it sees enabled=false)
        self._set_service_enabled_in_config(plugin_id, True)
        psp.port = psp._resolve_port()  # Re-resolve port (config may have been updated)
        psp.start()
        pid_val = psp.process.pid if psp.process else None
        return self._send_json({"message": f"{plugin_id} started", "pid": pid_val, "port": psp.port})

    def _handle_plugin_service_stop(self, plugin_id: str):
        """POST /api/plugin-services/{id}/stop — Stop a plugin service"""
        psp = ensure_plugin_service_registered(plugin_id)
        if psp is None:
            return self._send_json({"error": f"Plugin service '{plugin_id}' not found"}, 404)
        if not psp.is_alive():
            psp.should_run = False
            # Still sync the flag in case it was previously enabled
            self._set_service_enabled_in_config(plugin_id, False)
            return self._send_json({"message": f"{plugin_id} already stopped"})
        psp.stop()
        # Sync services.X.enabled = false so a future opensquad start won't re-launch
        self._set_service_enabled_in_config(plugin_id, False)
        return self._send_json({"message": f"{plugin_id} stopped"})

    def _handle_plugin_service_auto_start(self, plugin_id: str, body: dict):
        """PUT /api/plugin-services/{id}/auto-start — Toggle auto-start on boot.
        Body: {"enabled": true/false}
        Updates system_config.json services.{plugin_id}.enabled.
        """
        enabled = body.get("enabled", True) if isinstance(body, dict) else True
        self._set_service_enabled_in_config(plugin_id, enabled)
        return self._send_json(
            {
                "ok": True,
                "plugin_id": plugin_id,
                "auto_start": enabled,
            }
        )

    def _set_service_enabled_in_config(self, plugin_id: str, enabled: bool):
        """Update system_config.json services.{plugin_id}.enabled and invalidate cache.

        Used by /api/plugin-services/{id}/start and /stop so the service itself
        sees the right state and won't immediately exit on next launch.
        """
        try:
            sys_cfg_path = syscfg.workspace_config_path()
            with open(sys_cfg_path, encoding="utf-8") as f:
                full_cfg = json.load(f)
            if "services" not in full_cfg:
                full_cfg["services"] = {}
            if plugin_id not in full_cfg["services"]:
                full_cfg["services"][plugin_id] = {}
            full_cfg["services"][plugin_id]["enabled"] = enabled
            with open(sys_cfg_path, "w", encoding="utf-8") as f:
                json.dump(full_cfg, f, indent=2, ensure_ascii=False)
            # Invalidate system_config cache
            from opensquad import system_config as _syscfg_mod

            _syscfg_mod._cache = None
            _log.info(f"[Launcher] Synced services.{plugin_id}.enabled = {enabled}")
        except Exception as e:
            _log.warning(f"[Launcher] Warning: Failed to sync services.{plugin_id}.enabled: {e}")

    def _handle_shutdown(self, body: dict):
        """POST /api/shutdown — Gracefully stop agents, then confirm for force-kill.

        Agent termination is parallel: serial ``wait(timeout)`` per agent
        made shutdown take N x timeout seconds and blocked the HTTP response.
        """
        timeout = body.get("timeout", 5) if isinstance(body, dict) else 5
        targets = []
        stopped = 0
        for name, ap in list(_processes.items()):
            if ap.is_alive():
                try:
                    _log.info(f"[Launcher] Graceful shutdown: stopping agent {name}...")
                    ap.should_run = False
                    if ap.process and ap.process.poll() is None:
                        targets.append(ap.process)
                except Exception:
                    pass

        def _stop_one(proc):
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return 1
            except Exception:
                return 0

        if targets:
            try:
                from concurrent.futures import ThreadPoolExecutor

                with ThreadPoolExecutor(max_workers=min(len(targets), 8)) as pool:
                    stopped = sum(pool.map(_stop_one, targets))
            except Exception:
                for proc in targets:
                    stopped += _stop_one(proc)

        # Also stop plugin services (parallel via same pool is unnecessary:
        # psp.stop() is a signal + return)
        for _pid, psp in list(_plugin_services.items()):
            if psp.is_alive():
                try:
                    psp.stop()
                    stopped += 1
                except Exception:
                    pass
        return self._send_json({"message": f"Shutdown: {stopped} processes stopped", "ok": True})

    def _handle_plugin_service_restart(self, plugin_id: str):
        """POST /api/plugin-services/{id}/restart — Restart a plugin service"""
        psp = ensure_plugin_service_registered(plugin_id)
        if psp is None:
            return self._send_json({"error": f"Plugin service '{plugin_id}' not found"}, 404)
        # Stop if running
        if psp.is_alive():
            psp.stop()
            # Wait up to 5s for the process to exit
            for _ in range(50):
                if not psp.is_alive():
                    break
                time.sleep(0.1)
        # Re-resolve port (config may have been updated)
        psp.port = psp._resolve_port()
        psp.should_run = True
        psp.start()
        pid_val = psp.process.pid if psp.process else None
        return self._send_json({"message": f"{plugin_id} restarted", "pid": pid_val, "port": psp.port})

    def _handle_plugin_service_logs(self, plugin_id: str, lines: int):
        """GET /api/plugin-services/{id}/logs — Retrieve plugin service logs"""
        psp = ensure_plugin_service_registered(plugin_id)
        if psp is None:
            return self._send_json({"error": f"Plugin service '{plugin_id}' not found"}, 404)
        logs = psp.get_logs(lines)
        return self._send_json({"plugin_id": plugin_id, "logs": logs, "total": len(logs)})
