"""
OpenSquad Plugin Manager

Discovers, loads, and manages all plugins in the plugins/ directory.
All plugins must use the new-style decorator API (opensquad.plugin_api).

Integrates with boot.py to register plugin-provided tools into agent ToolRegistry.
Provides hook chain execution for runner.py lifecycle hooks.
"""

import asyncio
import importlib
import json
import logging
import os
import sys
from typing import Any

# Add opensquad to sys.path if needed
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

import contextlib

from opensquad.system_config import syscfg

logger = logging.getLogger("plugins.manager")

# Per-handler timeout for run_hook. A handler that exceeds this is logged and
# skipped (its context mutations are lost). 10s is generous for IM webhook
# fetches / memory lookups but protects the main turn loop from indefinite
# stalls. Hook handlers doing heavy I/O should spawn their own task.
_HOOK_HANDLER_TIMEOUT = 10.0


class PluginManager:
    """
    Central manager for all OpenSquad plugins.

    Plugin style:
    - Plugin class decorated with @register(...)
    - Methods decorated with @tool, @hook.on_xxx, @on_event
    - plugin.json is auto-generated from decorators

    Usage in boot.py:
        pm = PluginManager()
        pm.discover_and_load()
        pm.register_tools_to_agent(registry, agent_id, agent_tool_names)

    Usage in runner.py:
        ctx = await pm.run_hook("on_message_received", context)
    """

    def __init__(self, plugins_dir: str | None = None, agent_id: str = ""):
        """
        Args:
            plugins_dir: absolute path to the plugins/ directory.
                         Defaults to the directory containing this file.
            agent_id: ID of the agent this plugin manager serves.
        """
        if plugins_dir is None:
            plugins_dir = os.path.dirname(os.path.abspath(__file__))
        self.plugins_dir = plugins_dir
        self.agent_id = agent_id

        # {plugin_name: {
        #     "plugin": instance,
        #     "metadata": dict,
        #     "dir": str,
        #     "hook_map": {hook_name: [bound_method, ...]},
        #     "tool_wrappers": [ToolModuleWrapper, ...],
        # }}
        self._plugins: dict[str, dict[str, Any]] = {}

        # Cached hook chain: {hook_name: [(priority, plugin_name, bound_method), ...]}
        # Per-hook_name granularity: unloading/reloading a plugin only invalidates
        # the hook_names it actually registered, not the entire cache. This avoids
        # performance抖动 during frequent hot-reloads (P2 optimization).
        self._hook_chain_cache: dict[str, list] = {}
        # Reverse index: {plugin_name: set(hook_name)} for targeted invalidation.
        self._plugin_hooks_index: dict[str, set[str]] = {}

        # Hot-reload: track EventBus subscriptions per plugin for clean unload
        # {plugin_name: [(event_type, callback), ...]}
        self._event_subscriptions: dict[str, list] = {}

        # Hot-reload: last known timestamp from .reload_ts file
        self._last_reload_ts: float = 0.0

        # Hot-reload: track config.json mtime per plugin for config-change detection
        # {plugin_name: mtime_float}
        self._config_mtimes: dict[str, float] = {}
        # Set of plugin names that need forced unload+reload due to config change
        self._config_reload_needed: set = set()

    @staticmethod
    def _plugin_wanted_by_manifest(manifest: dict, wanted: set[str], dir_name: str) -> bool:
        """True if this plugin should be imported for the given agent tool set.

        Loads when:
        - plugin name / directory name is in wanted
        - any declared tool name is in wanted
        - any tool has auto_register=true (always needed at boot)
        - plugin declares hooks (may run without an explicit tool toggle)
        """
        plugin_name = str(manifest.get("name") or dir_name)
        if plugin_name in wanted or dir_name in wanted:
            return True
        tools = manifest.get("tools") or []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            tname = str(tool.get("name") or "")
            if tname and tname in wanted:
                return True
            if tool.get("auto_register"):
                return True
        hooks = manifest.get("hooks") or []
        return bool(hooks)

    def discover_and_load(self, wanted_names: list[str] | None = None) -> list[str]:
        """
        Scan plugins/ directory for plugin directories, load each plugin.

        Discovery:
        1. Look for plugin.py in each subdirectory
        2. Find class with __plugin_meta__ (@register decorator)
        3. Use decorator metadata, auto-generate plugin.json

        Args:
            wanted_names: optional agent ``config.tools`` list. When provided,
                skip importing plugins that cannot contribute (not named, no
                matching tool, no auto_register, no hooks). Pass ``None`` to
                load every agent-capable plugin (tests / full scan).

        Returns:
            List of loaded plugin names.
        """
        loaded = []
        if not os.path.isdir(self.plugins_dir):
            logger.warning(f"[PluginManager] Plugins directory not found: {self.plugins_dir}")
            return loaded

        wanted: set[str] | None = None
        if wanted_names is not None:
            wanted = {str(n) for n in wanted_names if n}
        skipped = 0

        for entry in sorted(os.listdir(self.plugins_dir)):
            plugin_dir = os.path.join(self.plugins_dir, entry)
            if not os.path.isdir(plugin_dir):
                continue

            plugin_py = os.path.join(plugin_dir, "plugin.py")
            if not os.path.isfile(plugin_py):
                continue

            # Skip service_only plugins — they have no agent tools and should
            # never be imported into the agent process.
            plugin_json_path = os.path.join(plugin_dir, "plugin.json")
            _pmeta: dict | None = None
            if os.path.isfile(plugin_json_path):
                try:
                    with open(plugin_json_path, encoding="utf-8") as _f:
                        _pmeta = json.load(_f)
                    if _pmeta.get("service_only"):
                        logger.info(f"[PluginManager] Plugin '{entry}' is service_only, skipping agent load.")
                        continue
                except Exception:
                    _pmeta = None

            if wanted is not None:
                if _pmeta is not None:
                    if not self._plugin_wanted_by_manifest(_pmeta, wanted, entry):
                        skipped += 1
                        logger.debug(
                            "[PluginManager] Skipping '%s' (not in agent tools / no auto_register / no hooks)",
                            entry,
                        )
                        continue
                elif entry not in wanted:
                    # No manifest: only load when explicitly requested by dir name.
                    skipped += 1
                    logger.debug("[PluginManager] Skipping '%s' (no plugin.json and not in wanted)", entry)
                    continue

            try:
                name = self._load_plugin(plugin_dir, entry)
                if name:
                    loaded.append(name)
            except Exception as e:
                logger.error(f"[PluginManager] Failed to load plugin from {entry}: {e}", exc_info=True)

        self._hook_chain_cache.clear()
        self._plugin_hooks_index.clear()
        if skipped:
            logger.info(f"[PluginManager] Loaded {len(loaded)} plugins (skipped {skipped} unused): {loaded}")
        else:
            logger.info(f"[PluginManager] Loaded {len(loaded)} plugins: {loaded}")
        return loaded

    def _import_plugin_class(self, plugin_dir: str, dir_name: str):
        """Import plugin.py and find the @register class. Pure import — runs off-loop."""
        module_path = f"plugins.{dir_name}.plugin"
        try:
            plugin_module = importlib.import_module(module_path)
        except ImportError as e:
            logger.error(f"[PluginManager] Cannot import {module_path}: {e}")
            return None
        for attr_name in dir(plugin_module):
            attr = getattr(plugin_module, attr_name)
            if isinstance(attr, type) and hasattr(attr, "__plugin_meta__"):
                return attr
        logger.error(f"[PluginManager] No @register plugin class found in {module_path}")
        return None

    def _load_plugin(self, plugin_dir: str, dir_name: str) -> str | None:
        """Synchronous load (compat entry)."""
        plugin_class = self._import_plugin_class(plugin_dir, dir_name)
        if plugin_class is None:
            return None
        return self._load_new_style(plugin_class, plugin_dir, dir_name)

    async def discover_and_load_async(self, wanted_names: list[str] | None = None) -> list[str]:
        """Async discovery: import/IO/scan off the event loop; on_load + registration on it.

        Same policy as discover_and_load; keeps plugin.on_load() on the loop
        because plugins call asyncio.get_running_loop() there.
        """
        import asyncio as _asyncio

        loaded = []
        if not os.path.isdir(self.plugins_dir):
            logger.warning(f"[PluginManager] Plugins directory not found: {self.plugins_dir}")
            return loaded

        wanted: set[str] | None = None
        if wanted_names is not None:
            wanted = {str(n) for n in wanted_names if n}
        skipped = 0

        for entry in sorted(os.listdir(self.plugins_dir)):
            plugin_dir = os.path.join(self.plugins_dir, entry)
            if not os.path.isdir(plugin_dir):
                continue
            plugin_py = os.path.join(plugin_dir, "plugin.py")
            if not os.path.isfile(plugin_py):
                continue

            plugin_json_path = os.path.join(plugin_dir, "plugin.json")
            _pmeta: dict | None = None
            if os.path.isfile(plugin_json_path):
                try:
                    with open(plugin_json_path, encoding="utf-8") as _f:
                        _pmeta = json.load(_f)
                    if _pmeta.get("service_only"):
                        logger.info(f"[PluginManager] Plugin '{entry}' is service_only, skipping agent load.")
                        continue
                except Exception:
                    _pmeta = None

            if wanted is not None:
                if _pmeta is not None:
                    if not self._plugin_wanted_by_manifest(_pmeta, wanted, entry):
                        skipped += 1
                        logger.debug(
                            "[PluginManager] Skipping '%s' (not in agent tools / no auto_register / no hooks)",
                            entry,
                        )
                        continue
                elif entry not in wanted:
                    skipped += 1
                    logger.debug("[PluginManager] Skipping '%s' (no plugin.json and not in wanted)", entry)
                    continue

            try:
                # Import + scan + manifest IO on a worker thread; on_load and
                # hook/tool registration stay on the event loop.
                plugin_class = await _asyncio.to_thread(self._import_plugin_class, plugin_dir, entry)
                if plugin_class is None:
                    continue
                prepared = await _asyncio.to_thread(self._prepare_new_style, plugin_class, plugin_dir, entry)
                if prepared is None:
                    continue
                name = self._finalize_new_style(prepared)
                if name:
                    loaded.append(name)
            except Exception as e:
                logger.error(f"[PluginManager] Failed to load plugin from {entry}: {e}", exc_info=True)

        self._hook_chain_cache.clear()
        self._plugin_hooks_index.clear()
        if skipped:
            logger.info(f"[PluginManager] Loaded {len(loaded)} plugins (skipped {skipped} unused): {loaded}")
        else:
            logger.info(f"[PluginManager] Loaded {len(loaded)} plugins: {loaded}")
        return loaded

    # ------------------------------------------------------------------
    # Plugin loading
    # ------------------------------------------------------------------

    def _load_new_style(self, plugin_class, plugin_dir: str, dir_name: str) -> str | None:
        """Synchronous load (tests / direct calls): prepare off-loop, finalize on-loop."""
        prepared = self._prepare_new_style(plugin_class, plugin_dir, dir_name)
        if prepared is None:
            return None
        return self._finalize_new_style(prepared)

    def _prepare_new_style(self, plugin_class, plugin_dir: str, dir_name: str) -> dict | None:
        """
        Load a plugin decorated with @register.

        Steps:
        1. Extract metadata from __plugin_meta__
        2. Check enabled status from existing plugin.json (if any)
        3. Build Context object
        4. Instantiate plugin with Context
        5. Scan @tool, @hook, @on_event decorators
        6. Auto-generate/update plugin.json
        7. Call plugin.on_load()
        """
        from opensquad.plugin_api import (
            Context,
            ToolModuleWrapper,
            generate_plugin_json,
            get_event_methods,
            get_hook_methods,
            get_plugin_meta,
            get_tool_methods,
        )

        meta = get_plugin_meta(plugin_class)
        if not meta:
            return None

        name = meta["name"]
        plugin_type = meta["type"]

        # Check enabled status from existing plugin.json
        # Policy:
        # - Plugins that require a global service toggle (service/service_toggle) still honor enabled=false.
        # - Direct-import plugins (no service dependency) default to loadable, even if legacy enabled=false remains.
        manifest_path = os.path.join(plugin_dir, "plugin.json")
        if os.path.isfile(manifest_path):
            try:
                with open(manifest_path, encoding="utf-8") as f:
                    existing = json.load(f)
                enabled_flag = existing.get("enabled", True)
                has_service = bool(existing.get("service"))
                has_service_toggle = bool(existing.get("service_toggle", False))
                requires_global_toggle = has_service or has_service_toggle
                if (not enabled_flag) and requires_global_toggle:
                    logger.info(f"[PluginManager] Plugin '{name}' is globally disabled, skipping.")
                    return None
                if (not enabled_flag) and (not requires_global_toggle):
                    logger.info(
                        f"[PluginManager] Plugin '{name}' has legacy enabled=false but no global service dependency; "
                        f"loading by default."
                    )
            except Exception:
                pass

        # Build Context
        # project_root / data_dir MUST resolve to the writable workspace, not
        # the install dir. In frozen mode self.plugins_dir is
        # ``_internal/plugins/`` (read-only Program Files), so
        # ``os.path.dirname(self.plugins_dir)`` = ``_internal/`` — writing
        # data_dir / project_root there raises PermissionError. Use syscfg
        # (workspace-aware) for both. Falls back to dirname(plugins_dir) only
        # in dev mode where the workspace == the project root.
        try:
            project_root = syscfg.get_workspace()
        except Exception:
            project_root = os.path.dirname(self.plugins_dir)
        try:
            data_dir = syscfg.workspace_data_dir("plugins", name)
        except Exception:
            data_dir = os.path.join(project_root, "data", "plugins", name)

        event_bus = None
        try:
            from opensquad.events import bus

            event_bus = bus
        except ImportError:
            pass

        # Resolve config values: schema defaults first, then persisted user values override
        config_schema = meta.get("config_schema", {})
        config_values = {}
        for key, schema in config_schema.items():
            if isinstance(schema, dict) and "default" in schema:
                config_values[key] = schema["default"]

        # Load persisted config saved via UI (data/plugins/{name}/config.json)
        persisted_config_path = syscfg.workspace_data_dir("plugins", name, "config.json")
        if os.path.isfile(persisted_config_path):
            try:
                import json as _json

                with open(persisted_config_path, encoding="utf-8") as _f:
                    persisted = _json.load(_f)
                if isinstance(persisted, dict):
                    config_values.update(persisted)
            except Exception:
                pass

        context = Context(
            agent_id=self.agent_id,
            project_root=project_root,
            event_bus=event_bus,
            config=config_values,
            data_dir=data_dir,
            plugin_dir=plugin_dir,
        )

        # Instantiate plugin
        plugin_instance = plugin_class(context)
        plugin_instance.name = name
        plugin_instance.version = meta.get("version", "1.0.0")
        plugin_instance.plugin_type = plugin_type

        # Scan @tool methods -> build ToolModuleWrappers
        tool_wrappers = []
        tool_methods = get_tool_methods(plugin_instance)
        if tool_methods:
            ns_groups: dict[str, list] = {}
            for tm in tool_methods:
                ns = tm["meta"]["name"]
                if ns not in ns_groups:
                    ns_groups[ns] = []
                ns_groups[ns].append(tm)

            for ns, methods in ns_groups.items():
                wrapper = ToolModuleWrapper(plugin_instance, namespace=ns)
                # Set module-level docstring so ToolRegistry extended-mode prompt shows
                # the tool description instead of the internal ToolModuleWrapper class doc.
                tool_desc = methods[0]["meta"].get("description", "") or meta.get("description", "")
                if tool_desc:
                    wrapper.__doc__ = tool_desc
                for tm in methods:
                    wrapper.add_method(
                        method_name=tm["method_name"],
                        bound_method=tm["bound_method"],
                        doc=tm["bound_method"].__doc__ or "",
                    )
                tool_wrappers.append(
                    {
                        "wrapper": wrapper,
                        "namespace": ns,
                        "meta": methods[0]["meta"],
                    }
                )

        # Scan @hook methods
        hook_map = get_hook_methods(plugin_instance)

        # Scan @on_event methods (subscription happens in _finalize on the
        # event loop; EventBus.subscribe may schedule loop work).
        event_methods = get_event_methods(plugin_instance)

        # Auto-generate plugin.json
        generated = generate_plugin_json(plugin_class, plugin_instance)

        # If plugin has get_tool_modules() (proxy pattern), merge those tools
        if hasattr(plugin_instance, "get_tool_modules"):
            try:
                proxy_tools = plugin_instance.get_tool_modules()
                existing_names = {t["name"] for t in generated.get("tools", [])}
                for pt in proxy_tools:
                    pt_name = pt.get("name", "")
                    if pt_name and pt_name not in existing_names:
                        generated.setdefault("tools", []).append(
                            {
                                "name": pt_name,
                                "module": "proxy",
                                "level": pt.get("level", "extended"),
                                "auto_register": pt.get("auto_register", False),
                                "requires_agent_id": pt.get("requires_agent_id", False),
                            }
                        )
            except Exception as e:
                logger.debug(f"[PluginManager] get_tool_modules() failed for '{name}': {e}")

        # Preserve runtime-only fields from existing file (not declared in @register)
        if os.path.isfile(manifest_path):
            try:
                with open(manifest_path, encoding="utf-8") as f:
                    existing = json.load(f)
                generated["enabled"] = existing.get("enabled", generated.get("enabled", True))
                # Preserve service field (process management) - not part of @register metadata
                if "service" in existing:
                    generated["service"] = existing["service"]
                # Preserve service_toggle (launcher auto-start/stop control)
                if "service_toggle" in existing:
                    generated["service_toggle"] = existing["service_toggle"]
                # Preserve config.section (bridges plugin config to system_config.json)
                existing_section = existing.get("config", {}).get("section", "")
                if existing_section:
                    generated.setdefault("config", {})["section"] = existing_section
            except Exception:
                pass

        try:
            os.makedirs(plugin_dir, exist_ok=True)
            # ── plugin.json merge strategy ───────────────────────────────
            # The manifest is the source of truth for the Launcher, but it is
            # partly generated (from @register metadata) and partly hand-edited
            # at runtime (e.g. via the Plugin Manager UI). The merge policy:
            #
            # OVERWRITTEN by @register metadata (generated dict wins):
            #   name, display_name, version, type, description, author, tags,
            #   tools, hooks, config.schema, config_schema, contributes,
            #   dependencies
            #   → These reflect code reality and should not be hand-edited.
            #
            # PRESERVED from existing manifest (runtime-only fields):
            #   service          — process management config (port/entry/health)
            #   service_toggle   — launcher auto-start/stop control
            #   enabled          — per-node enable/disable (node_scope=single)
            #   config.section   — bridge to system_config.json (platform plugins)
            #   → These are runtime state, not declared in @register.
            #
            # If @register's `dependencies` is updated, it OVERWRITES any
            # hand-added extra deps in plugin.json. To add runtime-only deps,
            # update the @register decorator, not plugin.json.
            if os.path.isfile(manifest_path):
                with open(manifest_path, encoding="utf-8") as f:
                    existing_manifest = json.load(f)
                # Overlay generated metadata on top of existing, preserving runtime keys
                merged = existing_manifest.copy()
                merged.update(generated)
                # Explicitly preserve critical runtime-only fields even if generated touched them
                for runtime_key in ("service", "service_toggle", "enabled"):
                    if runtime_key in existing_manifest:
                        merged[runtime_key] = existing_manifest[runtime_key]
                with open(manifest_path, "w", encoding="utf-8") as f:
                    json.dump(merged, f, indent=2, ensure_ascii=False)
                logger.debug(f"[PluginManager] Merged plugin.json for '{name}'")
            else:
                with open(manifest_path, "w", encoding="utf-8") as f:
                    json.dump(generated, f, indent=2, ensure_ascii=False)
                logger.debug(f"[PluginManager] Created plugin.json for '{name}'")
        except Exception as e:
            logger.warning(f"[PluginManager] Failed to write plugin.json for '{name}': {e}")

        # Build full metadata
        metadata = generated.copy()
        metadata["_runtime"] = {
            "agent_id": self.agent_id,
            "project_root": project_root,
        }

        # Split boundary: everything above runs off the event loop
        # (import/IO/scan); on_load + registration stay on the loop because
        # several plugins call asyncio.get_running_loop() in on_load.
        return {
            "name": name,
            "plugin_type": plugin_type,
            "plugin_instance": plugin_instance,
            "metadata": metadata,
            "hook_map": hook_map,
            "tool_wrappers": tool_wrappers,
            "event_methods": event_methods,
            "plugin_dir": plugin_dir,
            "project_root": project_root,
            "version": meta.get("version", "?"),
        }

    def _finalize_new_style(self, prepared: dict) -> str | None:
        """Event-loop half of plugin loading: on_load + registration."""
        # on_load MUST run on the event loop: several plugins call
        # asyncio.get_running_loop() here (reminder/self_learn/websearch).
        plugin_instance = prepared["plugin_instance"]
        name = prepared["name"]
        plugin_type = prepared.get("plugin_type", "unknown")
        event_methods = prepared.get("event_methods") or []
        metadata = prepared["metadata"]
        hook_map = prepared["hook_map"]
        tool_wrappers = prepared["tool_wrappers"]
        plugin_dir = prepared["plugin_dir"]
        project_root = prepared["project_root"]
        plugin_instance.on_load()

        # EventBus subscription + shared-state writes stay on the loop.
        if event_methods:
            try:
                from opensquad.events import bus as _event_bus

                self._event_subscriptions[name] = []
                for em in event_methods:
                    _event_bus.subscribe(em["event_type"], em["bound_method"])
                    self._event_subscriptions[name].append((em["event_type"], em["bound_method"]))
                    logger.info(f"[PluginManager] Plugin '{name}': subscribed to EventBus '{em['event_type']}'")
            except Exception as _sub_e:
                logger.warning(f"[PluginManager] Event subscription failed for '{name}': {_sub_e}")

        # Record initial config.json mtime so check_reload_needed() can detect future changes
        persisted_config_path_for_mtime = os.path.join(project_root, "data", "plugins", name, "config.json")
        if os.path.isfile(persisted_config_path_for_mtime):
            with contextlib.suppress(Exception):
                self._config_mtimes[name] = os.path.getmtime(persisted_config_path_for_mtime)

        self._plugins[name] = {
            "plugin": plugin_instance,
            "metadata": metadata,
            "dir": plugin_dir,
            "hook_map": hook_map,
            "tool_wrappers": tool_wrappers,
        }
        self._index_plugin_hooks(name, hook_map)

        logger.info(
            f"[PluginManager] Loaded: {name} v{prepared.get('version', '?')} "
            f"(type={plugin_type}, tools={len(tool_wrappers)}, "
            f"hooks={list(hook_map.keys())}, events={len(event_methods)})"
        )
        return name

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def register_tools_to_agent(
        self,
        registry,
        agent_id: str,
        agent_tool_names: list[str] | None = None,
        agent_tool_levels: dict[str, str] | None = None,
    ) -> int:
        """
        Register plugin-provided tools to an agent's ToolRegistry.

        Args:
            registry: ToolRegistry instance
            agent_id: current agent's ID
            agent_tool_names: list of tool names from agent's config.json
            agent_tool_levels: per-tool level overrides from agent's config.json
                               e.g. {"email_assistant": "core", "quick_note": "extended"}
                               If a tool is not listed, falls back to the plugin's default level.

        Returns:
            Number of tool modules registered.
        """
        if agent_tool_names is None:
            agent_tool_names = []
        if agent_tool_levels is None:
            agent_tool_levels = {}

        count = 0

        # Per-plugin try-except isolation: a single plugin's failure (e.g.
        # registry.register raising on a malformed wrapper, or get_tool_modules
        # throwing an unexpected non-ImportError) MUST NOT cascade to break
        # tool registration for all subsequent plugins. This is the "class of
        # bugs" pattern documented in project_memory: single-point failure
        # cascading to global. Each plugin's registration is independent.
        for plugin_name, info in self._plugins.items():
            try:
                plugin = info["plugin"]

                # 1) Register @tool decorated methods via ToolModuleWrapper
                # plugin_name in agent_tool_names means "enable all tools from this plugin"
                # (set by the per-agent toggle in Plugin Manager UI)
                plugin_enabled_by_name = plugin_name in agent_tool_names

                for tw in info.get("tool_wrappers", []):
                    try:
                        wrapper = tw["wrapper"]
                        namespace = tw["namespace"]
                        meta = tw["meta"]
                        # Per-agent level override takes precedence over plugin default
                        level = agent_tool_levels.get(
                            namespace, agent_tool_levels.get(plugin_name, meta.get("level", "extended"))
                        )
                        auto_register = meta.get("auto_register", False)
                        enabled_by_namespace = namespace in agent_tool_names

                        if not (auto_register or plugin_enabled_by_name or enabled_by_namespace):
                            logger.info(
                                f"[PluginManager] Skipped tool '{namespace}' from plugin '{plugin_name}': "
                                f"not enabled for agent (auto_register={auto_register}, "
                                f"plugin_toggle={plugin_enabled_by_name}, namespace_toggle={enabled_by_namespace})"
                            )
                            continue

                        registry.register(wrapper, namespace, level=level)
                        count += 1
                        logger.info(
                            f"[PluginManager] Registered tool '{namespace}' from plugin '{plugin_name}' (level={level})"
                        )
                    except Exception as _tw_err:
                        logger.error(
                            f"[PluginManager] Failed to register @tool from plugin '{plugin_name}': "
                            f"{type(_tw_err).__name__}: {_tw_err}",
                            exc_info=True,
                        )

                # 2) Also check get_tool_modules() for proxy-pattern tools
                if hasattr(plugin, "get_tool_modules"):
                    try:
                        _proxy_descs = plugin.get_tool_modules()
                    except Exception as _gtm_err:
                        logger.error(
                            f"[PluginManager] get_tool_modules() raised for plugin '{plugin_name}': "
                            f"{type(_gtm_err).__name__}: {_gtm_err}",
                            exc_info=True,
                        )
                        _proxy_descs = []
                    for desc in _proxy_descs:
                        try:
                            tool_name = desc.get("name", "")
                            module = desc.get("module")
                            # Per-agent level override takes precedence over plugin default
                            level = agent_tool_levels.get(
                                tool_name, agent_tool_levels.get(plugin_name, desc.get("level", "extended"))
                            )
                            auto_register = desc.get("auto_register", False)
                            requires_agent_id = desc.get("requires_agent_id", False)
                            enabled_by_tool_name = tool_name in agent_tool_names

                            if not (auto_register or plugin_enabled_by_name or enabled_by_tool_name):
                                logger.info(
                                    f"[PluginManager] Skipped proxy tool '{tool_name}' from plugin '{plugin_name}': "
                                    f"not enabled for agent (auto_register={auto_register}, "
                                    f"plugin_toggle={plugin_enabled_by_name}, tool_toggle={enabled_by_tool_name})"
                                )
                                continue
                            if module is None:
                                logger.warning(
                                    f"[PluginManager] Skipped proxy tool '{tool_name}' from plugin '{plugin_name}': module is None"
                                )
                                continue

                            registry.register(module, tool_name, level=level)
                            if requires_agent_id and hasattr(module, "set_agent_id") and agent_id:
                                module.set_agent_id(agent_id)

                            count += 1
                            logger.info(
                                f"[PluginManager] Registered tool '{tool_name}' from "
                                f"plugin '{plugin_name}' (proxy, level={level})"
                            )
                        except Exception as _desc_err:
                            logger.error(
                                f"[PluginManager] Failed to register proxy tool from plugin '{plugin_name}': "
                                f"{type(_desc_err).__name__}: {_desc_err}",
                                exc_info=True,
                            )
            except Exception as _plugin_err:
                logger.error(
                    f"[PluginManager] Plugin '{plugin_name}' registration failed, "
                    f"continuing with remaining plugins: "
                    f"{type(_plugin_err).__name__}: {_plugin_err}",
                    exc_info=True,
                )

        return count

    # ------------------------------------------------------------------
    # Hook chain execution
    # ------------------------------------------------------------------

    def _build_hook_chain(self) -> dict[str, list]:
        """
        Build the hook chain from all loaded plugins.

        Returns:
            {hook_name: [(priority, plugin_name, bound_method), ...]}
            sorted by (-priority, plugin_name) so higher-priority handlers run first,
            with alphabetical plugin name as the tiebreaker.
        """
        chain: dict[str, list] = {}

        for name in sorted(self._plugins.keys()):
            hook_map = self._plugins[name].get("hook_map", {})
            for hook_name, methods in hook_map.items():
                if hook_name not in chain:
                    chain[hook_name] = []
                for method in methods:
                    priority = 0
                    if hasattr(method, "__hook_meta__"):
                        for entry in method.__hook_meta__:
                            if entry.get("hook_name") == hook_name:
                                priority = entry.get("priority", 0)
                                break
                    chain[hook_name].append((priority, name, method))

        for hook_name in chain:
            chain[hook_name].sort(key=lambda t: (-t[0], t[1]))

        return chain

    def _build_hook_chain_for(self, hook_name: str) -> list:
        """Build the handler list for a single hook_name (on-demand cache fill)."""
        handlers: list = []
        for name in sorted(self._plugins.keys()):
            hook_map = self._plugins[name].get("hook_map", {})
            if hook_name not in hook_map:
                continue
            for method in hook_map[hook_name]:
                priority = 0
                if hasattr(method, "__hook_meta__"):
                    for entry in method.__hook_meta__:
                        if entry.get("hook_name") == hook_name:
                            priority = entry.get("priority", 0)
                            break
                handlers.append((priority, name, method))
        handlers.sort(key=lambda t: (-t[0], t[1]))
        return handlers

    def _invalidate_hooks_for_plugin(self, plugin_name: str) -> None:
        """Drop cached hook chains for hook_names registered by plugin_name."""
        affected = self._plugin_hooks_index.pop(plugin_name, set())
        for hn in affected:
            self._hook_chain_cache.pop(hn, None)

    def _index_plugin_hooks(self, plugin_name: str, hook_map: dict[str, list]) -> None:
        """Record which hook_names a plugin registered, for targeted invalidation."""
        if hook_map:
            self._plugin_hooks_index[plugin_name] = set(hook_map.keys())
        else:
            self._plugin_hooks_index.pop(plugin_name, None)

    async def run_hook(self, hook_name: str, context: dict[str, Any]) -> dict[str, Any]:
        """
        Execute a hook across all registered plugins (chain pattern).

        Handlers are sorted by (-priority, plugin_name) for deterministic execution order.
        A handler can stop the chain by setting context['__stop__'] = True.

        Each handler is wrapped in asyncio.wait_for with a per-handler timeout
        (default 10s) so a single slow handler cannot stall the main request
        path (on_before_llm / on_after_llm / on_before_send etc. are all on the
        user-facing turn loop). A timeout is logged but does NOT break the
        chain — subsequent handlers still run.

        Args:
            hook_name: name of the hook (e.g. "on_message_received")
            context: the context dict to pass through the chain

        Returns:
            The final context dict after all hooks have processed it.
        """
        if hook_name not in self._hook_chain_cache:
            self._hook_chain_cache[hook_name] = self._build_hook_chain_for(hook_name)

        handlers = self._hook_chain_cache.get(hook_name, [])
        if not handlers:
            return context

        for priority, plugin_name, method in handlers:
            try:
                try:
                    context = await asyncio.wait_for(method(context), timeout=_HOOK_HANDLER_TIMEOUT)
                except asyncio.TimeoutError:
                    logger.error(
                        f"[PluginManager] Hook '{hook_name}' handler in plugin '{plugin_name}' "
                        f"timed out after {_HOOK_HANDLER_TIMEOUT}s, skipping (handler did not return)"
                    )
            except Exception as e:
                logger.error(f"[PluginManager] Hook '{hook_name}' error in plugin '{plugin_name}': {e}", exc_info=True)
            if context.get("__stop__"):
                logger.info(
                    f"[PluginManager] Hook '{hook_name}' chain stopped by plugin '{plugin_name}' (priority={priority})"
                )
                break

        return context

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_plugin(self, name: str) -> Any | None:
        """Get a loaded plugin by name."""
        info = self._plugins.get(name)
        return info["plugin"] if info else None

    def get_all_plugins(self) -> dict[str, Any]:
        """Return all loaded plugins as {name: plugin_instance}."""
        return {name: info["plugin"] for name, info in self._plugins.items()}

    def get_plugins_by_type(self, plugin_type: str) -> list[Any]:
        """Return all loaded plugins of a specific type."""
        return [info["plugin"] for info in self._plugins.values() if info["metadata"].get("type") == plugin_type]

    def get_plugin_metadata(self, name: str) -> dict[str, Any] | None:
        """Get the plugin.json metadata for a plugin."""
        info = self._plugins.get(name)
        return info["metadata"] if info else None

    def list_plugins(self) -> list[dict[str, str]]:
        """Return a summary list of all loaded plugins."""
        result = []
        for name, info in self._plugins.items():
            meta = info["metadata"]
            result.append(
                {
                    "name": name,
                    "display_name": meta.get("display_name", name),
                    "version": meta.get("version", "0.0.0"),
                    "type": meta.get("type", ""),
                    "description": meta.get("description", ""),
                }
            )
        return result

    # ------------------------------------------------------------------
    # Hot-Reload support
    # ------------------------------------------------------------------

    def unload_plugin(self, name: str, registry=None) -> bool:
        """
        Fully unload a plugin: call on_unload(), unsubscribe EventBus,
        unregister tools from ToolRegistry, remove from _plugins.

        Args:
            name: plugin name to unload
            registry: ToolRegistry instance (needed to unregister tools)

        Returns:
            True if successfully unloaded.
        """
        info = self._plugins.get(name)
        if not info:
            logger.warning(f"[PluginManager] Cannot unload '{name}': not loaded")
            return False

        plugin = info["plugin"]

        # 1) Call on_unload()
        try:
            plugin.on_unload()
        except Exception as e:
            logger.error(f"[PluginManager] on_unload() error for '{name}': {e}")

        # 2) Unsubscribe from EventBus
        if name in self._event_subscriptions:
            try:
                from opensquad.events import bus

                for event_type, callback in self._event_subscriptions[name]:
                    bus.unsubscribe(event_type, callback)
                    logger.debug(f"[PluginManager] Unsubscribed '{name}' from '{event_type}'")
            except Exception as e:
                logger.error(f"[PluginManager] EventBus unsubscribe error for '{name}': {e}")
            del self._event_subscriptions[name]

        # 3) Unregister tools from ToolRegistry
        if registry:
            for tw in info.get("tool_wrappers", []):
                registry.unregister(tw["namespace"])

            if hasattr(plugin, "get_tool_modules"):
                try:
                    for desc in plugin.get_tool_modules():
                        tool_name = desc.get("name", "")
                        if tool_name:
                            registry.unregister(tool_name)
                except Exception:
                    pass

        # 4) Remove from _plugins and invalidate only this plugin's hook chains
        del self._plugins[name]
        self._invalidate_hooks_for_plugin(name)
        logger.info(f"[PluginManager] Unloaded plugin '{name}'")
        return True

    def reload_plugins(
        self, registry=None, agent_id: str = "", agent_tool_names: list[str] | None = None
    ) -> dict[str, str]:
        """
        Compare disk plugin.json enabled state vs in-memory _plugins.
        Unload newly-disabled plugins, load newly-enabled plugins.

        Args:
            registry: ToolRegistry instance
            agent_id: current agent ID
            agent_tool_names: tool names from agent config

        Returns:
            {"loaded": [...], "unloaded": [...]} summary
        """
        if agent_tool_names is None:
            agent_tool_names = []

        result = {"loaded": [], "unloaded": []}

        # Force-reload plugins whose config.json changed while they were already loaded.
        # Unload them here so the normal "load newly-enabled" loop below re-instantiates
        # them with the fresh config.
        for name in list(self._config_reload_needed):
            self._config_reload_needed.discard(name)
            if name in self._plugins:
                logger.info(f"[PluginManager] Force-reloading '{name}' due to config change")
                self.unload_plugin(name, registry=registry)
                result["unloaded"].append(name)

        # Scan all plugin directories on disk
        disk_plugins = {}
        for entry in sorted(os.listdir(self.plugins_dir)):
            plugin_dir = os.path.join(self.plugins_dir, entry)
            if not os.path.isdir(plugin_dir):
                continue
            if not os.path.isfile(os.path.join(plugin_dir, "plugin.py")):
                continue

            manifest_path = os.path.join(plugin_dir, "plugin.json")
            enabled = True
            plugin_name = entry

            if os.path.isfile(manifest_path):
                try:
                    with open(manifest_path, encoding="utf-8") as f:
                        manifest = json.load(f)
                    # service_only plugins are never loaded into agents
                    if manifest.get("service_only"):
                        continue
                    plugin_name = manifest.get("name", entry)

                    enabled_flag = manifest.get("enabled", True)
                    has_service = bool(manifest.get("service"))
                    has_service_toggle = bool(manifest.get("service_toggle", False))
                    requires_global_toggle = has_service or has_service_toggle

                    # Same policy as _load_new_style:
                    # direct-import plugins are loadable by default, even with legacy enabled=false.
                    enabled = enabled_flag if requires_global_toggle else True
                except Exception:
                    pass

            disk_plugins[plugin_name] = {
                "enabled": enabled,
                "dir": plugin_dir,
                "dir_name": entry,
            }

        # Unload plugins that are now disabled on disk OR whose directory was deleted (uninstalled)
        for name in list(self._plugins.keys()):
            disk = disk_plugins.get(name)
            if not disk or not disk["enabled"]:
                self.unload_plugin(name, registry=registry)
                result["unloaded"].append(name)

        # Load plugins that are now enabled on disk but not loaded in memory
        for name, disk in disk_plugins.items():
            if disk["enabled"] and name not in self._plugins:
                try:
                    loaded_name = self._load_plugin(disk["dir"], disk["dir_name"])
                    if loaded_name and registry and loaded_name in self._plugins:
                        info = self._plugins[loaded_name]
                        plugin = info["plugin"]

                        # plugin name in agent_tool_names means "enable all tools from this plugin"
                        plugin_enabled_by_name = loaded_name in agent_tool_names

                        for tw in info.get("tool_wrappers", []):
                            ns = tw["namespace"]
                            meta = tw["meta"]
                            level = meta.get("level", "extended")
                            if meta.get("auto_register") or plugin_enabled_by_name or ns in agent_tool_names:
                                registry.register(tw["wrapper"], ns, level=level)

                        if hasattr(plugin, "get_tool_modules"):
                            for desc in plugin.get_tool_modules():
                                t_name = desc.get("name", "")
                                module = desc.get("module")
                                level = desc.get("level", "extended")
                                req_aid = desc.get("requires_agent_id", False)
                                if (
                                    desc.get("auto_register") or plugin_enabled_by_name or t_name in agent_tool_names
                                ) and module:
                                    registry.register(module, t_name, level=level)
                                    if req_aid and hasattr(module, "set_agent_id") and agent_id:
                                        module.set_agent_id(agent_id)

                    if loaded_name:
                        result["loaded"].append(loaded_name)
                except Exception as e:
                    logger.error(f"[PluginManager] Failed to reload plugin '{name}': {e}", exc_info=True)

        if result["loaded"] or result["unloaded"]:
            self._hook_chain_cache.clear()
            self._plugin_hooks_index.clear()
            logger.info(f"[PluginManager] Reload complete: loaded={result['loaded']}, unloaded={result['unloaded']}")

        return result

    def check_reload_needed(self) -> bool:
        """
        Check if the .reload_ts file has been updated since last check,
        OR if any loaded plugin's config.json has been updated.
        Called periodically by AgentRunner.

        Returns:
            True if reload is needed (timestamp changed or config changed).
        """
        needed = False

        # Check .reload_ts signal file
        ts_file = os.path.join(self.plugins_dir, ".reload_ts")
        if os.path.isfile(ts_file):
            try:
                mtime = os.path.getmtime(ts_file)
                if mtime > self._last_reload_ts:
                    self._last_reload_ts = mtime
                    needed = True
            except Exception:
                pass

        # Check per-plugin config.json mtime for already-loaded plugins.
        # MUST resolve project_root the same way _load_new_style does
        # (syscfg.get_workspace() in production). Otherwise the mtime recorded
        # at load time (workspace data dir) is compared against a DIFFERENT
        # file (install dir / dev src dir), whose mtime never matches — every
        # idle tick then looks like a config change and force-reloads the
        # plugin, clearing the tool-schema cache in a tight loop. Fall back to
        # dirname(plugins_dir) only when syscfg is unavailable (dev mode).
        try:
            project_root = syscfg.get_workspace()
        except Exception:
            project_root = os.path.dirname(self.plugins_dir)
        for plugin_name in list(self._plugins.keys()):
            config_path = os.path.join(project_root, "data", "plugins", plugin_name, "config.json")
            if not os.path.isfile(config_path):
                continue
            try:
                mtime = os.path.getmtime(config_path)
                last = self._config_mtimes.get(plugin_name, 0.0)
                if mtime > last:
                    self._config_mtimes[plugin_name] = mtime
                    if last > 0.0:
                        # Only force-reload if we've seen this file before
                        # (i.e., this is a genuine update, not first-time discovery)
                        self._config_reload_needed.add(plugin_name)
                        logger.info(
                            f"[PluginManager] Config change detected for '{plugin_name}', scheduling force-reload"
                        )
                        needed = True
            except Exception:
                pass

        return needed
